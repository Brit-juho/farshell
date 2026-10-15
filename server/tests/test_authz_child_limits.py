"""자식 한계 — 자식 grant 는 (부모 grant ∩ 요청) 에서 상속 불가를 뺀 것이다.

어떤 자식에게도 상속되지 않는 것: `browser.js`, `browser.sensitive`, 한 줄을 넘는 raw 입력(`session.input` modes∋raw), `session.manage`,
`relay`, `authz.admin`, `pair.manage`. 자식은 사람에게 묻는 길인 `authz.request` 만은 늘 가지며, 그 요청에는 부모 위임 id 가 남는다
(받은편지함이 계보를 보여 줄 수 있게 — 필드만, 판정에는 쓰지 않는다).
"""

from __future__ import annotations

import pytest

import delegation
from authz import deleg_svc, engine, migrate, msg_svc, store
from authz import principals as P
from authz import scopes as S

from test_delegation import _caller, tm  # noqa: F401  (격리 가짜 tmux 픽스처)


def g(scopes, sconds=None):
    return {"scopes": scopes, "sconds": sconds or {}}


def view(res: dict) -> dict:
    """결과를 풀어 비교하기 쉬운 `{scope: {조건: [값]}}` 로."""
    return S.caps_view(S.effective(res["scopes"], res["sconds"]))


# (이름, 부모 grant 들, 요청, 기대 자식 caps)
TABLE = [
    ("부모에게 없는 것은 못 받는다", [g(["session.view"])], g(["session.view", "session.read"]), {"session.view": {}}),
    ("교집합", [g(["session.view", "session.read", "msg.send"])], g(["session.read", "msg.send", "fs.read"]),
     {"msg.send": {}, "session.read": {}}),
    ("요청에 없는 것은 안 준다", [g(["session.view", "session.read", "msg.send"])], g(["session.view"]), {"session.view": {}}),
    ("조건은 값마다 교집합", [g(["fs.write"], {"fs.write": {"ops": ["write", "commit"]}})],
     g(["fs.write"], {"fs.write": {"ops": ["commit", "drop"]}}), {"fs.write": {"ops": ["commit"]}}),
    ("조건 값이 비면 scope 를 뺀다", [g(["fs.write"], {"fs.write": {"ops": ["write"]}})],
     g(["fs.write"], {"fs.write": {"ops": ["drop"]}}), {}),
    ("부모 grant 여럿은 합쳐서 본다", [g(["session.view"]), g(["msg.send"]), g(["fs.write"], {"fs.write": {"ops": ["write"]}})],
     g(["session.view", "msg.send", "fs.write"], {"fs.write": {"ops": ["write", "drop"]}}),
     {"session.view": {}, "msg.send": {}, "fs.write": {"ops": ["write"]}, "fs.read": {}}),
    ("한 줄 입력은 상속된다", [g(["session.input"], {"session.input": {"modes": ["line"]}})],
     g(["session.input"], {"session.input": {"modes": ["line"]}}), {"session.input": {"modes": ["line"], "via": ["direct"]}}),
    ("raw 는 부모가 가져도 line 까지만", [g(["session.input"], {"session.input": {"modes": ["line", "raw"]}})],
     g(["session.input"], {"session.input": {"modes": ["line", "raw"]}}), {"session.input": {"modes": ["line"], "via": ["direct"]}}),
    ("raw 만 요청해도 raw 는 안 준다(함의된 line 까지)", [g(["session.input"], {"session.input": {"modes": ["raw"]}})],
     g(["session.input"], {"session.input": {"modes": ["raw"]}}), {"session.input": {"modes": ["line"], "via": ["direct"]}}),
    ("와일드카드 요청도 부모 몫으로 줄어든다", [g(["session.view", "msg.send"])], g(["*"]),
     {"session.view": {}, "msg.send": {}, "authz.request": {}}),
    ("authz.request 는 부모와 무관하게 요청대로", [g(["session.view"])], g(["authz.request"]), {"authz.request": {}}),
    ("요청이 비면 아무것도 없다", [g(["session.view"])], g([]), {}),
    ("부모가 비면 authz.request 뿐", [], g(["session.view", "authz.request"]), {"authz.request": {}}),
]


