<div align="center">

<a id="中文"></a>

# WorkBuddy 自动签到

用 GitHub Actions 每天自动领 WorkBuddy 签到积分 —— 一次配置，之后不用管。

[![每日签到](https://github.com/ArkURL/workbuddy-daily-sign/actions/workflows/signin.yml/badge.svg)](https://github.com/ArkURL/workbuddy-daily-sign/actions/workflows/signin.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/GitHub%20Actions-ubuntu--latest-2088FF.svg)](https://github.com/ArkURL/workbuddy-daily-sign/actions)

[中文](#中文) · [English](#english)

</div>

## 特性

- **全云端** —— 不用开机、不用挂着桌面端、不装本地定时任务
- **凭据自动续期** —— `refreshToken` 每次轮换，最新的那套存在 Actions Cache
- **幂等** —— 先查状态再领，重复运行不会多领
- **失败会喊你** —— 出错自动开 Issue，恢复后自动关闭
- **零依赖** —— 纯 Python 标准库，Runner 上什么都不用装
- **防休眠** —— 每月自动提交一次，避免定时任务被判定不活跃而停用

## 工作原理

```
本机（跑一次）                    GitHub Actions（每天）
extract_local.py  ──Secret──▶   Cache 优先，回落 Secret
  借桌面端运行时解密               │ 临期才刷新令牌
                                  │ 查状态 → 未签才领
                                  └ 新凭据写回 Cache
```

WorkBuddy 把令牌存成 `$wbEncrypted` 信封（AES-256-GCM），解密必须借客户端自带的 Electron
运行时，Runner 上没有它。所以凭据只在本机导一次，之后全靠 `refreshToken` 续期。

## 快速开始

**1. 导出凭据**（需要登录过 WorkBuddy 桌面端、Python 3、GitHub CLI）

```bash
git clone https://github.com/ArkURL/workbuddy-daily-sign.git
cd workbuddy-daily-sign
python3 tools/extract_local.py --set-secret
```

没装 gh 就去掉 `--set-secret`，把输出的那一行 JSON 手动存成仓库 Secret `WORKBUDDY_AUTH`。

**2. 跑一次**

仓库 → Actions → 每日签到 → Run workflow。日志里出现 `CLAIMED` 或 `ALREADY` 就算通了。

**3. 放着不管**

| 时间（北京时间） | 做什么 |
|---|---|
| 每天 00:05 | 签到 |
| 每天 08:00 | 兜底重跑（接口幂等） |
| 每月 1 号 | 仓库保活 |

## 命令

| 命令 | 说明 |
|---|---|
| `python3 wb_signin.py auto` | 签到（workflow 用的就是这个） |
| `python3 wb_signin.py status` | 只查状态 |
| `python3 wb_signin.py doctor` | 离线检查凭据格式，不联网 |

本地调试：`WORKBUDDY_STATE=/tmp/wb.json python3 wb_signin.py status`。

## 凭据字段

`WORKBUDDY_AUTH` 是一行 JSON：

| 字段 | 说明 |
|---|---|
| `uid` | 账号 uid，对应请求头 `X-User-Id` |
| `accessToken` | 访问令牌，30 天有效 |
| `refreshToken` | 续期令牌，60 天有效，每次续期都会换新 |
| `accessExpiresAt` / `refreshExpiresAt` | 过期时间（epoch 秒） |
| `domain` | 请求头 `X-Domain`，一般是 `www.workbuddy.cn` |
| `enterpriseId` | 企业号才需要，个人号没有 |
| `endpoint` | 接口地址，默认 `https://copilot.tencent.com` |

## 排错

| 现象 | 含义 / 处理 |
|---|---|
| `ALREADY` | 今天已签过，正常 |
| `CLAIMED` | 刚领到，正常 |
| `INACTIVE` | 当前没有签到活动，正常 |
| `NETWORK` | 网络或服务端抖动，下次定时运行自动重试 |
| `NO_AUTH` | Secret 缺失或不是合法 JSON，重新导出 |
| `AUTH_REJECTED` / `AUTH_EXPIRED` | 令牌被拒或已过期，重新导出并更新 Secret |

## 常见问题

- **Cache 丢了怎么办？** 自动回落到 Secret 里的初始凭据；只有那份也过期时才需要你重新导出。
- **桌面端要我重新登录？** 正常现象：续期会轮换 `refreshToken`，谁先刷谁赢。重新登录后按需再导一次即可。
- **WorkBuddy 改版失效？** 签到会失败并开 Issue，不会静默跳过。
- **凭据安全吗？** 仓库里没有任何凭据。但 `extract_local.py` 的输出等于账号密码，别贴到 issue、聊天记录或任何公开地方。

## 目录结构

```
wb_signin.py                     CI 侧签到客户端（纯标准库）
tools/extract_local.py           本机一次性凭据导出（借客户端运行时解密）
.github/workflows/signin.yml     每日签到 + Cache 轮换 + 失败开 Issue
.github/workflows/keepalive.yml  每月保活，防止定时任务被停用
```

## 致谢与许可

签到接口、解密链路与失败处理思路来自 [88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin)（MIT）。
本项目只保留每日签到这一条链路，把凭据迁到 Secret + 自动续期，使其能在 CI 里跑。

[MIT](LICENSE) · 仅供个人自动化使用，请遵守服务条款。

---

<a id="english"></a>

# WorkBuddy Daily Check-in (GitHub Actions)

Automatically claim your daily WorkBuddy credits from the cloud — set it up once and forget it.

[中文](#中文) · [English](#english)

## Features

- **Cloud only** — no machine left on, no desktop app running, no local cron
- **Self-renewing credentials** — the `refreshToken` rotates on every refresh; the newest pair lives in Actions Cache
- **Idempotent** — checks the status first, so reruns never double-claim
- **Fails loudly** — opens an Issue on error, closes it automatically once recovered
- **Zero dependencies** — stdlib-only Python, nothing to install on the runner
- **Stays alive** — a monthly commit keeps the scheduled workflow from being disabled

## How it works

```
Local (once)                    GitHub Actions (daily)
extract_local.py  ──Secret──▶   Cache first, Secret as fallback
  decrypts via desktop runtime    │ refresh token when near expiry
                                  │ check status → claim if needed
                                  └ write rotated credentials to Cache
```

WorkBuddy stores its tokens as `$wbEncrypted` envelopes (AES-256-GCM). Decrypting them requires the
client's own Electron runtime, which runners don't have. So credentials are exported once on your machine,
and from then on the pipeline keeps itself alive with `refreshToken`.

## Quick start

**1. Export credentials** (requires a logged-in WorkBuddy desktop app, Python 3, GitHub CLI)

```bash
git clone https://github.com/ArkURL/workbuddy-daily-sign.git
cd workbuddy-daily-sign
python3 tools/extract_local.py --set-secret
```

No `gh`? Drop `--set-secret` and paste the printed JSON line into a repository secret named `WORKBUDDY_AUTH`.

**2. Run it once**

Actions → 每日签到 → Run workflow. A `CLAIMED` or `ALREADY` line in the log means you're set.

**3. Leave it alone**

| Time (UTC+8) | What happens |
|---|---|
| 00:05 daily | check-in |
| 08:00 daily | backup run (idempotent) |
| 1st of each month | keep-alive commit |

## Commands

| Command | Description |
|---|---|
| `python3 wb_signin.py auto` | check in (what the workflow runs) |
| `python3 wb_signin.py status` | query status only |
| `python3 wb_signin.py doctor` | offline credential format check |

## Credentials

`WORKBUDDY_AUTH` is a single JSON line: `uid`, `accessToken` (30 days), `refreshToken` (60 days,
rotated on every refresh), `accessExpiresAt` / `refreshExpiresAt` (epoch seconds), `domain`,
optional `enterpriseId`, and `endpoint`.

## Troubleshooting

| Result | Meaning |
|---|---|
| `ALREADY` / `CLAIMED` | already checked in / just claimed — both fine |
| `INACTIVE` | no check-in campaign running, normal |
| `NETWORK` | transient failure, retried on the next scheduled run |
| `NO_AUTH` | secret missing or malformed, re-export |
| `AUTH_REJECTED` / `AUTH_EXPIRED` | token rejected or expired, re-export and update the secret |

## FAQ

- **Cache evicted?** The script falls back to the secret; you only need to re-export if that one expired too.
- **Desktop app asks you to log in again?** Expected — refreshing rotates the `refreshToken`, first one wins.
- **WorkBuddy changes its API?** The run fails and opens an Issue instead of silently skipping.
- **Is it safe?** No credentials live in this repo. The output of `extract_local.py` is as sensitive as your password.

## Credits

Endpoint discovery, decryption flow and failure handling are based on
[88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin) (MIT). This project keeps only the
daily check-in path and moves credentials to a secret with automatic rotation so it can run in CI.

[MIT](LICENSE) · For personal automation only.
