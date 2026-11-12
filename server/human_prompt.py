"""사람이 직접 친 지시인지 가리는 한 곳(세션 로직 점검 단계 2·4).

대화 기록(session_history/archive.py)의 「마지막 지시」와 실행 중 세션의 `last_prompt`(agent_status)가 같은 규칙을
쓴다. 다른 에이전트·FarShell이 넣은 입력(위임 작업·위임 결과·에이전트 메시지, 시스템 꼬리표)은 사람의 지시가 아니다.
"""
from __future__ import annotations

from typing import Optional

LIMIT = 200
NOT_HUMAN_PREFIXES = ('<', 'Caveat:', '[FarShell 위임', '[위임 결과', '[FarShell 에이전트')
# 위임한 작업은 본문 뒤에 이 꼬리가 붙는다(delegation.py `_task_footer`) — 앞부분은 부모 에이전트가 쓴 글이다.
NOT_HUMAN_MARKERS = ('\n[FarShell 위임 ',)


def human_prompt(body) -> Optional[str]:
    """사람이 친 지시면 첫 줄 요약(200자), 아니면 None."""
    if not isinstance(body, str):
        return None
    text = body.strip()
    if not text or text.startswith(NOT_HUMAN_PREFIXES) or any(m in text for m in NOT_HUMAN_MARKERS):
        return None
    first = text.splitlines()[0].strip()
    return first[:LIMIT] or None
