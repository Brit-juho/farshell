"""(하위 호환 껍데기) 구현은 server/tmux_target.py 로 옮겼다.

옮긴 이유: 이 패키지의 config.py 가 `from pynput import keyboard` 를 하기 때문에,
voice 경유로는 **음성 미설치 환경에서 import 자체가 실패한다.** 프롬프트 큐(P4)가
같은 타깃 규칙을 써야 해서 의존성 없는 위치로 끌어냈다.
기존 import 경로(`from .tmux_target import ...`)는 그대로 동작한다.
"""

import sys
from pathlib import Path

# server/ 를 import 경로에 — voice_daemon.py 로 직접 실행되는 경우를 대비.
_SERVER_DIR = Path(__file__).resolve().parent.parent
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from tmux_target import (  # noqa: E402,F401
    TMUX_BASE,
    get_active_tmux_pane,
    get_any_tmux_pane,
    get_locked_session_pane,
    pane_exists,
    read_voice_target_lock,
    resolve_voice_target_pane,
    send_to_tmux,
    session_pane,
)

__all__ = [
    "TMUX_BASE",
    "get_active_tmux_pane",
    "get_any_tmux_pane",
    "get_locked_session_pane",
    "pane_exists",
    "read_voice_target_lock",
    "resolve_voice_target_pane",
    "send_to_tmux",
    "session_pane",
]
