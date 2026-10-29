# WorkBuddy 自动签到（GitHub Actions）

每天用 GitHub Actions 自动领 WorkBuddy 的签到积分 —— 不用开机、不用桌面端挂着、不用本地定时任务。

> 仓库里没有任何凭据。你的登录态只存在两个地方：仓库 Secret 和 Actions Cache。

## 为什么不是「直接调接口」

WorkBuddy 新版把令牌存成 `$wbEncrypted` 信封（AES-256-GCM），解密要借客户端自带的 Electron
运行时，GitHub Runner 上没有它。所以把凭据**一次性**从本机导出，之后在 CI 里用 `refreshToken` 自动续期。

流程：

```
本机（跑一次）                      GitHub Actions（每天自动）
tools/extract_local.py  ──Secret──▶  取最新凭据（Cache 优先，回落 Secret）
  借桌面端运行时解密                     │ 临期才调 /v2/plugin/auth/token/refresh
  拿到 at + rt                         │ 查状态 → 未签则领
                                       └ 轮换后的凭据写回 Cache
```

## 快速开始

### 1. 导出本机凭据（只需一次）

前置：本机装过并**登录过** WorkBuddy 桌面端；有 Python 3 和 [GitHub CLI](https://cli.github.com/)。

```bash
git clone https://github.com/<you>/workbuddy-daily-sign.git
cd workbuddy-daily-sign

python3 tools/extract_local.py                 # 只打印，自己粘到 Secrets
python3 tools/extract_local.py --set-secret    # 或直接写入当前仓库的 Secret
```

没装 gh 的话，打开仓库 Settings → Secrets and variables → Actions → New repository secret，
名字填 `WORKBUDDY_AUTH`，值就是脚本输出的那一行 JSON。

### 2. 打开 Actions

仓库 → Actions → 允许工作流运行 → 选中「每日签到」→ Run workflow 手动跑一次。
看到绿色对勾 + 日志里的 `"result": "CLAIMED"` 或 `"result": "ALREADY"` 就通了。

### 3. 等它自己跑

| 时间（北京时间） | 说明 |
|---|---|
| 每天 00:05 | 主运行 |
| 每天 08:00 | 兜底重跑（接口幂等，已签过只是查一次状态） |
| 每月 1 号 11:00 | 仓库保活，防止定时任务被判定不活跃而停用 |

## 命令

```bash
python3 wb_signin.py auto      # 签到（workflow 用的就是这个）
python3 wb_signin.py status    # 只查状态
python3 wb_signin.py doctor    # 离线检查凭据格式，不联网
```

本地调试：`WORKBUDDY_STATE=/tmp/wb.json python3 wb_signin.py status`。

## 凭据字段

`WORKBUDDY_AUTH` 是一行 JSON：

| 字段 | 说明 |
|---|---|
| `uid` | 账号 uid，请求头 `X-User-Id` |
| `accessToken` | 访问令牌，30 天有效 |
| `refreshToken` | 续期令牌，60 天有效，**每次续期都会换新** |
| `accessExpiresAt` / `refreshExpiresAt` | 过期时间（epoch 秒） |
| `domain` | 请求头 `X-Domain`，一般是 `www.workbuddy.cn` |
| `enterpriseId` | 企业号才需要，个人号没有 |
| `endpoint` | 接口地址，默认 `https://copilot.tencent.com` |

## 排错

| 现象 | 处理 |
|---|---|
| `NO_AUTH` | 没有 Secret，或者 Secret 不是合法 JSON。重新跑 `extract_local.py --set-secret` |
| `AUTH_REJECTED` / `AUTH_EXPIRED` | 令牌被拒或已过期。重新提取凭据并更新 Secret |
| `NETWORK` | 网络或服务端抖动，下次定时运行会自动重试 |
| `INACTIVE` | 当前没有签到活动，正常 |
| `ALREADY` | 今天已经签过了，正常 |
| Cache 被清空 | 会回落到 Secret 里的初始凭据；如果那份也过期了，Issue 会提醒你重新提取 |

## 已知限制

- **和桌面端共用同一套令牌。** 续期会在服务端轮换 `refreshToken`：谁先续期，另一方的旧令牌就失效。
  如果桌面端被要求重新登录，重新登录一次即可；如果需要让 CI 继续，重新跑一次 `extract_local.py --set-secret`。
- **Cache 不是保险箱，只是中转。** 它可能被 GitHub 回收，脚本因此准备了 Secret 兜底。
- **接口是私有的。** 端点和载荷来自桌面端，WorkBuddy 改版就可能失效；失效时脚本会失败并开 Issue，而不是静默跳过。
- 公开仓库里只有代码，但你如果把 `extract_local.py` 的输出贴到 issue / 聊天记录里，就等于把账号交出去了。

## 目录

```
wb_signin.py                     CI 侧签到客户端（纯标准库）
tools/extract_local.py           本机一次性凭据导出（借客户端运行时解密）
.github/workflows/signin.yml     每日签到 + Cache 轮换 + 失败开 Issue
.github/workflows/keepalive.yml  每月保活，防止定时任务被停用
```

## 致谢

签到接口、解密链路与失败处理思路来自 [88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin)（MIT）。
本项目只保留「每日签到」这一条链路，把凭据迁到 Secret + 自动续期，使其能在 CI 里运行。

仅供个人自动化使用，请遵守服务条款。
