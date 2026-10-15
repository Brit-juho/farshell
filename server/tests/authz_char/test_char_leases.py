"""폴더 lease API(`routes/leases.py`) — 호출자 × 서버 모드 matrix. 기대값은 matrix.json(gen_matrix.py 가 기록)."""

from __future__ import annotations

import pytest

from authz_cases import cases_for


@pytest.mark.parametrize("case", cases_for("leases"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))
