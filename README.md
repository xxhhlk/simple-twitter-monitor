# WorkBuddy_AI 免费活动监控器

监控 `@WorkBuddy_AI` 的新推文，识别免费活动并通过钉钉、企业微信或通用 Webhook 推送。抓取使用锁定版本的 [Scweet 5.8.1](https://github.com/Altimis/Scweet)，不需要 TwitterAPI.io API Key；推文去重、检查游标和待发送通知保存在 SQLite。

监控规则覆盖免费领取积分、额度或 Token、模型免费使用、免费试用、限时免费、延期以及开源等关键词。筛选在本地完成，不调用 LLM。

## 1. 安装

```bash
python -m venv .venv
```

PowerShell：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Linux/macOS：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

## 2. 配置抓取凭据

Scweet 使用 X 登录 Cookie 中的 `auth_token`。它相当于账号凭据：建议使用专门用于监控的 X 账号，并且只把这个值放在本机环境变量或 GitHub Actions Secret 中。不要写进代码、提交到仓库、发到聊天里或放进截图。X 的接口和非官方客户端可能变化，Cookie 也可能过期或触发 X 的风控。

如果 GitHub Actions 日志显示 `Auth bootstrap ... response_status=403`，可以额外设置同一浏览器登录会话中的 `ct0` Cookie 为 `SCWEET_CT0`。程序会直接导入 `auth_token` 和 `ct0`，跳过 X 首页的认证初始化。两项 Cookie 都是敏感凭据，只通过本机环境变量或 GitHub Actions Secrets 配置；不要发到聊天、截图或代码仓库。此方式只能绕过首页初始化，若后续 X API 请求也被 runner 拒绝，仍需改用能访问 X 的运行环境。

本机 PowerShell 示例：

```powershell
$env:SCWEET_AUTH_TOKEN = "你的 X auth_token Cookie 值"
# 仅当 GitHub Actions 初始化遇到 403 时设置，必须与 auth_token 来自同一会话
# $env:SCWEET_CT0 = "同一会话的 ct0 Cookie 值"
$env:TARGET_ACCOUNT = "WorkBuddy_AI"
$env:DB_PATH = ".\monitor.db"
$env:SCWEET_DB_PATH = ".\scweet_state.db"
$env:MAX_TWEETS_PER_FETCH = "100"
$env:WEBHOOK_TYPE = "dingtalk"
$env:WEBHOOK_URL = "钉钉机器人 Webhook"
$env:DINGTALK_SECRET = "钉钉机器人加签密钥"
$env:DRY_RUN = "true"
```

Linux/macOS 示例：

```bash
export SCWEET_AUTH_TOKEN='你的 X auth_token Cookie 值'
# 仅当 GitHub Actions 初始化遇到 403 时取消注释，并填同一会话的 ct0 Cookie
# export SCWEET_CT0='同一会话的 ct0 Cookie 值'
export TARGET_ACCOUNT='WorkBuddy_AI'
export DB_PATH='./monitor.db'
export SCWEET_DB_PATH='./scweet_state.db'
export MAX_TWEETS_PER_FETCH='100'
export WEBHOOK_TYPE='dingtalk'
export WEBHOOK_URL='钉钉机器人 Webhook'
export DINGTALK_SECRET='钉钉机器人加签密钥'
export DRY_RUN='true'
```

`.env.example` 列出了全部常用配置；程序直接读取环境变量，不会自动加载 `.env` 文件。

`WEBHOOK_TYPE` 支持：

- `dingtalk`：钉钉自定义机器人；配置加签时同时设置 `DINGTALK_SECRET`。
- `wecom`：企业微信机器人。
- `generic`：通用 JSON Webhook。

## 3. 本机运行

先进行一轮干跑，确认抓取和筛选结果。`DRY_RUN=true` 时不会发送通知：

```powershell
.venv\Scripts\python.exe monitor.py --once
```

确认后设为 `DRY_RUN=false`。持续运行可省略 `--once`；GitHub Actions 则每次只运行一轮。

## 4. 在 GitHub Actions 上运行

仓库中的 `.github/workflows/monitor.yml` 默认每天运行一次（UTC 00:00，即北京时间 08:00），也可以在 GitHub 仓库的 **Actions** 页面手动启动 `WorkBuddy free activity monitor`。定时任务使用 UTC，且 workflow 需要先推送到默认分支才会生效；GitHub 的定时任务可能延迟启动。

在仓库 **Settings → Secrets and variables → Actions** 中设置：

| 名称 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `SCWEET_AUTH_TOKEN` | Secret | 是 | X 登录 Cookie 中的 `auth_token` 值 |
| `SCWEET_CT0` | Secret | 否 | 仅当 Actions 初始化返回 403 时设置；需与 `SCWEET_AUTH_TOKEN` 来自同一登录会话 |
| `WEBHOOK_URL` | Secret | 否 | 钉钉/企业微信/通用通知地址；不设置时命中内容会保留为待通知 |
| `DINGTALK_SECRET` | Secret | 否 | 钉钉机器人加签密钥 |
| `TARGET_ACCOUNT` | Variable | 否 | 要监控的账号，默认 `WorkBuddy_AI` |
| `INITIAL_HOURS` | Variable | 否 | 每轮至少回溯小时数，默认 `72`（最近 3 天） |
| `MAX_TWEETS_PER_FETCH` | Variable | 否 | 每轮最多读取的推文数，默认 `100` |
| `EXCLUDE_REPLIES` | Variable | 否 | 是否排除回复，默认 `true` |
| `WEBHOOK_TYPE` | Variable | 否 | `dingtalk`、`wecom` 或 `generic`，默认 `dingtalk` |
| `DRY_RUN` | Variable | 否 | `true` 时只抓取和记录、不发送；默认 `false` |

`monitor.db` 会通过 GitHub Actions cache 在不同运行之间恢复，保存去重记录和检查游标。Scweet 自己的 SQLite 状态文件放在 runner 临时目录，不进入缓存；X 凭据只从 Secret 注入。每轮至少回看最近 `INITIAL_HOURS` 小时；如果游标更早，则从游标处补查。这个重叠窗口可以弥补定时任务延迟或短暂失败，重复推文由 SQLite 去重。清除缓存后仍可能重复通知。

## 5. 配置参数

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `SCWEET_AUTH_TOKEN` | 必填 | X 登录 Cookie 中的 `auth_token` |
| `SCWEET_CT0` | 空 | 可选，X 首页初始化返回 403 时使用同一会话中的 `ct0` Cookie 跳过初始化 |
| `TARGET_ACCOUNT` | `WorkBuddy_AI` | 不含 `@` 的目标账号名 |
| `POLL_SECONDS` | `600` | 本机持续运行时的轮询间隔，最小 30 秒；Actions 使用 workflow 的 cron |
| `INITIAL_HOURS` | `72` | 每轮至少回溯的小时数，用于覆盖最近 3 天、调度延迟和短暂失败 |
| `MAX_TWEETS_PER_FETCH` | `100` | 每轮最多读取的推文数；如账号在回查窗口内发帖较多，可调大。达到上限时，程序会确认最早推文已覆盖窗口起点，否则本轮失败并提示提高上限 |
| `DB_PATH` | `monitor.db` | 去重记录、检查游标和待通知内容 |
| `SCWEET_DB_PATH` | `scweet_state.db` | Scweet 内部状态库；其中可能保存认证状态，不要公开或上传 |
| `EXCLUDE_REPLIES` | `true` | 是否排除回复推文 |
| `WEBHOOK_TYPE` | `generic` | 通知类型；Actions 默认值为 `dingtalk` |
| `WEBHOOK_URL` | 空 | 通知 Webhook |
| `DINGTALK_SECRET` | 空 | 钉钉机器人加签密钥 |
| `REQUEST_TIMEOUT` | `30` | Webhook 请求超时秒数 |
| `DRY_RUN` | `false` | `true` 时只记录、不发送 |

## 6. 筛选与通知

规则使用本地关键词和正则表达式筛选，每条推文可以匹配多个类别。它偏向召回，可能会提示有地区、资格或支付条件的活动；通知会保留推文原文和链接，便于人工确认。

Webhook 发送失败时，推文会保留为待通知状态，后续运行会重试。没有配置 `WEBHOOK_URL` 或启用干跑时，程序也不会把命中内容标记为已发送。

## 7. 安全提示

- `SCWEET_AUTH_TOKEN` 和通知 Webhook 都是秘密；只通过环境变量或 GitHub Actions Secrets 配置。
- 不要将真实凭据放入 `.env.example`、代码、日志、Issue 或公开仓库。
- Scweet 是非官方客户端，X 的接口、登录验证和限流策略变化可能导致抓取中断。遇到失败时先查看 Actions 日志；不要在日志中打印 Cookie。
- SQLite 文件已由 `.gitignore` 排除，不要提交这些文件。
