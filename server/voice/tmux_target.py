"""tmux pane 결정 + 텍스트 주입 + voice target lock 처리."""
import os
import subprocess

from .config import TMUX_BASE, logger


def get_active_tmux_pane() -> str | None:
    """현재 활성 tmux pane ID."""
    try:
        result = subprocess.run(
            TMUX_BASE + ["display-message", "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        pane_id = result.stdout.strip()
        return pane_id if pane_id else None
    except Exception:
        return None


def get_any_tmux_pane() -> str | None:
    """아무 tmux 세션의 첫 번째 pane."""
    try:
        result = subprocess.run(
            TMUX_BASE + ["list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        panes = result.stdout.strip().split("\n")
        return panes[0] if panes and panes[0] else None
    except Exception:
        return None


def read_voice_target_lock() -> str | None:
    """~/.vt/voice_target에 lock된 세션 이름."""
    try:
        path = os.path.expanduser("~/.vt/voice_target")
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            target = f.read().strip()
        return target if target else None
    except Exception:
        return None


def get_locked_session_pane(session_name: str) -> str | None:
    """lock된 세션의 active pane id."""
    try:
        result = subprocess.run(
            TMUX_BASE + ["display-message", "-p", "-t", session_name, "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        pane_id = result.stdout.strip()
        return pane_id if pane_id else None
    except Exception:
        return None


def resolve_voice_target_pane() -> tuple[str | None, str]:
    """음성 daemon이 보낼 pane 결정. (pane_id, mode) 반환.
    mode: "lock:<name>" / "auto" / "none"
    """
    locked = read_voice_target_lock()
    if locked:
        pane = get_locked_session_pane(locked)
        if pane:
            return pane, f"lock:{locked}"
        logger.warning(f"음성 타깃 lock된 세션 '{locked}' 없음 → AUTO 폴백")
    pane = get_active_tmux_pane() or get_any_tmux_pane()
    return pane, ("auto" if pane else "none")


def send_to_tmux(pane_id: str, text: str) -> bool:
    """tmux pane에 텍스트 전송."""
    try:
        subprocess.run(
            TMUX_BASE + ["send-keys", "-t", pane_id, "--", text, "Enter"],
            timeout=2, check=True,
        )
        return True
    except Exception as e:
        logger.error(f"tmux send-keys 실패: {e}")
        return False
