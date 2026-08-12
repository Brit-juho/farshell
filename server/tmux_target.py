"""tmux pane 결정 + 텍스트 주입 — 타깃 결정 규칙의 단일 진실의 원천.

원래 server/voice/tmux_target.py 에 있었으나 여기로 옮겼다. 이유:
voice/config.py 가 `from pynput import keyboard` 를 하기 때문에, voice 패키지를
거치면 **음성 미설치 환경에서 import 자체가 실패한다.** 프롬프트 큐(P4)처럼
음성과 무관한 기능도 같은 타깃 규칙을 써야 하므로 의존성 없는 위치로 끌어냈다.
server/voice/tmux_target.py 는 이 모듈을 재export하는 얇은 껍데기로 남아 있다.

타깃 결정 순서 (음성·큐가 공유한다 — 규칙을 새로 만들지 말 것):
  1. ~/.vt/voice_target 에 lock된 세션의 active pane
  2. lock이 없거나 그 세션이 사라졌으면 → 현재 활성 pane
  3. 그것도 없으면 → 아무 pane
"""

from __future__ import annotations

import logging
import os
import subprocess

import vt_env

logger = logging.getLogger(__name__)

TMUX_SOCKET = vt_env.getenv("VT_TMUX_SOCKET", "vt")
TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]

VOICE_TARGET_LOCK = "~/.vt/voice_target"


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
        path = os.path.expanduser(VOICE_TARGET_LOCK)
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
    """보낼 pane 결정. (pane_id, mode) 반환.
    mode: "lock:<name>" / "auto" / "none"
    """
    locked = read_voice_target_lock()
    if locked:
        pane = get_locked_session_pane(locked)
        if pane:
            return pane, f"lock:{locked}"
        logger.warning(f"타깃 lock된 세션 '{locked}' 없음 → AUTO 폴백")
    pane = get_active_tmux_pane() or get_any_tmux_pane()
    return pane, ("auto" if pane else "none")


def session_pane(session_name: str) -> str | None:
    """이름으로 지정된 tmux 세션의 pane. 큐 항목이 타깃을 명시할 때 쓴다."""
    return get_locked_session_pane(session_name)


def pane_exists(pane_id: str) -> bool:
    try:
        result = subprocess.run(
            TMUX_BASE + ["list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        return pane_id in result.stdout.split()
    except Exception:
        return False


def send_to_tmux(pane_id: str, text: str) -> bool:
    """tmux pane에 텍스트 전송 (끝에 Enter)."""
    try:
        subprocess.run(
            TMUX_BASE + ["send-keys", "-t", pane_id, "--", text, "Enter"],
            timeout=2, check=True,
        )
        return True
    except Exception as e:
        logger.error(f"tmux send-keys 실패: {e}")
        return False
