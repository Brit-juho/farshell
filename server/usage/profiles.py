"""Native, root-scoped quota readers. No clauth feed or token refresh engine.

Only normalized windows and opaque identity evidence survive a request. Provider
endpoints are compatibility APIs; unsupported access is explicit, never 0% usage.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import agent_profiles as registry
import fsguard
from .codex import CodexProvider

TTL = 90
MAX_RESPONSE = 256 * 1024
_instances = {}
_instances_lock = threading.Lock()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_credentials(profile):
    root = registry.root(profile["id"])
    raw = None
    if profile["tool"] == "claude" and sys.platform == "darwin":
        service = "Claude Code-credentials-" + hashlib.sha256(unicodedata.normalize("NFC", str(root)).encode()).hexdigest()[:8]
        user = os.environ.get("USER") or getpass.getuser()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", user):
            user = "claude-code-user"
        try:
            # Only the managed namespace, never the system-default Keychain item.
            result = subprocess.run(["security", "find-generic-password", "-s", service, "-a", user, "-w"],
                                    capture_output=True, timeout=5)
            if result.returncode == 0 and len(result.stdout) <= MAX_RESPONSE:
                raw = result.stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
    filename = ".credentials.json" if profile["tool"] == "claude" else "auth.json"
    if raw is None:
        try:
            raw = fsguard.read_profile_bytes(root, filename, MAX_RESPONSE)
        except (OSError, fsguard.FsDenied):
            return None
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None
        tokens = value.get("claudeAiOauth") if profile["tool"] == "claude" else value.get("tokens")
        if not isinstance(tokens, dict):
            return None
        key = "accessToken" if profile["tool"] == "claude" else "access_token"
        token = tokens.get(key)
        if not isinstance(token, str) or not 1 <= len(token) <= 32768 or any(ord(c) < 33 for c in token):
            return None
        expires = tokens.get("expiresAt")
        expires = expires / 1000 if type(expires) in (int, float) and math.isfinite(expires) else None
        return {"token": token, "account": tokens.get("account_id"), "expires": expires,
                "fingerprint": hashlib.sha256(token.encode()).hexdigest()}
    except (ValueError, TypeError):
        return None


def _request(url, headers):
    request = Request(url, headers=headers)
    with build_opener(_NoRedirect()).open(request, timeout=10) as response:
        body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise ValueError("response-too-large")
        value = json.loads(body)
        if not isinstance(value, dict):
            raise ValueError("invalid-response")
        return value


def _window(raw, key, label, now):
    if not isinstance(raw, dict):
        return None
    value = raw.get("utilization")
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
        return None
    reset = raw.get("resets_at")
    try:
        parsed = datetime.fromisoformat(reset.replace("Z", "+00:00")) if isinstance(reset, str) else None
        timestamp = parsed.timestamp() if parsed and parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        timestamp = None
    return {"id": key, "label": label, "pct": float(value),
            "resets_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None,
            "resets_in_sec": max(0, int(timestamp - now)) if timestamp is not None else None}


class NativeProfileProvider:
    def __init__(self, profile_id):
        self.id = profile_id
        self.lock = threading.Lock()
        self.checked = 0
        self.deadline = 0
        self.fingerprint = None
        self.cache = None
        self.subject = None
        self.error = "unchecked"

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def _snapshot(self):
        profile = registry.get(self.id)
        from agent_profile_secret import read as read_private
        private = read_private(registry.root(self.id))
        auth = None if private else _read_credentials(profile)
        now = time.time()
        fp = auth["fingerprint"] if auth else None
        if self.cache is not None and now < self.deadline and fp == self.fingerprint:
            return dict(self.cache)
        if fp != self.fingerprint:
            self.subject = None
        self.checked, self.fingerprint = now, fp
        self.deadline = now + TTL
        row = {"name": profile["name"], "profile_id": self.id, "account_id": profile["account_id"],
               "provider": profile["tool"], "active": False, "tier": "", "auth_ok": False,
               "auth_status": "unavailable", "fetch_status": "Unavailable", "has_live_session": False,
               "rolling_token": False, "stale": False, "windows": [], "fallback": None,
               "checked_at": now, "quota_scope": "account", "identity_state": "unverified"}
        if private:
            row.update(auth_status="api-key-configured", fetch_status="Unsupported", quota_scope="unsupported")
            self.cache = row
            return dict(row)
        if not auth:
            row.update(auth_status="missing", fetch_status="NoAuth")
            self.cache = row
            return dict(row)
        headers = {"Authorization": "Bearer " + auth["token"], "Accept": "application/json"}
        try:
            if profile["tool"] == "codex":
                from .codex import USAGE_URL, _USER_AGENT
                headers["User-Agent"] = _USER_AGENT
                account = auth["account"]
                if isinstance(account, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", account):
                    headers["ChatGPT-Account-Id"] = account
                data = _request(USAGE_URL, headers)
                row.update(CodexProvider()._normalize(data, now)["profile"])
                # The authenticated response, not unverified decoded JWT claims.
                subject = data.get("account_id")
                self.subject = registry.digest(("codex:" + subject).encode()) if isinstance(subject, str) and subject else None
            else:
                headers.update({"anthropic-beta": "oauth-2025-04-20", "User-Agent": "farshell-profile-usage"})
                data = _request("https://api.anthropic.com/api/oauth/usage", headers)
                windows = []
                for key, label in (("five_hour", "5h"), ("seven_day", "weekly"), ("seven_day_opus", "Opus weekly"), ("seven_day_sonnet", "Sonnet weekly")):
                    window = _window(data.get(key), key, label, now)
                    if window:
                        windows.append(window)
                row.update(windows=windows, auth_ok=True, auth_status="ok", fetch_status="Fresh")
                if self.subject is None:
                    identity = _request("https://api.anthropic.com/api/oauth/profile", headers)
                    account = identity.get("account")
                    subject = account.get("uuid") if isinstance(account, dict) else None
                    org = identity.get("organization")
                    tenant = org.get("uuid") if isinstance(org, dict) else None
                    self.subject = registry.digest(json.dumps(["claude", subject, tenant]).encode()) if isinstance(subject, str) and subject else None
            row.update(name=profile["name"], profile_id=self.id, account_id=profile["account_id"], checked_at=now,
                       identity_state="provider" if self.subject else "unverified", subject_hash=self.subject,
                       active=False)
            if not row["windows"]:
                row["fetch_status"] = "Unsupported"
            self.error = None
            if self.subject and row["windows"]:
                record_observation(profile, auth, self.subject, now)
        except HTTPError as exc:
            self.error = "expired" if exc.code in (401, 403) else "rate-limited" if exc.code == 429 else "unavailable"
            row.update(auth_status=self.error, fetch_status="AuthExpired" if self.error == "expired" else "Unavailable")
            if exc.code == 429:
                try:
                    self.deadline = now + min(3600, max(TTL, int(exc.headers.get("Retry-After", TTL))))
                except (TypeError, ValueError):
                    pass
        except (ValueError, TypeError, URLError, TimeoutError, OSError):
            self.error = "unavailable"
        if not row["auth_ok"] and row["auth_status"] not in ("expired", "missing") and self.cache and self.cache.get("windows"):
            row["windows"] = self.cache["windows"]
            row["stale"] = True
        self.cache = row
        return dict(row)


def record_observation(profile, auth, subject, now):
    # No raw token or decoded claims are persisted. Expiry + fingerprint change
    # are observations, not a claim about which OAuth grant operation occurred.
    with registry.database() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS observations (
            profile_id TEXT PRIMARY KEY REFERENCES profiles(id), subject TEXT NOT NULL,
            first_at REAL NOT NULL,last_at REAL NOT NULL,initial_expiry REAL,
            initial_fingerprint TEXT NOT NULL,rotation_after_expiry INTEGER NOT NULL)""")
        if 'last_fingerprint' not in {r[1] for r in db.execute('PRAGMA table_info(observations)')}:
            db.execute('ALTER TABLE observations ADD COLUMN last_fingerprint TEXT')
        old = db.execute("SELECT * FROM observations WHERE profile_id=?", (profile["id"],)).fetchone()
        rotated = bool(old and old["subject"] == subject and old["initial_expiry"] and now > old["initial_expiry"]
                       and old["initial_fingerprint"] != auth["fingerprint"])
        if old and old["subject"] != subject:
            # A login changed the identity; invalidate all previous evidence.
            db.execute("DELETE FROM observations WHERE profile_id=?", (profile["id"],))
            old = None
        if old:
            db.execute("UPDATE observations SET last_at=?,rotation_after_expiry=?,last_fingerprint=? WHERE profile_id=?", (now, int(rotated or old["rotation_after_expiry"]), auth['fingerprint'], profile["id"]))
        else:
            db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)", (profile["id"], subject, now, now, auth["expires"], auth["fingerprint"], 0, auth['fingerprint']))
        test = db.execute("SELECT * FROM refresh_tests WHERE profile_id=?", (profile['id'],)).fetchone()
        if test and test['subject'] == subject and test['initial_fingerprint'] != auth['fingerprint'] and now > test['started_at']:
            db.execute("UPDATE refresh_tests SET completed_at=? WHERE profile_id=?", (now, profile['id']))


