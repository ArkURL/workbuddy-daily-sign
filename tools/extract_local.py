#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从本机导出一个或多个账号的 WorkBuddy 凭据，生成 WORKBUDDY_ACCOUNTS。

加密凭据（$wbEncrypted / AES-256-GCM）会借 WorkBuddy 自带的 Electron 运行时解密；
明文凭据（例如 CodeBuddy CLI 写出的）直接用，不需要客户端。

用法：
    python3 tools/extract_local.py --home ~             --label workbuddy
    python3 tools/extract_local.py --home ~/codebuddy-2 --label codebuddy-2 --set-secret
    python3 tools/extract_local.py --list               # 看本地账本里有哪些账号
    python3 tools/extract_local.py --from-store --set-secret

--set-secret 会把本地账本 ~/.config/workbuddy-daily-sign/accounts.json 里的全部账号推到 Secret。
该文件含明文令牌，不在仓库里，目录 700 / 文件 600，绝不提交。

解密链路移植自 88lin/workbuddy-auto-signin（MIT，见 LICENSE）。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

AUTH_FILE_RELPATHS = {
    "darwin": [
        "Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info",
        "Library/Application Support/CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info",
    ],
    "win32": [
        "AppData/Roaming/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info",
        "AppData/Roaming/CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info",
    ],
    "linux": [
        ".local/share/CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info",
        ".local/share/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info",
    ],
}

RUNTIME_CANDIDATES = {
    "darwin": [
        "/Applications/WorkBuddy.app/Contents/MacOS/Electron",
        "~/Applications/WorkBuddy.app/Contents/MacOS/Electron",
    ],
    "win32": [
        "~/AppData/Local/Programs/WorkBuddy/WorkBuddy.exe",
        "%PROGRAMFILES%/WorkBuddy/WorkBuddy.exe",
        "%LOCALAPPDATA%/WorkBuddy/WorkBuddy.exe",
    ],
    "linux": [],
}

