"""에이전트 간 메시지 — agent_msg.py · /api/agent/message · /api/peer/agent/message · MCP.

실제 tmux는 쓰지 않는다(세션 존재·pane 해석·send-keys는 몽키패치). 두 맥 사이 왕복은
격리 서버 두 대로 따로 확인한다 — 여기서는 규칙(권한·hop·큐 연결·머리글)을 고정한다.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import time

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_WORKSPACE_PATH", str(tmp_path / "workspace.json"))
    import host_store
    importlib.reload(host_store)
    import agent_msg
    importlib.reload(agent_msg)
    import queue_store
    import main
    importlib.reload(main)
    import routes.peer as peer_mod
    import routes.agent_msg as route_mod

    async def _exists(name):
        return name in {"dev", "api"}

    monkeypatch.setattr(peer_mod, "_has_exact", _exists)
    monkeypatch.setattr(route_mod, "_has_exact", _exists)
    with TestClient(main.app) as c:
        yield c, host_store, agent_msg, queue_store


# --- 규칙 ---------------------------------------------------------------------------


def test_parse_to(env):
    _, _, am, _ = env
    assert am.parse_to("dev") == ("local", "dev")
    assert am.parse_to("M5/api") == ("M5", "api")
    for bad in ("", "M5/", "M5/-x", "a b", "M5/../x"):
        with pytest.raises(am.MessageError):
            am.parse_to(bad)


def test_envelope_is_one_line_and_says_it_is_from_an_agent(env):
    _, _, am, _ = env
    msg = {"id": "a" * 12, "from": {"host": "m1", "hostLabel": "M1", "session": "dev"},
           "text": "첫 줄\n둘째 줄\n\n셋째"}
    out = am.envelope(msg)
    assert "\n" not in out
    assert "다른 에이전트가 보낸 요청" in out
    assert 'reply_to="aaaaaaaaaaaa"' in out and 'to="m1/dev"' in out
    assert "첫 줄 둘째 줄 셋째" in out


def test_reply_envelope_says_no_answer_needed(env):
    """M1↔M5 실측: 답장을 받은 에이전트가 그걸 새 요청으로 읽고 또 답했다.
    답장 머리글은 요청과 달라야 하고 "다시 답할 필요 없다"를 밝혀야 한다."""
    _, _, am, _ = env
    msg = {"id": "b" * 12, "replyTo": "a" * 12,
           "from": {"host": "m5", "hostLabel": "M5", "session": "beta"}, "text": "결과: 통과"}
    out = am.envelope(msg)
    assert "에이전트 답장" in out and "요청" not in out.split("]")[0]
    assert "다시 답할 필요가 없습니다" in out
    assert "#aaaaaaaaaaaa에 답함" in out


def test_request_without_sender_session_says_it_cannot_be_answered(env):
    _, _, am, _ = env
    out = am.envelope({"id": "c" * 12, "from": {"host": "m1", "hostLabel": "M1", "session": ""},
                       "text": "hi"})
    assert "답장할 수 없습니다" in out and "reply_to" not in out


def test_hop_limit_counts_from_own_records_not_only_the_claim(env):
    _, _, am, _ = env
    first = am.receive(from_host="m1", from_label="M1", from_session="dev",
                       to_session="api", text="hi", claimed_hop=am.MAX_HOPS - 2)
    # 보낸 쪽이 hop 0이라고 주장해도 기록상 다음은 MAX_HOPS-1
    second = am.receive(from_host="m1", from_label="M1", from_session="dev",
                        to_session="api", text="again", reply_to=first["id"], claimed_hop=0)
    assert second["hop"] == am.MAX_HOPS - 1 and second["conv"] == first["conv"]
    with pytest.raises(am.MessageError) as e:
        am.receive(from_host="m1", from_label="M1", from_session="dev",
                   to_session="api", text="more", reply_to=second["id"])
    assert e.value.code == "hop_limit"


def test_rate_limit_per_sender_host(env):
    _, _, am, _ = env
    now = time.time()
    for _ in range(am.RATE_PER_MIN):
        am.check_rate("m9", now)
    with pytest.raises(am.MessageError) as e:
        am.check_rate("m9", now)
    assert e.value.status == 429
    am.check_rate("other", now)          # 다른 호스트는 영향 없음
    am.check_rate("m9", now + 61)        # 1분 지나면 풀린다


def test_receive_queues_an_item_and_delivery_updates_status(env, monkeypatch):
    _, _, am, qs = env
    msg = am.receive(from_host="m1", from_label="M1", from_session="dev",
                     to_session="api", text="테스트 좀 돌려줘")
    items = qs.list_items()
    assert len(items) == 1
    assert items[0]["target"] == {"session": "api"}
    assert items[0]["source"] == {"agentMsg": msg["id"]}
    assert am.get(msg["id"])["status"] == am.STATUS_QUEUED

    import queue_runner
    import tmux_target
    monkeypatch.setattr(tmux_target, "session_pane", lambda s: "%9" if s == "api" else None)
    sent = []
    monkeypatch.setattr(tmux_target, "send_to_tmux", lambda pane, text: sent.append((pane, text)) or True)
    r = queue_runner.drain_once()
    assert r["drained"] == 1 and sent[0][0] == "%9"
    assert am.get(msg["id"])["status"] == am.STATUS_DELIVERED


def test_blocked_delivery_is_recorded(env, monkeypatch):
    _, _, am, qs = env
    msg = am.receive(from_host="m1", from_label="M1", from_session="dev",
                     to_session="api", text="hello")
    import queue_runner
    import tmux_target
    monkeypatch.setattr(tmux_target, "session_pane", lambda s: None)
    queue_runner.drain_once()
    got = am.get(msg["id"])
    assert got["status"] == am.STATUS_BLOCKED and got.get("reason")


# --- /api/agent/message (이 맥에서 보내기) -------------------------------------------


def test_local_send_resolves_sender_from_pane_and_queues(env, monkeypatch):
    client, _, am, qs = env
    import pane_resolve
    monkeypatch.setattr(pane_resolve, "resolve", lambda pane, tmux, cwd: ("dev", "pane"))
    r = client.post("/api/agent/message", json={"to": "api", "text": "리뷰 부탁", "pane": "%1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["to"] == "local/api"
    m = am.get(body["id"])
    assert m["from"]["session"] == "dev" and m["to"]["session"] == "api"
    assert len(qs.list_items()) == 1


def test_local_send_refuses_missing_session_and_self(env, monkeypatch):
    client, _, _, qs = env
    import pane_resolve
    monkeypatch.setattr(pane_resolve, "resolve", lambda pane, tmux, cwd: ("dev", "pane"))
    assert client.post("/api/agent/message", json={"to": "nope", "text": "x"}).status_code == 404
    assert client.post("/api/agent/message", json={"to": "dev", "text": "x"}).status_code == 400
    assert qs.list_items() == []


def test_send_to_unknown_host_is_404(env):
    client, _, _, _ = env
    r = client.post("/api/agent/message", json={"to": "ghost/dev", "text": "x"})
    assert r.status_code == 404 and r.json()["error"] == "host_not_found"


# --- /api/peer/agent/message (다른 맥에서 받기) ---------------------------------------


def _signed(hs, secret, peer_id, body: bytes, path="/api/peer/agent/message"):
    ts = int(time.time())
    nonce = f"n-{time.time_ns()}"
    digest = hashlib.sha256(body).hexdigest()
    return {"X-Peer-Id": peer_id, "X-Peer-Ts": str(ts), "X-Peer-Nonce": nonce,
            "X-Peer-Sig": hs.sign_request(secret, "POST", path, ts, nonce, digest),
            "X-Peer-Body": digest, "Content-Type": "application/octet-stream"}


def _grant(hs, level="control", agent=True):
    _, secret = hs.add_grant("m1", "M1")
    hs.set_grant_level("m1", level)
    if agent:
        hs.set_grant_agent("m1", True)
    return secret


def _post(client, hs, secret, payload):
    raw = json.dumps(payload).encode()
    return client.post("/api/peer/agent/message", content=raw, headers=_signed(hs, secret, "m1", raw))


def test_peer_message_needs_the_agent_switch(env):
    client, hs, _, qs = env
    secret = _grant(hs, agent=False)
    r = _post(client, hs, secret, {"id": "b" * 12, "to": "api", "text": "hi", "from": "dev"})
    assert r.status_code == 403 and r.json()["error"] == "agent_messages_off"
    assert qs.list_items() == []


def test_peer_message_needs_control(env):
    client, hs, _, qs = env
    secret = _grant(hs, level="view")
    r = _post(client, hs, secret, {"id": "b" * 12, "to": "api", "text": "hi"})
    assert r.status_code == 403
    assert qs.list_items() == []


def test_peer_message_is_queued_with_sender_identity_from_the_grant(env):
    client, hs, am, qs = env
    secret = _grant(hs)
    r = _post(client, hs, secret, {"id": "c" * 12, "to": "api", "text": "빌드 결과 알려줘", "from": "dev"})
    assert r.status_code == 200, r.text
    m = am.get("c" * 12)
    # 보낸 호스트는 본문 주장이 아니라 서명한 grant에서 온다
    assert m["from"]["host"] == "m1" and m["from"]["hostLabel"] == "M1"
    assert m["from"]["session"] == "dev" and m["dir"] == "in"
    assert qs.list_items()[0]["source"] == {"agentMsg": "c" * 12}


def test_peer_message_unknown_session_is_404(env):
    client, hs, _, qs = env
    secret = _grant(hs)
    r = _post(client, hs, secret, {"id": "d" * 12, "to": "ghost", "text": "hi"})
    assert r.status_code == 404
    assert qs.list_items() == []


def test_peer_message_body_is_covered_by_the_signature(env):
    client, hs, _, qs = env
    secret = _grant(hs)
    raw = json.dumps({"id": "e" * 12, "to": "api", "text": "safe"}).encode()
    headers = _signed(hs, secret, "m1", raw)
    tampered = json.dumps({"id": "e" * 12, "to": "api", "text": "rm -rf ~"}).encode()
    r = client.post("/api/peer/agent/message", content=tampered, headers=headers)
    assert r.status_code == 401
    assert qs.list_items() == []


# --- MCP --------------------------------------------------------------------------


def test_mcp_initialize_and_tools_list():
    import mcp_farshell as m
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-06-18"}})
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in r["result"]["capabilities"]
    names = [t["name"] for t in m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    assert names == ["list_agents", "send_message", "read_messages"]
    assert m.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert m.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"] == -32601


def test_mcp_send_message_passes_pane_and_reports_failure(monkeypatch):
    import mcp_farshell as m
    seen = {}

    def fake_api(method, path, body=None):
        seen.update(method=method, path=path, body=body)
        return 404, {"error": "session_not_found", "reason": "이 맥에 'x' 세션이 없습니다"}

    monkeypatch.setattr(m, "_api", fake_api)
    monkeypatch.setenv("TMUX_PANE", "%7")
    r = m.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                  "params": {"name": "send_message", "arguments": {"to": "x", "text": "hi"}}})
    assert r["result"]["isError"] is True
    assert "세션이 없습니다" in r["result"]["content"][0]["text"]
    assert seen["body"]["pane"] == "%7" and seen["path"] == "/api/agent/message"


# --- 전달 경로(2026-09-28 M1↔M5 실측 수정) -----------------------------------------


def test_send_to_tmux_sends_text_literally_then_enter_separately(monkeypatch):
    """한 번의 send-keys에 텍스트와 Enter를 같이 실으면 Claude Code가 붙여넣기로 받아
    Enter를 줄바꿈으로 처리했다(5회 중 0회 제출). 텍스트는 `-l`로, Enter는 따로."""
    import tmux_target
    calls = []
    monkeypatch.setattr(tmux_target.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(tmux_target, "_ENTER_GAP_SEC", 0)
    assert tmux_target.send_to_tmux("%3", "Enter") is True
    assert len(calls) == 2
    assert calls[0][-5:] == ["-t", "%3", "-l", "--", "Enter"]   # 글자 그대로 — 키로 해석 안 함
    assert calls[1][-3:] == ["-t", "%3", "Enter"]


def test_type_to_tmux_is_literal(monkeypatch):
    import tmux_target
    calls = []
    monkeypatch.setattr(tmux_target.subprocess, "run", lambda args, **kw: calls.append(args))
    tmux_target.type_to_tmux("%3", "Enter")
    assert len(calls) == 1 and "-l" in calls[0] and calls[0][-1] == "Enter"


def test_targeted_only_skips_untargeted_items(env):
    _, _, _, qs = env
    qs.add("아무 세션에나", None)
    qs.add("api 전용", {"session": "api"})
    item = qs.pop_next("api", session_scoped=True, targeted_only=True)
    assert item["text"] == "api 전용"
    assert qs.pop_next("api", session_scoped=True, targeted_only=True) is None
    assert [x["text"] for x in qs.list_items()] == ["아무 세션에나"]


def test_session_start_marks_idle_but_compact_does_not(env):
    import agent_status
    agent_status._state.clear()
    ent = agent_status.on_event("session_start", {"session_id": "s1", "source": "startup"}, session="api")
    assert ent["status"] == agent_status.IDLE
    assert agent_status.known_status_for_session("api") == agent_status.IDLE
    agent_status.on_event("pre", {"session_id": "s1", "tool_name": "Bash"}, session="api")
    agent_status.on_event("session_start", {"session_id": "s1", "source": "compact"}, session="api")
    assert agent_status.known_status_for_session("api") == agent_status.WORKING


def test_session_start_event_drains_only_targeted_items(env, monkeypatch):
    client, _, _, _ = env
    import agent_status
    import pane_resolve
    import queue_runner
    agent_status._state.clear()
    monkeypatch.setattr(pane_resolve, "resolve", lambda pane, tmux, cwd: ("api", "pane"))
    seen = {}
    monkeypatch.setattr(queue_runner, "schedule_drain",
                        lambda **kw: seen.update(kw) or True)
    r = client.post("/api/agent/event", json={"event": "session_start", "pane": "%1",
                                              "payload": {"session_id": "s9", "source": "startup"}})
    assert r.status_code == 200 and r.json()["queue_scheduled"] is True
    assert seen == {"session": "api", "session_scoped": True, "targeted_only": True}


def test_claude_hooks_register_session_start_without_compact():
    import claude_hooks
    settings, _ = claude_hooks.apply_install({})
    groups = settings["hooks"]["SessionStart"]
    assert groups[0]["matcher"] == "startup|resume|clear"
    assert groups[0]["hooks"][0]["command"].endswith("agent_hook.sh session_start claude")
    assert "matcher" not in settings["hooks"]["Stop"][0]