def prepare_refresh_test(profile_id):
    """Expire only a disposable managed test login; the native CLI refreshes it.

    Work profiles and live panes are refused. No refresh token is copied, sent
    by FarShell, or restored after a native rotation.
    """
    from i18n import tr
    from agent_profile_runtime import _created, require_inactive
    registry.require_no_pending_jobs(profile_id)
    require_inactive(profile_id)
    profile = registry.get(profile_id)
    if profile['purpose'] != 'test':
        raise ValueError(tr("갱신 검증은 테스트 프로필에서만 가능합니다"))
    with registry.database() as db:
        sessions = list(db.execute('SELECT name,created FROM sessions WHERE profile_id=?', (profile_id,)))
    for session in sessions:
        try:
            alive = _created(session['name']) == session['created']
        except (ValueError, OSError):
            alive = False
        if alive:
            raise ValueError(tr("테스트 프로필의 CLI를 정상 종료한 뒤 갱신 검증을 시작하세요"))
    provider = NativeProfileProvider(profile_id)
    observed = provider.snapshot()
    if not observed.get('auth_ok') or not observed.get('subject_hash'):
        raise ValueError(tr("계정 신원과 사용량을 먼저 확인하세요"))
    root = registry.root(profile_id)
    filename = '.credentials.json' if profile['tool'] == 'claude' else 'auth.json'
    keychain = profile['tool'] == 'claude' and sys.platform == 'darwin'
    if keychain:
        service = 'Claude Code-credentials-' + hashlib.sha256(unicodedata.normalize('NFC', str(root)).encode()).hexdigest()[:8]
        user = os.environ.get('USER') or getpass.getuser()
        if not re.fullmatch(r'[A-Za-z0-9._-]+', user):
            user = 'claude-code-user'
        result = subprocess.run(['security', 'find-generic-password', '-s', service, '-a', user, '-w'], capture_output=True, timeout=5)
        if result.returncode or len(result.stdout) > MAX_RESPONSE:
            raise ValueError(tr("테스트 프로필의 Keychain 항목을 읽지 못했습니다"))
        raw = result.stdout
    else:
        raw = fsguard.read_profile_bytes(root, filename, MAX_RESPONSE)
    data = json.loads(raw)
    if profile['tool'] == 'claude':
        tokens = data.get('claudeAiOauth')
        if not isinstance(tokens, dict) or not tokens.get('refreshToken'):
            raise ValueError(tr("네이티브 갱신 자격이 없습니다. 다시 로그인하세요"))
        tokens['expiresAt'] = int((time.time() - 60) * 1000)
        fingerprint = hashlib.sha256(tokens['accessToken'].encode()).hexdigest()
        mode = 'forced-local-expiry'
    else:
        tokens = data.get('tokens')
        if not isinstance(tokens, dict) or not tokens.get('refresh_token') or not isinstance(tokens.get('access_token'), str) or len(tokens['access_token']) < 2:
            raise ValueError(tr("네이티브 갱신 자격이 없습니다. 다시 로그인하세요"))
        token = tokens['access_token']
        index = token.rfind('.') + 1
        if index >= len(token):
            raise ValueError(tr("테스트 access token 형식이 올바르지 않습니다"))
        tokens['access_token'] = token[:index] + ('A' if token[index] != 'A' else 'B') + token[index + 1:]
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        mode = 'forced-unauthorized'
    updated = json.dumps(data, ensure_ascii=False).encode()
    if keychain:
        def quote(value):
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
        command = 'add-generic-password -U -s ' + quote(service) + ' -a ' + quote(user) + ' -w ' + quote(updated.decode()) + '\n'
        result = subprocess.run(['security', '-i'], input=command.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if result.returncode:
            raise ValueError(tr("테스트 Keychain 항목을 갱신하지 못했습니다"))
    else:
        import config_io
        from agent_profile_migration import _write_bytes
        path = fsguard.profile_file(root, filename)
        with config_io.locked(path):
            if fsguard.read_profile_bytes(root, filename, MAX_RESPONSE) != raw:
                raise ValueError(tr("로그인 자격이 바뀌어 갱신 검증을 중단했습니다"))
            _write_bytes(path, updated)
    with registry.database() as db:
        db.execute('INSERT OR REPLACE INTO refresh_tests VALUES (?,?,?,?,?,NULL)',
                   (profile_id, observed['subject_hash'], fingerprint, mode, time.time()))
    return {'status': 'prepared', 'mode': mode,
            'note': tr("테스트 프로필을 다시 열고 짧은 요청을 실행한 뒤 사용량을 읽으세요. 실패하면 이 테스트 프로필에 다시 로그인하세요")}


def snapshot():
    result = []
    for row in registry.listing()["profiles"]:
        with _instances_lock:
            key = (str(registry.directory()), row["id"])
            if len(_instances) >= registry.MAX_PROFILES * 2:
                _instances.clear()
            provider = _instances.setdefault(key, NativeProfileProvider(row["id"]))
        result.append(provider.snapshot())
    # Deduplicate only provider-proven identities. Labels/manual grouping are
    # not enough to collapse unrelated accounts or invent separate quotas.
    subjects = {}
    for row in result:
        if row.get('subject_hash'):
            subjects.setdefault(row['account_id'], set()).add(row['subject_hash'])
    for row in result:
        row['account_identity_state'] = 'mismatch' if len(subjects.get(row['account_id'], set())) > 1 else row['identity_state']
    seen = {}
    for row in result:
        key = row.get("subject_hash")
        if key and key in seen:
            seen[key].setdefault("profile_ids", [seen[key]["profile_id"]]).append(row["profile_id"])
        else:
            seen[key or row["profile_id"]] = row
    return list(seen.values())
