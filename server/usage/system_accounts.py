"""The default (system) accounts: the global ~/.claude and ~/.codex that no managed profile owns.

Codex: the existing `CodexProvider` (it reads ~/.codex/auth.json and asks the usage endpoint).
Claude: FarShell never reads the system-default Keychain item (see `profiles._read_credentials`). Its
usage comes from the statusline input Claude Code itself hands to the global statusLine command, which
the user can have wrapped with `bin/fsh-statusline --profile system` (`set_claude`). The wrapper runs the
user's own command unchanged; turning it off puts the original value back.

Writing the global settings follows `claude_hooks`: one lock, the file must not change between read and
write, a `.vtbak` copy first, then an atomic replace. Only the `statusLine` key is touched.
"""
from __future__ import annotations

import time

import agent_profile_settings as settings_sync
import claude_hooks
import config_io
import fsguard
import store_lock
from i18n import tr

from . import statusline

SYSTEM_ID = settings_sync.SYSTEM_ID
KEY = settings_sync.STATUSLINE


class Refused(Exception):
    pass


def _path():
    return fsguard.global_claude_dir() / "settings.json"


def _read():
    path = _path()
    if path.is_symlink():
        raise Refused(tr("전역 설정을 안전하게 읽지 못했습니다"))
    try:
        return claude_hooks.load_settings(path)
    except (OSError, ValueError) as exc:
        raise Refused(tr("전역 설정을 안전하게 읽지 못했습니다")) from exc


def _state(settings):
    """on | stale (wrapped, but not by this install's wrapper) | off | unsupported."""
    value = settings.get(KEY)
    if settings_sync.wrapped_id(value) == SYSTEM_ID:
        original = settings_sync.global_view(value)
        want = settings_sync.wrap_command(SYSTEM_ID, None if original is settings_sync._MISSING else original["command"])
        return "on" if value.get("command") == want else "stale"
    if settings_sync.wrapped_id(value) is not None:
        return "unsupported"   # a profile's wrapper in the global file: not ours to rewrite
    if value is None or (isinstance(value, dict) and value.get("type") == "command" and isinstance(value.get("command"), str)):
        return "off"
    return "unsupported"


def claude_state():
    try:
        return _state(_read())
    except Refused:
        return "unreadable"


def set_claude(enabled: bool) -> str:
    """Wrap (or unwrap) the global statusLine for the default account. Returns the new state."""
    path = _path()
    if enabled and not settings_sync.WRAPPER.is_file():
        raise Refused(tr("사용량 수집 스크립트(bin/fsh-statusline)를 찾지 못했습니다"))
    try:
        with config_io.locked(path):
            stamp = config_io.stamp(path)
            settings = _read()
            state = _state(settings)
            if state == "unsupported":
                raise Refused(tr("전역 statusLine이 명령 형식이 아니라 감쌀 수 없습니다"))
            current = settings.get(KEY, settings_sync._MISSING)
            original = settings_sync.global_view(current) if current is not settings_sync._MISSING else current
            updated = dict(settings)
            if enabled:
                if state == "on":
                    return state
                command = None if original is settings_sync._MISSING else original["command"]
                base = {"type": "command"} if original is settings_sync._MISSING else original
                updated[KEY] = {**base, "command": settings_sync.wrap_command(SYSTEM_ID, command)}
            else:
                if state == "off":
                    return state
                if original is settings_sync._MISSING:
                    updated.pop(KEY, None)
                else:
                    updated[KEY] = original
            claude_hooks.write_settings(path, updated, stamp)
            return _state(updated)
    except (config_io.ConfigWriteRefused, store_lock.LockTimeout) as exc:
        raise Refused(str(exc)) from exc
    except settings_sync.profiles.Refused as exc:
        raise Refused(str(exc)) from exc


def claude_row(now=None):
    """The default Claude account's card, or None when there is no global ~/.claude."""
    if not fsguard.global_claude_dir().is_dir():
        return None
    now = time.time() if now is None else now
    found = statusline.read(SYSTEM_ID, now)
    return {
        "name": "claude", "system": True, "provider": "claude", "profile_id": None,
        "collect": claude_state(), "active": False, "tier": "",
        "auth_ok": True, "auth_status": "ok", "fetch_status": "Fresh" if found else "NoData",
        "has_live_session": False, "rolling_token": False, "stale": False,
        "windows": found["windows"] if found else [], "fallback": None,
        "source": found["source"] if found else None, "checked_at": found["updated_at"] if found else None,
        "quota_scope": "account",
    }
