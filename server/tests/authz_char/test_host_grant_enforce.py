"""`VT_AUTHZ_ENFORCE=peer`(3d): `fsh host allow-control|deny-control|allow-agent|deny-agent` 가 서버에 요청이 된다.

  - 줄이는 변경(deny-*)은 바로 적용·감사, 늘리는 변경(allow-*)은 승인 요청 — 사람이 승인해야 적용된다.
  - pane 없는 cli·에이전트·기계 토큰·무인증은 승인할 수 없다. 승인은 재인증한 사람만(제어), 거절은 5분 쿨다운.
  - 승인되면 hosts.json 과 저장소 사본에 함께 쓴다(dual-write) — `fsh migrate-auth --verify` 는 깨끗하다. 끄면 옛 동작(롤백).
  - CLI(`host_store._cli`)는 서버가 요청을 받으면 직접 쓰지 않고, 소켓이 없거나 서버가 안 받으면 옛 직접 쓰기로 내려간다.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "peer")


def client():
    import main
    return TestClient(main.app, raise_server_exceptions=False)


def _run(world, who, method, path, body):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
    world.set_mode("password")
    h = world.headers_for(who, method, path, data)
    if data:
        h["Content-Type"] = "application/json"
    r = client().request(method, path, content=data or None, headers=h)
    try:
        j = r.json()
    except ValueError:
        j = None
    return r.status_code, j




@pytest.fixture
def cli_actor(monkeypatch):
    """HTTP 로는 만들 수 없는 unix socket 호출자를 시험에서만 주입한다."""
    from authz import host_gate
    from authz import principals as P
    box = {"p": P.cli(501, 4242)}
    monkeypatch.setattr(host_gate, "local_principal", lambda conn: box["p"])
    return box


def _change(world, action, pid="pv"):
    world.set_mode("password")
    # 실제 호출은 unix socket 게이트가 미들웨어를 건너뛰게 한다 — 시험은 토큰으로 미들웨어를 통과시키고 호출 주체(`local_principal`)를 주입한다.
    return client().post(f"/api/host-grants/{pid}/change", json={"action": action},
                         headers=world.headers_for("machine", "POST", "/x", b""))


def _human_decide(world, rid, decision, who="session_elev"):
    h = world.headers_for(who, "POST", "/x", b"")
    return client().post(f"/api/agent/share/requests/{rid}/decision", json={"decision": decision}, headers=h)


def test_deny_control_applies_at_once_and_is_audited(world, cli_actor):
    import host_store
    from authz import audit
    r = _change(world, "deny-control", "pc")
    assert r.status_code == 200 and r.json()["state"] == "applied"
    assert host_store.find_grant("pc")["level"] == host_store.LEVEL_VIEW
    rows = audit.read_recent(10, "peer.")
    assert rows[0]["action"] == "peer.grant" and rows[0]["result"] == "applied"
    again = _change(world, "deny-control", "pc")
    assert again.json()["state"] == "noop"


def test_allow_control_makes_a_request_and_changes_nothing_until_a_human_approves(world, cli_actor):
    import host_store
    r = _change(world, "allow-control", "pv")
    assert r.status_code == 202 and r.json()["state"] == "pending"
    rid = r.json()["requestId"]
    assert host_store.find_grant("pv")["level"] == host_store.LEVEL_VIEW
    # 같은 요청은 하나로 합친다
    assert _change(world, "allow-control", "pv").json()["requestId"] == rid
    # 승인함(사람 전용 목록)에 kind=peer 로 보인다
    world.set_mode("password")
    lst = client().get("/api/agent/share/requests", headers=world.headers_for("session", "GET", "/x", b"")).json()["requests"]
    mine = [x for x in lst if x["id"] == rid]
    assert mine and mine[0]["kind"] == "peer" and mine[0]["change"] == "allow-control" and mine[0]["target"]["name"] == "pv"
    # 승인: 재인증한 사람만(제어는 raw 입력을 주는 것이라 elevated 사유)
    r1 = _human_decide(world, rid, "allow", "session")
    assert r1.status_code == 403 and r1.json()["error"] == "elevation_required"
    assert host_store.find_grant("pv")["level"] == host_store.LEVEL_VIEW
    r2 = _human_decide(world, rid, "allow", "session_elev")
    assert r2.status_code == 200 and r2.json()["grantId"] == "pg-pv"
    assert host_store.find_grant("pv")["level"] == host_store.LEVEL_CONTROL
    # 같은 결정의 재전송은 처음 결과(replayed), 다른 결정은 409
    assert _human_decide(world, rid, "allow").json()["replayed"] is True
    assert _human_decide(world, rid, "deny").status_code == 409


def test_a_cli_an_agent_a_machine_token_and_anonymous_can_never_approve(world, cli_actor):
    import host_store
    from authz import principals as P
    rid = _change(world, "allow-control", "pv").json()["requestId"]
    world.set_mode("password")
    for who in ("machine", "agent_verified", "none"):
        r = client().post(f"/api/agent/share/requests/{rid}/decision", json={"decision": "allow"},
                          headers=world.headers_for(who, "POST", "/x", b""))
        assert r.status_code in (401, 403), (who, r.status_code)
    from authz import host_grant_svc
    from authz.errors import ShareError
    for p in (P.cli(501, 1), P.agent("$1:1001"), P.legacy_token(), None):
        with pytest.raises(ShareError) as e:
            host_grant_svc.decide_request(rid, "allow", None, p)
        assert e.value.code == "human_session_required"
    assert host_store.find_grant("pv")["level"] == host_store.LEVEL_VIEW


def test_a_denied_request_cools_down_for_five_minutes(world, cli_actor):
    rid = _change(world, "allow-agent", "pv").json()["requestId"]
    assert _human_decide(world, rid, "deny", "session").status_code == 200
    r = _change(world, "allow-agent", "pv")
    assert r.status_code == 403 and r.json()["error"] == "request_denied" and r.json()["retryAfter"] > 0


def test_allow_agent_is_approved_by_a_plain_human_and_turns_the_switch_on(world, cli_actor):
    import host_store
    rid = _change(world, "allow-agent", "pc").json()["requestId"]
    r = _human_decide(world, rid, "allow", "session")
    assert r.status_code == 200, r.text
    assert host_store.find_grant("pc")["agentMessages"] is True
    assert _change(world, "deny-agent", "pc").json()["state"] == "applied"
    assert not host_store.find_grant("pc").get("agentMessages")


def test_http_callers_and_unknown_peers_cannot_use_the_change_route(world, cli_actor):
    from authz import principals as P
    cli_actor["p"] = P.human("dev1")
    r = _change(world, "deny-control", "pc")
    assert r.status_code == 403 and r.json()["error"] == "local_caller_required"
    cli_actor["p"] = P.cli(1, 2)
    assert _change(world, "deny-control", "ghost").status_code == 404
    assert _change(world, "bogus", "pc").status_code == 400


def test_the_route_tells_the_cli_to_fall_back_when_the_flag_is_off(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    r = _change(world, "deny-control", "pc")
    assert r.status_code == 409 and r.json()["error"] == "peer_enforce_off"


def test_the_store_mirror_follows_a_change_and_verify_stays_clean(world, cli_actor):
    from authz import migrate, store
    migrate.apply(tmux={})
    assert migrate.verify_store(tmux={})["clean"]
    assert _change(world, "deny-control", "pc").json()["state"] == "applied"
    rid = _change(world, "allow-agent", "pv").json()["requestId"]
    assert _human_decide(world, rid, "allow", "session").status_code == 200
    v = migrate.verify_store(tmux={})
    assert v["clean"], v["diff"]
    items = store.snapshot().grants
    pv = {g["origin"]: g for g in items if str(g.get("origin", "")).startswith("legacy:hosts:pv")}
    assert "legacy:hosts:pv:agentMessages" in pv
    assert pv["legacy:hosts:pv:agentMessages"]["approved_via"] == "manual"


def test_rollback_flag_off_after_on_keeps_the_new_state_and_the_old_checks(world, cli_actor, monkeypatch):
    rid = _change(world, "allow-control", "pv").json()["requestId"]
    assert _human_decide(world, rid, "allow", "session_elev").status_code == 200
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")                    # 끈다
    st, j = _run(world, "peer_view", "POST", "/api/peer/sessions/kill", {"name": "other"})
    assert st == 200                                              # pv 는 이제 control — 옛 등급 검사가 같은 결과를 낸다
    assert _change(world, "deny-control", "pv").status_code == 409


# --- CLI (host_store._cli) ------------------------------------------------------------------------


def test_cli_falls_back_to_the_direct_write_without_a_socket(world, capsys):
    import host_store
    assert host_store._cli(["allow-control", "pv"]) == 0
    assert host_store.find_grant("pv")["level"] == host_store.LEVEL_CONTROL


def _fake_socket(monkeypatch, status=None, body=None, exc=None):
    import fsh_client
    calls = []

    def req(method, path, **kw):
        calls.append((method, path, json.loads(kw["body"].decode())))
        if exc:
            raise exc
        return status, json.dumps(body).encode()

    monkeypatch.setattr(fsh_client, "socket_request", req)
    return calls


def test_cli_goes_through_the_server_and_prints_that_a_request_was_made(world, monkeypatch, capsys):
    import host_store
    calls = _fake_socket(monkeypatch, 202, {"ok": True, "state": "pending", "requestId": "pr-1", "peer": {"id": "pv"}})
    assert host_store._cli(["allow-control", "pv"]) == 0
    out = capsys.readouterr().out
    assert calls == [("POST", "/api/host-grants/pv/change", {"action": "allow-control"})]
    assert "승인" in out or "approval" in out.lower()
    assert host_store.find_grant("pv")["level"] == host_store.LEVEL_VIEW     # 직접 쓰지 않았다


def test_cli_does_not_write_directly_when_the_server_refuses(world, monkeypatch, capsys):
    import host_store
    _fake_socket(monkeypatch, 429, {"error": "too_many_requests", "reason": "x"})
    assert host_store._cli(["allow-control", "pv"]) == 1
    assert host_store.find_grant("pv")["level"] == host_store.LEVEL_VIEW


def test_cli_does_not_retry_directly_after_a_dropped_connection(world, monkeypatch, capsys):
    import host_store
    _fake_socket(monkeypatch, exc=OSError("boom"))
    assert host_store._cli(["allow-control", "pv"]) == 1
    assert host_store.find_grant("pv")["level"] == host_store.LEVEL_VIEW


@pytest.mark.parametrize("status,body", [(409, {"error": "peer_enforce_off"}), (403, {"error": "socket_route_not_allowed"})])
def test_cli_falls_back_when_the_server_does_not_take_requests(world, monkeypatch, capsys, status, body):
    import host_store
    _fake_socket(monkeypatch, status, body)
    assert host_store._cli(["allow-control", "pv"]) == 0
    assert host_store.find_grant("pv")["level"] == host_store.LEVEL_CONTROL


def test_cli_falls_back_when_there_is_no_socket(world, monkeypatch, capsys):
    import fsh_client
    import host_store

    def boom(*a, **k):
        raise fsh_client.SocketUnavailable("none")

    monkeypatch.setattr(fsh_client, "socket_request", boom)
    assert host_store._cli(["allow-agent", "pv"]) == 0
    assert host_store.find_grant("pv")["agentMessages"] is True


def test_the_socket_allow_table_has_the_change_route():
    from authz import sock
    assert sock.route_allowed("POST", "/api/host-grants/pv/change")
    assert not sock.route_allowed("GET", "/api/host-grants/pv/change")
