"""rate limit 표(10-policy.md A12)와 판정 보조.

값은 메모리 보관, 재시작 시 초기화(기존과 같다). 1단계의 엔진은 **카운터를 직접 올리지 않는다** —
shadow 판정이 실제 호출 횟수를 두 번 세면 안 되기 때문이다. 판정 지점이 이미 센 값을
`ctx["counts"]`로 넘기면 `exceeded()`로 비교만 한다. 3단계에서 `Window`가 기존 rate map
(`agent_share._rate`, `agent_msg._rate`)을 대체한다.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

# (한도, 창 초) — 키는 판정 scope 또는 묶음 이름.
LIMITS: dict[str, tuple[int, float]] = {
    "session.read": (30, 60.0),          # (주체, 세션)당
    "session.input.line": (10, 60.0),
    "msg.send": (20, 60.0),              # 보내는 호스트당
    "deleg.spawn": (5, 60.0),            # 전역
    "authz.request": (6, 60.0),          # 주체당
    "pairing.code": (10, 3600.0),
    "pane_token": (10, 60.0),
    "browser.tab": (60, 60.0),
    "browser.js": (10, 60.0),
    "browser.tab.navigate": (30, 60.0),
    "lease": (30, 60.0),
    "peer": (600, 60.0),
}
MSG_MAX_HOP = 10
DELEG_MAX_OPEN = 3          # delegation.MAX_OPEN_CHILDREN 과 같아야 한다(B1 "상수 유지")
DELEG_SPAWN_PER_MIN = 5     # delegation.SPAWN_PER_MIN
PAIR_CODE_ATTEMPTS = 5
PENDING_PER_PRINCIPAL = 20  # R4.7
PENDING_TOTAL = 200
PENDING_TTL = 24 * 3600
DENY_COOLDOWN = 300         # R4.6


def exceeded(name: str, count: int, limit: Optional[int] = None) -> bool:
    """이미 센 횟수가 한도에 닿았는가(닿으면 다음 호출은 거부)."""
    lim = limit if limit is not None else LIMITS[name][0]
    return count >= lim


class Window:
    """슬라이딩 창 카운터(스레드 안전). 3단계 강제 모드에서 쓴다 — 1단계 엔진은 쓰지 않는다."""

    def __init__(self, limit: int, period: float):
        self.limit, self.period = limit, period
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.period:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True
