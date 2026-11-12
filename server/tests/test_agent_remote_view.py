"""다른 맥으로 나가는 에이전트 상태는 한 함수(remote_view)를 거친다(세션 로직 점검 단계 0, 원격 필터 기반).

이 맥 전용 필드(`agent_status.LOCAL_ONLY_FIELDS`)는 중계 소켓·중계 REST 응답에서 빠지고, 이 맥의 브라우저에는
그대로 간다. 지금은 그런 필드가 없어 테스트에서 하나를 끼워 넣어 경로를 고정한다.
"""

import asyncio

import pytest

import agent_status
import routes.agents as agents_route


class _WS:
    def __init__(self, relayed=False, fail=False):
        self.scope = {"vt_relay": {"peer": "p1"}} if relayed else {}
        self.sent = []
        self.fail = fail

    async def send_json(self, msg):
        if self.fail:
            raise RuntimeError("gone")
        self.sent.append(msg)

    async def send_text(self, text):
        await self.send_json(text)


@pytest.fixture
def local_only(monkeypatch):
    monkeypatch.setattr(agent_status, "LOCAL_ONLY_FIELDS", frozenset({"secret_note"}))


def _entry():
    return {"status": "working", "tool": "Edit", "secret_note": "이 맥에서만"}


def test_broadcast_sends_the_local_field_only_to_local_sockets(local_only, monkeypatch):
    here, there = _WS(), _WS(relayed=True)
    monkeypatch.setattr(agents_route, "_agent_event_clients", {here, there})
    msg = {"type": "agent_status_sync", "all": {"a": _entry()}, "active": [_entry()], "state": _entry()}
    asyncio.run(agents_route._broadcast_agent(msg))
    assert here.sent[0]["state"]["secret_note"] == "이 맥에서만"
    remote = there.sent[0]
    assert "secret_note" not in remote["state"]
    assert "secret_note" not in remote["all"]["a"] and "secret_note" not in remote["active"][0]
    assert remote["state"]["tool"] == "Edit"


def test_failed_sockets_leave_the_shared_set(monkeypatch):
    ok, broken = _WS(), _WS(relayed=True, fail=True)
    clients = {ok, broken}
    monkeypatch.setattr(agents_route, "_agent_event_clients", clients)
    asyncio.run(agents_route._broadcast_agent({"type": "agents_change", "agents": {}}))
    assert clients == {ok}


def test_rest_status_hides_the_local_field_from_relayed_requests(local_only, monkeypatch):
    monkeypatch.setattr(agent_status, "visible_state", lambda: {"k": _entry()})
    monkeypatch.setattr(agent_status, "all_active", lambda: [_entry()])
    monkeypatch.setattr(agent_status, "public_entry", lambda e: dict(e))
    relayed = type("R", (), {"scope": {"vt_relay": {"peer": "p1"}}})()
    local = type("R", (), {"scope": {}})()
    assert "secret_note" not in asyncio.run(agents_route.agent_status_get(relayed))["all"]["k"]
    assert asyncio.run(agents_route.agent_status_get(local))["all"]["k"]["secret_note"] == "이 맥에서만"


def test_remote_view_is_identity_when_nothing_is_local_only():
    entry = _entry()
    assert agent_status.remote_view(entry) is entry
