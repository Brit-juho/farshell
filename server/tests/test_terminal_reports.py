"""터미널 응답(프로토콜 리포트)과 사람 입력의 구분 — 폰 실측(2026-10-06)에서 나온 경계 사례."""

import pytest

from terminal_reports import is_terminal_report as r


@pytest.mark.parametrize("data", [
    "\x1b[<0;30;17M",       # 터치/클릭 누름 (SGR 마우스)
    "\x1b[<0;30;17m",       # 뗌
    "\x1b[<64;5;5M",        # 휠
    "\x1b[<0;30;17M\x1b[<0;30;17m",
])
def test_sgr_mouse_is_not_human_input(data):
    # 폰에서 키보드를 올리려고 터미널을 한 번 탭하기만 해도 waiting이 풀리면 안 된다.
    assert r(data) and r(data.encode())


@pytest.mark.parametrize("data", [
    "y",
    "\x1b[<0;30;17My",       # 마우스 뒤에 사람 키가 섞이면 사람 입력
    "\x1b[<0;30M",           # 문법이 틀리면 사람 입력으로 둔다(모호하면 사람)
    "\x1b[1;5R",             # Ctrl+F3
])
def test_mixed_or_malformed_stays_human(data):
    assert not r(data)
