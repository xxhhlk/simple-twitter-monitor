# WorkBuddy_AI 免费活动监控器

轻量 Python 服务，轮询 `@WorkBuddy_AI` 的新推文，识别以下免费活动并发送通知：

- 免费领取积分、额度、Token、Credits
- 模型免费使用、免费开放、免费计划、永久免费
- 免费试用、限时免费、免费赠送
- 免费期延期、试用期延长
- 开源模型、无费用使用

默认使用 TwitterAPI.io 的 `advanced_search` 接口；状态和推文 ID 保存在 SQLite，重启后不会重复通知。

## 1. 安装

```bash
cd workbuddy-ai-free-monitor
python -m venv .venv
.venv\\Scripts\\python.exe -m pip install -r requirements.txt
```

Linux/macOS 将最后一条改为：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

## 2. 配置

复制 `.env.example`，然后设置环境变量。**不要把真实 API Key 写进代码或提交到 Git。**

PowerShell：

```powershell
$env:TWITTERAPI_IO_KEY = "你的 TwitterAPI.io Key"
$env:TARGET_ACCOUNT = "WorkBuddy_AI"
$env:DB_PATH = "D:\\data\\workbuddy-monitor.db"
$env:POLL_SECONDS = "600"
$env:WEBHOOK_TYPE = "dingtalk"
$env:WEBHOOK_URL = "钉钉机器人 Webhook"
$env:DINGTALK_SECRET = "钉钉机器人加签密钥"
$env:DRY_RUN = "true"
```

Linux/macOS：

```bash
export TWITTERAPI_IO_KEY='你的 TwitterAPI.io Key'
export TARGET_ACCOUNT='WorkBuddy_AI'
export DB_PATH='./monitor.db'
export POLL_SECONDS='600'
export WEBHOOK_TYPE='dingtalk'
export WEBHOOK_URL='钉钉机器人 Webhook'
export DINGTALK_SECRET='钉钉机器人加签密钥'
export DRY_RUN='true'
```

`WEBHOOK_TYPE` 支持：

- `dingtalk`：钉钉加签机器人；
- `wecom`：企业微信机器人；
- `generic`：发送通用 JSON，适合自建 Webhook。

## 3. 首次验证

先用干跑模式，只抓取和打印命中项，不发送通知：

```bash
python monitor.py --once
```

确认规则后：

```powershell
$env:DRY_RUN = "false"
.venv\\Scripts\\python.exe monitor.py
```

Linux/macOS：

```bash
DRY_RUN=false .venv/bin/python monitor.py
```

## 5. 在 GitHub Actions 上运行

仓库已包含 `.github/workflows/monitor.yml`：默认每 10 分钟运行一次，也可以在 GitHub 仓库的 **Actions** 页面手动启动 `WorkBuddy free activity monitor`。定时运行使用 UTC 时间，并且只有 workflow 合并到默认分支后才会生效。

在仓库 **Settings → Secrets and variables → Actions** 中添加：

| 名称 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `TWITTERAPI_IO_KEY` | Secret | 是 | TwitterAPI.io API Key |
| `WEBHOOK_URL` | Secret | 否 | 通知 Webhook；不设置时命中活动会保留为待通知 |
| `DINGTALK_SECRET` | Secret | 否 | 钉钉机器人加签密钥 |
| `WEBHOOK_TYPE` | Variable | 否 | `dingtalk`、`wecom` 或 `generic`，默认 `dingtalk` |
| `DRY_RUN` | Variable | 否 | 设为 `true` 可只记录、不发送通知 |

可选变量 `TARGET_ACCOUNT`、`INITIAL_HOURS`、`EXCLUDE_REPLIES` 用于覆盖对应默认设置。SQLite 状态库通过 GitHub Actions cache 在运行之间恢复，workflow 会串行处理同一分支的运行，避免同时读写状态。首次运行没有缓存时会从最近 24 小时开始查询；如果缓存被清理，重建状态时可能再次通知回溯窗口内的活动。

## 6. 运行参数

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `TWITTERAPI_IO_KEY` | 必填 | TwitterAPI.io API Key |
| `TARGET_ACCOUNT` | `WorkBuddy_AI` | 不含 `@` 的账号名 |
| `POLL_SECONDS` | `600` | 轮询间隔，最小 30 秒 |
| `INITIAL_HOURS` | `24` | 首次启动回溯时长 |
| `DB_PATH` | `monitor.db` | SQLite 状态文件 |
| `EXCLUDE_REPLIES` | `true` | 是否排除回复推文 |
| `WEBHOOK_TYPE` | `generic` | 通知类型 |
| `WEBHOOK_URL` | 空 | 通知 Webhook |
| `DINGTALK_SECRET` | 空 | 钉钉加签密钥 |
| `DRY_RUN` | `false` | 只记录不发送 |

## 7. 规则说明

规则采用“本地关键词/正则初筛”，不调用 LLM，因此无额外模型费用，且行为可审计。每条推文可以命中多个分类；通知会携带分类和命中的规则。

关键词是高召回策略，可能把“免费但不适用于中国地区”“仅限新用户”“需要绑定支付方式”等内容也通知出来。通知中保留原文和链接，由人工确认最终条件。

后续如需减少误报，可增加第二阶段 LLM 判断，但不要替换本地初筛。

## 8. 安全与可靠性

- API Key 只放环境变量；不要提交 `.env`、SQLite 文件或日志。
- 先使用 `DRY_RUN=true` 验证查询和规则。
- 服务请求失败时不推进 `last_checked`，下一轮自动重试，避免静默漏报。
- Webhook 发送失败时保留 SQLite 待通知记录；下轮先重试，成功后才标记已通知。
- 未配置 `WEBHOOK_URL` 时同样保留待通知记录，不会把命中活动误标成已发送。
- 分页最多处理 100 页，避免异常响应造成意外费用。
- TwitterAPI.io 当前时间查询使用 `since_time:<Unix秒>` / `until_time:<Unix秒>`，不是旧式 `since:...Z`。
- API 额度、价格和返回范围以 TwitterAPI.io 控制台及文档为准。

接口文档：<https://docs.twitterapi.io/api-reference/endpoint/tweet_advanced_search>
