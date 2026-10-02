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

Scweet 使用 X 登录 Cookie 中的 `auth_token`。它相当于账号凭据：建议使用专门用于监控的 X 账号，并只把 Cookie 放在 `.env` 或 VPS 上权限受限的配置文件中。不要写进代码、提交到仓库、发到聊天里或放进截图。X 的接口和非官方客户端可能变化，Cookie 也可能过期或触发 X 的风控。

如果运行环境的日志显示 `Auth bootstrap ... response_status=403`，可以额外设置同一浏览器登录会话中的 `ct0` Cookie 为 `SCWEET_CT0`。程序会直接导入 `auth_token` 和 `ct0`，跳过 X 首页的认证初始化。两项 Cookie 都是敏感凭据；此方式只能跳过首页初始化，不能解决后续 X API 请求也被拒绝的情况。

本机 PowerShell 示例：

```powershell
$env:SCWEET_AUTH_TOKEN = "你的 X auth_token Cookie 值"
# 仅当初始化遇到 403 时设置，必须与 auth_token 来自同一会话
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
# 仅当初始化遇到 403 时取消注释，并填同一会话的 ct0 Cookie
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

程序会自动读取与 `monitor.py` 同目录的 `.env` 文件，`.env.example` 是可复制的模板。已有的系统环境变量优先于 `.env` 中的同名配置；真实 `.env` 已加入 `.gitignore`，不会被 Git 跟踪。

本机可复制模板后填写：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

`WEBHOOK_TYPE` 支持：

- `dingtalk`：钉钉自定义机器人；配置加签时同时设置 `DINGTALK_SECRET`。
- `wecom`：企业微信机器人。
- `generic`：通用 JSON Webhook。

## 3. 本机运行

先进行一轮干跑，确认抓取和筛选结果。`DRY_RUN=true` 时不会发送通知：

```powershell
.venv\Scripts\python.exe monitor.py --once
```

确认后设为 `DRY_RUN=false`。持续运行可省略 `--once`；VPS 部署使用 systemd 定时器每天执行一轮。

## 4. 部署到 VPS（Ubuntu/Debian）

服务以无登录权限的 `workbuddy-monitor` 系统用户运行；代码和 `.env` 放在 `/opt/simple-twitter-monitor`，SQLite 状态放在 `/var/lib/workbuddy-monitor`。定时器默认每天 UTC 00:00 运行一次（北京时间 08:00）；若 VPS 关机错过一次，systemd 会在下次启动时补跑。

先安装依赖、创建系统用户并拉取仓库：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
sudo useradd --system --home-dir /var/lib/workbuddy-monitor --create-home --shell /usr/sbin/nologin workbuddy-monitor
sudo git clone https://github.com/xxhhlk/simple-twitter-monitor.git /opt/simple-twitter-monitor
sudo python3 -m venv /opt/simple-twitter-monitor/.venv
sudo /opt/simple-twitter-monitor/.venv/bin/pip install -r /opt/simple-twitter-monitor/requirements.txt
```

复制 `.env.example` 并填入凭据。设置为 root 所有、服务组可读（`0640`），这样 monitor 用户可加载配置，但其他用户无法读取：

```bash
sudo install -o root -g workbuddy-monitor -m 640 /opt/simple-twitter-monitor/.env.example /opt/simple-twitter-monitor/.env
sudoedit /opt/simple-twitter-monitor/.env
sudo chown root:workbuddy-monitor /opt/simple-twitter-monitor/.env
sudo chmod 640 /opt/simple-twitter-monitor/.env
```

确保 `SCWEET_AUTH_TOKEN`、钉钉 `WEBHOOK_URL` 和 `DINGTALK_SECRET` 已填写。第一次先保留 `DRY_RUN=true`，这样会抓取和筛选，但不发通知。

安装 systemd 单元并手动试跑一次：

