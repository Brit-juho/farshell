"""P1-1(ADR-33) — 승인 응답 경쟁. `agent_status.py`의 토큰 발급/소비 단위 테스트.

실제 증상("두 번째 키가 다음 프롬프트로 샌다")은 실기기 두 대가 아니면 완전히
재현되지 않는다(docs/plan-2.3/10-approval-race.md §5 「실기기」). 여기서
확인하는 것은 그 수정이 딛고 선 원자성·판정 규칙 — 지문이 안정적인지, 같은
토큰을 두 번 소비하면 두 번째가 반드시 지는지, 내부 필드가 밖으로 안 새는지.

엔트리는 되도록 공개 함수(`report`/`on_waiting`)로 만든다 — 프로덕션 경로가
실제로 지나는 길과 같다. `_prompt_fingerprint`/`_entries_for`처럼 공개 함수가
없는 대상만 내부 이름으로 직접 부른다.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

import agent_status
from agent_status import LOCAL_HOST, WAITING, WORKING, _prompt_fingerprint, consume_prompt


@pytest.fixture(autouse=True)
def _reset_state():
    """다른 agent_status 테스트 파일과 같은 관례(test_agent_status.py 참고)."""
    agent_status._state.clear()
    yield
    agent_status._state.clear()


def _make_waiting(sid="s1", tmux_name="dev", host=LOCAL_HOST,
                   question="계속할까요?", options=None):
    """working 엔트리를 만들고 waiting으로 전이시켜 토큰을 발급받는다.

    `report(..., WORKING, session=tmux_name)`이 `tmux_session`을 채우고,
    `on_waiting`이 그 위에서 WAITING 전이 + 토큰 발급을 한다 — 실제 운영
    경로(deps.py의 `_on_waiting_change`)와 같은 순서다.
    """
    agent_status.report(sid, WORKING, session=tmux_name, host=host)
    return agent_status.on_waiting(sid, True, question=question, options=options, host=host)


def test_waiting_issues_a_prompt_id():
    ent = _make_waiting()
    assert ent["prompt_id"] is not None


def test_e4_repeated_refresh_of_the_same_question_keeps_the_token():
    """PTY 감지가 같은 프롬프트를 반복 재확인해도 토큰은 불변이어야 한다 —
    안 그러면 사용자가 보던 토큰이 탭하기 전에 만료된다."""
    opts = [{"key": "1", "label": "예"}]
    ent = _make_waiting(question="계속할까요?", options=opts)
    first = ent["prompt_id"]
    for _ in range(10):
        ent = agent_status.on_waiting("s1", True, question="계속할까요?", options=opts)
        assert ent["prompt_id"] == first


def test_e13_consuming_the_same_token_twice_only_the_first_wins():
    ent = _make_waiting()
    token = ent["prompt_id"]
    assert consume_prompt("dev", token) is True
    assert consume_prompt("dev", token) is False


def test_e13_consume_prompt_never_awaits():
    """원자성의 근거 자체가 '검사와 대입 사이에 await가 없다'는 것이다 —
    런타임에는 안 보이니 AST를 파싱해 **실행 코드**에 `await` 표현식이 없는지
    고정한다(docstring 산문에 "await"라는 낱말이 있는 것과는 무관해야 한다)."""
    assert not inspect.iscoroutinefunction(consume_prompt)
    src = textwrap.dedent(inspect.getsource(consume_prompt))
    tree = ast.parse(src)
    awaits = [n for n in ast.walk(tree) if isinstance(n, ast.Await)]
    assert awaits == [], "consume_prompt에 await가 들어가면 원자성이 깨진다"


def test_e5_trailing_whitespace_does_not_rotate_the_token():
    """재렌더로 질문 문자열이 미세하게 달라져도(후행 공백·중복 공백) 지문은
    같아야 한다 — 안 그러면 사용자가 영원히 못 누른다."""
    ent = _make_waiting(question="계속할까요?")
    first = ent["prompt_id"]
    ent = agent_status.on_waiting("s1", True, question="계속할까요?   \n")
    assert ent["prompt_id"] == first


def test_e6_a_real_option_change_rotates_the_token():
    """선택지 키가 실제로 바뀌면(새 질문) 새 토큰이 나가고, 옛 토큰은 죽는다."""
    ent = _make_waiting(options=[{"key": "1", "label": "예"}, {"key": "2", "label": "아니오"}])
    old = ent["prompt_id"]
    ent = agent_status.on_waiting("s1", True, question="계속할까요?", options=[{"key": "1", "label": "예"}])
    assert ent["prompt_id"] != old
    assert consume_prompt("dev", old) is False


def test_e11_leaving_waiting_clears_the_token():
    ent = _make_waiting()
    assert ent["prompt_id"] is not None
    ent = agent_status.on_waiting("s1", False)
    assert ent["prompt_id"] is None


def test_e9_remote_host_entries_are_not_matched_locally():
    _make_waiting(sid="s-remote", tmux_name="dev", host="mac-2")
    assert agent_status._entries_for("dev", LOCAL_HOST) == []
    assert len(agent_status._entries_for("dev", "mac-2")) == 1


def test_entries_for_matches_tmux_name_and_host():
    ent = _make_waiting(sid="s1", tmux_name="dev")
    _make_waiting(sid="s2", tmux_name="other")
    found = agent_status._entries_for("dev")
    assert found == [ent]


def test_consume_prompt_requires_waiting_status():
    """WAITING이 아닌 엔트리에 남아 있는 옛 prompt_id는 소비되면 안 된다."""
    ent = _make_waiting()
    agent_status.on_waiting("s1", False)  # WORKING으로 되돌아간다 — prompt_id는 None이 된다
    assert ent["status"] == WORKING
    assert consume_prompt("dev", ent.get("prompt_id") or "stale") is False


def test_public_entry_strips_underscore_prefixed_fields():
    ent = _make_waiting()
    assert "_prompt_fp" in ent, "테스트 전제 — 내부 필드가 실제로 있어야 한다"
    out = agent_status.public_entry(ent)
    assert "_prompt_fp" not in out
    assert out["prompt_id"] == ent["prompt_id"]


def test_prompt_fingerprint_ignores_option_labels():
    """라벨은 잘릴 수 있어 지문에 안 쓴다 — 키만 본다."""
    a = {"question": "q", "options": [{"key": "1", "label": "예"}]}
    b = {"question": "q", "options": [{"key": "1", "label": "예 (다시 묻지 않음)"}]}
    assert _prompt_fingerprint(a) == _prompt_fingerprint(b)
