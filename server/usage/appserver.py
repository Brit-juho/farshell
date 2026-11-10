"""Codex usage from the CLI's own app-server — hotswap H2.

`codex app-server` (experimental, JSON-RPC lines over stdio) answers `account/rateLimits/read` with
`result.rateLimits.{primary,secondary}.{usedPercent, resetsAt (Unix seconds), windowDurationMins}`
(plus `accountId`). It follows `CODEX_HOME`, so one short-lived process per managed Codex profile
reads that profile's account without FarShell holding or sending a token itself.

Conditions (every one of them must hold, otherwise nothing is started):
  * the profile's login file exists
  * no Codex process uses that CODEX_HOME (the app-server may refresh the login, and a running CLI of
    the same account could race it for the refresh token) — the same ps check as `require_inactive`;
    a ps that cannot be read counts as "in use"
  * at least `INTERVAL` seconds since the last attempt (`UNSUPPORTED_INTERVAL` once the CLI was found
    not to support the method); the attempt time is remembered in the state dir, so a restart does not reset it
  * a `codex` executable on PATH
The process lives at most `TIMEOUT` seconds (initialize -> read -> exit), runs in its own process group
with a minimal environment, and is always killed on the way out. Any failure keeps the last good value.
The result is a file (`<VT_STATE_DIR>/usage/appserver/<profile-id>.json`, 0600) that readers use
without starting anything. `VT_USAGE_APPSERVER=0` turns the whole source off.
"""
from __future__ import annotations

import json
import math
import os
import select
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import vt_paths

SOURCE = "app-server"
INTERVAL = 300
UNSUPPORTED_INTERVAL = 6 * 3600
TIMEOUT = 10.0
MAX_LINE = 1024 * 1024
_ENV_KEEP = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
             "NO_PROXY", "https_proxy", "http_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR")

_lock = threading.Lock()
_running = set()
_skipped = {}      # profile id -> monotonic time before which a busy profile is not asked again
SKIP_RECHECK = 30


def enabled():
    return os.environ.get("VT_USAGE_APPSERVER", "1").strip().lower() not in ("0", "false", "off", "no")


def _file(profile_id):
    return vt_paths.state_dir() / "usage" / "appserver" / f"{profile_id}.json"


def _load(profile_id):
    try:
        info = os.lstat(_file(profile_id))
        if not (info.st_mode & 0o170000 == 0o100000) or info.st_uid != os.getuid() or info.st_size > 64 * 1024:
            return {}
        value = json.loads(_file(profile_id).read_bytes())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save(profile_id, value):
    target = _file(profile_id)
    old = os.umask(0o077)
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = target.with_name(f".{profile_id}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(value))
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError:
        pass
    finally:
        os.umask(old)


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _windows(limits, now, updated_at):
    from .codex import _span_label
    out = []
    for slot, fallback in (("primary", "5h"), ("secondary", "weekly")):
        raw = limits.get(slot) if isinstance(limits, dict) else None
        if not isinstance(raw, dict):
            continue
        pct = _number(raw.get("usedPercent"))
        if pct is None or not 0 <= pct <= 100:
            continue
        mins = _number(raw.get("windowDurationMins"))
        label, wid = _span_label({"limit_window_seconds": mins * 60} if mins else None, fallback)
        reset = _number(raw.get("resetsAt"))
        out.append({"id": wid, "label": label, "pct": float(pct), "resets_at_epoch": reset,
                    "source": SOURCE, "updated_at": updated_at})
    return out


def read(profile_id, now):
    """`{windows, updated_at, source, status}` for readers (no process, no network) or None."""
    value = _load(profile_id)
    windows = []
    for w in value.get("windows") or []:
        if not isinstance(w, dict) or _number(w.get("pct")) is None:
            continue
        reset = _number(w.get("resets_at_epoch"))
        if reset is not None and reset <= now:
            continue
        windows.append({"id": str(w.get("id")), "label": str(w.get("label")), "pct": float(w["pct"]),
                        "resets_at": datetime.fromtimestamp(reset, timezone.utc).isoformat() if reset is not None else None,
                        "resets_in_sec": max(0, int(reset - now)) if reset is not None else None,
                        "source": SOURCE, "updated_at": _number(w.get("updated_at")) or _number(value.get("updated_at"))})
    if not windows:
        return None
    return {"windows": windows, "updated_at": _number(value.get("updated_at")), "source": SOURCE}


class _Unsupported(Exception):
    pass


class _Failed(Exception):
    pass


def _lines(stream, deadline):
    """Yield JSON objects from the process's stdout until the deadline. Raises _Failed on timeout/EOF."""
    fd = stream.fileno()
    buffer = b""
    while True:
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if line.strip():
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    yield value
        left = deadline - time.monotonic()
        if left <= 0:
            raise _Failed("timeout")
        ready, _, _ = select.select([fd], [], [], min(left, 0.5))
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            raise _Failed("eof")
        buffer += chunk
        if len(buffer) > MAX_LINE:
            raise _Failed("too-large")


def _kill(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            process.wait(timeout=1.5 if sig == signal.SIGTERM else 3)
            return
        except subprocess.TimeoutExpired:
            continue


def query(codex, home, timeout=TIMEOUT):
    """Run the short JSON-RPC exchange. Returns the `result` of `account/rateLimits/read`."""
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env["CODEX_HOME"] = str(home)
    process = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=env, cwd=str(home), start_new_session=True)
    try:
        deadline = time.monotonic() + timeout
        initialized = False

        def send(message):
            process.stdin.write((json.dumps(message) + "\n").encode())
            process.stdin.flush()

        try:
            send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "farshell", "title": None, "version": "1"}}})
            answers = _lines(process.stdout, deadline)
            for message in answers:
                if message.get("id") == 1:
                    if "error" in message:
                        raise _Failed("initialize")
                    shown = (message.get("result") or {}).get("codexHome")
                    # It must be the profile's own home. Falling back to the default ~/.codex would read another account.
                    if isinstance(shown, str) and Path(shown).resolve() != Path(home).resolve():
                        raise _Failed("wrong-home")
                    initialized = True
                    break
            send({"method": "initialized"})
            send({"id": 2, "method": "account/rateLimits/read", "params": {"excludeResetCreditDetails": True}})
            for message in answers:
                if message.get("id") != 2:
                    continue
                error = message.get("error")
                if isinstance(error, dict):
                    text = str(error.get("message", "")).lower()
                    if error.get("code") == -32601 or "method not found" in text or "unknown variant" in text or "unsupported" in text:
                        raise _Unsupported()
                    raise _Failed("rpc-error")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise _Failed("bad-result")
                return result
        except _Failed as exc:
            if str(exc) == "eof" and not initialized:
                # The process ended before it ever answered: a CLI without the `app-server` command
                # (usage error, exit 2) does not support this source; any other exit is a failure.
                try:
                    code = process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    code = None
                if code == 2:
                    raise _Unsupported() from exc
            raise
        except (BrokenPipeError, OSError) as exc:
            raise _Failed("io") from exc
        raise _Failed("no-answer")
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        _kill(process)
        try:
            process.stdout.close()
        except OSError:
            pass


