"""승인함·권한·감사 API(`/api/authz/*`) — 호출자별 경계, 결정(allow/deny/later × once/session), 옛 라우트와의 일치, 이벤트, 비상 정지.

가짜 tmux 세계(`World`)에서 돈다. 플래그 꺼짐이 기본이고(엔진 집행 없이도 안전해야 한다), 켜짐이 필요한 성질은 그 시험이 직접 켠다.
세션: agent-a(요청자) · target(공유 `shell`, readwrite) · other · child-b.
"""

from __future__ import annotations

import json
import time

import pytest
from starlette.testclient import TestClient

from authz import audit, denylog, inbox, store


def call(world, caller, method, path, body=None, mode="password", extra=None):
    import main
    world.set_mode(mode)
    c = TestClient(main.app, raise_server_exceptions=False)
    data = json.dumps(body).encode() if body is not None else b""
    h = world.headers_for(caller, method, path, data)
    h.update(extra or {})
    if body is not None:
        h["Content-Type"] = "application/json"
    return c.request(method, path, content=data or None, headers=h)


def share_request(world) -> str:
    """agent-a 가 shell 을 읽으려다 만든 보류 요청 id."""
    r = call(world, "agent_verified", "POST", "/api/agent/shared/read", {"nickname": "shell", "lines": 5})
    assert r.status_code == 202, r.text
    return r.json()["requestId"]


def generic_request(world, who="agent_verified", **over):
    body = {"resource": "session:local/$2:1002", "scopes": ["session.read"], "reason": "need the log", "ttl": 600, **over}
    return call(world, who, "POST", "/api/authz/requests", body)


# --- 1. 호출자 경계 ---------------------------------------------------------------------------------

READS = ["/api/authz/approvals", "/api/authz/grants", "/api/authz/audit"]


@pytest.mark.parametrize("path", READS)
def test_reads_are_for_a_signed_in_human_only_over_tcp(world, path):
    assert call(world, "session", "GET", path).status_code == 200
    assert call(world, "session_elev", "GET", path).status_code == 200
    for who in ("machine", "agent_verified", "agent_foreign", "peer_control"):
        r = call(world, who, "GET", path)
        assert r.status_code in (401, 403) and r.json().get("error") in ("human_session_required", "unauthorized"), (who, r.text)
    assert call(world, "none", "GET", path).status_code == 401


def test_mutations_are_human_only(world):
    rid = share_request(world)
    for who in ("machine", "agent_verified", "agent_foreign"):
        r = call(world, who, "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow"})
        assert r.status_code == 403 and r.json()["error"] == "human_session_required", who
        r = call(world, who, "DELETE", "/api/authz/grants/ag-x")
        assert r.status_code == 403 and r.json()["error"] == "human_session_required", who
        r = call(world, who, "POST", "/api/authz/panic")
        assert r.status_code == 403 and r.json()["error"] == "human_session_required", who
    assert call(world, "session", "GET", "/api/authz/approvals").json()["approvals"][0]["id"] == rid       # 아무것도 승인되지 않았다


def test_agent_side_routes_need_a_verified_pane(world):
    assert generic_request(world).status_code == 202
    for who in ("machine", "agent_foreign"):
        r = generic_request(world, who)
        assert r.status_code == 403 and r.json()["error"] == "identity_unverified", who
    for path in ("/api/authz/requests", "/api/authz/my-grants"):
        assert call(world, "agent_verified", "GET", path).status_code == 200
        assert call(world, "agent_foreign", "GET", path).status_code == 403
    assert call(world, "session", "POST", "/api/authz/requests", {"resource": "session:local/$2:1002", "scopes": ["session.read"]}).status_code == 403


def test_whoami_over_tcp_stays_socket_only(world):
    r = call(world, "agent_verified", "GET", "/api/authz/whoami")
    assert r.status_code == 403 and r.json()["error"] == "socket_only"


# --- 2. 승인함 목록 -----------------------------------------------------------------------------------


