#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从本机 WorkBuddy 桌面端凭据里解出 accessToken / refreshToken，生成 WORKBUDDY_AUTH。

WorkBuddy 新版把令牌存成 $wbEncrypted 信封（AES-256-GCM），解密必须借客户端自带的
Electron 运行时，所以本脚本只能在登录过 WorkBuddy 的机器上跑，且只跑一次。

用法：
    python3 tools/extract_local.py                  # 打印 JSON（含凭据，别贴到公开的地方）
    python3 tools/extract_local.py --set-secret     # 直接 gh secret set WORKBUDDY_AUTH
    python3 tools/extract_local.py --set-secret -n 名字

凭据文件路径可用 WORKBUDDY_AUTH_FILE 覆盖，客户端可执行文件可用 WORKBUDDY_EXE 覆盖。

解密链路移植自 88lin/workbuddy-auto-signin（MIT，见 LICENSE）。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

AUTH_FILE_CANDIDATES = {
    "darwin": "~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info",
    "win32": "~/AppData/Roaming/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info",
    "linux": "~/.local/share/CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info",
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

SECRET_NAME = "WORKBUDDY_AUTH"


def die(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)


def find_auth_file():
    override = os.environ.get("WORKBUDDY_AUTH_FILE")
    if override:
        path = os.path.expanduser(override)
        if not os.path.isfile(path):
            die("WORKBUDDY_AUTH_FILE 指向的文件不存在：%s" % path)
        return path
    candidate = AUTH_FILE_CANDIDATES.get(sys.platform)
    if not candidate:
        die("不支持的平台：%s" % sys.platform)
    path = os.path.expanduser(candidate)
    if not os.path.isfile(path):
        die("没找到登录凭据：%s\n请先登录一次 WorkBuddy 桌面端（或设置 WORKBUDDY_AUTH_FILE）。" % path)
    return path


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


def build_state():
    path = find_auth_file()
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


def main():
    parser = argparse.ArgumentParser(description="提取 WorkBuddy 凭据，生成 WORKBUDDY_AUTH")
    parser.add_argument("--set-secret", action="store_true", help="直接调用 gh secret set 写入仓库 Secret")
    parser.add_argument("-n", "--name", default=SECRET_NAME, help="Secret 名称（默认 %s）" % SECRET_NAME)
    parser.add_argument("--repo", default=None, help="配合 --set-secret 指定 owner/repo")
    args = parser.parse_args()

    state = build_state()
    line = json.dumps(state, ensure_ascii=False, separators=(",", ":"))

    if not args.set_secret:
        print(line)
        sys.stderr.write("\n上面这一行含真实凭据，只粘到 GitHub Secrets，别贴到公开的地方。\n")
        return 0

    gh = shutil.which("gh")
    if not gh:
        die("没找到 gh 命令，请先安装 GitHub CLI，或去掉 --set-secret 手动设置。")
    command = [gh, "secret", "set", args.name]
    if args.repo:
        command += ["--repo", args.repo]
    completed = subprocess.run(command, input=line.encode("utf-8"), stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    if completed.returncode != 0:
        die("写入 Secret 失败：%s" % completed.stderr.decode("utf-8", "replace").strip()[:400])
    print("已写入 Secret %s（未回显凭据）。" % args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