@pytest.mark.parametrize("name,parent,req,want", TABLE, ids=[t[0] for t in TABLE])
def test_child_grants_table(name, parent, req, want):
    assert view(S.child_grants(parent, req)) == want


NEVER = ["browser.js", "browser.sensitive", "session.manage", "relay", "authz.admin", "pair.manage"]


@pytest.mark.parametrize("scope", NEVER)
def test_never_inheritable_scopes_are_denied_even_if_the_parent_holds_them(scope):
    parent = [g(["*"])]                                     # 부모는 어휘 전부를 가졌다
    assert scope in S.effective(parent[0]["scopes"], parent[0]["sconds"])
    out = S.child_grants(parent, g([scope, "session.view"]))
    assert scope not in out["scopes"] and scope not in S.effective(out["scopes"], out["sconds"])
    assert "session.view" in out["scopes"]                  # 나머지는 그대로 상속


def test_wildcard_parent_and_wildcard_request_yield_only_the_inheritable_rest():
    out = S.child_grants([g(["*"], {"relay": {"groups": ["*"]}})], g(["*"]))
    caps = S.effective(out["scopes"], out["sconds"])
    assert not set(caps) & S.NEVER_INHERIT_SCOPES
    assert caps["session.input"]["modes"] == frozenset({"line"})
    assert {"session.view", "session.read", "msg.send", "fs.read", "authz.request", "browser", "deleg.spawn"} <= set(caps)


def test_result_never_exceeds_parent_except_authz_request():
    parent = [g(["session.view", "fs.write"], {"fs.write": {"ops": ["write"]}})]
    out = S.child_grants(parent, g(["*"]))
    caps = S.effective(out["scopes"], out["sconds"])
    caps.pop("authz.request")
    have = S.effective(parent[0]["scopes"], parent[0]["sconds"])
    assert S.caps_subset(caps, have)


def test_never_inherit_tables_cover_the_spec():
    assert S.NEVER_INHERIT_SCOPES == {"browser.js", "browser.sensitive", "session.manage", "relay", "authz.admin", "pair.manage"}
    assert S.NEVER_INHERIT_CONDS == {"session.input": {"modes": frozenset({"raw"})}}
    assert S.CHILD_ALWAYS == {"authz.request"}


# --- 위임이 grant 를 파생하는 자리는 이 헬퍼를 거친다 -----------------------------------------------------------------


def test_fs_record_derives_the_child_share_through_child_grants(tm, monkeypatch, tmp_path):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg")
    parent = _caller(tm, "main")
    proj = tmp_path / "proj"
    proj.mkdir()
    with store.txn() as d:                                  # 부모는 세션 관리·브라우저 JS 까지 한 grant 에 묶어 가졌다
        d["grants"]["items"].append({
            "id": "pg1", "origin": "x", "subject": f"agent:local/{parent.key}", "resource": f"fs:local{proj}/*",
            "scopes": ["fs.write", "session.manage", "browser.js"], "schema": 2,
            "sconds": {"fs.write": {"ops": ["write", "commit"]}, "session.manage": {"ops": ["kill"]}},
            "state": "active", "approved_by": "human:t", "expires": None, "conds": {}})
    calls = []
    real = S.child_grants
    monkeypatch.setattr(S, "child_grants", lambda *a, **k: calls.append(a) or real(*a, **k))

    rec = deleg_svc.fs_record(parent.key, str(proj), worktree=False)

    assert len(calls) == 1
    ref = rec["from"][0]
    assert set(ref["scopes"]) <= {"fs.read", "fs.write"} and ref["sconds"] == {"fs.write": {"ops": ["write", "commit"]}}