def test_unified_list_has_share_and_generic_with_preset_scopes_and_no_content(world):
    rid = share_request(world)
    g = generic_request(world).json()["requestId"]
    items = call(world, "session", "GET", "/api/authz/approvals").json()["approvals"]
    assert [i["id"] for i in items] == [rid, g] and [i["kind"] for i in items] == ["share", "generic"]
    s, gen = items
    assert s["scopes"] == ["session.read", "session.view"] and s["preset"] == {"id": "view", "label": "보기", "exact": True}
    assert s["subject"]["name"] == "agent-a" and s["resource"]["label"] == "shell" and s["elevated"] == []
    assert s["state"] == "pending" and s["age"] >= 0 and s["expiresIn"] > 0 and s["lineage"] is None
    assert gen["reason"] == "need the log" and gen["ttl"] == 600 and gen["scopes"] == ["session.read"]
    assert "fake screen line" not in json.dumps(items)


def test_write_request_shows_line_input_and_a_covering_preset(world):
    r = call(world, "agent_verified", "POST", "/api/agent/shared/send", {"nickname": "shell", "text": "ls"})
    assert r.status_code == 202
    it = call(world, "session", "GET", "/api/authz/approvals").json()["approvals"][0]
    assert "session.input" in it["scopes"] and it["conds"]["session.input"]["modes"] == ["line"]
    assert it["preset"]["id"] == "drive" and it["preset"]["exact"] is False
    assert it["elevated"] == []                                       # line 입력은 elevated 가 아니다


def test_raw_input_request_is_marked_elevated(world):
    g = generic_request(world, scopes=["session.input"], conds={"session.input": {"modes": ["raw"]}})
    assert g.status_code == 202
    it = call(world, "session", "GET", "/api/authz/approvals").json()["approvals"][0]
    assert it["elevated"] == ["session.input[raw]"]
    # 비elevated 사람은 승인할 수 없다 — 403, 요청은 그대로
    r = call(world, "session", "POST", f"/api/authz/approvals/{it['id']}/decision", {"decision": "allow"})
    assert r.status_code == 403 and r.json()["error"] == "elevation_required"
    assert call(world, "session_elev", "POST", f"/api/authz/approvals/{it['id']}/decision",
                {"decision": "allow", "choice": "once"}).status_code == 200


# --- 3. 결정 ----------------------------------------------------------------------------------------


def test_decision_input_validation(world):
    rid = share_request(world)
    path = f"/api/authz/approvals/{rid}/decision"
    for body, err in (({}, "bad_decision"), ({"decision": "maybe"}, "bad_decision"), ({"decision": "allow", "choice": "forever"}, "bad_choice"),
                      ({"decision": "allow", "choice": "deny"}, "bad_choice"), ({"decision": "deny", "choice": "once"}, "bad_choice"),
                      ({"decision": "allow", "ttl": "x"}, "bad_ttl"), ({"decision": "allow", "ttl": 5}, "bad_ttl"),
                      ({"decision": "allow", "scopes": "session.read"}, "bad_scopes")):
        r = call(world, "session", "POST", path, body)
        assert r.status_code == 400 and r.json()["error"] == err, (body, r.text)
    assert call(world, "session", "POST", "/api/authz/approvals/nope/decision", {"decision": "allow"}).status_code == 404


def test_later_changes_nothing(world):
    rid = share_request(world)
    r = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "later"})
    assert r.status_code == 200 and r.json()["state"] == "pending" and r.json()["later"] is True
    assert [i["id"] for i in call(world, "session", "GET", "/api/authz/approvals").json()["approvals"]] == [rid]
    assert call(world, "session", "POST", "/api/authz/approvals/zzz/decision", {"decision": "later"}).status_code == 404


def test_share_allow_session_via_new_route_matches_the_old_route(world):
    rid = share_request(world)
    r = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "choice": "session"})
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] is True and b["kind"] == "share" and b["state"] == "allowed" and b["id"] == rid and b["grant"]["read"] is True
    # 옛 라우트의 grants 목록이 같은 권한을 본다 (같은 저장소)
    old = call(world, "session", "GET", "/api/agent/share/grants").json()["grants"]
    assert [g["id"] for g in old] == [b["grant"]["id"]]
    assert call(world, "agent_verified", "POST", "/api/agent/shared/read", {"nickname": "shell", "lines": 5}).status_code == 200


