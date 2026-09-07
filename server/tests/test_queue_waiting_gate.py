"""A4 — 큐의 5번째 관문: 승인 대기 중인 pane 에는 절대 투입하지 않는다.

🔴 이건 안전 문제다. 승인 프롬프트가 떠 있는 pane 에 send-keys 를 하면
`tmux_target.send_to_tmux` 가 무조건 Enter 를 붙이므로 **큐 텍스트가 승인
답변으로 소비된다** — 사용자가 승인한 적 없는 동작이 승인돼버린다.
"""

import pytest

import agent_status
import queue_runner
import queue_store


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    agent_status._state.clear()
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux",
                        lambda pane, text: sent.append((pane, text)) or True)
    monkeypatch.setattr(queue_runner, "_resolve_pane", lambda item: ("%1", "auto"))
    yield sent
    agent_status._state.clear()


def _mark(session: str, status: str):
    agent_status.report(f"pane:{session}", status, session=session)


def test_waiting_target_blocks_the_drain(q):
    queue_store.add("rm -rf /tmp/x", "dev")
    _mark("dev", agent_status.WAITING)

    result = queue_runner.drain_once("dev", session_scoped=True)
    assert result["drained"] == 0
    assert result["error"] == "waiting"
    assert q == [], "tmux 로 아무것도 나가면 안 된다"


def test_blocked_item_is_kept_not_discarded(q):
    queue_store.add("echo hi", "dev")
    _mark("dev", agent_status.WAITING)
    queue_runner.drain_once("dev", session_scoped=True)

    items = queue_store.list_items()
    assert len(items) == 1
    assert items[0]["status"] == "blocked"
    assert "승인 대기" in items[0].get("blocked_reason", "")


def test_drain_proceeds_once_waiting_clears(q):
    queue_store.add("echo hi", "dev")
    _mark("dev", agent_status.WAITING)
    queue_runner.drain_once("dev", session_scoped=True)
    assert q == []

    # 승인이 끝났다 → 항목을 되살리고 다시 흘린다(사용자가 unblock 하는 흐름).
    _mark("dev", agent_status.WORKING)
    item_id = queue_store.list_items()[0]["id"]
    queue_store.unblock(item_id)
    result = queue_runner.drain_once("dev", session_scoped=True)
    assert result["drained"] == 1
    assert q == [("%1", "echo hi")]


def test_working_target_is_not_blocked(q):
    queue_store.add("echo hi", "dev")
    _mark("dev", agent_status.WORKING)
    assert queue_runner.drain_once("dev", session_scoped=True)["drained"] == 1


def test_unknown_status_does_not_block(q):
    """감지가 없는 에이전트(codex 등)에서 큐가 영구히 막히면 안 된다.

    "모르면 막는다"가 아니라 "알 때만 막는다".
    """
    queue_store.add("echo hi", "dev")
    assert queue_runner.drain_once("dev", session_scoped=True)["drained"] == 1


def test_pane_notation_target_is_matched_by_session_name(q):
    """타깃이 `세션:윈도.페인` 표기여도 세션 이름으로 상태를 찾는다.

    수동 실행(session_scoped=False) 경로로 확인한다 — 자동 드레인은 타깃
    문자열이 세션 이름과 정확히 같아야 그 항목을 꺼내므로(pop_next 의 기존
    규칙) pane 표기 항목은 애초에 5관문까지 오지 않는다.
    """
    queue_store.add("echo hi", "dev:0.0")
    _mark("dev", agent_status.WAITING)
    assert queue_runner.drain_once()["error"] == "waiting"
