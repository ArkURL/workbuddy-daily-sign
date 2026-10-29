<div align="center">

# WorkBuddy Daily Check-in

Automatically claim your daily WorkBuddy credits from the cloud — set it up once and forget it.

[![Daily check-in](https://github.com/ArkURL/workbuddy-daily-sign/actions/workflows/signin.yml/badge.svg)](https://github.com/ArkURL/workbuddy-daily-sign/actions/workflows/signin.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

[中文](README.md) · [English](README.en.md)

</div>

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
