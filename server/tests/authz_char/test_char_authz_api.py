"""승인함·권한·감사 API(`routes/authz.py`) — 호출자 × 서버 모드 matrix. 기대값은 matrix.json(gen_matrix.py 가 기록).

사람 전용 라우트는 기계 토큰·에이전트·peer 에게 403, 읽기 라우트는 사람(또는 소켓의 CLI)만, 요청 생성은 확인된 에이전트만 보류(202)다.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for


@pytest.mark.parametrize("case", cases_for("authz_api"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))