AUTH_HELPER_JS = r"""'use strict';
const crypto = require('crypto');
const inputLimit = 65536;
const failure = reason => { throw {reason}; };
const object = x => x !== null && typeof x === 'object' && !Array.isArray(x);
function base64(value, length) {
  if (typeof value !== 'string' || value.length > inputLimit) failure('INVALID_FORMAT');
  const bytes = Buffer.from(value, 'base64');
  if (bytes.toString('base64') !== value || (length !== undefined && bytes.length !== length))
    failure('INVALID_FORMAT');
  return bytes;
}
function utf8(bytes) {
  const text = bytes.toString('utf8');
  if (!Buffer.from(text, 'utf8').equals(bytes)) failure('INVALID_FORMAT');
  return text;
}
function decode(value) {
  if (!object(value) || Object.keys(value).sort().join(',') !== '$wbEncrypted,envelope' || value.$wbEncrypted !== 1)
    failure('UNSUPPORTED_ENVELOPE');
  let envelope;
  try { envelope = JSON.parse(utf8(base64(value.envelope))); }
  catch (e) { failure(e.reason || 'INVALID_FORMAT'); }
  if (!object(envelope) || !Number.isInteger(envelope.suite)) failure('INVALID_FORMAT');
  if (envelope.suite !== 1) failure('UNSUPPORTED_ENVELOPE');
  if (Object.keys(envelope).sort().join(',') !== 'authTag,ciphertext,keyId,nonce,suite' ||
      typeof envelope.keyId !== 'string' || !/^[0-9a-f]{16}$/.test(envelope.keyId)) failure('INVALID_FORMAT');
  return {keyId: envelope.keyId, nonce: base64(envelope.nonce, 12),
    tag: base64(envelope.authTag, 16), ciphertext: base64(envelope.ciphertext)};
}
function nativeStorage() {
  try {
    const storage = process._linkedBinding('electron_browser_workbuddy_storage');
    if (typeof storage.loggerGet !== 'function') failure('RUNTIME_UNAVAILABLE');
    return storage;
  } catch (_) { failure('RUNTIME_UNAVAILABLE'); }
}
function keyMaterial() {
  let payload;
  try { payload = JSON.parse(nativeStorage().loggerGet()); }
  catch (_) { failure('RUNTIME_UNAVAILABLE'); }
  if (!object(payload) || payload.version !== 1) failure('RUNTIME_UNAVAILABLE');
  let secret;
  try { secret = base64(payload.atRestSecretKey, 32); }
  catch (_) { failure('RUNTIME_UNAVAILABLE'); }
  const empty = secret.every(b => b === 0);
  secret.fill(0);
  if (empty) failure('RUNTIME_UNAVAILABLE');
  return crypto.createHash('sha256').update(payload.atRestSecretKey, 'utf8').digest();
}
function decrypt(envelope, key) {
  if (crypto.createHash('sha256').update(key).digest('hex').slice(0, 16) !== envelope.keyId)
    failure('KEY_MISMATCH');
  const lp = s => {
    const bytes = Buffer.from(s, 'utf8');
    const length = Buffer.alloc(4); length.writeUInt32BE(bytes.length);
    return Buffer.concat([length, bytes]);
  };
  const aad = Buffer.concat([Buffer.from('WB-AAD\0', 'ascii'), Buffer.from([1]),
    lp('WBEV1'), lp('sym-v1'), Buffer.from([0, 0, 0, 1]), lp(envelope.keyId), Buffer.from([2, 0, 0])]);
  let plaintext;
  try {
    const cipher = crypto.createDecipheriv('aes-256-gcm', key, envelope.nonce, {authTagLength: 16});
    cipher.setAAD(aad); cipher.setAuthTag(envelope.tag);
    plaintext = Buffer.concat([cipher.update(envelope.ciphertext), cipher.final()]);
  } catch (_) { failure('DECRYPT_FAILED'); }
  const text = utf8(plaintext);
  plaintext.fill(0);
  if (!text.length || text.length > 32768) failure('INVALID_FORMAT');
  return text;
}
let chunks = [], size = 0;
function reply(value) {
  process.stdout.write(JSON.stringify({version: 1, ...value}), () => process.exit(value.ok ? 0 : 1));
}
process.stdin.on('data', chunk => {
  size += chunk.length;
  if (size > inputLimit) reply({ok: false, reason: 'INVALID_FORMAT'});
  else chunks.push(chunk);
});
process.stdin.on('error', () => reply({ok: false, reason: 'HELPER_PROTOCOL'}));
process.stdin.on('end', () => {
  try {
    const request = JSON.parse(utf8(Buffer.concat(chunks))); chunks = [];
    if (!object(request) || request.version !== 1) failure('HELPER_PROTOCOL');
    if (request.operation === 'probe') {
      nativeStorage();
      if (!crypto.getCiphers().includes('aes-256-gcm')) failure('RUNTIME_UNAVAILABLE');
      reply({ok: true, electron: process.versions.electron || 'unknown'});
    } else if (request.operation === 'decrypt') {
      if (!object(request.values)) failure('HELPER_PROTOCOL');
      const key = keyMaterial();
      const values = {};
      try {
        for (const name of Object.keys(request.values)) values[name] = decrypt(decode(request.values[name]), key);
      } finally { key.fill(0); }
      reply({ok: true, values});
    } else failure('HELPER_PROTOCOL');
  } catch (e) {
    const reasons = ['INVALID_FORMAT','UNSUPPORTED_ENVELOPE','RUNTIME_UNAVAILABLE',
      'KEY_MISMATCH','DECRYPT_FAILED','HELPER_PROTOCOL'];
    reply({ok: false, reason: reasons.includes(e.reason) ? e.reason : 'HELPER_PROTOCOL'});
  }
});
"""

SECRET_NAME = "WORKBUDDY_ACCOUNTS"
STORE_PATH = os.path.join(os.path.expanduser("~"), ".config", "workbuddy-daily-sign", "accounts.json")
LEGACY_STORE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "accounts.json")


def die(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)


