"""에이전트 간 메시지(`routes/agent_msg.py`) — 지금 판정 고정.

보내는 쪽 API 에는 서버 평소 인증(기계 토큰) 외의 인가가 없고, 보낸 세션은 본문의 pane/tmux 로
정해진다(H2). 사례의 `local_spoofed_sender`는 남의 칸(other, %3)을 주장한다.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for


@pytest.mark.parametrize("case", cases_for("agent_msg"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))
