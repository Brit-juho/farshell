"""fsh manage 메인 Textual App."""
from __future__ import annotations

import os
import subprocess
import sys

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import DataTable, Footer, Header, Static
except ImportError:
    from i18n import tr
    sys.stderr.write(
        tr("ERROR: textual 미설치. 설치: pip install textual\n또는: pip install --require-hashes -r locks/core.txt\n")
    )
    sys.exit(1)

from .helpers import (
    VT_PORT,
    clean_label,
    get_hotkey,
    get_server_status,
    get_voice_target,
    list_tmux_sessions,
    server_call,
    server_request,
    server_sessions,
    set_voice_target,
    tmux,
)
from .modals import ConfirmDialog, RenameDialog
from i18n import tr


class RenameFailed(Exception):
    """이름(별칭) 바꾸기 실패 — 메시지가 화면에 그대로 보인다."""


def _rename_error(code: str | None, status: int) -> str:
    if code == "alias_taken":
        return tr("이미 다른 세션이 쓰는 이름입니다")
    if code == "invalid_title":
        return tr("쓸 수 없는 이름입니다(1~128자, 제어 문자·ID 모양 금지)")
    if code == "alias_unavailable" or status == 503:
        return tr("세션 기록을 아직 못 읽었습니다 — 잠시 뒤 다시 하세요")
    return clean_label(code) if code else f"HTTP {status or '-'}"


def rename_session(name: str, old: str, new: str, rows: dict[str, dict]) -> str:
    """세션 `name`(ID)의 별칭을 `new`로. 성공하면 알림 문구, 실패하면 RenameFailed. tmux 이름은 건드리지 않는다."""
    if not rows:
        raise RenameFailed(tr("서버에 연결할 수 없어 이름을 바꾸지 못했습니다 — 'fsh status'로 확인하세요"))
    web_session_id = (rows.get(name) or {}).get("web_session_id")
    if not web_session_id:
        raise RenameFailed(tr("서버가 이 세션을 아직 모릅니다 — 잠시 뒤 다시 하세요"))
    status, resp = server_call("PATCH", f"/api/sessions/{web_session_id}", body={"name": new})
    if status == 200 and isinstance(resp, dict):
        shown = resp.get("alias") or resp.get("name") or new
        return tr("이름 변경: {old} → {new}", old=clean_label(old), new=clean_label(shown))
    code = resp.get("error") if isinstance(resp, dict) else None
    raise RenameFailed(_rename_error(code if isinstance(code, str) else None, status))