def test_share_deny_starts_the_cooldown(world):
    rid = share_request(world)
    r = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "deny"})
    assert r.status_code == 200 and r.json()["state"] == "denied" and r.json()["grant"] is None
    again = call(world, "agent_verified", "POST", "/api/agent/shared/read", {"nickname": "shell", "lines": 5})
    assert again.status_code == 403 and again.json()["error"] == "request_denied"
    # choice 만으로도 거절
    world.tmux.calls.clear()


def test_share_choice_deny_alone_means_deny(world):
    rid = share_request(world)
    r = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"choice": "deny"})
    assert r.status_code == 200 and r.json()["state"] == "denied"


def test_share_narrowed_scopes_give_read_only(world):
    r = call(world, "agent_verified", "POST", "/api/agent/shared/send", {"nickname": "shell", "text": "ls"})
    rid = r.json()["requestId"]
    ok = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision",
              {"decision": "allow", "scopes": ["session.view", "session.read"]})
    assert ok.status_code == 200 and ok.json()["grant"]["write"] is False
    bad = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "scopes": ["fs.write"]})
    assert bad.status_code in (400, 404)


def test_once_and_ttl_on_a_share_request_need_the_share_flag(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    rid = share_request(world)
    for body in ({"decision": "allow", "choice": "once"}, {"decision": "allow", "ttl": 600}):
        r = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", body)
        assert r.status_code == 409 and r.json()["error"] == "choice_unavailable"
    assert [i["id"] for i in call(world, "session", "GET", "/api/authz/approvals").json()["approvals"]] == [rid]


def test_once_on_a_share_request_with_the_flag_reads_exactly_once(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share")
    rid = share_request(world)
    r = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "choice": "once"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["grantId"].startswith("gg-") and b["grant"]["maxUses"] == 1 and b["grant"]["kind"] == "share"
    read = {"nickname": "shell", "lines": 5}
    assert call(world, "agent_verified", "POST", "/api/agent/shared/read", read).status_code == 200        # 한 번
    second = call(world, "agent_verified", "POST", "/api/agent/shared/read", read)
    assert second.status_code == 202                                                                           # 다시 묻는다
    g = [x for x in call(world, "session", "GET", "/api/authz/grants").json()["grants"] if x["id"] == b["grantId"]]
    assert not g or g[0]["uses"] == 1                                                                        # 소진된 grant 는 목록에서 사라질 수 있다
    # 같은 결정의 재전송은 처음 결과 그대로
    again = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "choice": "once"})
    assert again.status_code == 200 and again.json().get("replayed") is True


def test_ttl_grant_expires(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share")
    rid = share_request(world)
    r = call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "ttl": 120})
    assert r.status_code == 200 and r.json()["grant"]["expires"] is not None
    read = {"nickname": "shell", "lines": 5}
    assert call(world, "agent_verified", "POST", "/api/agent/shared/read", read).status_code == 200
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    assert call(world, "agent_verified", "POST", "/api/agent/shared/read", read).status_code == 202


