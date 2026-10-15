"""호스트 목록·원격 조작 중계(`routes/hosts.py`) — 지금 판정 고정.

이 라우트들은 서버 평소 인증만 지나면 상대 맥의 control 기능(세션 생성·종료·이름 변경·화면 끊기)을
대신 부른다. 사람·기계 구분이 없다.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for


@pytest.fixture(autouse=True)
def _peer_flag_off(monkeypatch):
    """이 파일은 3d(H13) 이전의 옛 동작을 고정한다 — 환경에 `peer` 가 켜져 있어도 끄고 돈다(켠 판은 `test_char_peer_enforced.py`)."""
    from authz import flags
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", ",".join(sorted(flags.tokens() - {"peer"})))


@pytest.mark.parametrize("case", cases_for("hosts"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))
