"""Long-lived Claude token (`claude setup-token`) stored per profile.

A setup-token mint carries no refresh token, so nothing rotates and concurrent sessions
can never race on a single-use refresh chain. FarShell stores the pasted value like an API
key (0600, write-only: it is never printed, logged or returned by an API) and injects it as
CLAUDE_CODE_OAUTH_TOKEN for that profile's sessions only.

The ~1 year lifetime is the documented horizon clauth assumes, not a server-side guarantee;
the stamp here only drives an early warning.
"""
from __future__ import annotations

import json
import time

import agent_profiles as profiles
import fsguard
from i18n import tr

FILENAME = ".farshell-token.json"
LIFETIME_SECONDS = 365 * 24 * 3600
WARN_SECONDS = 30 * 24 * 3600


def validate(raw):
    """Shape-check a pasted mint before anything is written; the error never echoes the value."""
    token = raw.strip() if isinstance(raw, str) else ""
    if not token:
        raise profiles.Refused(tr("붙여 넣은 토큰이 없습니다"))
    if not token.startswith("sk-ant-"):
        raise profiles.Refused(tr("`claude setup-token`이 만든 값(sk-ant-...)이 아닙니다"))
    if token.startswith("sk-ant-api"):
        raise profiles.Refused(tr("API 키(sk-ant-api...)입니다. 장기 토큰이 아닙니다"))
    if any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in token):
        raise profiles.Refused(tr("토큰에 공백이나 제어 문자가 있습니다. 일부만 붙여 넣었을 수 있습니다"))
    if not 40 <= len(token) <= 4096:
        raise profiles.Refused(tr("토큰 길이가 올바르지 않습니다"))
    return token


def read(root):
    path = fsguard.profile_file(root, FILENAME)
    if not path.exists():
        return None
    if path.stat().st_mode & 0o077:
        raise ValueError("Long-lived token file permissions must be 0600")
    value = json.loads(fsguard.read_profile_bytes(root, FILENAME, 16 * 1024))
    if (not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("token"), str)
            or not isinstance(value.get("expires_at"), (int, float))):
        raise ValueError("Invalid long-lived token file")
    return value


def configured(profile_id):
    return read(profiles.root(profile_id)) is not None


def status(profile_id):
    """Nonsecret summary: whether a token is stored and how long until its assumed expiry."""
    value = read(profiles.root(profile_id))
    if value is None:
        return {"configured": False}
    left = value["expires_at"] - time.time()
    return {"configured": True, "created_at": value.get("created_at"), "expires_at": value["expires_at"],
            "days_left": int(left // 86400), "expiring": left < WARN_SECONDS, "expired": left <= 0}


def environment_value(profile_id):
    value = read(profiles.root(profile_id))
    return value["token"] if value else None


def _conflicts(profile):
    from agent_profile_secret import read as read_private
    if profile["tool"] != "claude":
        raise profiles.Refused(tr("장기 토큰은 Claude 프로필에서만 쓸 수 있습니다"))
    if read_private(profiles.root(profile["id"])) is not None:
        raise profiles.Refused(tr("API 인증이 설정된 프로필입니다. 장기 토큰과 함께 쓸 수 없습니다"))


def set_token(profile_id, raw, expires_at=None):
    from agent_profile_migration import _write_bytes
    from agent_profile_runtime import require_inactive
    profile = profiles.get(profile_id)
    profiles.require_active(profile)
    _conflicts(profile)
    token = validate(raw)
    profiles.require_no_pending_jobs(profile["id"])
    require_inactive(profile["id"])
    now = time.time()
    body = json.dumps({"version": 1, "token": token, "created_at": now, "expires_at": expires_at or now + LIFETIME_SECONDS}).encode()
    path = fsguard.profile_file(profiles.root(profile["id"]), FILENAME)
    fsguard.private_profile_directory(path.parent)
    _write_bytes(path, body, 0o600)
    profiles.bump(profile["id"], profile["revision"])
    return status(profile["id"])


def clear(profile_id):
    from agent_profile_runtime import require_inactive
    profile = profiles.get(profile_id)
    profiles.require_active(profile)
    profiles.require_no_pending_jobs(profile["id"])
    require_inactive(profile["id"])
    path = fsguard.profile_file(profiles.root(profile["id"]), FILENAME)
    existed = path.exists()
    path.unlink(missing_ok=True)
    if existed:
        profiles.bump(profile["id"], profile["revision"])
    return {"configured": False}
