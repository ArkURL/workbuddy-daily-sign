<div align="center">

# WorkBuddy Daily Check-in

Automatically claim your daily WorkBuddy credits from the cloud — set it up once and forget it.

[![Daily check-in](https://github.com/ArkURL/workbuddy-daily-sign/actions/workflows/signin.yml/badge.svg)](https://github.com/ArkURL/workbuddy-daily-sign/actions/workflows/signin.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

[中文](README.md) · [English](README.en.md)

</div>

## Features

- **Multiple accounts** — one workflow checks in every account independently, with a separate Summary section for each
- **Cloud only** — no machine left on, no desktop app running, no local cron
- **Self-renewing credentials** — the `refreshToken` rotates on every refresh; the newest pair lives in Actions Cache
- **Idempotent** — checks the status first, so reruns never double-claim
- **Fails loudly** — opens an Issue on error, closes it automatically once recovered
- **Zero dependencies** — stdlib-only Python, nothing to install on the runner
- **Stays alive** — a monthly commit keeps the scheduled workflow from being disabled

## How it works

```
Local (once per account)        GitHub Actions (daily)
extract_local.py ──Secret──▶   Cache first, Secret as fallback
  encrypted tokens need client   │ per account: refresh when near expiry
  plaintext tokens don't         │ per account: check status → claim if needed
  writes local accounts.json     └ each account's rotated tokens → Cache
```

Desktop tokens are `$wbEncrypted` envelopes (AES-256-GCM) that need the client's Electron runtime to
decrypt; CodeBuddy CLI tokens are plaintext. Either way you export once locally, and the pipeline keeps
each account alive with its own `refreshToken`.

## Quick start

**1. Export each account** (requires Python 3 and GitHub CLI)

```bash
git clone https://github.com/ArkURL/workbuddy-daily-sign.git
cd workbuddy-daily-sign

# Account A: desktop HOME
python3 tools/extract_local.py --home ~ --label workbuddy

# Account B: a different HOME (plaintext credentials, no Electron needed)
python3 tools/extract_local.py --home ~/codebuddy-2 --label codebuddy-2

# Both are in the local store; push them together
python3 tools/extract_local.py --set-secret
```

If several accounts share one HOME, point `--auth-file` at the exact credential file. Rerun `--set-secret`
after every change — it pushes the whole store. `--list` shows which accounts are stored without printing tokens.

No `gh`? Build `[{"name":..., "state":{...}}, ...]` from the store and save it as secret `WORKBUDDY_ACCOUNTS`.

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
| `python3 wb_signin.py auto` | check in every account (what the workflow runs) |
| `python3 wb_signin.py status` | query all accounts |
| `python3 wb_signin.py doctor` | offline credential format check |
| `python3 tools/extract_local.py --home <dir> --label <name>` | export one account into the local store |
| `python3 tools/extract_local.py --set-secret` | push every stored account to the secret |
| `python3 tools/extract_local.py --from-store --set-secret` | push without re-decrypting |
| `python3 tools/extract_local.py --list` | list stored accounts (no tokens) |

One JSON line per account (with `name`), then a final `SUMMARY` line. Any failure makes the exit code 1.

## Credentials

`WORKBUDDY_ACCOUNTS` is an array of `{name, state}`; the old single-account `WORKBUDDY_AUTH` still works
as a fallback. Each `state` has `uid`, `accessToken` (30 days), `refreshToken` (60 days, rotated on every
refresh), `accessExpiresAt` / `refreshExpiresAt` (epoch seconds), `domain`, optional `enterpriseId`, and `endpoint`.

## Troubleshooting

| Result | Meaning |
|---|---|
| `ALREADY` / `CLAIMED` | already checked in / just claimed — both fine |
| `INACTIVE` | no check-in campaign running, normal |
| `NETWORK` | transient failure, retried on the next scheduled run |
| `NO_AUTH` | secret missing or malformed, re-export |
| `AUTH_REJECTED` / `AUTH_EXPIRED` | token rejected or expired, re-export and update the secret |
| `SUMMARY` with `needs_attention: true` | at least one account failed; the Issue names which ones |

## FAQ

- **Do more tokens mean more credits?** Not necessarily. Check-in is per uid — two tokens for the same uid
  (e.g. desktop and CLI logged into one account) only earn one claim. Check uids with `--list` / `doctor`.
- **Does one failure block the others?** No. Each account runs independently; failures are named in the Issue.
- **Cache evicted?** The script falls back to the secret; you only need to re-export if that one expired too.
- **Will the desktop app get logged out?** No. In practice both sides renew independently: CI keeps running while the app still refreshes and stays logged in.
- **WorkBuddy changes its API?** The run fails and opens an Issue instead of silently skipping.
- **Is it safe?** No credentials live in this repo. The output of `extract_local.py` is as sensitive as your password.

## Credits

Endpoint discovery, decryption flow and failure handling are based on
[88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin) (MIT). This project keeps only the
daily check-in path and moves credentials to a secret with automatic rotation so it can run in CI.

[MIT](LICENSE) · For personal automation only.
