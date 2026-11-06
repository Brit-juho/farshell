"""셸에 입력할 경로 하나를 인용한다 — frontend/js/lib/shell-quote.js와 같은 규칙.

파일 이름은 보내는 쪽이 정한다(피어 Mac의 헤더, 받은 파일). `$(…)`·백틱·`;`·`'`가 든 이름이 경로에 그대로
섞여 명령줄에 입력되면 사용자가 Enter를 누르는 순간 실행된다. 문자·숫자·결합 기호와 `_@%+=:,./-`만으로 된 경로는
그대로 두고(일반 업로드·보통의 설치 경로는 달라지지 않는다), 아니면 홑따옴표로 감싼다(`shlex.quote`와 달리 한글 이름은
인용하지 않는다). 의존성이 없는 작은 모듈이라 훅 등록(claude_hooks·codex_hooks)도 쓴다.
"""
from __future__ import annotations

import unicodedata

_SAFE_PATH_PUNCT = frozenset("_@%+=:,./-")


def shell_quote_path(path: str) -> str:
    """셸에 입력할 경로 하나를 인용한다 — frontend/js/lib/shell-quote.js와 같은 규칙.

    파일 이름은 보내는 쪽이 정한다(피어 Mac의 헤더, 받은 파일). `$(…)`·백틱·`;`·`'`가 든 이름이 경로에 그대로
    섞여 명령줄에 입력되면 사용자가 Enter를 누르는 순간 실행된다. 문자·숫자·결합 기호와 `_@%+=:,./-`만으로 된 경로는
    그대로 두고(일반 업로드는 달라지지 않는다), 아니면 홑따옴표로 감싼다(`shlex.quote`와 달리 한글 이름은 인용하지 않는다).
    """
    if path == "":
        return "''"
    # 첫 글자: zsh는 `=cmd`(명령 경로 치환)·`%1`(작업)·`~`를, 모든 셸은 `-x`를 단어 맨 앞에서만 특별 취급한다.
    if path[0] not in "=%-+~" and all(
            c in _SAFE_PATH_PUNCT or c.isalnum() or unicodedata.category(c).startswith("M") for c in path):
        return path
    return "'" + path.replace("'", "'\"'\"'") + "'"


def has_control_chars(text: str) -> bool:
    r"""C0 제어문자(0x00-0x1F)나 DEL(0x7F)이 들어 있는가 — 터미널에 **경로로 입력하면 안 되는** 값인지 가른다.

    인용(`shell_quote_path`)은 셸이 읽을 때만 효과가 있다. 입력은 셸에 닿기 전에 tty 줄 규칙·readline·ZLE를 먼저
    지나고, 거기서 `^U`(줄 지우기)·`^W`·`\r`(Enter) 같은 바이트는 따옴표와 상관없이 즉시 처리된다:
    `x\x15touch /tmp/pwn\r` 이라는 이름은 `^U`가 여는 따옴표까지 지우고 `\r`이 사용자가 Enter를 누르기 전에
    `touch`를 실행한다. 그런 경로는 고쳐 넣지 않고 아예 넣지 않는다(frontend/js/lib/shell-quote.js `hasControlChars`와 같은 규칙).
    """
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)
