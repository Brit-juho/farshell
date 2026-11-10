"""pane 입력 직렬화 + 사람 입력 시각 .

**락.** 큐·음성·에이전트 메시지·세션 공유·원격 입력이 같은 pane에 동시에 쓰면
텍스트가 서로 끼어들거나, 한쪽의 Enter가 다른 쪽 텍스트를 제출해버린다. 자동 입력
경로는 전부 `lock(pane_id)` 안에서 "텍스트 → Enter"를 한 덩어리로 보낸다.
호출부가 to_thread 워커와 CLI 프로세스 양쪽이라 asyncio.Lock이 아니라
threading.Lock이다. 프로세스 밖(서버가 없을 때의 CLI)까지는 덮지 않는다 — 그때는
큐 파일 flock과 sending 표시가 같은 항목의 중복 투입을 막는다.

**사람 입력.** 브라우저에서 사람이 친 키(터미널 자동 응답 바이트 제외)의 시각을
tmux 세션 이름별로 남긴다(`deps.on_terminal_input`). 자동 투입은 최근에 사람이
입력했으면 보내지 않는다 — 사람이 쓰던 문장 뒤에 큐 텍스트가 붙어 함께 제출되는
사고를 막는다. 예전엔 stop 훅 뒤 무조건 3초를 기다리는 것으로 "타이핑을 시작했을
수 있다"에 대비했는데, 실제 입력을 보지 않아 3초 뒤에 치기 시작하면 소용이 없었다.

한계: 입력창에 이미 남아 있는 미완성 글(사람이 쓰다 말고 손을 뗀 것)은 모른다 —
화면을 읽어 판정하는 방식은 에이전트마다 입력줄 모양이 달라 오탐이 많아 뺐다.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from resilience.locks import TimedLock

_locks: dict[str, TimedLock] = {}
_locks_guard = threading.Lock()
# 쥔 쪽은 tmux 명령 몇 개(각 2초 기한)와 짧은 대기뿐이다. 기다리는 쪽은 이만큼만 기다리고 LockTimeout —
# 호출부(큐·음성·위임·공유)는 실패로 처리하고 다음 기회에 다시 보낸다.
LOCK_WAIT = 30.0

_human_input_at: dict[str, float] = {}


def _lock_for(pane_id: str) -> TimedLock:
    with _locks_guard:
        lk = _locks.get(pane_id)
        if lk is None:
            lk = _locks[pane_id] = TimedLock(f"pane_input:{pane_id}")
        return lk


@contextmanager
def lock(pane_id: str, timeout: float | None = None):
    """한 pane에 대한 자동 입력을 직렬화한다. 같은 스레드에서 중첩하지 말 것.
    `timeout`(기본 `LOCK_WAIT`)을 넘기면 `resilience.locks.LockTimeout`."""
    lk = _lock_for(pane_id)
    with lk.hold(LOCK_WAIT if timeout is None else timeout, op="pane_input"):
        yield


def human_idle_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("VT_HUMAN_INPUT_IDLE_SEC", "3")))
    except ValueError:
        return 3.0


def note_human_input(tmux_name: str | None) -> None:
    if tmux_name:
        _human_input_at[tmux_name] = time.monotonic()


def last_human_input_age() -> float:
    """어느 세션이든 사람이 마지막으로 입력한 뒤 몇 초 지났는가. 한 번도 없으면 무한대(자동 업데이트의 「한가함」 판정)."""
    if not _human_input_at:
        return float("inf")
    return time.monotonic() - max(_human_input_at.values())


def human_typing(tmux_name: str | None, window: float | None = None) -> bool:
    """최근 `window`초 안에 사람이 이 세션에 입력했는가. 세션을 모르면 False."""
    if not tmux_name:
        return False
    at = _human_input_at.get(tmux_name)
    if at is None:
        return False
    w = human_idle_sec() if window is None else window
    return time.monotonic() - at < w


def _reset_for_tests() -> None:
    _human_input_at.clear()
    with _locks_guard:
        _locks.clear()
