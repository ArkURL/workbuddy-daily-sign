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
- **桌面端会被顶下线吗？** 不会。实测 CI 续期后，桌面端仍能正常续期并保持登录。真需要重新登录时，重新登录后通常也不需要动 Secret。
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
