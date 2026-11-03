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
