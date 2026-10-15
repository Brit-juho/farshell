"""기계 토큰·control peer 구멍을 CURRENT_BEHAVIOR 로 고정한다.

**이 파일의 단언은 "안전하다"가 아니라 "지금 이렇다"이다.** 이후 단계가 구멍을 막으면 여기가
깨지는 게 정상이고, 그때 matrix.json 을 다시 만들면서 어느 사례가 바뀌었는지가 곧 변경 목록이다.

- H1: 공용 기계 토큰(+pane 주장만 한 프로세스)이 공유 grant 없이 PTY 입력·큐·tmux 종료에 닿는다.
- H2: 로컬 메시지의 보낸 세션을 본문(pane/tmux)이 정한다 — 남의 칸으로 답장 주소를 위조한다.
- H5: control peer 는 allow-agent 없이도 relay `/api/queue`·`/api/peer/input`으로 에이전트에 닿는다.
- H6: 비밀번호·token 이 둘 다 없으면 무인증 호출자에게 같은 것이 다 열린다.
"""

from __future__ import annotations

import pytest

from authz_cases import cases_for
from authz_harness import Case, TMUX_ENV

CURRENT_BEHAVIOR = True   # 표시용 — 아래 단언은 고쳐야 할 동작을 그대로 적은 것이다


@pytest.mark.parametrize("case", cases_for("machine_token"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))


def _run(world, **kw):
    return world.run(Case(id="adhoc", group="adhoc", **kw))


def test_h1_machine_token_kills_a_tmux_session_without_any_grant(world):
    import agent_share
    assert agent_share.list_state(agent_share.snapshot())["grants"] == []
    r = _run(world, caller="machine", method="DELETE", path="/api/tmux/kill/target")
    assert r == {"status": 200, "error": None}
    assert "target" not in world.tmux.sessions


def test_h1_machine_token_types_into_a_pty(world):
    r = _run(world, caller="agent_foreign", method="POST", path="/api/sessions/pty-1/keys", body={"text": "y\r"})
    assert r["status"] == 200
    assert world.pty_writes == [("pty-1", b"y\r")]


def test_h1_machine_token_queues_a_command_for_any_session(world):
    import queue_store
    r = _run(world, caller="machine", method="POST", path="/api/queue",
             body={"text": "echo pwned", "target": {"session": "target"}})
    assert r["status"] == 200
    assert any((x.get("target") or {}).get("session") == "target" for x in queue_store.list_items())


def test_h2_sender_session_comes_from_the_request_body(world):
    import agent_msg
    r = _run(world, caller="machine", method="POST", path="/api/agent/message",
             body={"to": "local/target", "text": "hi", "pane": "%3", "tmux": TMUX_ENV})
    assert r["status"] == 200
    last = agent_msg.list_messages("", 10)[-1]
    assert last["from"]["session"] == "other"   # 호출자는 other 칸이 아닌데 other 로 기록된다


def test_h5_control_peer_reaches_the_queue_without_allow_agent(world):
    import host_store
    import queue_store
    assert not host_store.find_grant("pc").get("agentMessages")
    r = _run(world, caller="peer_control", method="POST", path="/api/peer/relay/api/queue",
             body=b'{"text": "echo via relay", "target": {"session": "target"}}',
             headers=(("Content-Type", "application/json"),), real_dispatch=True)
    assert r["status"] == 200
    assert any(x.get("text") == "echo via relay" for x in queue_store.list_items())
    # 같은 peer 의 agent/message 는 스위치가 꺼져 있어 막힌다 — 경계가 아닌 이유가 이 대비다.
    r2 = _run(world, caller="peer_control", method="POST", path="/api/peer/agent/message",
              body={"to": "target", "text": "hi", "from": "dev", "id": "m1"})
    assert r2 == {"status": 403, "error": "agent_messages_off"}


def test_h5_control_peer_types_into_a_session_via_peer_input(world):
    r = _run(world, caller="peer_control", method="POST", path="/api/peer/input",
             body={"session": "target", "data": "echo via input", "enter": True})
    assert r == {"status": 200, "error": None}
    assert any(c[:1] == ["send-keys"] and "%2" in c for c in world.tmux.calls)


def test_h6_no_credentials_server_opens_the_queue_to_anyone(world):
    r = _run(world, caller="none", mode="none", method="POST", path="/api/queue",
             body={"text": "x", "target": {"session": "target"}})
    assert r["status"] == 200