def find_auth_file(home=None, auth_file=None):
    override = auth_file or os.environ.get("WORKBUDDY_AUTH_FILE")
    if override:
        path = os.path.abspath(os.path.expanduser(override))
        if not os.path.isfile(path):
            die("凭据文件不存在：%s" % path)
        return path

    home_dir = os.path.abspath(os.path.expanduser(home or "~"))
    relpaths = AUTH_FILE_RELPATHS.get(sys.platform)
    if not relpaths:
        die("不支持的平台：%s" % sys.platform)
    for relpath in relpaths:
        path = os.path.join(home_dir, relpath)
        if os.path.isfile(path):
            return path
    die("在 %s 下没找到登录凭据。已查找：\n  %s"
        % (home_dir, "\n  ".join(os.path.join(home_dir, p) for p in relpaths)))


def find_runtime():
    override = os.environ.get("WORKBUDDY_EXE")
    candidates = [override] if override else RUNTIME_CANDIDATES.get(sys.platform, [])
    for candidate in candidates:
        if not candidate:
            continue
        path = os.path.expanduser(os.path.expandvars(candidate))
        if os.path.isfile(path) and (os.name == "nt" or os.access(path, os.X_OK)):
            return os.path.abspath(path)
    die("没找到 WorkBuddy 客户端可执行文件；请用 WORKBUDDY_EXE 指定路径。")


def run_helper(runtime, request, timeout=20):
    payload = json.dumps(dict(request, version=1), ensure_ascii=True).encode("ascii")
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("NODE_", "ELECTRON_", "WORKBUDDY_"))}
    env["ELECTRON_RUN_AS_NODE"] = "1"
    try:
        completed = subprocess.run(
            [runtime, "-e", AUTH_HELPER_JS],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        die("客户端凭据助手超时（可能弹了窗口），请稍后重试。")
    except OSError as exc:
        die("无法启动客户端运行时：%s" % exc)

    raw = completed.stdout.decode("utf-8", "replace").strip()
    try:
        reply = json.loads(raw)
    except ValueError:
        detail = completed.stderr.decode("utf-8", "replace").strip()[:300]
        die("凭据助手没有返回有效结果%s" % ("：" + detail if detail else ""))
    if not isinstance(reply, dict) or reply.get("ok") is not True:
        die("解密失败：%s" % (reply.get("reason") if isinstance(reply, dict) else raw[:200]))
    return reply


def ms_to_seconds(value):
    if isinstance(value, (int, float)) and value > 10 ** 11:
        return int(value / 1000)
    if isinstance(value, (int, float)):
        return int(value)
    return None


def build_state(auth_file=None, home=None):
    path = find_auth_file(home, auth_file)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            session = json.load(handle)
    except (OSError, ValueError) as exc:
        die("凭据文件无法解析（重新登录一次 WorkBuddy 可重建）：%s" % exc)

    auth = session.get("auth") or {}
    account = session.get("account") or {}
    uid = account.get("uid")
    if not uid:
        die("凭据里没有 account.uid，请重新登录 WorkBuddy 桌面端。")

    envelopes = {}
    plain = {}
    for name in ("accessToken", "refreshToken"):
        value = auth.get(name)
        if isinstance(value, dict):
            envelopes[name] = value
        elif isinstance(value, str) and value:
            plain[name] = value
    if "accessToken" not in envelopes and "accessToken" not in plain:
        die("凭据里没有 accessToken。")

    values = dict(plain)
    if envelopes:
        runtime = find_runtime()
        reply = run_helper(runtime, {"operation": "decrypt", "values": envelopes})
        values.update(reply.get("values") or {})

    state = {
        "uid": uid,
        "accessToken": values.get("accessToken"),
        "refreshToken": values.get("refreshToken"),
        "accessExpiresAt": ms_to_seconds(auth.get("expiresAt")),
        "refreshExpiresAt": ms_to_seconds(auth.get("refreshExpiresAt")),
        "domain": auth.get("domain"),
        "enterpriseId": account.get("enterpriseId") or None,
        "endpoint": auth.get("endpoint") or "https://copilot.tencent.com",
    }
    state = {k: v for k, v in state.items() if v not in (None, "")}
    if not state.get("accessToken"):
        die("解密后没有拿到 accessToken。")
    return state


def load_store():
    # 老位置（仓库根目录的 accounts.json）仍可读，下一次写入会自动迁到 ~/.config
    path = STORE_PATH if os.path.isfile(STORE_PATH) else LEGACY_STORE_PATH
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, ValueError) as exc:
        die("本地账本 %s 无法读取：%s" % (path, exc))
    if not isinstance(entries, list):
        die("本地账本格式错误，应为数组：%s" % path)
    return [item for item in entries
            if isinstance(item, dict) and item.get("name") and isinstance(item.get("state"), dict)]


