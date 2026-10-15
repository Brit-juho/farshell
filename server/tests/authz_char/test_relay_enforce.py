"""`VT_AUTHZ_ENFORCE=peer`(3d): 호스트 전환 중계의 받는 쪽(`/api/peer/relay/*`, `/api/peer/relay-ws/*`)을 엔진이 판정한다.

relay_policy 의 규칙마다 그룹(`scopes.RELAY_*_GROUPS`)이 있고 엔진에 `relay` + `group` 조건으로 묻는다. 확인 순서(등급 관문 -> 허용 목록)와 오류 코드·본문은
옛과 같다(H16: 보기 등급은 허용 목록 안의 GET 도 level_required). 기록 전체와의 비교는 `test_char_peer_enforced.py`.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "peer")


def client():
    import main
    return TestClient(main.app, raise_server_exceptions=False)


def _relay(world, who, method, path):
    world.set_mode("password")
    p = "/api/peer/relay" + path
    h = world.headers_for(who, method, p, b"")
    r = client().request(method, p, headers=h)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else None


def test_relay_checks_the_level_gate_before_the_allow_list_like_before(world):
    """H16 순서: view 는 허용 목록 안의 GET 도 level_required, 목록 밖은 control 에게만 relay_forbidden."""
    st, j = _relay(world, "peer_view", "GET", "/api/workspace")
    assert (st, j["error"]) == (403, "level_required")
    st, j = _relay(world, "peer_view", "GET", "/api/auth/status")
    assert (st, j["error"]) == (403, "level_required")
    st, j = _relay(world, "peer_control", "GET", "/api/auth/status")
    assert (st, j["error"]) == (403, "relay_forbidden")
    st, j = _relay(world, "peer_control", "GET", "/api/files/abc/share")
    assert (st, j["error"]) == (403, "relay_forbidden")
    st, j = _relay(world, "peer_control", "GET", "/api/workspace")
    assert st == 200 and j == {"relayDispatched": True}


def test_relay_group_decisions_map_each_rule_to_its_group(world):
    from authz import peer_svc, scopes as S
    import host_store
    g = host_store.find_grant("pc")
    for rx, grp in S.RELAY_HTTP_GROUPS.items():
        assert peer_svc.relay_group_decide(g, grp).allow, grp
    assert not peer_svc.relay_group_decide(g, None).allow           # 허용 목록 밖: 그룹이 없다
    assert peer_svc.relay_group_decide(g, None).reason == "cond_missing"
    v = host_store.find_grant("pv")
    assert not peer_svc.relay_group_decide(v, "workspace").allow


def test_a_deny_rule_for_one_group_turns_only_that_group_into_relay_forbidden(world):
    from authz import store
    with store.txn() as docs:
        docs["grants"]["items"].append({"id": "d-q", "effect": "deny", "subject": "peer:pc", "resource": "relay:local/queue",
                                        "scopes": ["relay"], "sconds": {"relay": {"groups": ["queue"]}},
                                        "state": "active", "schema": 2})
    st, j = _relay(world, "peer_control", "POST", "/api/queue")
    assert (st, j["error"]) == (403, "relay_forbidden")
    st, j = _relay(world, "peer_control", "GET", "/api/workspace")
    assert st == 200


def test_relay_ws_watchdog_polls_fast_when_enforced(world, monkeypatch):
    """relay 소켓의 점검 주기: 켜짐 2초, 꺼짐 30초(옛 값)."""
    from authz import peer_svc
    assert peer_svc.WATCH_INTERVAL == 2.0 and peer_svc.LEGACY_WATCH_INTERVAL == 30.0
    from routes import peer_relay
    import asyncio
    import host_store

    sent = []

    class _WS:
        async def _send(self, m):
            sent.append(m)

    async def go():
        t = asyncio.create_task(peer_relay._revocation_watchdog(_WS(), "pc", interval=0.05))
        await asyncio.sleep(0.2)
        assert not sent
        host_store.set_grant_level("pc", host_store.LEVEL_VIEW)
        await asyncio.wait_for(t, 2.0)

    asyncio.run(go())
    assert sent and sent[0]["code"] == 4401
