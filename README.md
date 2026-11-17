<div align="center">

# WorkBuddy 自动签到

用 GitHub Actions 每天自动领 WorkBuddy 签到积分 —— 一次配置，之后不用管。

[![每日签到](https://github.com/ArkURL/workbuddy-daily-sign/actions/workflows/signin.yml/badge.svg)](https://github.com/ArkURL/workbuddy-daily-sign/actions/workflows/signin.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/GitHub%20Actions-ubuntu--latest-2088FF.svg)](https://github.com/ArkURL/workbuddy-daily-sign/actions)

[中文](README.md) · [English](README.en.md)

</div>

## 特性

- **多账号** —— 一次 workflow 给所有账号各签一次，互不影响，报表按账号分节
- **全云端** —— 不用开机、不用挂着桌面端、不装本地定时任务
- **凭据自动续期** —— `refreshToken` 每次轮换，最新的那套存在 Actions Cache
- **幂等** —— 先查状态再领，重复运行不会多领
- **失败会喊你** —— 出错自动开 Issue，恢复后自动关闭
- **零依赖** —— 纯 Python 标准库，Runner 上什么都不用装
- **防休眠** —— 每月自动提交一次，避免定时任务被判定不活跃而停用

## 工作原理

```
本机（每个账号跑一次）            GitHub Actions（每天）
extract_local.py ──Secret──▶  Cache 优先，回落 Secret
  加密凭据借客户端运行时解密        │ 逐账号：临期刷新令牌
  明文凭据直接用                    │ 逐账号：查状态 → 未签才领
  写进本地账本 accounts.json        └ 各账号新凭据写回 Cache
```

WorkBuddy 把桌面端令牌存成 `$wbEncrypted` 信封（AES-256-GCM），解密要借客户端自带的 Electron
运行时，Runner 上没有它。所以凭据只在本机导一次；CodeBuddy CLI 的明文凭据则不需要客户端。
之后所有账号都靠各自的 `refreshToken` 在 CI 里续期。

## 快速开始

**1. 导出每个账号的凭据**（需要 Python 3、GitHub CLI；最后再加 `--set-secret`）

```bash
git clone https://github.com/ArkURL/workbuddy-daily-sign.git
cd workbuddy-daily-sign

# 账号 A：桌面端 HOME
python3 tools/extract_local.py --home ~ --label workbuddy

# 账号 B：另一个 HOME（明文凭据，不需要 Electron）
python3 tools/extract_local.py --home ~/codebuddy-2 --label codebuddy-2

# 两个账号都在本地账本里了，一次性推到 GitHub
python3 tools/extract_local.py --set-secret
```

同一 HOME 下有多个账号时，用 `--auth-file` 直接指到那份凭据文件即可。
每加一个账号就重跑一次 `--set-secret`（它推的是整个账本）。`python3 tools/extract_local.py --list` 可看都已收了哪些账号。

没装 gh 的话，把账本里各账号的 `state` 组成 `[{"name":..., "state":{...}}]` 数组，存成 Secret `WORKBUDDY_ACCOUNTS`。

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
| `python3 wb_signin.py auto` | 所有账号签到（workflow 用的就是这个） |
| `python3 wb_signin.py status` | 所有账号只查状态 |
| `python3 wb_signin.py doctor` | 离线检查所有账号的凭据格式 |
| `python3 tools/extract_local.py --home <dir> --label <name>` | 导出一个账号到本地账本 |
| `python3 tools/extract_local.py --set-secret` | 把账本全部账号推到 Secret |
| `python3 tools/extract_local.py --from-store --set-secret` | 不重新解密，只推送账本 |
| `python3 tools/extract_local.py --list` | 列出账本里的账号（不显示令牌） |

输出：每个账号一行 JSON（带 `name`），最后一行 `SUMMARY` 汇总；任一账号失败则退出码 1。

## 凭据字段

`WORKBUDDY_ACCOUNTS` 是数组，每个账号一项；旧的 `WORKBUDDY_AUTH`（单账号）仍兼容，可作兜底：

```json
[{"name": "workbuddy", "state": { ... }}, {"name": "codebuddy-2", "state": { ... }}]
```

`state` 字段：

| 字段 | 说明 |
|---|---|
| `uid` | 账号 uid，对应请求头 `X-User-Id` |
| `accessToken` | 访问令牌，30 天有效 |
| `refreshToken` | 续期令牌，60 天有效，每次续期都会换新 |
| `accessExpiresAt` / `refreshExpiresAt` | 过期时间（epoch 秒） |
| `domain` | 请求头 `X-Domain`，`www.workbuddy.cn` 或 `www.codebuddy.cn` |
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
| `SUMMARY` 里 `needs_attention: true` | 至少一个账号失败，Issue 会点名具体账号 |

## 常见问题

- **多个 token 一定能多领吗？** 不一定。签到按账号 uid 计，如果两个凭据是同一个 uid（例如同一个账号在桌面端和 CLI 各登一次），只会领到一份；请用 `--list` / `doctor` 核对 uid。
- **某个账号失败会拖累其他账号吗？** 不会。每个账号独立执行；失败的账号写进 Issue 正文，成功的照常领取。
- **Cache 丢了怎么办？** 自动回落到 Secret 里对应账号的凭据；只有那份也过期时才需要你重新导出。
- **桌面端会被顶下线吗？** 不会。实测 CI 续期后，桌面端仍能正常续期并保持登录。真需要重新登录时，重新登录后通常也不需要动 Secret。
- **WorkBuddy 改版失效？** 签到会失败并开 Issue，不会静默跳过。
- **凭据安全吗？** 仓库里没有任何凭据。但 `extract_local.py` 的输出等于账号密码，别贴到 issue、聊天记录或任何公开地方。

## 目录结构

```
wb_signin.py                     CI 侧签到客户端（纯标准库，按账号循环）
tools/extract_local.py           本机多账号凭据导出 + 本地账本
accounts.json                    本地账本（含明文令牌，已 gitignore，勿提交）
.github/workflows/signin.yml     每日签到 + Cache 轮换 + 失败开 Issue
.github/workflows/keepalive.yml  每月保活，防止定时任务被停用
```

## 致谢与许可

签到接口、解密链路与失败处理思路来自 [88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin)（MIT）。
本项目只保留每日签到这一条链路，把凭据迁到 Secret + 自动续期，使其能在 CI 里跑。

[MIT](LICENSE) · 仅供个人自动化使用，请遵守服务条款。
