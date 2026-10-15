"""authz 엔진(plan-2.6 1단계) — 판정식·함의·상한·조건·확대·window, 주체, 저장소, 감사 체인."""

from __future__ import annotations

import json
import os
import stat

import pytest

from authz import audit, ceilings, defaults, engine, limits, principals as P, scopes as S, store


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    return tmp_path / "vt"


NOW = 1_000_000.0
A, B, C = "$1:1001", "$2:1002", "$3:1003"


def snap(grants=(), shares=None, corrupt=()):
    return store.Snapshot(grants=list(grants), shares=dict(shares or {}), corrupt=frozenset(corrupt), now=NOW)


def share_grant(scopes, **kw):
    return {"id": "g1", "subject": f"agent:local/{A}", "resource": f"session:local/{B}", "scopes": scopes,
            "subject_session": A, "resource_session": B, "state": "active", **kw}


# --- scope ------------------------------------------------------------------------------


def test_closure_has_only_the_declared_implications():
    assert S.closure(["session.input.raw"]) == {"session.input.raw", "session.input.line"}
    assert S.closure(["session.stream"]) == {"session.stream", "session.read"}
    assert S.closure(["fs.write"]) == {"fs.write", "fs.read"}
    assert "session.input.raw" not in S.closure(["session.input.line"])
    assert "session.stream" not in S.closure(["session.read"])
    assert S.closure(["nope.scope"]) == frozenset()
    assert S.closure(["relay.*"]) == {s for s in S.CATALOG if s.startswith("relay.")}


def test_relay_groups_cover_every_relay_policy_rule():
    import relay_policy
    assert {rx.pattern for rx, _ in relay_policy._HTTP_RULES} == set(S.RELAY_HTTP_GROUPS)
    assert {rx.pattern for rx in relay_policy._WS_RULES} == set(S.RELAY_WS_GROUPS)
    assert S.relay_group("POST", "/api/queue") == "relay.queue"
    assert S.relay_group("GET", "/api/files/x/share") is None          # 거부 목록
    assert S.relay_group("POST", "/api/git/status") is None             # 메서드 밖
    assert S.relay_ws_group("/ws/abc") == "relay.ws.term"


def test_unknown_scope_and_resource_are_denied():
    p = P.legacy_token()
    assert engine.decide(p, "session.teleport", "session:local/x").reason == "unknown_scope"
    assert engine.decide(p, "session.read", "galaxy:far").reason == "unknown_resource"


def test_requires_elevated_r43():
    assert S.requires_elevated("human", ["session.input.raw"]) == set()
    assert "session.input.raw" in S.requires_elevated("agent", ["session.input.raw"])
    assert S.requires_elevated("agent", ["session.read"]) == set()
    assert "session.input.line" in S.requires_elevated("peer", ["session.input.line"])
    assert S.requires_elevated("peer", ["session.read", "session.stream"]) == set()
    assert "deleg.spawn(remote)" in S.requires_elevated("peer", ["deleg.spawn"], "deleg-quota:peer:m5")


# --- 판정식 ---------------------------------------------------------------------------


def test_agent_owns_its_session_but_not_others():
    p = P.agent(A)
    assert engine.decide(p, "session.input.raw", f"session:local/{A}").allow
    d = engine.decide(p, "session.read", f"session:local/{B}", {"share": {"nick": "s", "mode": "read"}})
    assert (d.allow, d.reason) == (False, "no_grant")


@pytest.mark.parametrize("mode,read,line", [("read", True, False), ("readwrite", True, True), (None, False, False)])
def test_grant_is_capped_by_the_share_ceiling(mode, read, line):
    p = P.agent(A)
    g = share_grant(["session.list", "session.read", "session.input.line"])
    ctx = {"share": {"nick": "s", "mode": mode} if mode else None, "alive": {A, B}}
    assert engine.decide(p, "session.read", f"session:local/{B}", ctx, snap([g])).allow is read
    d = engine.decide(p, "session.input.line", f"session:local/{B}", ctx, snap([g]))
    assert d.allow is line
    if not line:
        assert d.reason == "ceiling" and d.grant_id == "g1"


def test_full_share_level_allows_raw_only_with_a_raw_grant():
    p = P.agent(A)
    ctx = {"share": {"nick": "s", "mode": "full"}}
    assert not engine.decide(p, "session.input.raw", f"session:local/{B}", ctx,
                             snap([share_grant(["session.input.line"])])).allow
    assert engine.decide(p, "session.input.raw", f"session:local/{B}", ctx,
                         snap([share_grant(["session.input.raw"])])).allow


