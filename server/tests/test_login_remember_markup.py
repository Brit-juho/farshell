"""로그인 화면의 "기기 기억" 선택지와 서버 허용값(REMEMBER_TTLS)이 같은지 대조한다.

어긋나면 화면에서 고른 값이 400 invalid_remember로 거절된다 — 로그인 자체가
막히는 종류라, 한쪽만 바꾸는 실수를 여기서 잡는다.
"""

import re
from pathlib import Path

import auth

INDEX_HTML = Path(__file__).resolve().parents[2] / "frontend" / "index.html"


def _radio_values():
    html = INDEX_HTML.read_text(encoding="utf-8")
    block = re.search(r'<fieldset id="login-remember".*?</fieldset>', html, re.S)
    assert block, "index.html에 #login-remember가 없다"
    return re.findall(r'name="vt-remember" value="([^"]+)"', block.group(0)), block.group(0)


def test_remember_radios_match_server_allowlist():
    values, _ = _radio_values()
    assert sorted(values) == sorted(auth.REMEMBER_TTLS)


def test_default_radio_is_server_default():
    _, block = _radio_values()
    checked = re.findall(r'value="([^"]+)" checked', block)
    assert checked == [auth.REMEMBER_DEFAULT]