def test_first_decision_wins_and_replays_are_idempotent_with_the_flag(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share")
    rid = share_request(world)
    path = f"/api/authz/approvals/{rid}/decision"
    first = call(world, "session", "POST", path, {"decision": "allow"})
    assert first.status_code == 200 and not first.json().get("replayed")
    again = call(world, "session", "POST", path, {"decision": "allow"})
    assert again.status_code == 200 and again.json()["replayed"] is True
    other = call(world, "session", "POST", path, {"decision": "deny"})
    assert other.status_code == 409 and other.json()["error"] == "already_decided"


def test_generic_allow_once_session_and_deny(world):
    a = generic_request(world).json()["requestId"]
    r = call(world, "session", "POST", f"/api/authz/approvals/{a}/decision", {"decision": "allow", "choice": "once"})
    assert r.status_code == 200 and r.json()["kind"] == "generic" and r.json()["grant"]["maxUses"] == 1
    gid = r.json()["grantId"]
    assert gid.startswith("gg-") and next(g for g in store.snapshot().grants if g["id"] == gid)["conds"] == {"max_uses": 1}
    # 세션 선택: 요청자·리소스 세션에 묶인다 (만료 없음), TTL 이 있으면 그 TTL
    b = generic_request(world, scopes=["session.view"]).json()["requestId"]
    s = call(world, "session", "POST", f"/api/authz/approvals/{b}/decision", {"decision": "allow", "choice": "session"}).json()
    rec = next(x for x in store.snapshot().grants if x["id"] == s["grantId"])
    assert rec["subject_session"] and rec["resource_session"] == "$2:1002" and rec["expires"] is not None and rec["conds"] == {}
    c = generic_request(world, scopes=["session.view", "msg.send"], resource="session:local/$2:1002").json()["requestId"]
    d = call(world, "session", "POST", f"/api/authz/approvals/{c}/decision", {"decision": "deny"})
    assert d.status_code == 200 and d.json()["state"] == "denied"
    redo = generic_request(world, scopes=["session.view", "msg.send"])
    assert redo.status_code == 403 and redo.json()["error"] == "request_denied" and redo.json()["retryAfter"] > 0


def test_generic_narrowing_only(world):
    a = generic_request(world, scopes=["session.read", "session.view"], ttl=600).json()["requestId"]
    wide = call(world, "session", "POST", f"/api/authz/approvals/{a}/decision", {"decision": "allow", "scopes": ["session.read", "fs.read"]})
    assert wide.status_code == 400 and wide.json()["error"] == "wider_than_requested"
    longer = call(world, "session", "POST", f"/api/authz/approvals/{a}/decision", {"decision": "allow", "ttl": 900})
    assert longer.status_code == 400 and longer.json()["error"] == "wider_than_requested"
    ok = call(world, "session", "POST", f"/api/authz/approvals/{a}/decision", {"decision": "allow", "scopes": ["session.read"], "ttl": 300})
    assert ok.status_code == 200
    rec = next(x for x in store.snapshot().grants if x["id"] == ok.json()["grantId"])
    assert rec["scopes"] == ["session.read"] and rec["expires"] - rec["created"] == 300


def test_generic_replay_and_conflict(world):
    a = generic_request(world).json()["requestId"]
    path = f"/api/authz/approvals/{a}/decision"
    first = call(world, "session", "POST", path, {"decision": "allow"}).json()
    again = call(world, "session", "POST", path, {"decision": "allow"})
    assert again.status_code == 200 and again.json()["replayed"] is True and again.json()["grantId"] == first["grantId"]
    assert call(world, "session", "POST", path, {"decision": "deny"}).status_code == 409
    assert len([g for g in store.snapshot().grants if g["id"].startswith("gg-")]) == 1                 # grant 는 하나


def test_msg_and_peer_requests_are_listed_and_decided_through_the_same_route(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg,peer")
    from authz import host_grant_svc
    from authz import principals as P
    r = call(world, "agent_verified", "POST", "/api/agent/message", {"to": "local/target", "text": "hello", "pane": "%1", "tmux": "x"})
    assert r.status_code == 202 and r.json()["requestId"].startswith("ms-")
    mid = r.json()["requestId"]
    host_grant_svc.change(P.cli(1, 2), "pv", "allow-control")
    items = call(world, "session", "GET", "/api/authz/approvals").json()["approvals"]
    kinds = {i["kind"]: i for i in items}
    assert set(kinds) == {"msg", "peer"} and kinds["msg"]["scopes"] == ["msg.send"] and kinds["msg"]["preset"]["id"] == "collab"
    assert kinds["peer"]["change"] == "allow-control" and kinds["peer"]["elevated"] != []     # control 등급은 어느 프리셋에도 딱 맞지 않아도 elevated 로 표시된다
    pid = kinds["peer"]["id"]
    # msg/peer 는 한 번만·기간·범위 조정을 받지 않는다
    for rid in (mid, pid):
        r = call(world, "session_elev", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow", "choice": "once"})
        assert r.status_code == 400 and r.json()["error"] == "choice_unsupported"
    assert call(world, "session", "POST", f"/api/authz/approvals/{mid}/decision", {"decision": "allow", "choice": "session"}).json()["kind"] == "msg"
    # peer control 은 elevated 필요
    assert call(world, "session", "POST", f"/api/authz/approvals/{pid}/decision", {"decision": "allow"}).status_code == 403
    done = call(world, "session_elev", "POST", f"/api/authz/approvals/{pid}/decision", {"decision": "allow"})
    assert done.status_code == 200 and done.json()["kind"] == "peer" and done.json()["state"] == "allowed"


# --- 4. 권한 목록 · 회수 ---------------------------------------------------------------------------------


def test_grants_list_filters_and_provenance(world):
    rid = share_request(world)
    call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow"})
    gq = generic_request(world, scopes=["session.view"]).json()["requestId"]
    call(world, "session", "POST", f"/api/authz/approvals/{gq}/decision", {"decision": "allow", "choice": "once"})
    allg = call(world, "session", "GET", "/api/authz/grants").json()["grants"]
    kinds = [g["kind"] for g in allg]
    assert "share" in kinds and "generic" in kinds and kinds.count("peer") == 4          # pg-pv · pg-pc · pg-pca · pm-pca
    assert [g["kind"] for g in call(world, "session", "GET", "/api/authz/grants?kind=generic").json()["grants"]] == ["generic"]
    sh = call(world, "session", "GET", "/api/authz/grants?kind=share").json()["grants"][0]
    assert sh["id"].startswith("ag-") and sh["preset"]["id"] == "view" and sh["subject"]["name"] == "agent-a"
    assert sh["resource"].startswith("session:local/") and "approvedBy" in sh and sh["maxUses"] is None
    gen = call(world, "session", "GET", "/api/authz/grants?kind=generic").json()["grants"][0]
    assert gen["approvedBy"].startswith("human:") and gen["approvedVia"] == "manual" and gen["maxUses"] == 1 and gen["uses"] == 0
    assert call(world, "session", "GET", "/api/authz/grants?subject=agent-a").json()["grants"]
    assert not call(world, "session", "GET", "/api/authz/grants?subject=nobody-xyz").json()["grants"]
    assert call(world, "session", "GET", "/api/authz/grants?resource=session:local/$2").json()["grants"]
    peer = call(world, "session", "GET", "/api/authz/grants?kind=peer").json()["grants"]
    assert {p["id"] for p in peer} >= {"pg-pv", "pg-pc"} and "secret" not in json.dumps(peer).lower()


def test_revoke_share_all_and_write_only_via_the_old_code_path(world):
    r = call(world, "agent_verified", "POST", "/api/agent/shared/send", {"nickname": "shell", "text": "ls"})
    call(world, "session", "POST", f"/api/authz/approvals/{r.json()['requestId']}/decision", {"decision": "allow"})
    gid = call(world, "session", "GET", "/api/authz/grants?kind=share").json()["grants"][0]["id"]
    w = call(world, "session", "DELETE", f"/api/authz/grants/{gid}?scopes=session.input")
    assert w.status_code == 200 and w.json()["kind"] == "share"
    left = call(world, "session", "GET", "/api/authz/grants?kind=share").json()["grants"][0]
    assert "session.input" not in left["scopes"] and "session.read" in left["scopes"]          # 읽기는 남는다
    assert call(world, "session", "DELETE", f"/api/authz/grants/{gid}").status_code == 200
    assert call(world, "session", "GET", "/api/authz/grants?kind=share").json()["grants"] == []
    assert call(world, "session", "DELETE", f"/api/authz/grants/{gid}").status_code == 404
    assert call(world, "agent_verified", "POST", "/api/agent/shared/read", {"nickname": "shell"}).status_code == 202


def test_revoke_generic_whole_and_partial(world):
    q = generic_request(world, scopes=["session.view", "session.read"]).json()["requestId"]
    gid = call(world, "session", "POST", f"/api/authz/approvals/{q}/decision", {"decision": "allow"}).json()["grantId"]
    p = call(world, "session", "DELETE", f"/api/authz/grants/{gid}?scopes=session.read")
    assert p.status_code == 200 and next(x for x in store.snapshot().grants if x["id"] == gid)["scopes"] == ["session.view"]
    assert call(world, "session", "DELETE", f"/api/authz/grants/{gid}?scopes=session.view").status_code == 200
    assert not [x for x in store.snapshot().grants if x["id"] == gid]
    assert call(world, "session", "DELETE", f"/api/authz/grants/{gid}?scopes=bogus.scope").status_code == 400


def test_revoke_peer_grant_lowers_it_like_fsh_host_deny(world):
    import host_store
    assert host_store.find_grant("pc")["level"] == "control"
    r = call(world, "session", "DELETE", "/api/authz/grants/pg-pc")
    assert r.status_code == 200 and r.json()["kind"] == "peer"
    assert host_store.find_grant("pc")["level"] == "view"
    assert call(world, "session", "DELETE", "/api/authz/grants/pm-pca").status_code == 200
    assert not host_store.find_grant("pca").get("agentMessages")
    assert call(world, "session", "DELETE", "/api/authz/grants/pg-nobody").status_code == 404


# --- 5. 비상 정지 -------------------------------------------------------------------------------------------


def test_panic_needs_elevation_and_revokes_everything_non_human(world):
    rid = share_request(world)
    call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow"})
    pending_share = call(world, "agent_verified", "POST", "/api/agent/shared/send", {"nickname": "shell", "text": "ls"}).json()["requestId"]
    gq = generic_request(world, scopes=["session.view"]).json()["requestId"]
    call(world, "session", "POST", f"/api/authz/approvals/{gq}/decision", {"decision": "allow"})
    pending_generic = generic_request(world, scopes=["session.read", "session.view"]).json()["requestId"]
    r = call(world, "session", "POST", "/api/authz/panic")
    assert r.status_code == 401 and r.json()["error"] == "elevation_required"
    assert call(world, "session", "GET", "/api/authz/grants?kind=share").json()["grants"]                    # 아무것도 안 거둬졌다
    r = call(world, "session_elev", "POST", "/api/authz/panic")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["failed"] == []
    assert r.json()["revoked"]["share"] == 1 and r.json()["revoked"]["generic"] == 1 and r.json()["revoked"]["peer"] >= 1
    assert call(world, "session", "GET", "/api/authz/approvals").json()["approvals"] == []
    gs = call(world, "session", "GET", "/api/authz/grants").json()["grants"]
    assert [g["kind"] for g in gs if g["kind"] != "peer"] == []
    import host_store
    assert host_store.find_grant("pc")["level"] == "view" and not host_store.find_grant("pca").get("agentMessages")
    assert host_store.find_grant("pc") is not None                         # 등록(페어링)은 남는다
    assert pending_share and pending_generic
    chain = [e for e in audit.read_recent(50) if e["action"] == "authz.panic"]
    assert chain and chain[0]["decision"] == "allow"


# --- 6. 요청 생성 한도 -------------------------------------------------------------------------------------------


def test_request_validation(world):
    for over, err in (({"scopes": []}, "bad_scopes"), ({"scopes": ["nope.nope"]}, "bad_scopes"),
                      ({"scopes": ["authz.admin"]}, "scope_not_requestable"), ({"scopes": ["pair.manage"]}, "scope_not_requestable"),
                      ({"scopes": ["authz.request"]}, "scope_not_requestable"), ({"resource": "weird:thing"}, "bad_resource"),
                      ({"resource": ""}, "bad_resource"), ({"ttl": 1}, "bad_ttl"), ({"ttl": 99999999}, "bad_ttl"),
                      ({"reason": 5}, "bad_reason"), ({"conds": {"session.read": {"x": ["y"]}}}, "bad_conds")):
        r = generic_request(world, **over)
        assert r.status_code == 400 and r.json()["error"] == err, (over, r.text)
    assert store.snapshot().approvals == []


def test_duplicate_requests_collapse_and_return_the_same_id(world):
    a = generic_request(world)
    b = generic_request(world)
    assert a.status_code == b.status_code == 202 and a.json()["requestId"] == b.json()["requestId"]
    assert a.json()["created"] is True and b.json()["created"] is False and a.json()["kind"] == "generic"
    assert a.json()["state"] == "pending" and "승인" in a.json()["reason"]
    mine = call(world, "agent_verified", "GET", "/api/authz/requests").json()["requests"]
    assert [x["requestId"] for x in mine] == [a.json()["requestId"]] and mine[0]["state"] == "pending"


def test_six_requests_per_minute_per_principal(world):
    scopes = ["session.view", "session.read", "msg.send", "fs.read", "browser", "deleg.spawn", "session.manage"]
    codes = [generic_request(world, scopes=[s]).status_code for s in scopes]
    assert codes == [202] * 6 + [429]
    r = generic_request(world, scopes=["agent.report"])
    assert r.status_code == 429 and r.json()["error"] == "rate_limited" and r.json()["retryAfter"] == 60
    inbox.reset_rate()
    assert generic_request(world, scopes=["agent.report"]).status_code == 202


def test_pending_caps_per_principal_and_total(world, monkeypatch):
    monkeypatch.setattr(inbox, "RATE_PER_MIN", 1000)
    monkeypatch.setattr(inbox, "PER_PRINCIPAL", 3)
    monkeypatch.setattr(inbox, "TOTAL", 4)
    sc = ["session.view", "session.read", "msg.send", "fs.read", "browser", "deleg.spawn"]
    codes = [generic_request(world, scopes=[s]).status_code for s in sc[:4]]
    assert codes == [202, 202, 202, 429]
    # 다른 주체(child-b)는 주체당 한도에 안 걸리지만 전체 한도(4)에는 걸린다
    assert generic_request(world, "agent_child", scopes=[sc[4]]).status_code == 202
    assert generic_request(world, "agent_child", scopes=[sc[5]]).status_code == 429


def test_agent_reason_text_is_not_written_to_the_audit(world):
    generic_request(world, reason="SECRET-REASON-" + "9x7")
    rows = [r for r in audit.read_recent(20) if r["action"] == "authz.request"]
    assert rows and rows[0]["sizes"]["chars"] > 0 and "SECRET-REASON" not in json.dumps(rows)
    assert "SECRET-REASON" not in (audit.path().read_text() + (denylog.path().read_text() if denylog.path().exists() else ""))
    items = call(world, "session", "GET", "/api/authz/approvals").json()["approvals"]
    assert items[0]["reason"].startswith("SECRET-REASON")          # 승인 화면에는 보인다


def test_child_request_carries_the_parent_delegation_lineage(world):
    r = generic_request(world, "agent_child")
    assert r.status_code == 202
    it = call(world, "session", "GET", "/api/authz/approvals").json()["approvals"][0]
    assert it["lineage"] == {"delegation": "dseed00001"}


def test_flags_off_means_a_generic_grant_enforces_nothing_by_itself(world, monkeypatch):
    """flags 꺼짐: 승인된 generic grant 도 집행 지점이 엔진을 부르기 전에는 아무 응답도 바꾸지 않는다(공유 읽기는 여전히 보류)."""
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    gq = generic_request(world).json()["requestId"]
    call(world, "session", "POST", f"/api/authz/approvals/{gq}/decision", {"decision": "allow"})
    r = call(world, "agent_verified", "POST", "/api/agent/shared/read", {"nickname": "shell"})
    assert r.status_code == 202


# --- 7. 감사 조회 · 이벤트 ---------------------------------------------------------------------------------------


def _chain_lines(world):
    gq = generic_request(world).json()["requestId"]
    call(world, "session", "POST", f"/api/authz/approvals/{gq}/decision", {"decision": "allow"})


def test_audit_merges_chain_and_denials_and_verifies_the_chain(world):
    _chain_lines(world)
    denylog.record({"principal": "agent:local/x", "action": "share.send", "resource": "session:local/y", "decision": "deny",
                    "result": "denied", "reason": "ceiling", "text": "SHOULD-NOT-APPEAR"})
    b = call(world, "session", "GET", "/api/authz/audit?limit=50").json()
    assert b["chain"] == "ok" and b["lines"] >= 2 and b["brokenSeq"] is None
    srcs = {e["source"] for e in b["entries"]}
    assert srcs == {"chain", "denials"}
    assert "SHOULD-NOT-APPEAR" not in json.dumps(b)
    ts = [e["ts"] for e in b["entries"]]
    assert ts == sorted(ts, reverse=True)
    f = call(world, "session", "GET", "/api/authz/audit?principal=agent:local/x").json()["entries"]
    assert f and all(e["principal"].startswith("agent:local/x") for e in f)
    assert call(world, "session", "GET", "/api/authz/audit?resource=session:local/y").json()["entries"][0]["source"] == "denials"
    assert call(world, "session", "GET", f"/api/authz/audit?since={time.time() + 100}").json()["entries"] == []
    assert len(call(world, "session", "GET", "/api/authz/audit?limit=1").json()["entries"]) == 1
    assert call(world, "session", "GET", "/api/authz/audit?limit=0").status_code == 422


def test_audit_reports_a_broken_chain(world):
    _chain_lines(world)
    assert call(world, "session", "GET", "/api/authz/audit").json()["chain"] == "ok"
    p = audit.path()
    lines = p.read_text().splitlines()
    assert len(lines) >= 2
    rec = json.loads(lines[0])
    rec["principal"] = "human:tampered"
    p.write_text("\n".join([json.dumps(rec, sort_keys=True, ensure_ascii=False)] + lines[1:]) + "\n")
    b = call(world, "session", "GET", "/api/authz/audit").json()
    assert b["chain"] == "broken" and b["brokenSeq"] is not None


def test_events_are_content_free_and_old_events_still_flow(world):
    import main
    world.set_mode("password")
    human = TestClient(main.app, raise_server_exceptions=False)
    h = world.headers_for("session", "GET", "/ws-workspace", b"")
    with human.websocket_connect("/ws-workspace", headers=h) as ws:
        assert ws.receive_json()["type"] == "workspace_snapshot"
        assert ws.receive_json()["type"] == "tmux_sessions"
        rid = share_request(world)
        assert ws.receive_json()["type"] == "agent_share_request"
        assert ws.receive_json() == {"type": "authz_request", "id": rid, "kind": "share"}
        gq = generic_request(world).json()["requestId"]
        assert ws.receive_json() == {"type": "authz_request", "id": gq, "kind": "generic"}      # generic 은 옛 모양이 없어 새 이벤트만
        call(world, "session", "POST", f"/api/authz/approvals/{rid}/decision", {"decision": "allow"})
        assert ws.receive_json() == {"type": "agent_share_request_resolved", "id": rid, "state": "allowed"}
        assert ws.receive_json() == {"type": "authz_resolved", "id": rid, "kind": "share", "state": "allowed"}
        gid = call(world, "session", "GET", "/api/authz/grants?kind=share").json()["grants"][0]["id"]
        call(world, "session", "DELETE", f"/api/authz/grants/{gid}")
        assert ws.receive_json() == {"type": "authz_revoked", "id": gid, "kind": "share"}
        call(world, "session", "POST", f"/api/authz/approvals/{gq}/decision", {"decision": "deny"})
        assert ws.receive_json() == {"type": "authz_resolved", "id": gq, "kind": "generic", "state": "denied"}


def test_old_share_routes_are_shims_with_unchanged_bodies(world):
    rid = share_request(world)
    old = call(world, "session", "GET", "/api/agent/share/requests").json()
    assert set(old) == {"requests"} and old["requests"][0]["id"] == rid and old["requests"][0]["kind"] == "read"
    dec = call(world, "session", "POST", f"/api/agent/share/requests/{rid}/decision", {"decision": "allow"}).json()
    assert set(dec) - {"grantId"} == {"ok", "request", "grant"}          # grantId 는 share 집행이 켜졌을 때만 더해지는 옛 키
    new_list = call(world, "session", "GET", "/api/authz/grants?kind=share").json()["grants"]
    assert [g["id"] for g in new_list] == [f"ag-{dec['grant']['id']}"]
    rv = call(world, "session", "DELETE", f"/api/agent/share/grants/{dec['grant']['id']}").json()
    assert set(rv) - {"grantId"} == {"ok", "grant"}
    assert call(world, "session", "GET", "/api/authz/grants?kind=share").json()["grants"] == []