def test_derived_child_grants_never_carry_never_inheritable_scopes(tm, monkeypatch, tmp_path):
    """위조된 장부가 fs 참조에 다른 scope 를 끼워 넣어도 자식 grant 로 파생되지 않는다."""
    item = {"id": "d1", "state": "running", "parent": {"key": "$1:1"}, "child": {"key": "$2:2"},
            "fs": {"mode": "inherit", "from": [{"grant": "pg1", "resource": "fs:local/tmp/x/*", "schema": 2,
                                                  "scopes": ["fs.read", "session.manage", "browser.js", "authz.admin", "relay"],
                                                  "sconds": {"session.manage": {"ops": ["kill"]}}}]}}
    out = migrate.translate_delegation(item)
    child = [r for r in out if r["subject"] == "agent:local/$2:2"]
    scopes = {s for r in child for s in S.caps_of(r)}
    assert scopes == {"deleg.control", "fs.read"}
    assert not scopes & S.NEVER_INHERIT_SCOPES


# --- 자식은 승인 채널·authz.admin·pair.manage 를 갖지 않고 authz.request 는 유지 -----------------------------------------


def test_child_defaults_have_no_admin_pair_or_approver_scope_but_keep_request(tm, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg,msg,share")
    parent = _caller(tm, "main")
    delegation.spawn(parent, task="x", title="kid")
    kid = P.agent(_caller(tm, "kid").key)
    snap = deleg_svc._snapshot(0.0)
    for scope, res in (("authz.admin", "authz:*"), ("pair.manage", "host:local"), ("session.manage", "session:local/x"),
                       ("browser.js", "browser:local/t"), ("relay", "relay:local/x")):
        ctx = {"op": "kill", "group": "sessions"}
        assert not engine.decide(kid, scope, res, ctx, snap).allow, scope
    assert engine.decide(kid, "authz.request", "authz:*", {}, snap).allow


# --- 계보: 자식의 승인 요청은 부모 위임 id 를 남긴다 -----------------------------------------------------------------


def test_child_msg_request_records_the_parent_delegation_id(tm, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg,msg")
    parent = _caller(tm, "main")
    did = delegation.spawn(parent, task="x", title="kid")["delegation"]
    kid, other = _caller(tm, "kid"), _caller(tm, "stranger")

    rec, created = msg_svc.request_access(kid.key, "kid", other.key, "stranger", None)
    plain, _ = msg_svc.request_access(other.key, "stranger", parent.key, "main", None)

    assert created and rec["delegation"] == did
    assert "delegation" not in plain                        # 자식이 아니면 필드가 없다(옛 모양 그대로)
    assert "delegation" not in msg_svc.request_view(rec)    # 응답 모양은 바뀌지 않는다(필드만)


def test_child_share_request_records_the_parent_delegation_id(tm, monkeypatch, tmp_path):
    import agent_share
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg,share")
    parent = _caller(tm, "main")
    did = delegation.spawn(parent, task="x", title="kid")["delegation"]
    kid = _caller(tm, "kid")
    tgt = agent_share.Sess("$7", 7000, "tgt", "%7", "bash", {"nick": "shell", "mode": "readwrite"})
    snap = {tgt.key: tgt, kid.key: agent_share.Sess(kid.sid, kid.created, "kid", kid.pane_id, "claude", None)}

    req, created = agent_share.request_access(snap, kid, tgt, agent_share.KIND_READ)

    assert created and "delegation" not in req              # 응답(옛 모양)은 그대로
    recs = [a for a in store._read("approvals")[0]["items"] if a.get("subject_session") == kid.key]
    assert len(recs) == 1 and recs[0]["delegation"] == did
    assert migrate.verify_store(tmux=None)["clean"]         # --verify 가 계보 필드를 차이로 보지 않는다


def test_lineage_is_none_for_a_session_that_is_not_a_child(tm, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg")
    parent = _caller(tm, "main")
    delegation.spawn(parent, task="x", title="kid")
    assert deleg_svc.lineage_of(parent.key) is None
    assert deleg_svc.lineage_of(_caller(tm, "kid").key)


def test_lineage_ignores_closed_delegations(tm, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg")
    parent = _caller(tm, "main")
    did = delegation.spawn(parent, task="x", title="kid")["delegation"]
    delegation.close(did, caller_key=parent.key)
    assert deleg_svc.lineage_of(_caller(tm, "kid").key) is None
