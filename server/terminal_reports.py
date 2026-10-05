"""터미널이 스스로 보내는 응답(프로토콜 리포트)을 사람의 입력과 가른다.

tmux는 attach(새로고침·두 번째 화면 포함)마다 클라이언트 터미널에 질의를 보낸다 —
`\\x1b[c`(DA1) `\\x1b[>c`(DA2) `\\x1b[>q`(XTVERSION) `\\x1b]10;?` `\\x1b]11;?`(색) `\\x1b[?996n`.
xterm.js는 그 답을 `onData`로 내보내고, 프런트는 `onData`를 그대로 WS 바이너리로 보낸다.
서버 입장에서는 사람이 친 키와 구분이 안 돼, 승인 대기 중인 pane에 폰을 붙이는 순간
「사용자가 답했다」로 읽혀 waiting이 풀렸다(2026-10-06 실측: waiting -> working).
사람이 치는 키가 아닌 것만 정확한 문법으로 가려낸다 — 모호하면 사람의 입력으로 둔다.
"""

from __future__ import annotations

import re

_ESC = r"\x1b"
_REPORT_RE = re.compile(
    "|".join([
        _ESC + r"\[\?[\d;]*c",                    # DA1 응답
        _ESC + r"\[>[\d;]*c",                     # DA2 응답
        _ESC + r"\[\d+;\d+R",                     # CPR (아래에서 F3+수식키와 구분)
        _ESC + r"\[\?\d+;\d+(?:;\d+)?R",          # DECXCPR
        _ESC + r"\[0n",                           # DSR 정상
        _ESC + r"\[\?\d+(?:;\d+)*n",              # DSR 사설(색 스킴 등)
        _ESC + r"\[[IO]",                         # 포커스 in/out
        # SGR 마우스(탭·클릭·휠) — 폰에서 키보드를 올리려고 터미널을 한 번 탭하기만 해도 이게
        # 올라가 waiting이 풀렸다(2026-10-06 폰 실측). 답이 아니라 포인터 이벤트다.
        _ESC + r"\[<\d+;\d+;\d+[Mm]",
        _ESC + r"\[\??[\d;]+\$y",                 # DECRPM
        _ESC + r"\[\d+(?:;\d+)+t",                # 창 크기 보고
        _ESC + r"\[\?\d+u",                       # kitty 키보드 플래그 응답
        _ESC + r"P[^\x1b]*" + _ESC + r"\\",       # DCS (XTVERSION, XTGETTCAP)
        _ESC + r"\][^\x07\x1b]*(?:\x07|" + _ESC + r"\\)",  # OSC 응답
        _ESC + r"_[^\x1b]*" + _ESC + r"\\",       # APC (kitty 그래픽 응답)
    ])
)
_MOD_F3_RE = re.compile(r"\x1b\[1;[2-8]R")  # Shift/Ctrl+F3 — CPR와 문법이 같은 사람 키


def _drop(m: re.Match) -> str:
    return m.group(0) if _MOD_F3_RE.fullmatch(m.group(0)) else ""


def is_terminal_report(data: bytes | str | None) -> bool:
    """`data`가 터미널 응답으로만 이뤄졌는가. 사람 키가 하나라도 섞이면 False."""
    if not data:
        return False
    text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else data
    return _REPORT_RE.sub(_drop, text) == "" and _REPORT_RE.search(text) is not None