def due(profile_id, now):
    state = _load(profile_id)
    attempted = _number(state.get("attempted_at"))
    if attempted is None or attempted > now:
        return True
    wait = UNSUPPORTED_INTERVAL if state.get("status") == "unsupported" else INTERVAL
    return now - attempted >= wait


def refresh(profile, now=None):
    """One refresh attempt when every condition holds. Returns a status word (never raises)."""
    import agent_profiles as registry
    import fsguard
    now = time.time() if now is None else now
    if not enabled() or profile.get("tool") != "codex":
        return "disabled"
    pid = profile["id"]
    try:
        root = registry.root(pid)
        if not fsguard.profile_file(root, "auth.json").exists():
            return "no-login"
    except (OSError, fsguard.FsDenied, registry.Refused):
        return "no-login"
    if not due(pid, now):
        return "not-due"
    if time.monotonic() < _skipped.get(pid, 0):
        return "busy"
    try:
        from agent_profile_runtime import require_inactive
        require_inactive(pid)
    except registry.Refused:
        _skipped[pid] = time.monotonic() + SKIP_RECHECK
        return "busy"
    codex = shutil.which("codex")
    if not codex:
        return "no-cli"
    state = _load(pid)
    state["attempted_at"] = now
    status = "failed"
    try:
        result = query(codex, root, TIMEOUT)
        windows = _windows(result.get("rateLimits"), now, now)
        if windows:
            state.update(windows=windows, updated_at=now, status="ok")
            status = "ok"
        else:
            state["status"] = "empty"
            status = "empty"
    except _Unsupported:
        state["status"] = "unsupported"
        status = "unsupported"
    except (_Failed, OSError, ValueError, subprocess.SubprocessError):
        state["status"] = "failed"   # the last good windows stay
    _save(pid, state)
    return status


def refresh_async(profile):
    """Fire-and-forget refresh so a usage poll never waits for the CLI."""
    if not enabled() or profile.get("tool") != "codex":
        return
    pid = profile["id"]
    with _lock:
        if pid in _running:
            return
        _running.add(pid)

    def work():
        try:
            refresh(profile)
        except Exception:  # noqa: BLE001 — a background source must never take the poll down
            pass
        finally:
            with _lock:
                _running.discard(pid)

    threading.Thread(target=work, name="usage-appserver", daemon=True).start()