def test_share_mismatch_resolves_to_the_lower_level_in_store_mode():
    p = P.agent(A)
    g = share_grant(["session.read", "session.input.line"])
    ctx = {"share": {"nick": "s", "mode": "readwrite"}}
    stored = {B: {"nick": "s", "level": "read"}}
    assert engine.decide(p, "session.input.line", f"session:local/{B}", ctx, snap([g], stored)).allow
    assert not engine.decide(p, "session.input.line", f"session:local/{B}", {**ctx, "prefer_live": False},
                             snap([g], stored)).allow
    # 판정 지점이 tmux 를 안 넘기면 저장소 값
    assert engine.decide(p, "session.read", f"session:local/{B}", {}, snap([g], stored)).allow


def test_liveness_drops_grants_of_dead_sessions_but_not_when_tmux_is_unreadable():
    p = P.agent(A)
    g = share_grant(["session.read"])
    ctx = {"share": {"nick": "s", "mode": "read"}}
    assert not engine.decide(p, "session.read", f"session:local/{B}", {**ctx, "alive": {A}}, snap([g])).allow
    assert engine.decide(p, "session.read", f"session:local/{B}", {**ctx, "alive": None}, snap([g])).allow


def test_time_window_state_and_deny_rules():
    p = P.agent(A)
    ctx = {"share": {"nick": "s", "mode": "read"}}
    r = f"session:local/{B}"
    assert not engine.decide(p, "session.read", r, ctx, snap([share_grant(["session.read"], expires=NOW)])).allow
    assert not engine.decide(p, "session.read", r, ctx, snap([share_grant(["session.read"], not_before=NOW + 1)])).allow
    assert not engine.decide(p, "session.read", r, ctx, snap([share_grant(["session.read"], state="revoking")])).allow
    deny = {"id": "d1", "effect": "deny", "subject": "agent:*", "resource": "session:local/*", "scopes": ["session.*"]}
    d = engine.decide(p, "session.read", r, ctx, snap([share_grant(["session.read"]), deny]))
    assert (d.allow, d.rule) == (False, "deny")


def test_store_corruption_fails_closed_for_agents_but_not_humans():
    g = share_grant(["session.read"])
    ctx = {"share": {"nick": "s", "mode": "read"}}
    d = engine.decide(P.agent(A), "session.read", f"session:local/{B}", ctx, snap([g], corrupt={"grants"}))
    assert (d.allow, d.reason) == (False, "store_corrupt")
    assert engine.decide(P.human("dev1"), "session.input.raw", f"session:local/{B}", {}, snap(corrupt={"grants"})).allow


@pytest.mark.parametrize("deleg,reason", [
    ({"enabled": False}, "spawn_disabled"),
    ({"enabled": True, "is_child": True}, "depth_limit"),
    ({"enabled": True, "open_children": 3}, "child_limit"),
    ({"enabled": True, "recent_spawns": 5}, "rate_limited"),
    ({"enabled": True, "open_children": 2, "recent_spawns": 4}, "ok"),
])
def test_deleg_spawn_quota(deleg, reason):
    p = P.agent(A)
    d = engine.decide(p, "deleg.spawn", f"deleg-quota:{p.id}", {"deleg": deleg})
    assert d.reason == reason and d.allow is (reason == "ok")
    # 남의 quota 는 못 쓴다
    assert not engine.decide(p, "deleg.spawn", f"deleg-quota:agent:local/{B}", {"deleg": {"enabled": True}}).allow


def test_deleg_constants_match_delegation_module():
    import delegation
    assert (limits.DELEG_MAX_OPEN, limits.DELEG_SPAWN_PER_MIN) == (delegation.MAX_OPEN_CHILDREN, delegation.SPAWN_PER_MIN)


def test_rate_counts_from_ctx():
    p = P.agent(A)
    r = f"session:local/{A}"
    assert engine.decide(p, "session.read", r, {"counts": {"session.read": 29}}).allow
    assert engine.decide(p, "session.read", r, {"counts": {"session.read": 30}}).reason == "rate_limited"


# --- 주체별 기본(A3)과 PROVISIONAL ---------------------------------------------------------


def test_legacy_token_is_the_full_rights_shim_minus_admin():
    p = P.legacy_token()
    assert engine.decide(p, "session.input.raw", f"session:local/{B}").allow
    assert engine.decide(p, "deleg.spawn", "deleg-quota:x").allow
    assert not engine.decide(p, "authz.admin", "authz:*").allow


def test_anon_depends_on_protection_d8():
    assert not engine.decide(P.anon(protected=True), "api.access", "host:local").allow
    assert engine.decide(P.anon(protected=False), "msg.send", "msg:local/x").allow is defaults.UNPROTECTED_ANON_FULL


def test_human_excludes_browser_js_and_admin_needs_elevation():
    assert not engine.decide(P.human("d"), "browser.js", "browser:tab:1").allow
    assert not engine.decide(P.human("d"), "authz.admin", "authz:*").allow
    assert engine.decide(P.human("d", elevated=True), "authz.admin", "authz:*").allow


