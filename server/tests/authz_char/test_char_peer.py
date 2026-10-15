"""peer capability(`routes/peer.py`) — 모든 `@capability` 라우트 × 등급, 페어링, peer WS.

`/api/peer/*`는 TokenAuthMiddleware 예외라 서버 모드와 무관하게 서명만 본다 — 모드 축을 그대로
돌려 그 성질도 고정한다. view 는 세션 목록·검색(cwd·출력)을 본다.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for


@pytest.mark.parametrize("case", cases_for("peer"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))
