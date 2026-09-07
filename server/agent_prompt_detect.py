"""A3 — PTY 출력에서 "승인/입력 대기(waiting)"를 감지한다.

구조는 `auto_responder`를 그대로 빌려온다(세션별 슬라이딩 윈도우 + cooldown).
다른 점은 **히트했을 때 하는 일**이다: auto_responder는 답을 대신 써 넣어
waiting을 *없애고*, 이쪽은 아무것도 쓰지 않고 상태만 `waiting`으로 *알린다*.

    auto_responder : VT_AUTO_TRUST=1 옵트인, 기본 OFF, 프롬프트를 삼킨다
    이 모듈        : 항상 ON, 아무것도 안 쓴다

**상호배제**: 둘은 `routes/pty.py`에서 같은 바이트 스트림을 나란히 먹는 형제라
순서 보장이 없다. `VT_AUTO_TRUST=1`이면 auto_responder가 프롬프트를 먼저
삼켜버려 waiting이 뜰 새가 없는데, 그 짧은 창에 waiting을 띄우면 화면이
깜빡이고 (더 나쁘게) 큐가 그 pane을 막힌 것으로 오해한다. 그래서 auto_responder가
응답한 세션은 **같은 cooldown 창 동안 waiting 판정을 억제**한다.

**해제(exit) 판정** — 이게 A3에서 새로 만드는 핵심이다. 넷 중 아무거나:
    1. exit 패턴이 윈도우에 등장         (`feed`)
    2. 그 pane에 사용자 입력이 들어감     (`on_user_input` — routes/pty.py의 WS 입력 경로)
    3. `pre`/`stop` 훅 수신               (agent_status가 상태 전이로 이미 처리)
    4. TTL 120초                          (agent_status.sweep의 waiting TTL)

오탐이 화면에 영구히 남지 않게 하는 것이 4번의 존재 이유다. 감지가 틀렸어도
2분이면 스스로 풀린다.

패턴은 `server/detect/<agent>.toml`로 외부화한다 — 에이전트 CLI가 업데이트되면
문구가 바뀌므로, 파이썬을 고치고 서버를 재시작하는 대신 TOML을 고친다.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 이하
    tomllib = None  # type: ignore

logger = logging.getLogger(__name__)

DETECT_DIR = Path(os.environ.get("VT_DETECT_DIR", Path(__file__).parent / "detect"))

# auto_responder와 같은 크기 — 큰 출력이 여러 청크로 쪼개져도 마지막 N바이트에서 찾는다.
WINDOW_SIZE = 2048
# 같은 세션에서 상태를 연속으로 뒤집지 않기 위한 최소 간격.
FLAP_GUARD_SEC = 1.0

_patterns: Optional[dict] = None


def load_patterns(force: bool = False) -> dict:
    """`detect/*.toml`을 읽어 {agent: {"enter": [...], "exit": [...]}}.

    파싱 실패한 파일은 **건너뛴다** — 잘못된 패턴 하나가 서버를 죽이면 안 된다.
    결과는 캐시한다(매 출력 청크마다 디스크를 읽을 수는 없다). 파일을 고친 뒤엔
    force=True 또는 서버 재시작.
    """
    global _patterns
    if _patterns is not None and not force:
        return _patterns
    out: dict[str, dict[str, list[bytes]]] = {}
    if tomllib is None:
        logger.warning("tomllib 없음(Python 3.11+ 필요) — waiting 감지 비활성")
        _patterns = out
        return out
    if DETECT_DIR.is_dir():
        for path in sorted(DETECT_DIR.glob("*.toml")):
            try:
                data = tomllib.loads(path.read_text())
            except Exception as e:
                logger.warning(f"detect 패턴 파싱 실패 — 건너뜀: {path.name}: {e}")
                continue
            name = str(data.get("name") or path.stem)
            enter = [str(p).encode() for p in (data.get("enter") or []) if str(p)]
            exit_ = [str(p).encode() for p in (data.get("exit") or []) if str(p)]
            if enter or exit_:
                out[name] = {"enter": enter, "exit": exit_}
    _patterns = out
    return out


class PromptDetector:
    """세션별 출력 윈도우 + enter/exit 패턴 매처.

    on_change(session_id, waiting: bool)로 상태 변화만 알린다 — **변화가 있을
    때만** 부른다(매 청크마다 부르면 WS 브로드캐스트가 폭주한다).
    """

    def __init__(self, on_change: Callable[[str, bool], None]):
        self._on_change = on_change
        self._windows: dict[str, deque[bytes]] = {}
        self._waiting: dict[str, bool] = {}
        self._changed_at: dict[str, float] = {}

    # ── 입력 ─────────────────────────────────────────────────────────────
    def feed(self, session_id: str, data: bytes) -> None:
        pats = load_patterns()
        if not pats:
            return
        win = self._windows.get(session_id)
        if win is None:
            win = deque(maxlen=4)
            self._windows[session_id] = win
        win.append(data)

        joined = b"".join(win)
        if len(joined) > WINDOW_SIZE:
            joined = joined[-WINDOW_SIZE:]

        hit_exit = any(p in joined for spec in pats.values() for p in spec["exit"])
        hit_enter = any(p in joined for spec in pats.values() for p in spec["enter"])

        # exit이 우선이다 — 프롬프트가 떴다가 방금 사라진 윈도우에는 둘 다 들어
        # 있을 수 있는데, 그 경우 현재 화면 상태는 "사라진 뒤"다.
        if hit_exit:
            self._set(session_id, False)
        elif hit_enter:
            if _auto_trust_suppressed(session_id):
                logger.debug(f"[waiting] sid={session_id} auto_responder cooldown — 억제")
                return
            self._set(session_id, True)

    def on_user_input(self, session_id: str) -> None:
        """그 pane에 사용자가 뭔가 입력했다 → 승인 대기는 끝난 것으로 본다.

        해제 판정 4종 중 가장 확실한 신호다(사람이 실제로 답했다). 윈도우도
        비워서 이미 지나간 프롬프트 문자열이 다시 히트하지 않게 한다.
        """
        if self._waiting.get(session_id):
            self._windows.pop(session_id, None)
            self._set(session_id, False)

    # ── 상태 ─────────────────────────────────────────────────────────────
    def is_waiting(self, session_id: str) -> bool:
        return bool(self._waiting.get(session_id))

    def _set(self, session_id: str, waiting: bool) -> None:
        if self._waiting.get(session_id, False) == waiting:
            return
        now = time.monotonic()
        if now - self._changed_at.get(session_id, 0.0) < FLAP_GUARD_SEC:
            return
        self._waiting[session_id] = waiting
        self._changed_at[session_id] = now
        try:
            self._on_change(session_id, waiting)
        except Exception as e:  # 감지가 서버를 죽이지 않는다
            logger.warning(f"[waiting] 상태 통지 실패: {e}")

    def remove(self, session_id: str) -> None:
        self._windows.pop(session_id, None)
        self._waiting.pop(session_id, None)
        self._changed_at.pop(session_id, None)


def _auto_trust_suppressed(session_id: str) -> bool:
    """auto_responder가 방금 이 세션에 답을 써 넣었으면 waiting을 억제한다."""
    import auto_responder

    if not auto_responder.is_enabled():
        return False
    responder = auto_responder._global_responder
    if responder is None:
        return False
    last = responder._last_response_time.get(session_id)
    if last is None:
        return False
    return (time.monotonic() - last) < auto_responder.COOLDOWN_SECONDS


_global_detector: Optional[PromptDetector] = None


def get_global_detector(on_change: Callable[[str, bool], None]) -> PromptDetector:
    global _global_detector
    if _global_detector is None:
        _global_detector = PromptDetector(on_change)
    return _global_detector
