"""pane이 **지금 입력을 어떤 형태로 받는가**를 판정한다 (N25, 2.1.5 1/n).

지금까지 이 판단은 브라우저가 했다 — xterm.js가 자기가 본 DECSET 2004로
붙여넣기 마커를 붙일지 정했다. 브라우저는 맥에서 무엇이 돌고 있는지 모르므로
그건 추측이다. 서버는 PTY master fd를 쥐고 있어서 **직접 조회할 수 있다.**

## 실측 근거 (2026-09-14, 이 저장소 tests/test_input_mode.py가 회귀로 고정)

1. master fd의 termios는 슬레이브의 ICANON을 **실시간으로** 비춘다:
   `sh` 기본 False → `cat` 실행 중 True → `cat` 종료 후 다시 False.
2. macOS의 정규 모드 한 줄 한계는 **1024바이트(개행 포함)**이고,
   `os.pathconf(fd, "PC_MAX_CANON")`이 정확히 그 값을 돌려준다. 넘기면 잘리는
   것이 아니라 **그 줄 전체가 사라진다**(1023자+개행은 통과, 1024자+개행은 0바이트
   도착). `os.write`는 전량 성공을 보고하므로 서버 쪽에서는 성공으로 보인다.
3. **tmux 안에서는 위 두 신호가 모두 죽는다.** tmux는 attach하는 순간
   `ESC[?2004h`를 보내고 계속 켜둔 채 안쪽 pane의 실제 모드는 자기가 따로
   들고 판단하며, 바깥 fd의 ICANON은 pane에서 `cat`이 떠도 False 그대로다.
   → tmux 세션은 판정하지 말고 tmux에게 위임한다(`delegate="tmux"`).
"""

from __future__ import annotations

import logging
import os
import re
import termios
from typing import Optional

logger = logging.getLogger(__name__)

# DECSET/DECRST 2004 — 애플리케이션이 "붙여넣기를 마커로 감싸서 달라"고 켜는 모드.
# 출력 스트림에 그대로 실려 오므로 서버가 지나가며 주워 담을 수 있다.
_BRACKET_MODE_RE = re.compile(rb"\x1b\[\?([0-9;]*)2004([hl])")

# 정규 모드 한 줄 한계의 폴백. pathconf가 답을 못 줄 때만 쓴다 —
# macOS/BSD는 1024(실측), Linux n_tty는 4096(N_TTY_BUF_SIZE)이지만 **미측정**이라
# 작은 쪽을 기본값으로 둔다(거절이 조용한 소실보다 낫다).
FALLBACK_MAX_CANON = 1024


def scan_bracketed_mode(data: bytes, current: Optional[bool]) -> Optional[bool]:
    """출력 조각에서 DECSET/DECRST 2004를 주워 현재 모드를 갱신한다. 순수 함수.

    한 조각에 여러 번 들어 있으면 **마지막 것**이 이긴다(그게 지금 상태다).
    아무것도 없으면 이전 값을 그대로 돌려준다 — 모르는 것과 꺼진 것은 다르다
    (아직 한 번도 못 본 상태는 None이고, 호출자는 그때 마커를 붙이지 않는다).
    """
    last = None
    for m in _BRACKET_MODE_RE.finditer(data):
        last = m.group(2) == b"h"
    return current if last is None else last


def is_canonical(fd: int) -> Optional[bool]:
    """이 PTY가 지금 정규(줄 단위) 모드인가. 조회 실패면 None(모른다)."""
    try:
        attrs = termios.tcgetattr(fd)
    except (termios.error, OSError, ValueError):
        return None
    return bool(attrs[3] & termios.ICANON)


def max_canon(fd: int) -> int:
    """정규 모드에서 한 줄에 넣을 수 있는 최대 바이트(개행 포함)."""
    try:
        v = os.pathconf(fd, "PC_MAX_CANON")
    except (OSError, ValueError, AttributeError, KeyError):
        return FALLBACK_MAX_CANON
    # 0이나 음수(= "제한 없음"을 뜻하는 구현도 있다)는 신뢰하지 않는다.
    return int(v) if isinstance(v, int) and v > 0 else FALLBACK_MAX_CANON


def pane_input_mode(fd: int, *, tmux: bool = False,
                    bracketed: Optional[bool] = None) -> dict:
    """이 세션에 붙여넣기를 어떻게 보내야 하는지.

    - `delegate="tmux"`: 판정하지 말고 tmux에게 넘겨라(위 3번). 이때 `icanon`·
      `bracketed`는 **바깥 tmux의 값**이라 의미가 없으므로 None으로 비운다.
    - `icanon=True`: 줄 단위로 보내야 하고 `max_line`이 걸린다.
    - `bracketed=True`: 앱이 마커를 원한다. None은 "아직 모른다"이고, 모를 때는
      붙이지 않는다 — 안 켠 앱에 마커를 보내면 `[200~` 같은 쓰레기가 찍힌다.
    """
    if tmux:
        return {"delegate": "tmux", "icanon": None, "bracketed": None, "max_line": None}
    canonical = is_canonical(fd)
    return {
        "delegate": None,
        "icanon": canonical,
        "bracketed": bracketed,
        "max_line": max_canon(fd) if canonical else None,
    }