```bash
sudo install -m 644 /opt/simple-twitter-monitor/deploy/systemd/workbuddy-monitor.service /etc/systemd/system/
sudo install -m 644 /opt/simple-twitter-monitor/deploy/systemd/workbuddy-monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start workbuddy-monitor.service
sudo journalctl -u workbuddy-monitor.service -n 100 --no-pager
```

确认抓取成功后，将 `.env` 里的 `DRY_RUN=true` 改为 `false`，再启用每日定时运行：

```bash
sudoedit /opt/simple-twitter-monitor/.env
sudo chown root:workbuddy-monitor /opt/simple-twitter-monitor/.env
sudo chmod 640 /opt/simple-twitter-monitor/.env
sudo systemctl enable --now workbuddy-monitor.timer
sudo systemctl list-timers workbuddy-monitor.timer
```

需要立即再跑一轮时使用 `sudo systemctl start workbuddy-monitor.service`；查看最近日志使用 `sudo journalctl -u workbuddy-monitor.service -n 100 --no-pager`。更新代码后执行 `sudo git -C /opt/simple-twitter-monitor pull --ff-only`，再按需更新依赖并执行 `sudo systemctl daemon-reload`。

## 5. 配置参数

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `SCWEET_AUTH_TOKEN` | 必填 | X 登录 Cookie 中的 `auth_token` |
| `SCWEET_CT0` | 空 | 可选，X 首页初始化返回 403 时使用同一会话中的 `ct0` Cookie 跳过初始化 |
| `TARGET_ACCOUNT` | `WorkBuddy_AI` | 不含 `@` 的目标账号名 |
| `POLL_SECONDS` | `600` | 本机持续运行时的轮询间隔，最小 30 秒；VPS 推荐使用 systemd 定时器每天运行 `--once` |
| `INITIAL_HOURS` | `72` | 每轮至少回溯的小时数，用于覆盖最近 3 天、调度延迟和短暂失败 |
| `MAX_TWEETS_PER_FETCH` | `100` | 每轮最多读取的推文数；如账号在回查窗口内发帖较多，可调大。达到上限时，程序会确认最早推文已覆盖窗口起点，否则本轮失败并提示提高上限 |
| `DB_PATH` | `monitor.db` | 去重记录、检查游标和待通知内容 |
| `SCWEET_DB_PATH` | `scweet_state.db` | Scweet 内部状态库；其中可能保存认证状态，不要公开或上传 |
| `EXCLUDE_REPLIES` | `true` | 是否排除回复推文 |
| `WEBHOOK_TYPE` | `generic` | 通知类型；VPS 示例配置为 `dingtalk` |
| `WEBHOOK_URL` | 空 | 通知 Webhook |
| `DINGTALK_SECRET` | 空 | 钉钉机器人加签密钥 |
| `REQUEST_TIMEOUT` | `30` | Webhook 请求超时秒数 |
| `DRY_RUN` | `false` | `true` 时只记录、不发送 |

## 6. 筛选与通知

规则使用本地关键词和正则表达式筛选，每条推文可以匹配多个类别。它偏向召回，可能会提示有地区、资格或支付条件的活动；通知会保留推文原文和链接，便于人工确认。

Webhook 发送失败时，推文会保留为待通知状态，后续运行会重试。没有配置 `WEBHOOK_URL` 或启用干跑时，程序也不会把命中内容标记为已发送。

## 7. 安全提示

- `SCWEET_AUTH_TOKEN`、`SCWEET_CT0` 和通知 Webhook 都是秘密；VPS 的 `.env` 应由 root 所有、权限为 `0640`，并只允许 monitor 服务组读取。
- 不要将真实凭据放入 `.env.example`、代码、日志、Issue 或公开仓库。
- Scweet 是非官方客户端，X 的接口、登录验证和限流策略变化可能导致抓取中断。遇到失败时先查看 `journalctl` 日志；不要在日志中打印 Cookie。
- SQLite 文件已由 `.gitignore` 排除，不要提交这些文件。
