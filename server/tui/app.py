"""vt manage 메인 Textual App."""
from __future__ import annotations

import os
import subprocess
import sys

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import DataTable, Footer, Header, Static
except ImportError:
    sys.stderr.write(
        "ERROR: textual 미설치. 설치: pip install textual\n"
        "또는: pip install -r requirements-core.txt\n"
    )
    sys.exit(1)

from .helpers import (
    VT_PORT,
    get_hotkey,
    get_server_status,
    get_voice_target,
    list_tmux_sessions,
    server_request,
    set_voice_target,
    tmux,
)
from .modals import ConfirmDialog, RenameDialog


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
        self.sub_title = "farshell Manager"
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

        status = self.query_one("#status-bar", Static)
        srv_str = f"● 서버 :{VT_PORT}" if srv.get("running") else "○ 서버 정지"
        voice_str = "● Voice" if srv.get("voice") else "○ Voice"
        target_str = f"🔒 LOCK: {self.target}" if self.target else "🔓 AUTO"
        hotkey_str = f"⌨ {spec}" + (" (DISABLED)" if disabled else "")
        status.update(
            f"{srv_str}    {voice_str}    {target_str}    {hotkey_str}"
        )

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
        if 0 <= cur_row < len(self.sessions):
            table.move_cursor(row=cur_row)

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
            srv_running, _ = server_request("GET", "/api/capabilities", timeout=0.5)
            web_session_id = None
            if srv_running:
                ok, sessions = server_request("GET", "/api/tmux/sessions", timeout=1.0)
                if ok and isinstance(sessions, list):
                    for s in sessions:
                        if isinstance(s, dict) and s.get("name") == name:
                            web_session_id = s.get("web_session_id")
                            break
            if web_session_id:
                ok, resp = server_request(
                    "PATCH", f"/api/sessions/{web_session_id}",
                    body={"name": new_name},
                )
                if ok and resp and resp.get("tmux_renamed"):
                    self.notify(f"이름 변경: {name} → {new_name}")
                    self.refresh_data()
                    return
            rc, _, err = tmux("rename-session", "-t", name, new_name)
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
            srv_running, _ = server_request("GET", "/api/capabilities", timeout=0.5)
            killed = False
            if srv_running:
                ok_srv, _ = server_request("DELETE", f"/api/tmux/kill/{name}")
                killed = ok_srv
            if not killed:
                rc, _, err = tmux("kill-session", "-t", name)
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
