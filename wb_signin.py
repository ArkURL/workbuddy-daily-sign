#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy 每日签到（GitHub Actions 版）。

只做一件事：查今天签没签，没签就领。凭据按下面的顺序取：

  1. WORKBUDDY_STATE 指向的状态文件 —— Actions Cache 里轮换后的最新凭据
  2. 回落 WORKBUDDY_AUTH 环境变量 —— 仓库 Secret 里的初始凭据

凭据是 JSON，字段：uid / accessToken / refreshToken / accessExpiresAt /
refreshExpiresAt / domain / enterpriseId / endpoint。

用法：
    python3 wb_signin.py auto     # 签到（默认）
    python3 wb_signin.py status   # 只查状态
    python3 wb_signin.py doctor   # 离线检查凭据格式，不联网

接口来自 WorkBuddy 桌面端，仅供个人自动化使用。
"""

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ENDPOINT = "https://copilot.tencent.com"
STATUS_PATH = "/v2/billing/meter/checkin-activity-status"
CLAIM_PATH = "/v2/billing/meter/daily-checkin"
REFRESH_PATH = "/v2/plugin/auth/token/refresh"

REQUEST_TIMEOUT = 30
REFRESH_MARGIN_SECONDS = 120
STATUS_ATTEMPTS = 3
CLAIM_ATTEMPTS = 2
RETRY_DELAYS = (5, 15)
TOKEN_LIMIT = 32768

CODE_NO_NETWORK = -1

_sensitive = set()


class SignError(Exception):
    """任何需要人介入的问题。report 会原样进日志与 Issue。"""

    def __init__(self, result, report):
        super().__init__(report)
        self.result = result
        self.report = report


def redact(text):
    text = str(text)
    for value in _sensitive:
        if value and value in text:
            text = text.replace(value, "[REDACTED]")
    return text


def remember_secret(value):
    if isinstance(value, str) and len(value) >= 8:
        _sensitive.add(value)
    return value


# --------------------------------------------------------------------------- 凭据

def _valid_token(token):
    return (isinstance(token, str) and 0 < len(token) <= TOKEN_LIMIT
            and re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token) is not None)


def _valid_endpoint(value):
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme == "https" and bool(parsed.hostname)
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment)


def validate_state(state):
    if not isinstance(state, dict):
        raise SignError("AUTH_ERROR", "凭据不是 JSON 对象")
    if not isinstance(state.get("uid"), str) or not state["uid"]:
        raise SignError("NO_SESSION", "凭据缺少 account uid，请重新提取")
    if not _valid_token(state.get("accessToken")):
        raise SignError("AUTH_ERROR", "凭据里的 accessToken 格式无效，请重新提取")
    refresh = state.get("refreshToken")
    if refresh not in (None, "") and not _valid_token(refresh):
        raise SignError("AUTH_ERROR", "凭据里的 refreshToken 格式无效，请重新提取")
    domain = state.get("domain")
    if domain not in (None, "") and not isinstance(domain, str):
        raise SignError("AUTH_ERROR", "凭据里的 domain 格式无效")
    endpoint = state.get("endpoint") or DEFAULT_ENDPOINT
    if not _valid_endpoint(endpoint):
        raise SignError("AUTH_ERROR", "凭据里的 endpoint 不是合法的 https 地址")
    state["endpoint"] = endpoint


# id(state) -> (name, roster_entry, roster)。轮换发生在原 state 上，id 不变，
# 于是所有深层的 persist_state() 调用点都不需要知道当前是哪个账号。
STATE_INDEX = {}
SECRET_STATES = {}
DEFAULT_NAME = "workbuddy"


def _as_roster(payload, default_name=DEFAULT_NAME):
    """把 Secret / Cache 的内容统一成 [{name, state}, ...]，兼容旧的单 dict。"""
    if isinstance(payload, dict):
        if "state" in payload and isinstance(payload["state"], dict):
            payload = [payload]
        else:
            payload = [{"name": default_name, "state": payload}]
    if not isinstance(payload, list):
        raise SignError("AUTH_ERROR", "凭据必须是 JSON 对象或数组")
    roster = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("state"), dict):
            raise SignError("AUTH_ERROR", "凭据数组里的账号缺少 state 字段")
        name = item.get("name") or default_name
        state = item["state"]
        remember_secret(state.get("accessToken"))
        remember_secret(state.get("refreshToken"))
        roster.append({"name": name, "state": state})
    return roster


def load_secret_roster():
    """优先 WORKBUDDY_ACCOUNTS，回落旧的 WORKBUDDY_AUTH。"""
    raw = (os.environ.get("WORKBUDDY_ACCOUNTS") or "").strip()
    if raw:
        try:
            return _as_roster(json.loads(raw)), "secret"
        except ValueError:
            raise SignError("NO_AUTH", "WORKBUDDY_ACCOUNTS 不是合法 JSON，请重新导出")
    raw = (os.environ.get("WORKBUDDY_AUTH") or "").strip()
    if raw:
        try:
            return _as_roster(json.loads(raw)), "secret"
        except ValueError:
            raise SignError("NO_AUTH", "WORKBUDDY_AUTH 不是合法 JSON，请重新导出")
    return [], "secret"


def load_accounts():
    """返回 [(name, state, source)]：Cache 里的令牌优先，Secret 用来补齐和兜底。"""
    cached = []
    path = os.environ.get("WORKBUDDY_STATE", "")
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                cached = _as_roster(json.load(handle))
        except (OSError, ValueError) as exc:
            raise SignError("AUTH_ERROR", "状态文件无法读取：%s" % redact(exc))

    secret_roster, secret_source = load_secret_roster()
    SECRET_STATES.clear()
    for item in secret_roster:
        SECRET_STATES[item["name"]] = item["state"]

    by_name = {item["name"]: item for item in cached}
    roster = []
    for item in secret_roster:
        hit = by_name.get(item["name"])
        if hit:
            roster.append({"name": item["name"], "state": hit["state"]})
        else:
            roster.append(item)

    for item in cached:
        if item["name"] not in SECRET_STATES:
            roster.append(item)

    if not roster:
        raise SignError("NO_AUTH", "没有任何凭据：请设置 WORKBUDDY_ACCOUNTS Secret"
                                   "（在本机跑 tools/extract_local.py 生成）")

    STATE_INDEX.clear()
    for entry in roster:
        STATE_INDEX[id(entry["state"])] = (entry["name"], entry, roster)
    return [(entry["name"], entry["state"],
             "state_file" if entry["name"] in {i["name"] for i in cached} else secret_source)
            for entry in roster], roster


def register_state(name, state):
    """回落 Secret 时，让这个 state 的落盘也写回正确的账号条目。"""
    for entry_name, entry, roster in STATE_INDEX.values():
        if entry_name == name:
            entry["state"] = state
            STATE_INDEX[id(state)] = (name, entry, roster)
            return roster
    return None


def persist_state(state):
    """把轮换后的凭据写回整个状态数组，供 workflow 存进 Cache。"""
    path = os.environ.get("WORKBUDDY_STATE_OUT") or os.environ.get("WORKBUDDY_STATE")
    if not path:
        return False
    info = STATE_INDEX.get(id(state))
    payload = info[2] if info else state
    if info:
        info[1]["state"] = state
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return True


# --------------------------------------------------------------------------- HTTP

def build_headers(state):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % state["accessToken"],
        "X-User-Id": state["uid"],
        "User-Agent": "WorkBuddy",
    }
    enterprise = state.get("enterpriseId")
    if enterprise:
        headers["X-Enterprise-Id"] = enterprise
        headers["X-Tenant-Id"] = enterprise
    if state.get("domain"):
        headers["X-Domain"] = state["domain"]
    return headers


def _request(url, headers, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT, context=context) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw[:500]}
    except urllib.error.URLError as exc:
        return CODE_NO_NETWORK, {"error": redact(exc.reason)}
    except Exception as exc:  # noqa: BLE001 - 网络层任何异常都归为不可达
        return CODE_NO_NETWORK, {"error": redact(exc)}


def call(state, path, payload=None, attempts=1):
    """只对网络错误和 5xx 做退避重试；401/403 交给调用方处理。"""
    last = (CODE_NO_NETWORK, {"error": "unreachable"})
    for index in range(max(1, attempts)):
        code, body = _request(state["endpoint"] + path, build_headers(state), payload)
        last = (code, body)
        if code == CODE_NO_NETWORK or 500 <= code < 600:
            if index < attempts - 1:
                time.sleep(RETRY_DELAYS[min(index, len(RETRY_DELAYS) - 1)])
                continue
        return last
    return last


def _find_auth(obj):
    """在任意信封结构里找出带 accessToken 的对象。"""
    if isinstance(obj, dict):
        if _valid_token(obj.get("accessToken")):
            return obj
        for value in obj.values():
            found = _find_auth(value)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_auth(value)
            if found:
                return found
    return None


def refresh_state(state):
    """用 refreshToken 换一套新凭据；服务端不返新 refreshToken 时保留旧的。"""
    refresh = state.get("refreshToken")
    if not _valid_token(refresh):
        raise SignError("NO_AUTH", "凭据里没有可用的 refreshToken，无法自动续期；"
                                   "请在本机重新运行 tools/extract_local.py")
    now = time.time()
    expires = state.get("refreshExpiresAt")
    if isinstance(expires, (int, float)) and expires <= now:
        raise SignError("AUTH_EXPIRED", "refreshToken 已过期（%s），请在本机重新提取凭据"
                        % time.strftime("%Y-%m-%d", time.localtime(expires)))

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "WorkBuddy",
        "X-Refresh-Token": refresh,
        "X-Auth-Refresh-Source": "plugin",
    }
    if state.get("domain"):
        headers["X-Domain"] = state["domain"]
    if state.get("enterpriseId"):
        headers["X-Enterprise-Id"] = state["enterpriseId"]
        headers["X-Tenant-Id"] = state["enterpriseId"]

    # 续期是写操作，超时后重试可能撞上已轮换的旧 token，因此只试一次。
    code, body = _request(state["endpoint"] + REFRESH_PATH, headers, {})
    if code == CODE_NO_NETWORK:
        raise SignError("NETWORK", "续期请求未能送达（%s），下次运行自动重试"
                        % (body.get("error") or "网络不可达"))
    if code in (401, 403):
        raise SignError("AUTH_EXPIRED", "refreshToken 被服务端拒绝（HTTP %d），"
                                        "请在本机重新提取凭据" % code)
    if not 200 <= code < 300:
        raise SignError("ERROR", "续期接口返回异常（HTTP %d）" % code)

    auth = _find_auth(body)
    if not auth:
        raise SignError("ERROR", "续期响应里没有 accessToken，接口可能已变更")

    now = time.time()
    state["accessToken"] = auth["accessToken"]
    remember_secret(auth["accessToken"])
    if _valid_token(auth.get("refreshToken")):
        state["refreshToken"] = auth["refreshToken"]
        remember_secret(auth["refreshToken"])
    expires_in = auth.get("expiresIn")
    if isinstance(expires_in, (int, float)) and 0 < expires_in < 10 * 365 * 86400:
        state["accessExpiresAt"] = int(now + expires_in)
    else:
        state.pop("accessExpiresAt", None)
    refresh_expires_in = auth.get("refreshExpiresIn")
    if isinstance(refresh_expires_in, (int, float)) and 0 < refresh_expires_in < 10 * 365 * 86400:
        state["refreshExpiresAt"] = int(now + refresh_expires_in)
    if isinstance(auth.get("domain"), str) and auth["domain"]:
        state["domain"] = auth["domain"]
    return state


def ensure_fresh(state):
    """临期才续期：平时不碰续期接口，减少和桌面端抢同一个 refreshToken 的机会。"""
    expires = state.get("accessExpiresAt")
    if not isinstance(expires, (int, float)):
        return False
    if time.time() < expires - REFRESH_MARGIN_SECONDS:
        return False
    refresh_state(state)
    persist_state(state)
    return True


# --------------------------------------------------------------------------- 签到

def dig(obj, key):
    """在可能被 data/result/resp/response 包裹的响应里找字段。"""
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for wrapper in ("data", "result", "resp", "response"):
            inner = obj.get(wrapper)
            if isinstance(inner, dict):
                found = dig(inner, key)
                if found is not None:
                    return found
    return None


def fmt_credit(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value


def already_report(status):
    parts = []
    today = dig(status, "today_credit")
    if today is None:
        today = dig(status, "daily_credit")
    streak = dig(status, "streak_days")
    total = dig(status, "total_credits")
    if today is not None:
        parts.append("今日 +%s" % fmt_credit(today))
    if streak is not None:
        parts.append("连续 %s 天" % streak)
    if total is not None:
        parts.append("累计 %s 积分" % fmt_credit(total))
    return "今日已签过（%s）" % "，".join(parts) if parts else "今日已签过"


def claimed_report(claim_body, status_body):
    credit = dig(claim_body, "credit")
    streak = dig(status_body, "streak_days")
    total = dig(status_body, "total_credits")
    streak_day = dig(status_body, "is_streak_day")
    bonus = "，且为连签奖励日" if streak_day else ""
    cumulative = "，累计 %s 积分" % fmt_credit(total) if total is not None else ""
    if streak is not None:
        tail = "（连续 %s 天%s）" % (streak, cumulative)
    else:
        tail = "（%s）" % cumulative.lstrip("，") if cumulative else ""
    return "成功领取 %s 积分%s%s" % (fmt_credit(credit), bonus, tail)


def is_already_checked_in(body):
    if body is None:
        return True
    if isinstance(body, dict):
        message = body.get("msg") or dig(body, "msg") or ""
        if body.get("code") == 10001 or dig(body, "code") == 10001 or "已签" in str(message):
            return True
    return False


def auth_failure(code):
    if code == 401:
        return SignError("AUTH_REJECTED", "服务端拒绝认证（HTTP 401），请在本机重新提取凭据")
    return SignError("FORBIDDEN", "服务端拒绝操作（HTTP 403），请检查账号状态或活动条件")


def query_status(state):
    code, body = call(state, STATUS_PATH, attempts=STATUS_ATTEMPTS)
    if code == 401 and state.get("refreshToken"):
        refresh_state(state)
        persist_state(state)
        code, body = call(state, STATUS_PATH, attempts=STATUS_ATTEMPTS)
    return code, body


def claim(state):
    code, body = call(state, CLAIM_PATH, attempts=CLAIM_ATTEMPTS)
    if code == 401 and state.get("refreshToken"):
        refresh_state(state)
        persist_state(state)
        code, body = call(state, CLAIM_PATH, attempts=CLAIM_ATTEMPTS)
    return code, body


def run_auto(state):
    ensure_fresh(state)

    code, body = query_status(state)
    if code == CODE_NO_NETWORK:
        raise SignError("NETWORK", "网络不可达（%s），本次跳过" % (body.get("error") or ""))
    if code in (401, 403):
        raise auth_failure(code)
    if not 200 <= code < 300:
        raise SignError("ERROR", "签到状态接口返回异常（HTTP %d）" % code)

    status = body if isinstance(body, dict) else {}
    if dig(status, "active") is False:
        name = dig(status, "activity_name")
        return {"result": "INACTIVE", "needs_attention": False,
                "report": "签到活动未开启" + ("（%s）" % name if name else "")}

    if dig(status, "today_checked_in") in (True, 1):
        return {"result": "ALREADY", "needs_attention": False, "report": already_report(status)}

    code, claim_body = claim(state)
    if code == CODE_NO_NETWORK:
        raise SignError("NETWORK", "领取请求未能送达，下次运行自动重试")
    if code in (401, 403):
        raise auth_failure(code)
    if is_already_checked_in(claim_body):
        fresh_code, fresh_body = query_status(state)
        fresh = fresh_body if 200 <= fresh_code < 300 and isinstance(fresh_body, dict) else status
        return {"result": "ALREADY", "needs_attention": False, "report": already_report(fresh)}
    if dig(claim_body, "credit") is None:
        message = dig(claim_body, "msg") or ("HTTP %d" % code)
        raise SignError("ERROR", "领取失败：%s" % redact(message))

    fresh_code, fresh_body = query_status(state)
    fresh = fresh_body if 200 <= fresh_code < 300 and isinstance(fresh_body, dict) else status
    return {
        "result": "CLAIMED",
        "needs_attention": False,
        "report": claimed_report(claim_body, fresh),
        "credit": dig(claim_body, "credit"),
        "streak_days": dig(fresh, "streak_days"),
        "total_credits": dig(fresh, "total_credits"),
    }


def run_status(state):
    ensure_fresh(state)
    code, body = query_status(state)
    if code in (401, 403):
        raise auth_failure(code)
    if code == CODE_NO_NETWORK:
        raise SignError("NETWORK", "网络不可达（%s）" % (body.get("error") or ""))
    if not 200 <= code < 300:
        raise SignError("ERROR", "状态接口返回异常（HTTP %d）" % code)
    status = body if isinstance(body, dict) else {}
    detail = {key: dig(status, key) for key in
              ("active", "activity_name", "today_checked_in", "today_credit",
               "streak_days", "total_credits", "is_streak_day")}
    checked = detail["today_checked_in"] in (True, 1)
    return {"result": "STATUS", "needs_attention": False,
            "report": ("今日已签" if checked else "今日未签"), "status": detail}


# --------------------------------------------------------------------------- 输出

def write_summary(payload, name=None):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    icon = {"CLAIMED": ":white_check_mark:", "ALREADY": ":heavy_check_mark:",
            "INACTIVE": ":zzz:"}.get(payload.get("result"), ":warning:")
    title = "### %s %s签到" % (icon, ("%s · " % name) if name else "")
    lines = [title, "", payload.get("report", ""), ""]
    if name:
        lines.append("- 账号：%s" % name)
    if payload.get("result"):
        lines.append("- 结果：%s" % payload["result"])
    if payload.get("credit") is not None:
        lines.append("- 本次积分：%s" % fmt_credit(payload["credit"]))
    if payload.get("streak_days") is not None:
        lines.append("- 连续天数：%s" % payload["streak_days"])
    if payload.get("total_credits") is not None:
        lines.append("- 累计积分：%s" % fmt_credit(payload["total_credits"]))
    lines.append("- 时间：%s UTC" % time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


def emit(payload, extra_out=False, name=None, write_file=True):
    out = dict(payload)
    if name:
        out["name"] = name
    if extra_out and isinstance(out.get("status"), dict):
        out["status"] = {k: v for k, v in out["status"].items() if v is not None}
    print(redact(json.dumps(out, ensure_ascii=False)))
    sys.stdout.flush()
    if write_file:
        write_summary(payload, name)


def doctor():
    try:
        accounts, _ = load_accounts()
    except SignError as exc:
        emit({"result": exc.result, "needs_attention": True, "report": exc.report})
        return 1

    now = time.time()

    def left(value):
        return None if not isinstance(value, (int, float)) else int(value - now)

    failed = False
    for name, state, source in accounts:
        try:
            validate_state(state)
        except SignError as exc:
            emit({"result": exc.result, "needs_attention": True, "report": exc.report}, name=name)
            failed = True
            continue
        emit({
            "result": "READY",
            "needs_attention": False,
            "report": "凭据格式可用（来源：%s）" % source,
            "uid": state.get("uid"),
            "has_refresh_token": bool(state.get("refreshToken")),
            "access_seconds_left": left(state.get("accessExpiresAt")),
            "refresh_seconds_left": left(state.get("refreshExpiresAt")),
            "domain": state.get("domain"),
            "online_checked": False,
        }, name=name)
    return 1 if failed else 0


RECOVERABLE = ("AUTH_REJECTED", "AUTH_EXPIRED", "NO_SESSION", "AUTH_ERROR")


def run_action(state, action):
    validate_state(state)
    return run_status(state) if action == "status" else run_auto(state)


def run_account(name, state, source, action):
    """单个账号的完整流程：出错时用 Secret 里的同名账号再试一次。"""
    try:
        return run_action(state, action), state
    except SignError as exc:
        if exc.result not in RECOVERABLE or source != "state_file":
            raise
        fallback = SECRET_STATES.get(name)
        if fallback is None:
            raise
        register_state(name, fallback)
        return run_action(fallback, action), fallback


def main(argv):
    action = argv[1] if len(argv) > 1 else "auto"
    if action == "doctor":
        return doctor()
    if action not in ("auto", "status"):
        print(json.dumps({"result": "USAGE", "needs_attention": True,
                          "report": "未知子命令：%s（可用 auto / status / doctor）" % action},
                         ensure_ascii=False))
        return 2

    try:
        accounts, _ = load_accounts()
    except SignError as exc:
        emit({"result": exc.result, "needs_attention": True, "report": redact(exc.report)})
        return 1

    failures = []
    results = []
    for name, state, source in accounts:
        try:
            payload, final_state = run_account(name, state, source, action)
            persist_state(final_state)
        except SignError as exc:
            payload = {"result": exc.result, "needs_attention": True,
                       "report": redact(exc.report)}
        except Exception as exc:  # noqa: BLE001 - 单账号异常不影响其他账号
            payload = {"result": "ERROR", "needs_attention": True,
                       "report": "脚本运行异常：%s" % redact(exc)}
        emit(payload, extra_out=True, name=name)
        results.append({"name": name, "result": payload.get("result"),
                        "report": payload.get("report")})
        if payload.get("needs_attention"):
            failures.append(name)

    summary = {
        "result": "SUMMARY",
        "needs_attention": bool(failures),
        "report": ("全部成功（%d 个账号）" % len(results) if not failures
                   else "失败账号：%s" % "、".join(failures)),
        "accounts": results,
    }
    emit(summary, write_file=False)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
