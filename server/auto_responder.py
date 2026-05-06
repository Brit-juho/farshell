"""trust prompt 자동 응답 (Phase 8 G5, 옵트인).

VT_AUTO_TRUST=1 일 때만 활성. PTY broadcast를 받아 패턴 매칭 시 응답 키 전송.
claude-mux:2540-2580 패턴 차용 (단일 세션 capture-pane → broadcast 변형).
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 패턴: (감지 문자열, 응답 키)
TRUST_PATTERNS: list[tuple[bytes, bytes]] = [
    (b"Yes, I trust this folder", b"\r"),
]
BYPASS_WARNING_PATTERNS: list[tuple[bytes, bytes]] = [
    (b"Yes, I accept the risk", b"\x1b[B\r"),  # Down + Enter
]

ALL_PATTERNS = TRUST_PATTERNS + BYPASS_WARNING_PATTERNS

# 매칭 윈도우 크기 (큰 출력이 분할돼도 마지막 N바이트에서 검색)
WINDOW_SIZE = 2048
# cooldown: 같은 세션에서 응답 후 N초 내 재응답 차단
COOLDOWN_SECONDS = 5.0


def is_enabled() -> bool:
    return os.environ.get("VT_AUTO_TRUST", "").strip() in ("1", "true", "yes")


class AutoResponder:
    """세션별 출력 윈도우 + 패턴 매처."""

    def __init__(self, write_fn: Callable[[str, bytes], None]):
        self._write = write_fn
        self._windows: dict[str, deque[bytes]] = {}
        self._last_response_time: dict[str, float] = {}

    def feed(self, session_id: str, data: bytes) -> None:
        if not is_enabled():
            return
        win = self._windows.get(session_id)
        if win is None:
            win = deque(maxlen=4)  # 4 청크 = 보통 16KB+ 윈도우
            self._windows[session_id] = win
        win.append(data)

        # cooldown 검사
        now = time.monotonic()
        last = self._last_response_time.get(session_id, 0.0)
        if now - last < COOLDOWN_SECONDS:
            return

        joined = b"".join(win)
        # 윈도우가 너무 크면 마지막 N바이트만
        if len(joined) > WINDOW_SIZE:
            joined = joined[-WINDOW_SIZE:]

        for pattern, response in ALL_PATTERNS:
            if pattern in joined:
                logger.info(f"[auto-trust] sid={session_id} pattern={pattern!r} → respond")
                try:
                    self._write(session_id, response)
                    self._last_response_time[session_id] = now
                except Exception as e:
                    logger.warning(f"[auto-trust] write 실패: {e}")
                # 응답 후 윈도우 비움 (중복 매칭 방지)
                win.clear()
                break

    def remove(self, session_id: str) -> None:
        self._windows.pop(session_id, None)
        self._last_response_time.pop(session_id, None)


_global_responder: Optional[AutoResponder] = None


def get_global_responder(write_fn: Callable[[str, bytes], None]) -> AutoResponder:
    global _global_responder
    if _global_responder is None:
        _global_responder = AutoResponder(write_fn)
    return _global_responder