class VTManagerApp(App):
    """fsh manage TUI 메인 앱."""

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
        Binding("q", "quit", tr("종료")),
        Binding("r", "rename", tr("이름 변경")),
        Binding("k", "kill", "kill"),
        Binding("a", "attach", "attach"),
        Binding("l", "lock", tr("lock 토글")),
        Binding("u", "unlock", tr("lock 해제")),
        Binding("R", "refresh", tr("새로고침")),
        Binding("?", "help", tr("도움말")),
    ]

    def __init__(self):
        super().__init__()
        self.sessions: list[dict] = []
        self.target: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(tr("로딩 중..."), id="status-bar")
        yield DataTable(id="sessions-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="footer-info")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "fsh manage"
        self.sub_title = "FarShell Manager"
        table = self.query_one(DataTable)
        table.add_column(tr("세션"), width=24)
        table.add_column("Window", width=8)
        table.add_column("Attached", width=10)
        table.add_column("🎤 Voice Target", width=20)
        self.refresh_data()
        self.set_interval(3.0, self.refresh_data)

    def _label(self, name: str | None) -> str:
        """화면에 보일 세션 이름 — 별칭(서버 기록), 없으면 ID. 열쇠(잠금·kill·attach)는 언제나 ID."""
        if not name:
            return ""
        for s in self.sessions:
            if s["name"] == name:
                return s.get("label") or clean_label(name)
        return clean_label(name)

    def refresh_data(self) -> None:
        sessions = list_tmux_sessions()
        rows = server_sessions() if sessions else {}
        for s in sessions:
            s["label"] = clean_label((rows.get(s["name"]) or {}).get("alias") or s["name"])
        self.sessions = sessions
        self.target = get_voice_target()
        spec, disabled = get_hotkey()
        srv = get_server_status()

        status = self.query_one("#status-bar", Static)
        srv_str = tr("● 서버 :{port}", port=VT_PORT) if srv.get("running") else tr("○ 서버 정지")
        voice_str = "● Voice" if srv.get("voice") else "○ Voice"
        target_str = f"🔒 LOCK: {self._label(self.target)}" if self.target else "🔓 AUTO"
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
                s["label"],
                str(s["windows"]),
                "✓" if s["attached"] else "",
                target_mark,
                key=s["name"],
            )
        if 0 <= cur_row < len(self.sessions):
            table.move_cursor(row=cur_row)

        footer = self.query_one("#footer-info", Static)
        footer.update(
            tr("[r] rename  [k] kill  [a] attach (새 창)  [l] voice lock  [u] unlock  [R] refresh  [q] quit")
            + "\n"
            + tr("Hotkey 변경: 'fsh hotkey set voice <key>'  |  Help: 'fsh help concepts'")
        )

    def _selected_name(self) -> str | None:
        table = self.query_one(DataTable)
        if table.cursor_row < 0 or not self.sessions:
            return None
        return self.sessions[table.cursor_row]["name"]

    def action_refresh(self) -> None:
        self.refresh_data()
        self.notify(tr("새로고침 완료"), timeout=1.5)

    def action_rename(self) -> None:
        """별칭(보이는 이름)만 바꾼다 — 서버 기록이 주인이다(PATCH /api/sessions/{id}). tmux 이름(ID)은 바꾸지 않는다.
        서버가 없거나 이 세션을 모르면 바꾸지 않고 이유를 보인다(예전의 `tmux rename-session` 폴백은 없앴다)."""
        name = self._selected_name()
        if not name:
            return
        rows = server_sessions()
        current = (rows.get(name) or {}).get("alias") or name

        def on_done(new_name: str | None) -> None:
            if not new_name or new_name == current:
                return
            try:
                self.notify(rename_session(name, current, new_name, server_sessions()))
            except RenameFailed as e:
                self.notify(tr("실패: {err}", err=str(e)), severity="error")
            self.refresh_data()

        self.push_screen(RenameDialog(current), on_done)

    def action_kill(self) -> None:
        name = self._selected_name()
        if not name:
            return

        label = self._label(name)

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
                    self.notify(tr("실패: {err}", err=err.strip()), severity="error")
                    return
            self.notify(tr("세션 '{name}' 종료", name=label))
            if self.target == name:
                set_voice_target(None)
            self.refresh_data()

        self.push_screen(
            ConfirmDialog(tr("세션 '{name}'을 정말 종료하시겠습니까?", name=label)),
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
                self.notify(tr("fsh CLI 못 찾음"), severity="error")
                return
            subprocess.Popen(
                [vt_bin, "attach", name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.notify(tr("새 터미널 창에 '{name}' attach 시도", name=self._label(name)))
        except Exception as e:
            self.notify(tr("실패: {err}", err=e), severity="error")

    def _find_vt_bin(self) -> str | None:
        # fsh가 정식 명령어, vt는 하위 호환 심링크(둘 다 같은 bin/fsh를 가리킨다) —
        # 새 설치는 fsh만 있을 수 있으므로 fsh를 먼저 찾는다.
        for name in ("fsh", "vt"):
            for prefix in ("~/.local/bin", "/usr/local/bin", "/opt/homebrew/bin"):
                cand = os.path.expanduser(f"{prefix}/{name}")
                if os.path.isfile(cand) and os.access(cand, os.X_OK):
                    return cand
        return None

    def action_lock(self) -> None:
        name = self._selected_name()
        if not name:
            return
        set_voice_target(name)
        self.notify(tr("🔒 음성 타깃 LOCK: {name}", name=self._label(name)))
        self.refresh_data()

    def action_unlock(self) -> None:
        set_voice_target(None)
        self.notify(tr("🔓 음성 타깃 → AUTO"))
        self.refresh_data()

    def action_help(self) -> None:
        self.notify(
            tr("단축키:\n  ↑↓ 선택  Enter (선택)\n  r=rename  k=kill  a=attach (새 창)\n  l=음성 lock  u=lock 해제  R=새로고침  q=종료"),
            timeout=6,
        )


def main() -> int:
    app = VTManagerApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
