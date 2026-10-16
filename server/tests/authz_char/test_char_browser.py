"""브라우저 broker API(`routes/browser.py`) — 호출자 × 서버 모드 matrix. 기대값은 matrix.json(gen_matrix.py 가 기록).

기록하는 것은 마스터 스위치(`VT_AUTHZ_ENFORCE` 의 `browser`)가 **꺼진** 서버의 동작이다: 도달한 호출자는 모두 «없는 라우트»와 같은 404
(`{"detail": "Not Found"}`), 닿지 못하는 호출자(인증 없음 등)는 평소의 인증 응답. 켠 뒤의 규칙은 test_browser_*.py.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for


@pytest.fixture(autouse=True)
def _browser_flag_off(monkeypatch):
    from authz import flags
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", ",".join(sorted(flags.tokens() - {"browser"})))


@pytest.mark.parametrize("case", cases_for("browser"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))