def test_peer_defaults_and_migrated_presets():
    from authz import migrate
    pv, pc = P.peer("pv"), P.peer("pc")
    assert engine.decide(pv, "ping", "host:local").allow
    assert not engine.decide(pv, "session.read", "session:local/x").allow           # 기본은 ping 만
    gs = migrate.translate_peer_grant({"id": "pv", "level": "view"}) + \
        migrate.translate_peer_grant({"id": "pc", "level": "control", "agentMessages": True})
    s = snap(gs)
    assert engine.decide(pv, "session.stream", "session:local/x", {}, s).allow      # D3 (a)
    assert engine.decide(pv, "session.meta", "session:local/x", {}, s).allow
    assert not engine.decide(pv, "session.input.raw", "host:local", {}, s).allow
    assert not engine.decide(pv, "relay.connect", "relay:local", {}, s).allow
    assert engine.decide(pc, "relay.queue", "relay:local/queue", {}, s).allow        # D7 (a)
    assert engine.decide(pc, "fs.drop", "host:local", {}, s).allow
    assert not engine.decide(pc, "msg.send", "msg:local/-", {}, s).allow             # obo 없이
    assert engine.decide(P.peer("pc", obo="agent:pc/-"), "msg.send", "msg:local/-", {}, s).allow
    assert not engine.decide(P.peer("pv", obo="agent:pv/-"), "msg.send", "msg:local/-", {}, s).allow


def test_local_msg_send_open_d6():
    assert engine.decide(P.agent(A), "msg.send", f"msg:local/{B}").allow is defaults.LOCAL_MSG_SEND_OPEN


def test_provisional_constants_are_marked():
    src = (S.__file__.replace("scopes.py", "defaults.py"))
    text = open(src, encoding="utf-8").read()
    for d in ("D3", "D6", "D7", "D8", "D10", "D13"):
        assert f"# PROVISIONAL {d}: current behavior" in text, d


# --- 확대(R4.4) ---------------------------------------------------------------------------


def test_escalation_detects_every_widening_and_ignores_narrowing():
    base = {"subject": "agent:local/a", "resource": "session:local/b", "scopes": ["session.read"],
            "expires": 100, "conds": {"max_lines": 200, "path_glob": ["/r/a/*", "/r/b/*"]}, "role": "navigator"}
    assert S.escalation(None, base) == ["new_grant"]
    assert S.escalation(base, dict(base)) == []
    assert S.escalation(base, {**base, "scopes": ["session.stream"]}) == ["scopes:session.stream"]
    assert S.escalation(base, {**base, "scopes": []}) == []
    assert "resource" in S.escalation(base, {**base, "resource": "session:local/*"})
    assert S.escalation({**base, "resource": "session:local/*"}, base) == []
    assert "expires" in S.escalation(base, {**base, "expires": 200})
    assert "expires" in S.escalation(base, {**base, "expires": None})
    assert S.escalation(base, {**base, "expires": 50}) == []
    assert "cond_removed:max_lines" in S.escalation(base, {**base, "conds": {"path_glob": ["/r/a/*"]}})
    assert S.escalation(base, {**base, "conds": {"max_lines": 100, "path_glob": ["/r/a/*"]}}) == []
    assert "cond_widened:max_lines" in S.escalation(base, {**base, "conds": {**base["conds"], "max_lines": 500}})
    assert "subject" in S.escalation(base, {**base, "subject": "agent:*"})
    assert "role:driver" in S.escalation(base, {**base, "role": "driver"})


# --- 자동 승인 window(R4.5) ---------------------------------------------------------------


def _w(**kw):
    return {"subject": "agent:local/*", "resources": ["session:local/*"], "covers": ["session.read", "session.list"],
            "max_ttl_per_grant": 600, "expires": NOW + 1800, **kw}


def test_window_covers_only_what_it_names():
    p = P.agent(A)
    r = f"session:local/{B}"
    assert engine.window_covers(_w(), p, r, ["session.read"], 300, NOW) == (True, "window")
    assert engine.window_covers(_w(), p, r, ["session.input.line"], 300, NOW)[1] == "window_scope"
    assert engine.window_covers(_w(), p, r, ["session.read"], 900, NOW)[1] == "window_ttl"
    assert engine.window_covers(_w(), p, r, ["session.read"], 300, NOW + 1800)[1] == "window_expired"
    assert engine.window_covers(_w(), p, "fs:local/x", ["session.read"], 300, NOW)[1] == "window_resource"
    assert engine.window_covers(_w(), P.peer("x"), r, ["session.read"], 300, NOW)[1] == "window_subject"
    # R4.3 scope 는 와일드카드로 덮이지 않는다 — 이름으로 적어야 한다
    assert engine.window_covers(_w(covers=["session.*"]), p, r, ["session.input.raw"], 300, NOW)[1] == \
        "window_elevated_scope"
    assert engine.window_covers(_w(covers=["session.input.raw"]), p, r, ["session.input.raw"], 300, NOW)[0]
    assert engine.window_covers(_w(covers=["authz.request"]), p, "authz:*", ["authz.request"], 1, NOW)[1] == \
        "window_forbidden_scope"


