"""TUI 모달 다이얼로그 — 이름 변경 + 확인."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label


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