def write_store(entries):
    os.makedirs(os.path.dirname(STORE_PATH), mode=0o700, exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STORE_PATH)


def upsert_store(name, home, state):
    entries = [item for item in load_store() if item.get("name") != name]
    entries.append({"name": name, "home": os.path.abspath(os.path.expanduser(home or "~")),
                    "state": state})
    write_store(entries)
    return entries


def show_store(entries):
    for item in entries:
        state = item["state"]
        expiry = state.get("accessExpiresAt")
        text = time.strftime("%Y-%m-%d", time.localtime(expiry)) if isinstance(expiry, (int, float)) else "?"
        print("%-14s %-20s uid %-10s 到期 %s"
              % (item["name"], state.get("domain") or "-", str(state.get("uid") or "-")[:8], text))
    if not entries:
        print("（账本是空的，先用 --home / --label 提取一次）")


def push_secret(entries, secret_name, repo):
    gh = shutil.which("gh")
    if not gh:
        die("没找到 gh 命令，请先安装 GitHub CLI，或去掉 --set-secret 手动设置 Secret。")
    payload = [{"name": item["name"], "state": item["state"]} for item in entries]
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    command = [gh, "secret", "set", secret_name]
    if repo:
        command += ["--repo", repo]
    completed = subprocess.run(command, input=line.encode("utf-8"), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    if completed.returncode != 0:
        die("写入 Secret 失败：%s" % completed.stderr.decode("utf-8", "replace").strip()[:400])
    print("已写入 Secret %s（%d 个账号，未回显凭据）。" % (secret_name, len(entries)))


def main():
    parser = argparse.ArgumentParser(description="提取账号凭据，生成本地账本与 WORKBUDDY_ACCOUNTS")
    parser.add_argument("--home", default="~", help="凭据所属账号的 HOME（默认当前用户）")
    parser.add_argument("--label", "--name", dest="label", default="workbuddy",
                        help="账号名，用于报表和账本（默认 workbuddy）")
    parser.add_argument("--auth-file", default=None, help="直接指定凭据文件路径")
    parser.add_argument("--set-secret", action="store_true", help="把账本里的全部账号推到 GitHub Secret")
    parser.add_argument("--secret-name", default=SECRET_NAME, help="Secret 名称（默认 %s）" % SECRET_NAME)
    parser.add_argument("--repo", default=None, help="配合 --set-secret 指定 owner/repo")
    parser.add_argument("--from-store", action="store_true", help="不重新解密，直接推送本地账本")
    parser.add_argument("--list", action="store_true", help="列出本地账本里的账号（不显示令牌）")
    args = parser.parse_args()

    if args.list:
        show_store(load_store())
        return 0

    if args.from_store:
        entries = load_store()
        if not entries:
            die("本地账本为空，先用 --home / --label 提取一次。")
        if not args.set_secret:
            show_store(entries)
            return 0
        push_secret(entries, args.secret_name, args.repo)
        return 0

    state = build_state(args.auth_file, args.home)
    entries = upsert_store(args.label, args.home, state)
    if not args.set_secret:
        print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
        sys.stderr.write("\n上面这一行含真实凭据；已同时写入本地账本 %s（含明文，勿提交）。\n"
                         % STORE_PATH)
        return 0

    push_secret(entries, args.secret_name, args.repo)
    print("账本现有账号：" + "、".join(item["name"] for item in entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
