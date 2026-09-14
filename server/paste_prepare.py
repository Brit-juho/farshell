"""N24(2.1.5 2/n) — 붙여넣기 페이로드 준비. 순수 함수(PTY 없이 테스트 가능).

지금까지 "마커를 붙일지·개행을 어떻게 바꿀지"는 브라우저(xterm.js)가 혼자
정했다. 그 판단을 서버로 옮기는 첫 단계 — `input_mode.py`(1/n)가 "이 pane이
지금 마커를 원하는가"를 알려주면, 여기서 그 결정에 맞게 텍스트를 실제
페이로드로 만든다.

2.1.5 결정(사용자 확정, 2026-09-14):
1. ESC 무력화 — bracketed일 때만 ESC를 ␛(U+241B)로 치환한다
   (xterm.js `bracketTextForPaste`·tmux 3.7 `vis(3)`와 같은 이유: 붙여넣기
   내용에 `ESC[201~`처럼 보이는 바이트가 있으면 마커를 조기 종료시킬 수
   있다). N27("붙여넣기 내용은 한 바이트도 안 바뀐다")은 **키 입력 스트림**
   에 대한 규칙이지 이 준비 단계에는 적용되지 않는다 — 여기서 만든 결과가
   바로 그 "붙여넣기 내용"이 된다.
2. 위험 제어문자 제거 — tab(9)·LF(10)·FF(12)·CR(13)·^V(22)만 남긴다
   (iTerm2 `iTermPasteHelper.m`의 `kPasteFlagsRemovingUnsafeControlCodes`
   규칙 그대로).
3. 개행은 전부 CR 하나로 합친다(`\r\n`·`\n`·`\r` 전부 `\r`) — xterm.js
   `prepareTextForTerminal`과 같다. bracket 여부와 무관하게 항상 적용한다.
"""

from __future__ import annotations

import re

# 위험 제어문자 화이트리스트 — iTerm2 kPasteFlagsRemovingUnsafeControlCodes.
_ALLOWED_CONTROL = frozenset({0x09, 0x0A, 0x0C, 0x0D, 0x16})  # tab LF FF CR ^V

_NEWLINE_RE = re.compile(r"\r\n|\r|\n")

PASTE_START = b"\x1b[200~"
PASTE_END = b"\x1b[201~"


def strip_unsafe_control_chars(text: str) -> str:
    """제어문자 중 화이트리스트 밖은 제거한다. 인쇄 가능 문자(유니코드
    포함)는 전부 통과한다 — 제어문자 판정만 코드포인트로 한다."""
    return "".join(
        ch for ch in text
        if not (ord(ch) < 0x20 or ord(ch) == 0x7F) or ord(ch) in _ALLOWED_CONTROL
    )


def normalize_newlines(text: str) -> str:
    """`\\r\\n`·`\\r`·`\\n` 전부 `\\r` 하나로. xterm.js `prepareTextForTerminal`
    (`text.replace(/\\r?\\n/g, '\\r')`)과 동등하되 단독 `\\r`도 함께 정규화한다
    — 이미 CR인 것과 새로 변환된 것 사이에 차이를 두지 않는다."""
    return _NEWLINE_RE.sub("\r", text)


def neutralize_escape(text: str) -> str:
    """ESC(0x1B) → ␛(U+241B). bracket 안에서만 부른다."""
    return text.replace("\x1b", "␛")


class LineTooLong(Exception):
    """N26(2.1.5 4/n) — 정규(줄 단위) 모드에서 어느 한 줄이 그 pane의 한 줄
    한계를 넘었다. macOS 실측(input_mode.py 문서 참고): 한계를 넘긴 줄은
    잘려서 일부만 들어가는 게 아니라 **통째로 사라진다**(1023자+개행은
    도착, 1024자+개행은 0바이트). 그래서 보내기 전에 거절한다 — 조용한
    실패보다 명시적 거절이 낫다.
    """

    def __init__(self, max_line: int, line_index: int, line_length: int):
        self.max_line = max_line
        self.line_index = line_index
        self.line_length = line_length
        super().__init__(
            f"{line_index + 1}번째 줄이 {line_length}바이트 — "
            f"이 프로그램은 한 줄 {max_line}바이트까지만 받습니다"
        )


def check_canonical_line_limits(text: str, *, max_line: int) -> None:
    """정규 모드 한 줄 한계를 넘는 줄이 있으면 `LineTooLong`을 던진다.
    아무 문제 없으면 조용히 반환한다 — 실제 전송은 `prepare_paste_payload`가
    그대로 한다(따로 줄마다 나눠 보내지 않는다: 커널 tty 줄 규율은 우리
    `write()` 호출 경계가 아니라 스트림 안의 개행 위치로 줄을 가르므로,
    한 번에 보내는 것과 줄마다 나눠 보내는 것이 동작상 같다).
    """
    body = strip_unsafe_control_chars(text)
    body = normalize_newlines(body)
    lines = body.split("\r")
    for i, line in enumerate(lines):
        is_last = i == len(lines) - 1
        raw = line if is_last else line + "\r"
        length = len(raw.encode("utf-8", "surrogatepass"))
        if length > max_line:
            raise LineTooLong(max_line, i, length)


def prepare_paste_payload(text: str, *, bracket: bool) -> bytes:
    """붙여넣기 원문 → PTY에 쓸 바이트열.

    `bracket=True`면 ESC를 무력화하고 `ESC[200~`…`ESC[201~`로 감싼다 —
    이 조각은 `pty_manager.split_paste_segments`(N27)가 마커로 인식해
    키 입력용 필터(자동응답 제거·부팅 grace)를 건너뛴다.
    `bracket=False`면 감싸지 않는다 — 마커를 이해 못 하는 앱에게 마커
    바이트 자체가 그대로 찍히는 것을 막는다(N24의 원래 동기: 마커를
    붙일지 말지를 더 이상 브라우저가 추측하지 않는다).
    """
    if not text:
        return b""
    body = text
    if bracket:
        # ESC를 인쇄 가능한 ␛로 먼저 바꿔둔다 — 그래야 바로 다음 단계(제어문자
        # 제거, ESC는 화이트리스트 밖이라 그냥 두면 지워진다)에서 살아남는다.
        body = neutralize_escape(body)
    body = strip_unsafe_control_chars(body)
    body = normalize_newlines(body)
    if bracket:
        return PASTE_START + body.encode("utf-8", "surrogatepass") + PASTE_END
    return body.encode("utf-8", "surrogatepass")
