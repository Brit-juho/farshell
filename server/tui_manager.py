"""vt manage — Textual 기반 TUI 관리 도구 (W4-1).

cross-platform (macOS/Linux). 의존성: textual.

기능:
- tmux 세션 목록 (라이브 갱신)
- rename / kill / attach
- 음성 타깃 lock 변경
- 핫키 표시 (편집은 vt hotkey CLI로 안내)
- 서버 상태 표시
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import (
        DataTable, Footer, Header, Input, Label, Static,
    )
    from textual.screen import ModalScreen
except ImportError:
    sys.stderr.write(
        "ERROR: textual 미설치. 설치: pip install textual\n"
        "또는: pip install -r requirements-core.txt\n"
    )
    sys.exit(1)


VT_TMUX_SOCKET = os.environ.get("VT_TMUX_SOCKET", "vt")
VT_PORT = int(os.environ.get("VT_PORT", "7777"))
VT_TARGET_FILE = Path.home() / ".vt" / "voice_target"
VT_ENV_FILE = Path.home() / ".vt.env"
VT_TOKEN = os.environ.get("VT_TOKEN", "")


def _server_request(method: str, path: str, body: dict | None = None, timeout: float = 2.0) -> tuple[bool, dict | None]:
    """server에 인증된 HTTP 요청 보냄. 서버 미실행 시 (False, None) 반환."""
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


def _tmux(*args: str, timeout: float = 2.0) -> tuple[int, str, str]:
    cmd = ["tmux", "-L", VT_TMUX_SOCKET, *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return 127, "", "tmux not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def list_tmux_sessions() -> list[dict]:
    rc, out, _ = _tmux(
        "list-sessions",
        "-F",
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
    # 이중/단일 따옴표 둘 다 처리
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


class RenameDialog(ModalScreen[str | None]):
    """세션 이름 변경 모달."""

    BINDINGS = [Binding("escape", "cancel", "취소")]

    def __init__(self, current_name: str):
        super().__init__()
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"'{self.current_name}' 새 이름:"),
            Input(value=self.current_name, id="rename-input"),
            Label("[Enter] 확정  [Esc] 취소", classes="dim"),
            id="rename-dialog",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        new_name = event.value.strip()
        self.dismiss(new_name if new_name else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmDialog(ModalScreen[bool]):
    """확인 모달."""

    BINDINGS = [
        Binding("y", "yes", "예"),
        Binding("n", "cancel", "아니오"),
        Binding("escape", "cancel", "취소"),
    ]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self.message),
            Label("[y] 예  [n/Esc] 아니오", classes="dim"),
            id="confirm-dialog",
        )

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class VTManagerApp(App):
    """vt manage TUI 메인 앱."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #status-bar {
        height: 3;
        dock: top;
        background: $primary-background;
        padding: 1;
    }
    #sessions-table {
        height: 1fr;
    }
    #footer-info {
        height: 5;
        dock: bottom;
        padding: 1;
    }
    .dim { color: $text-muted; }
    #rename-dialog, #confirm-dialog {
        align: center middle;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "종료"),
        Binding("r", "rename", "이름 변경"),
        Binding("k", "kill", "kill"),
        Binding("a", "attach", "attach"),
        Binding("l", "lock", "lock 토글"),
        Binding("u", "unlock", "lock 해제"),
        Binding("R", "refresh", "새로고침"),
        Binding("?", "help", "도움말"),
    ]

    def __init__(self):
        super().__init__()
        self.sessions: list[dict] = []
        self.target: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("로딩 중...", id="status-bar")
        yield DataTable(id="sessions-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="footer-info")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "vt manage"
        self.sub_title = "Voice Terminal Manager"
        table = self.query_one(DataTable)
        table.add_column("세션", width=24)
        table.add_column("Window", width=8)
        table.add_column("Attached", width=10)
        table.add_column("🎤 Voice Target", width=20)
        self.refresh_data()
        self.set_interval(3.0, self.refresh_data)

    def refresh_data(self) -> None:
        self.sessions = list_tmux_sessions()
        self.target = get_voice_target()
        spec, disabled = get_hotkey()
        srv = get_server_status()

        # status bar
        status = self.query_one("#status-bar", Static)
        srv_str = f"● 서버 :{VT_PORT}" if srv.get("running") else "○ 서버 정지"
        voice_str = "● Voice" if srv.get("voice") else "○ Voice"
        target_str = f"🔒 LOCK: {self.target}" if self.target else "🔓 AUTO"
        hotkey_str = f"⌨ {spec}" + (" (DISABLED)" if disabled else "")
        status.update(
            f"{srv_str}    {voice_str}    {target_str}    {hotkey_str}"
        )

        # table
        table = self.query_one(DataTable)
        cur_row = table.cursor_row
        table.clear()
        for s in self.sessions:
            target_mark = "🎤 LOCK" if (self.target == s["name"]) else ""
            table.add_row(
                s["name"],
                str(s["windows"]),
                "✓" if s["attached"] else "",
                target_mark,
                key=s["name"],
            )
        # cursor 복원
        if 0 <= cur_row < len(self.sessions):
            table.move_cursor(row=cur_row)

        # footer
        footer = self.query_one("#footer-info", Static)
        footer.update(
            "[r] rename  [k] kill  [a] attach (새 창)  [l] voice lock  "
            "[u] unlock  [R] refresh  [q] quit\n"
            f"Hotkey 변경: 'vt hotkey set voice <key>'  |  "
            f"Help: 'vt help concepts'"
        )

    def _selected_name(self) -> str | None:
        table = self.query_one(DataTable)
        if table.cursor_row < 0 or not self.sessions:
            return None
        return self.sessions[table.cursor_row]["name"]

    def action_refresh(self) -> None:
        self.refresh_data()
        self.notify("새로고침 완료", timeout=1.5)

    def action_rename(self) -> None:
        name = self._selected_name()
        if not name:
            return

        def on_done(new_name: str | None) -> None:
            if not new_name or new_name == name:
                return
            # 서버가 실행 중이고 해당 tmux에 attach된 세션이 있으면 PATCH API 사용
            # → server의 session_store 인덱스 동기화 + tmux rename 한 번에
            srv_running, caps = _server_request("GET", "/api/capabilities", timeout=0.5)
            web_session_id = None
            if srv_running:
                ok, sessions = _server_request("GET", "/api/tmux/sessions", timeout=1.0)
                if ok and isinstance(sessions, list):
                    for s in sessions:
                        if isinstance(s, dict) and s.get("name") == name:
                            web_session_id = s.get("web_session_id")
                            break
            if web_session_id:
                ok, resp = _server_request(
                    "PATCH", f"/api/sessions/{web_session_id}",
                    body={"name": new_name},
                )
                if ok and resp and resp.get("tmux_renamed"):
                    self.notify(f"이름 변경: {name} → {new_name}")
                    self.refresh_data()
                    return
                # 서버 경로 실패 → 직접 tmux 명령 폴백
            rc, _, err = _tmux("rename-session", "-t", name, new_name)
            if rc == 0:
                self.notify(f"이름 변경: {name} → {new_name}")
                self.refresh_data()
            else:
                self.notify(f"실패: {err.strip()}", severity="error")

        self.push_screen(RenameDialog(name), on_done)

    def action_kill(self) -> None:
        name = self._selected_name()
        if not name:
            return

        def on_confirm(ok: bool) -> None:
            if not ok:
                return
            # 서버 실행 중이면 DELETE /api/tmux/kill/{name} 사용
            # → pty_mgr/session_store/output_watcher까지 일괄 정리
            srv_running, _ = _server_request("GET", "/api/capabilities", timeout=0.5)
            killed = False
            if srv_running:
                ok_srv, _ = _server_request("DELETE", f"/api/tmux/kill/{name}")
                killed = ok_srv
            if not killed:
                # 서버 미실행 또는 API 실패 → 직접 tmux 명령
                rc, _, err = _tmux("kill-session", "-t", name)
                if rc != 0:
                    self.notify(f"실패: {err.strip()}", severity="error")
                    return
            self.notify(f"세션 '{name}' 종료")
            if self.target == name:
                set_voice_target(None)
            self.refresh_data()

        self.push_screen(
            ConfirmDialog(f"세션 '{name}'을 정말 종료하시겠습니까?"),
            on_confirm,
        )

    def action_attach(self) -> None:
        """새 OS 터미널 창에 attach."""
        name = self._selected_name()
        if not name:
            return
        # vt CLI의 attach 명령 사용. TUI stdio 격리 + 새 세션
        try:
            vt_bin = os.environ.get("VT_BIN") or self._find_vt_bin()
            if not vt_bin:
                self.notify("vt CLI 못 찾음", severity="error")
                return
            subprocess.Popen(
                [vt_bin, "attach", name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify(f"새 터미널 창에 '{name}' attach 시도")
        except Exception as e:
            self.notify(f"실패: {e}", severity="error")

    def _find_vt_bin(self) -> str | None:
        for cand in [
            os.path.expanduser("~/.local/bin/vt"),
            "/usr/local/bin/vt",
            "/opt/homebrew/bin/vt",
        ]:
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return None

    def action_lock(self) -> None:
        name = self._selected_name()
        if not name:
            return
        set_voice_target(name)
        self.notify(f"🔒 음성 타깃 LOCK: {name}")
        self.refresh_data()

    def action_unlock(self) -> None:
        set_voice_target(None)
        self.notify("🔓 음성 타깃 → AUTO")
        self.refresh_data()

    def action_help(self) -> None:
        self.notify(
            "단축키:\n"
            "  ↑↓ 선택  Enter (선택)\n"
            "  r=rename  k=kill  a=attach (새 창)\n"
            "  l=음성 lock  u=lock 해제  R=새로고침  q=종료",
            timeout=6,
        )


def main() -> int:
    app = VTManagerApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
