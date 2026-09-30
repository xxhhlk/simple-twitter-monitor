import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from monitor import (
    StateStore,
    build_query,
    classify_free_activity,
    process_once,
    tweet_url,
)


class ClassifierTests(unittest.TestCase):
    def test_covers_all_requested_free_activity_types(self):
        cases = {
            "Get 500 free credits for the new model": "免费领取/赠送额度",
            "The model is free forever": "免费计划/升级",
            "Try the model for free this week": "限时免费/试用",
            "We extended the free trial until Friday": "免费延期/延长",
            "The model is now open source": "开源/无费用",
            "免费模型限时开放，送积分": "模型免费使用",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                categories = {item["category"] for item in classify_free_activity(text)}
                self.assertIn(expected, categories)

    def test_ignores_unrelated_post(self):
        self.assertEqual(classify_free_activity("We improved the dashboard today"), [])


class QueryTests(unittest.TestCase):
    def test_uses_unix_time_syntax(self):
        since = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
        until = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)
        query = build_query("WorkBuddy_AI", since, until)
        self.assertIn("from:WorkBuddy_AI", query)
        self.assertIn("since_time:1790726400", query)
        self.assertIn("until_time:1790730000", query)
        self.assertNotIn("since:", query)
        self.assertNotIn("until:", query)
        self.assertIn("-is:reply", query)


class StoreTests(unittest.TestCase):
    def test_deduplicates_tweet_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(str(Path(directory) / "state.db"))
            tweet = {"id": "1", "text": "free credits"}
            matches = classify_free_activity(tweet["text"])
            self.assertTrue(store.save_tweet(tweet, "WorkBuddy_AI", matches))
            self.assertFalse(store.save_tweet(tweet, "WorkBuddy_AI", matches))
            count = store.connection.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
            self.assertEqual(count, 1)
            store.close()


class ProcessingTests(unittest.TestCase):
    def test_notifies_only_matching_new_tweets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(str(Path(directory) / "state.db"))
            client = Mock()
            client.fetch.return_value = [
                {"id": "1", "text": "A free model is now live", "createdAt": "Wed Sep 30 00:00:00 +0000 2026"},
                {"id": "2", "text": "New UI is available", "createdAt": "Wed Sep 30 00:01:00 +0000 2026"},
            ]
            notifier = Mock()
            settings = Mock(
                account="WorkBuddy_AI",
                initial_hours=24,
                dry_run=False,
                exclude_replies=True,
            )
            settings.db_path = str(Path(directory) / "state.db")
            with patch("monitor.utc_now", return_value=datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)):
                count = process_once(settings, store, client, notifier)
            self.assertEqual(count, 1)
            notifier.send.assert_called_once()
            notified = store.connection.execute(
                "SELECT tweet_id, notified FROM tweets WHERE tweet_id = '1'"
            ).fetchone()
            self.assertEqual(notified, ("1", 1))
            store.close()

    def test_failed_notification_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(str(Path(directory) / "state.db"))
            tweet = {"id": "1", "text": "Get free credits", "createdAt": "Wed Sep 30 00:00:00 +0000 2026"}
            matches = classify_free_activity(tweet["text"])
            self.assertTrue(store.save_tweet(tweet, "WorkBuddy_AI", matches))
            notifier = Mock()
            notifier.send.side_effect = [RuntimeError("temporary webhook failure"), True]
            settings = SimpleNamespace(
                account="WorkBuddy_AI",
                dry_run=False,
                initial_hours=24,
                exclude_replies=True,
                db_path=str(Path(directory) / "state.db"),
            )
            with self.assertRaises(RuntimeError):
                process_once(settings, store, Mock(), notifier)
            self.assertFalse(store.is_notified("1"))
            MockClient = Mock()
            MockClient.fetch.return_value = []
            with patch("monitor.utc_now", return_value=datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)):
                process_once(settings, store, MockClient, notifier)
            self.assertTrue(store.is_notified("1"))
            store.close()

    def test_fallback_url(self):
        self.assertEqual(tweet_url({"id": "123"}, "WorkBuddy_AI"), "https://x.com/WorkBuddy_AI/status/123")


if __name__ == "__main__":
    unittest.main()
