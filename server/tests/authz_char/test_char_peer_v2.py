"""peer 프로토콜 v2(`/api/peer/v2/*`) — **플래그가 꺼진 서버**의 동작을 고정한다: 모든 호출자·모든 서버 모드에서 «없는 라우트»와 같은 404
(`{"detail": "Not Found"}`). 인증을 보기 전에 답하므로 서명이 맞아도, 틀려도, 없어도 같다 — 켜기 전에는 새 표면이 보이지 않는다.
켠 뒤의 판정은 `test_char_peer_v2_enforced.py`.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for

pytestmark = pytest.mark.skipif("peer" in __import__("os").environ.get("VT_AUTHZ_ENFORCE", "").replace(" ", "").split(","),
                                reason="꺼진 서버의 동작을 고정하는 파일 — 켠 뒤는 test_char_peer_v2_enforced.py")


@pytest.mark.parametrize("case", cases_for("peer_v2"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))