def test_window_validation():
    assert engine.validate_window(_w(), elevated=True, now=NOW, env={}) == []
    assert "elevated_required" in engine.validate_window(_w(), elevated=False, now=NOW, env={})
    assert "too_long" in engine.validate_window(_w(expires=NOW + 5 * 3600), elevated=True, now=NOW, env={})
    assert "too_long" in engine.validate_window(_w(), elevated=True, now=NOW, env={"VT_AUTHZ_WINDOW_MAX_MIN": "10"})
    assert "forbidden_scope" in engine.validate_window(_w(covers=["pair.manage"]), elevated=True, now=NOW, env={})


# --- 주체 -----------------------------------------------------------------------------


def _req(cookie="", bearer="", query=""):
    from starlette.requests import Request
    headers = []
    if cookie:
        headers.append((b"cookie", f"vt_session={cookie}".encode()))
    if bearer:
        headers.append((b"authorization", f"Bearer {bearer}".encode()))
    return Request({"type": "http", "method": "GET", "path": "/api/x", "headers": headers,
                    "query_string": query.encode()})


@pytest.fixture
def protected(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "x")
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "tok-" + "abc")
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", "k-" + "123")
    return auth


def test_principal_from_request_mirrors_auth(protected):
    auth = protected
    cookie = auth.make_session("")
    assert P.from_request(_req(cookie=cookie)).kind == P.HUMAN
    assert P.from_request(_req(bearer="tok-abc")).kind == P.LEGACY
    a = P.from_request(_req())
    assert (a.kind, a.authenticated) == (P.ANON, False)
    amb = P.from_request(_req(cookie=cookie, bearer="garbage"))     # R1.1 — 기존 미들웨어는 통과
    assert amb.attrs.get("ambiguous") and not amb.authenticated
    assert engine.decide(amb, "api.access", "host:local").reason == "ambiguous_principal"


def test_principal_unprotected_server(monkeypatch):
    import auth
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "")
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "")
    a = P.from_request(_req())
    assert (a.kind, a.protected, a.authenticated) == (P.ANON, False, True)


# --- 저장소 ---------------------------------------------------------------------------


def test_store_txn_writes_private_files_and_backs_up_corruption(_state):
    with store.txn() as docs:
        docs["grants"]["items"].append({"id": "x"})
    p = store.doc_path("grants")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.root().stat().st_mode) == 0o700
    assert store.snapshot().grants == [{"id": "x"}]
    p.write_text("{not json", encoding="utf-8")
    s = store.snapshot()
    assert s.corrupt == {"grants"} and s.grants == []
    with store.txn() as docs:
        docs["grants"]["items"].append({"id": "y"})
    backups = list(store.root().glob("grants.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == "{not json"
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600


def test_store_txn_does_not_save_on_exception():
    with pytest.raises(RuntimeError):
        with store.txn() as docs:
            docs["grants"]["items"].append({"id": "x"})
            raise RuntimeError("중단")
    assert not store.doc_path("grants").exists()


# --- 감사 -----------------------------------------------------------------------------


def test_audit_chain_scrubs_content_and_detects_tampering():
    for i in range(3):
        audit.append({"principal": "agent:local/a", "action": "session.input.line", "resource": "session:local/b",
                      "decision": "allow", "text": "rm -rf ~", "detail": {"body": "secret", "chars": 8},
                      "url": "https://x.example/p?q=tok#frag"})
    p = audit.path()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    raw = p.read_text()
    assert "rm -rf" not in raw and "secret" not in raw and "tok" not in raw and "frag" not in raw
    recs = [json.loads(x) for x in raw.splitlines()]
    assert [r["seq"] for r in recs] == [1, 2, 3] and recs[0]["prev_sha"] == audit.GENESIS
    assert recs[0]["detail"] == {"chars": 8}
    assert audit.verify() == (True, None, 3)
    lines = raw.splitlines()
    lines[1] = lines[1].replace("allow", "deny")
    p.write_text("\n".join(lines) + "\n")
    ok, broken, _ = audit.verify()
    assert not ok and broken == 3


def test_audit_rotation_keeps_the_chain(monkeypatch):
    monkeypatch.setattr(audit, "MAX_BYTES", 400)
    for i in range(12):
        audit.append({"principal": "p", "action": f"a{i}", "decision": "allow"})
    assert audit.path().with_name("authz.jsonl.1").exists()
    ok, broken, n = audit.verify()
    assert ok and n == 12
