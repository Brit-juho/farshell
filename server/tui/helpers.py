"""TUI 헬퍼 — 서버 HTTP, tmux 호출, 상태 파일 I/O."""
from __future__ import annotations

import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

VT_TMUX_SOCKET = os.environ.get("VT_TMUX_SOCKET", "vt")
VT_PORT = int(os.environ.get("VT_PORT", "7777"))
VT_TARGET_FILE = Path.home() / ".vt" / "voice_target"
VT_ENV_FILE = Path.home() / ".vt.env"
VT_TOKEN = os.environ.get("VT_TOKEN", "")


def server_request(method: str, path: str, body: dict | None = None, timeout: float = 2.0) -> tuple[bool, dict | None]:
    """server에 인증된 HTTP 요청. 서버 미실행 시 (False, None)."""
    import json as _json
    url = f"http://127.0.0.1:{VT_PORT}{path}"
    data = None if body is None else _json.dumps(body).encode()
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if VT_TOKEN:
        headers["Authorization"] = f"Bearer {VT_TOKEN}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            content = r.read()
            return True, (_json.loads(content) if content else {})
    except (urllib.error.URLError, ConnectionError, OSError, _json.JSONDecodeError):
        return False, None


def tmux(*args: str, timeout: float = 2.0) -> tuple[int, str, str]:
    cmd = ["tmux", "-L", VT_TMUX_SOCKET, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "tmux not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def list_tmux_sessions() -> list[dict]:
    rc, out, _ = tmux(
        "list-sessions", "-F",
        "#{session_name}\t#{session_windows}\t#{session_attached}",
    )
    if rc != 0:
        return []
    sessions: list[dict] = []
    for line in out.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            sessions.append({
                "name": parts[0],
                "windows": int(parts[1]) if parts[1].isdigit() else 1,
                "attached": parts[2] == "1",
            })
    return sessions


def get_voice_target() -> str | None:
    try:
        if not VT_TARGET_FILE.is_file():
            return None
        v = VT_TARGET_FILE.read_text().strip()
        return v if v else None
    except Exception:
        return None


def set_voice_target(name: str | None) -> None:
    VT_TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if name:
        VT_TARGET_FILE.write_text(name + "\n")
    else:
        try:
            VT_TARGET_FILE.unlink()
        except FileNotFoundError:
            pass


def get_server_status() -> dict:
    """서버 동작 여부 + capabilities 응답."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{VT_PORT}/api/capabilities", timeout=1.0
        ) as r:
            import json
            data = json.loads(r.read())
            return {"running": True, **data}
    except (urllib.error.URLError, ConnectionError, OSError):
        return {"running": False}


def _parse_env_value(line: str, key: str) -> str | None:
    """`KEY=value` 또는 `export KEY="value"` 라인에서 값 추출."""
    s = line.strip()
    if s.startswith("export "):
        s = s[len("export "):]
    if not s.startswith(key + "="):
        return None
    val = s[len(key) + 1:].strip()
    if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
        val = val[1:-1]
    return val


def get_hotkey() -> tuple[str, bool]:
    """(spec, disabled) 반환."""
    spec = "ctrl+shift+v"
    disabled = False
    if VT_ENV_FILE.is_file():
        for line in VT_ENV_FILE.read_text().splitlines():
            v = _parse_env_value(line, "VT_HOTKEY_VOICE")
            if v is not None:
                spec = v
                continue
            v = _parse_env_value(line, "VT_HOTKEY_VOICE_DISABLED")
            if v is not None:
                disabled = v.lower() == "true"
    return spec, disabled
