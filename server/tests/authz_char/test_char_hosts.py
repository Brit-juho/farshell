"""호스트 목록·원격 조작 중계(`routes/hosts.py`) — 지금 판정 고정.

이 라우트들은 서버 평소 인증만 지나면 상대 맥의 control 기능(세션 생성·종료·이름 변경·화면 끊기)을
대신 부른다. 사람·기계 구분이 없다.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for


@pytest.mark.parametrize("case", cases_for("hosts"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))
