#!/usr/bin/env python3
"""Monitor @WorkBuddy_AI for free AI/model promotions and send alerts."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable

import requests
from dotenv import load_dotenv

LOG = logging.getLogger("workbuddy-free-monitor")

DEFAULT_ACCOUNT = "WorkBuddy_AI"
DEFAULT_POLL_SECONDS = 600
DEFAULT_INITIAL_HOURS = 72
DEFAULT_DB_PATH = "monitor.db"
DEFAULT_SCWEET_DB_PATH = "scweet_state.db"
DEFAULT_MAX_TWEETS_PER_FETCH = 100
DEFAULT_TIMEOUT_SECONDS = 30

# Broad recall first. Classification is deliberately explainable and local.
CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "免费领取/赠送额度": (
        "free credits",
        "free credit",
        "free quota",
        "free tokens",
        "free token",
        "free points",
        r"free.{0,20}(credits?|tokens?|points?|quota)",
        r"(claim|get|receive).{0,20}(free credits?|free tokens?|free points?|free quota)",
        r"(free|赠送|送|免费领).{0,20}(credits?|tokens?|points?|quota|积分|额度)",
        "免费额度",
        "免费积分",
        "赠送额度",
        "送积分",
        "送额度",
        "领取积分",
        "领取额度",
        "免费 token",
        "免费 tokens",
        r"免费.{0,20}(积分|额度|token|tokens|credits?|quota)",
    ),
    "模型免费使用": (
        "free model",
        "free models",
        "free access",
        "use .* for free",
        "try .* for free",
        "available for free",
        r"(model|模型).{0,20}(free|免费)",
        r"(free|免费).{0,20}(model|模型)",
        "免费模型",
        "模型免费",
        "免费使用",
        "免费开放",
        "免费体验",
    ),
    "限时免费/试用": (
        "free trial",
        "trial for free",
        "extended trial",
        "trial extended",
        "limited[- ]time free",
        "free for a limited time",
        "free this week",
        "free until",
        "free to try",
        "complimentary",
        "giveaway",
        r"\bfree\b[\s\S]{0,60}\b(?:for|over|during)\s+(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s*(?:days?|weeks?|months?)\b",
        r"free.{0,60}(today|this week|this month|until|through)",
        r"(today|this week|this month|until|through).{0,60}free",
        "免费试用",
        "限时免费",
        "限时开放",
        "本周免费",
        "限时体验",
        "免费赠送",
    ),
    "免费延期/延长": (
        "extended free",
        "free period extended",
        "free trial extended",
        "extended .* free",
        "free .* extended",
        "extension of free",
        "free extension",
        r"(extend|extended|extension).{0,20}(free|trial)",
        r"(free|trial).{0,20}(extend|extended|extension)",
        "延期免费",
        "免费延期",
        "延长免费",
        "免费期延长",
        "试用期延长",
    ),
    "免费计划/升级": (
        "free tier",
        "free plan",
        "free to use",
        "free forever",
        "forever free",
        "free upgrade",
        r"(free|免费).{0,20}(forever|永久)",
        r"(forever|永久).{0,20}(free|免费)",
        "免费套餐",
        "免费计划",
        "免费升级",
        "永久免费",
    ),
    "开源/无费用": (
        "open source",
        "open-source",
        "no cost",
        "at no cost",
        "without charge",
        "free of charge",
        "永久免费使用",
        "免费开源",
        "开源模型",
    ),
}

# Terms that make a generic "free" mention more likely to be an offer.
OFFER_TERMS = (
    "free",
    "免费",
    "trial",
    "试用",
    "credit",
    "quota",
    "token",
    "积分",
    "额度",
    "模型",
    "model",
    "open source",
    "开源",
    "giveaway",
    "complimentary",
    "forever",
    "tier",
    "plan",
    "extension",
    "extended",
    "延期",
    "延长",
    "赠送",
    "送",
)


def _compile_terms() -> dict[str, tuple[re.Pattern[str], ...]]:
    compiled: dict[str, tuple[re.Pattern[str], ...]] = {}
    for category, terms in CATEGORY_TERMS.items():
        compiled[category] = tuple(re.compile(term, re.IGNORECASE) for term in terms)
    return compiled


COMPILED_CATEGORY_TERMS = _compile_terms()


@dataclass(frozen=True)
class Settings:
    auth_token: str
    csrf_token: str = ""
    account: str = DEFAULT_ACCOUNT
    db_path: str = DEFAULT_DB_PATH
    scweet_db_path: str = DEFAULT_SCWEET_DB_PATH
    poll_seconds: int = DEFAULT_POLL_SECONDS
    initial_hours: int = DEFAULT_INITIAL_HOURS
    max_tweets_per_fetch: int = DEFAULT_MAX_TWEETS_PER_FETCH
    webhook_url: str = ""
    webhook_type: str = "generic"
    request_timeout: int = DEFAULT_TIMEOUT_SECONDS
    exclude_replies: bool = True
    dry_run: bool = False


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=False)
    auth_token = os.environ.get("SCWEET_AUTH_TOKEN", "").strip()
    if not auth_token:
        raise ValueError("缺少 SCWEET_AUTH_TOKEN 环境变量（X 账号的 auth_token Cookie）")

    poll_seconds = int(os.environ.get("POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))
    initial_hours = int(os.environ.get("INITIAL_HOURS", str(DEFAULT_INITIAL_HOURS)))
    max_tweets_per_fetch = int(
        os.environ.get("MAX_TWEETS_PER_FETCH", str(DEFAULT_MAX_TWEETS_PER_FETCH))
    )
    request_timeout = int(os.environ.get("REQUEST_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))
    if poll_seconds < 30:
        raise ValueError("POLL_SECONDS 不能小于 30")
    if initial_hours < 1:
        raise ValueError("INITIAL_HOURS 不能小于 1")
    if max_tweets_per_fetch < 1:
        raise ValueError("MAX_TWEETS_PER_FETCH 不能小于 1")

    return Settings(
        auth_token=auth_token,
        csrf_token=os.environ.get("SCWEET_CT0", "").strip(),
        account=os.environ.get("TARGET_ACCOUNT", DEFAULT_ACCOUNT).strip().lstrip("@"),
        db_path=os.environ.get("DB_PATH", DEFAULT_DB_PATH).strip(),
        scweet_db_path=os.environ.get("SCWEET_DB_PATH", DEFAULT_SCWEET_DB_PATH).strip(),
        poll_seconds=poll_seconds,
        initial_hours=initial_hours,
        max_tweets_per_fetch=max_tweets_per_fetch,
        webhook_url=os.environ.get("WEBHOOK_URL", "").strip(),
        webhook_type=os.environ.get("WEBHOOK_TYPE", "generic").strip().lower(),
        request_timeout=request_timeout,
        exclude_replies=parse_bool(os.environ.get("EXCLUDE_REPLIES"), True),
        dry_run=parse_bool(os.environ.get("DRY_RUN"), False),
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_unix_seconds(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp())


def build_query(account: str, since_time: datetime, until_time: datetime, exclude_replies: bool = True) -> str:
    """Build the legacy query syntax for callers that still import this helper."""
    parts = [
        f"from:{account}",
        f"since_time:{to_unix_seconds(since_time)}",
        f"until_time:{to_unix_seconds(until_time)}",
    ]
    if exclude_replies:
        parts.append("-is:reply")
    return " ".join(parts)


def normalize_text(tweet: dict[str, Any]) -> str:
    text = tweet.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def classify_free_activity(text: str) -> list[dict[str, str]]:
    """Return all matching categories and exact trigger terms."""
    if not text or not any(term in text.lower() for term in OFFER_TERMS):
        return []

    matches: list[dict[str, str]] = []
    for category, patterns in COMPILED_CATEGORY_TERMS.items():
        terms = [pattern.pattern for pattern in patterns if pattern.search(text)]
        if terms:
            matches.append({"category": category, "terms": terms})
    return matches


def tweet_id(tweet: dict[str, Any]) -> str:
    value = tweet.get("id") or tweet.get("id_str") or tweet.get("tweet_id")
    return str(value).strip() if value is not None else ""


def tweet_author(tweet: dict[str, Any], fallback: str) -> str:
    author = tweet.get("author") or tweet.get("user")
    if isinstance(author, dict):
        return str(
            author.get("userName")
            or author.get("username")
            or author.get("screen_name")
            or fallback
        )
    if isinstance(author, str) and author:
        return author
    return fallback


def tweet_created_at(tweet: dict[str, Any]) -> str:
    value = tweet.get("createdAt") or tweet.get("created_at") or tweet.get("timestamp") or ""
    return str(value)


def tweet_url(tweet: dict[str, Any], account: str) -> str:
    url = tweet.get("url") or tweet.get("tweet_url")
    if isinstance(url, str) and url:
        return url
    identifier = tweet_id(tweet)
    return f"https://x.com/{account}/status/{identifier}" if identifier else ""


def parse_created_at(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def format_time(value: str) -> str:
    parsed = parse_created_at(value)
    if not parsed:
        return value or "未知时间"
    beijing = parsed.astimezone(timezone(timedelta(hours=8)))
    return beijing.strftime("%Y-%m-%d %H:%M:%S")


class StateStore:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            parent = Path(path).expanduser().resolve().parent
            parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tweets (
                tweet_id TEXT PRIMARY KEY,
                author TEXT NOT NULL,
                created_at TEXT,
                text TEXT NOT NULL,
                tweet_url TEXT,
                categories TEXT NOT NULL,
                matched_terms TEXT NOT NULL,
                notified INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def get_last_checked(self) -> datetime | None:
        row = self.connection.execute("SELECT value FROM state WHERE key = 'last_checked'").fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(row[0]).astimezone(timezone.utc)
        except ValueError:
            LOG.warning("last_checked 无法解析，将重新建立检查窗口")
            return None

    def set_last_checked(self, value: datetime) -> None:
        self.connection.execute(
            "INSERT INTO state(key, value) VALUES('last_checked', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (value.astimezone(timezone.utc).isoformat(),),
        )
        self.connection.commit()

    def save_tweet(
        self,
        tweet: dict[str, Any],
        account: str,
        matches: list[dict[str, str]],
    ) -> bool:
        identifier = tweet_id(tweet)
        if not identifier:
            return False
        categories = [item["category"] for item in matches]
        terms = sorted({term for item in matches for term in item["terms"]})
        try:
            self.connection.execute(
                """
                INSERT INTO tweets(
                    tweet_id, author, created_at, text, tweet_url,
                    categories, matched_terms, notified, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    identifier,
                    tweet_author(tweet, account),
                    tweet_created_at(tweet),
                    normalize_text(tweet),
                    tweet_url(tweet, account),
                    json.dumps(categories, ensure_ascii=False),
                    json.dumps(terms, ensure_ascii=False),
                    utc_now().isoformat(),
                ),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def is_notified(self, identifier: str) -> bool:
        row = self.connection.execute(
            "SELECT notified FROM tweets WHERE tweet_id = ?", (identifier,)
        ).fetchone()
        return bool(row and row[0])

    def pending_tweets(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT tweet_id, author, created_at, text, tweet_url, categories, matched_terms
            FROM tweets
            WHERE notified = 0 AND categories != '[]'
            ORDER BY first_seen_at ASC
            """
        ).fetchall()
        pending: list[dict[str, Any]] = []
        for identifier, author, created_at, text, url, categories, terms in rows:
            try:
                parsed_categories = json.loads(categories)
                parsed_terms = json.loads(terms)
            except (TypeError, ValueError):
                LOG.warning("跳过损坏的待通知记录: %s", identifier)
                continue
            pending.append(
                {
                    "id": identifier,
                    "author": {"userName": author},
                    "createdAt": created_at,
                    "text": text,
                    "url": url,
                    "_categories": parsed_categories,
                    "_terms": parsed_terms,
                }
            )
        return pending

    def mark_notified(self, identifier: str) -> None:
        self.connection.execute("UPDATE tweets SET notified = 1 WHERE tweet_id = ?", (identifier,))
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()


class ScweetClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: Any | None = None
        self._completion_check_installed = False
        self._run_status: dict[str, bool] | None = None

    def _install_completion_check(self) -> None:
        """Restore Scweet's run status that get_profile_tweets drops from its return value."""
        if self._completion_check_installed:
            return

        runner = getattr(self.client, "_runner", None)
        run_profile_tweets = getattr(runner, "run_profile_tweets", None)
        if not callable(run_profile_tweets):
            raise RuntimeError("当前 Scweet 版本无法确认时间线抓取是否完整")

        async def run_and_validate(*args: Any, **kwargs: Any) -> Any:
            response = await run_profile_tweets(*args, **kwargs)
            if not isinstance(response, dict):
                raise RuntimeError("Scweet 未返回时间线抓取状态")

            completed = response.get("completed") is True
            limit_reached = response.get("limit_reached") is True
            self._run_status = {"completed": completed, "limit_reached": limit_reached}
            if not completed and not limit_reached:
                raise RuntimeError(
                    "Scweet 时间线抓取未完成 "
                    f"(completed={response.get('completed')!r}, "
                    f"limit_reached={response.get('limit_reached')!r})"
                )
            return response

        runner.run_profile_tweets = run_and_validate
        self._completion_check_installed = True

    def fetch(self, since_time: datetime, until_time: datetime) -> list[dict[str, Any]]:
        if self.client is None:
            try:
                from Scweet import Scweet
            except ImportError as exc:
                raise RuntimeError("未安装 Scweet；请执行 pip install -r requirements.txt") from exc

            client_options: dict[str, Any] = {"db_path": self.settings.scweet_db_path}
            if self.settings.csrf_token:
                # Supplying both browser cookies skips the X homepage bootstrap, which
                # can return 403 from hosted CI runner IPs even when the cookies work locally.
                client_options["cookies"] = {
                    "auth_token": self.settings.auth_token,
                    "ct0": self.settings.csrf_token,
                }
            else:
                client_options["auth_token"] = self.settings.auth_token
            self.client = Scweet(**client_options)

        try:
            self._install_completion_check()
            self._run_status = None
            rows = self.client.get_profile_tweets(
                [self.settings.account],
                limit=self.settings.max_tweets_per_fetch,
                include_replies=not self.settings.exclude_replies,
            )
        except Exception as exc:
            raise RuntimeError(f"Scweet 获取 @{self.settings.account} 时间线失败: {exc}") from exc

        if not isinstance(rows, list):
            raise RuntimeError("Scweet 返回的时间线不是列表")

        parsed_tweets: list[tuple[datetime, dict[str, Any]]] = []
        for tweet in rows:
            if not isinstance(tweet, dict):
                raise RuntimeError("Scweet 返回了无法识别的推文记录")
            created_at = parse_created_at(tweet_created_at(tweet))
            if created_at is None:
                raise RuntimeError("Scweet 返回的推文缺少可解析的发布时间；本轮游标不会前移")
            parsed_tweets.append((created_at, tweet))

        status = self._run_status
        if status is None:
            raise RuntimeError("Scweet 未提供时间线抓取状态；本轮游标不会前移")
        if status["limit_reached"]:
            oldest_tweet_at = min((created_at for created_at, _ in parsed_tweets), default=None)
            if oldest_tweet_at is None or oldest_tweet_at > since_time:
                oldest_text = oldest_tweet_at.isoformat() if oldest_tweet_at else "无推文"
                raise RuntimeError(
                    f"Scweet 达到每轮上限 {self.settings.max_tweets_per_fetch} 条，"
                    f"最早推文时间 {oldest_text} 晚于检查起点 {since_time.isoformat()}；"
                    "可能遗漏窗口内推文，请调高 MAX_TWEETS_PER_FETCH 后重试"
                )
            LOG.warning(
                "Scweet 达到每轮上限 %d 条，但已覆盖检查起点；继续处理本轮窗口",
                self.settings.max_tweets_per_fetch,
            )
        elif not status["completed"]:
            raise RuntimeError("Scweet 时间线抓取未完整结束；本轮游标不会前移")

        return [
            tweet
            for created_at, tweet in parsed_tweets
            if since_time <= created_at <= until_time
        ]


class Notifier:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _sign_dingtalk(url: str, secret: str) -> str:
        import base64
        import hmac
        import urllib.parse

        timestamp = str(int(time.time() * 1000))
        sign_text = f"{timestamp}\n{secret}"
        digest = hmac.new(secret.encode(), sign_text.encode(), hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}timestamp={timestamp}&sign={sign}"

    def send(
        self,
        tweet: dict[str, Any],
        matches: list[dict[str, str]],
        account: str,
    ) -> bool:
        if not self.settings.webhook_url:
            LOG.warning("未配置 WEBHOOK_URL，命中内容保留为待通知状态")
            return False

        text = normalize_text(tweet)
        author = tweet_author(tweet, account)
        url = tweet_url(tweet, account)
        categories = [item["category"] for item in matches]
        terms = sorted({term for item in matches for term in item["terms"]})
        content = (
            f"【WorkBuddy_AI 免费活动】\n"
            f"账号：@{author}\n"
            f"时间：{format_time(tweet_created_at(tweet))}\n"
            f"类型：{'、'.join(categories)}\n"
            f"命中：{', '.join(terms)}\n\n"
            f"{text}\n\n"
            f"链接：{url}"
        )
        if self.settings.webhook_type == "dingtalk":
            secret = os.environ.get("DINGTALK_SECRET", "")
            target_url = self._sign_dingtalk(self.settings.webhook_url, secret) if secret else self.settings.webhook_url
            payload = {
                "msgtype": "markdown",
                "markdown": {"title": "WorkBuddy_AI 免费活动", "text": content.replace("\n", "  \n")},
                "at": {"isAtAll": False},
            }
        elif self.settings.webhook_type == "wecom":
            target_url = self.settings.webhook_url
            payload = {"msgtype": "text", "text": {"content": content}}
        else:
            target_url = self.settings.webhook_url
            payload = {"text": content, "content": content, "tweet": tweet, "matches": matches}

        response = requests.post(target_url, json=payload, timeout=self.settings.request_timeout)
        if response.status_code >= 400:
            raise RuntimeError(f"通知 Webhook 返回 HTTP {response.status_code}: {response.text[:500]}")
        LOG.info("已发送通知: %s", url)
        return True


def _retry_pending_notifications(
    settings: Settings,
    store: StateStore,
    notifier: Notifier,
) -> int:
    """Retry records saved before a webhook failure or process restart."""
    retried = 0
    for tweet in store.pending_tweets():
        identifier = tweet["id"]
        categories = tweet.pop("_categories", [])
        terms = tweet.pop("_terms", [])
        matches = [{"category": category, "terms": terms} for category in categories]
        if settings.dry_run:
            LOG.info("DRY_RUN=1，跳过待通知记录: %s", tweet_url(tweet, settings.account))
            continue
        if notifier.send(tweet, matches, settings.account):
            store.mark_notified(identifier)
            retried += 1
    return retried


def process_once(settings: Settings, store: StateStore, client: ScweetClient, notifier: Notifier) -> int:
    until_time = utc_now()
    retried_count = _retry_pending_notifications(settings, store, notifier)
    last_checked = store.get_last_checked()
    minimum_since = until_time - timedelta(hours=settings.initial_hours)
    # The overlap catches tweets if a scheduled run was delayed or an earlier
    # version advanced the cursor after an incomplete upstream response.
    since_time = min(last_checked, minimum_since) if last_checked else minimum_since
    if since_time >= until_time:
        since_time = until_time - timedelta(seconds=1)

    LOG.info("检查 @%s: %s - %s", settings.account, since_time.isoformat(), until_time.isoformat())
    tweets = client.fetch(since_time, until_time)
    # Notify chronologically even if the upstream timeline order changes.
    tweets.sort(
        key=lambda item: parse_created_at(tweet_created_at(item))
        or datetime.min.replace(tzinfo=timezone.utc)
    )
    matched_count = retried_count
    for tweet in tweets:
        identifier = tweet_id(tweet)
        text = normalize_text(tweet)
        matches = classify_free_activity(text)
        if not store.save_tweet(tweet, settings.account, matches):
            continue
        if not matches:
            continue
        matched_count += 1
        author = tweet_author(tweet, settings.account)
        LOG.info("命中免费活动 [%s] @%s: %s", ", ".join(item["category"] for item in matches), author, text[:160])
        if settings.dry_run:
            LOG.info("DRY_RUN=1，跳过发送: %s", tweet_url(tweet, settings.account))
            continue
        if notifier.send(tweet, matches, settings.account):
            store.mark_notified(identifier)

    # Advance only after the complete page set has been processed.
    store.set_last_checked(until_time)
    LOG.info("本轮获取 %d 条，命中/补发 %d 条", len(tweets), matched_count)
    return matched_count


def run(settings: Settings) -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    store = StateStore(settings.db_path)
    client = ScweetClient(settings)
    notifier = Notifier(settings)
    try:
        while True:
            started = time.monotonic()
            try:
                process_once(settings, store, client, notifier)
            except Exception:
                LOG.exception("本轮检查失败；保留上次游标，下一轮将重试")
            elapsed = time.monotonic() - started
            wait_seconds = max(1, settings.poll_seconds - int(elapsed))
            LOG.info("等待 %d 秒后检查", wait_seconds)
            time.sleep(wait_seconds)
    except KeyboardInterrupt:
        LOG.info("监控已停止")
    finally:
        store.close()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="监控 @WorkBuddy_AI 的免费 AI 活动")
    parser.add_argument("--once", action="store_true", help="只执行一轮检查")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        settings = load_settings()
    except (ValueError, OSError) as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.once:
        store = StateStore(settings.db_path)
        try:
            process_once(settings, store, ScweetClient(settings), Notifier(settings))
        except Exception:
            LOG.exception("单轮检查失败")
            return 1
        finally:
            store.close()
        return 0
    run(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
