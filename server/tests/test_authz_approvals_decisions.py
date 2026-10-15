"""승인 결정 — 먼저 온 쪽이 이긴다(N-1)와 승인자는 요청한 scope 를 이미 가져야 한다(N-5).

  - 같은 결정의 재전송은 처음 결과(`replayed`)를 돌려주고 아무것도 다시 만들지 않는다. 다른 결정은 409 `already_decided`.
    동시에 눌러도 이긴 쪽은 하나다. 해결 기록은 짧게(10분) 남고 그 뒤 정리된다. 거절 쿨다운은 그대로다.
  - 승인자: (사람 / elevated 사람 / 로컬 cli / 에이전트 / 훅) × (일반 요청 / elevated 요청). 엔진 판정(`decide`)을 거친다.
"""

from __future__ import annotations

import threading

import pytest

import agent_share
from authz import approvals, engine, msg_svc, share_svc, store
from authz import principals as P
from authz.errors import ShareError
from test_agent_share import aread, app_auth, approve, env, setup_pair, web  # noqa: F401

HUMAN = P.human("dev1")
ELEV = P.human("dev1", elevated=True)
CLI = P.cli(501, 4242)
AGENT = P.agent("$9:9")
HOOK = P.Principal(P.AGENT, "agent:local/$9:9", attrs={"key": "$9:9", "via": "hook"})
SVC = P.svc("tui", 501, 4243)
LEGACY = P.legacy_token()
PEER = P.peer("m5")

PLAIN = (("session.read",), None)
BROWSER_JS = (("browser.js",), None)
RELAY_READ = (("relay",), {"relay": {"groups": ["sessions"]}})
RAW = (("session.input",), {"session.input": {"modes": ["raw"]}})
QUEUE = (("relay",), {"relay": {"groups": ["queue"]}})
RES = "session:local/$1:1"


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))


@pytest.fixture
def share_on(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share")
    monkeypatch.setattr(agent_share, "_read_seen", {})


def _code(actor, req) -> str:
    try:
        approvals.check_authority(actor, req[0], RES, req[1])
        return "ok"
    except ShareError as e:
        return e.code


# --- N-5: 승인자 --------------------------------------------------------------------------------


@pytest.mark.parametrize("actor,plain,elevated", [
    (HUMAN, "ok", "elevation_required"),
    (ELEV, "ok", "ok"),
    (CLI, "human_session_required", "human_session_required"),
    (AGENT, "human_session_required", "human_session_required"),
    (HOOK, "human_session_required", "human_session_required"),
    (SVC, "human_session_required", "human_session_required"),
    (LEGACY, "human_session_required", "human_session_required"),
    (PEER, "human_session_required", "human_session_required"),
    (P.unauthenticated(), "human_session_required", "human_session_required"),
])
def test_approver_matrix(actor, plain, elevated):
    assert _code(actor, PLAIN) == plain
    assert _code(actor, RAW) == elevated
    assert _code(actor, QUEUE) == elevated
    assert _code(actor, BROWSER_JS) == elevated


def test_plain_relay_group_is_approvable_by_a_plain_human():
    assert _code(HUMAN, RELAY_READ) == "ok"
    assert _code(AGENT, RELAY_READ) == "human_session_required"


def test_approver_check_goes_through_the_engine():
    """엔진이 승인자의 scope 를 거부하면(금지 규칙) 사람이어도 승인할 수 없다."""
    deny = {"id": "d1", "effect": "deny", "subject": "human:*", "resource": "session:local/*", "scopes": ["session.read"]}
    snap = store.Snapshot(grants=[deny], now=1.0)
    with pytest.raises(ShareError) as ei:
        approvals.check_authority(ELEV, PLAIN[0], RES, None, snap)
    assert ei.value.code == "approver_lacks_scope" and ei.value.extra["scope"] == "session.read"
    approvals.check_authority(ELEV, ("session.view",), RES, None, snap)         # 다른 scope 는 그대로


def test_engine_decisions_without_approving_are_unchanged():
    """승인자 확인 모드(ctx.approving)가 아닌 판정은 바뀌지 않는다 — 비elevated human 도 raw 입력을 판정에서 허용받는다."""
    assert engine.decide(HUMAN, "session.input", RES, {"mode": "raw"}).allow
    d = engine.decide(HUMAN, "session.input", RES, {"mode": "raw", "approving": True})
    assert (d.allow, d.reason) == (False, "elevation_required")
    assert engine.decide(ELEV, "session.input", RES, {"mode": "raw", "approving": True}).allow
    assert engine.decide(HUMAN, "session.input", RES, {"mode": "line", "approving": True}).allow


def test_deny_decision_needs_only_a_human_session():
    approvals.check_authority(HUMAN, (), RES)
    for a in (CLI, AGENT, HOOK):
        with pytest.raises(ShareError) as ei:
            approvals.check_authority(a, (), RES)
        assert ei.value.code == "human_session_required"


# --- N-1: 메시지 승인 (저장소만 쓴다) ---------------------------------------------------------------


@pytest.fixture
def msgs(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")
    a, b = "$1:1", "$2:2"
    return a, b, {a, b}


def _pending(a, b, alive):
    return msg_svc.request_access(a, "a", b, "b", alive)[0]["id"]


def test_msg_same_decision_is_idempotent_and_different_is_409(msgs):
    a, b, alive = msgs
    rid = _pending(a, b, alive)
    first = msg_svc.decide(rid, "allow", None, HUMAN, alive)
    again = msg_svc.decide(rid, "allow", None, HUMAN, alive)
    assert again["replayed"] is True and again["grantId"] == first["grantId"] and again["grant"] == first["grant"]
    assert "replayed" not in first
    assert len(msg_svc.msg_grants({"grants": store._read("grants")[0]})) == 1       # 두 번째가 grant 를 또 만들지 않았다
    with pytest.raises(ShareError) as ei:
        msg_svc.decide(rid, "deny", None, HUMAN, alive)
    assert ei.value.code == "already_decided" and ei.value.status == 409 and ei.value.extra["decision"] == "allow"
    assert msg_svc.decide(rid[3:], "allow", None, HUMAN, alive)["replayed"]         # ap- 접두 유무와 상관없이 같은 요청


def test_msg_deny_replay_and_cooldown_stays(msgs):
    a, b, alive = msgs
    rid = _pending(a, b, alive)
    first = msg_svc.decide(rid, "deny", None, HUMAN, alive)
    assert msg_svc.decide(rid, "deny", None, HUMAN, alive)["replayed"]
    with pytest.raises(ShareError) as ei:
        msg_svc.decide(rid, "allow", None, HUMAN, alive)
    assert ei.value.code == "already_decided"
    assert first["grant"] is None
    with pytest.raises(ShareError) as ei:                       # 쿨다운(R4.6)은 그대로
        msg_svc.request_access(a, "a", b, "b", alive)
    assert ei.value.code == "request_denied" and ei.value.status == 403


def test_msg_replay_still_requires_a_human(msgs):
    a, b, alive = msgs
    rid = _pending(a, b, alive)
    msg_svc.decide(rid, "allow", None, HUMAN, alive)
    for actor in (None, AGENT, CLI, HOOK):
        with pytest.raises(ShareError) as ei:
            msg_svc.decide(rid, "allow", None, actor, alive)
        assert ei.value.code == "human_session_required"


def test_msg_resolved_records_are_pruned_after_retention(msgs, monkeypatch):
    a, b, alive = msgs
    rid = _pending(a, b, alive)
    msg_svc.decide(rid, "allow", None, HUMAN, alive)
    assert any(approvals.is_resolved(r) for r in store._read("approvals")[0]["items"])
    monkeypatch.setattr(approvals, "RESOLVED_RETENTION", 0.0)
    rid2 = _pending(a, "$3:3", {a, b, "$3:3"})
    msg_svc.decide(rid2, "allow", None, HUMAN, {a, b, "$3:3"})                  # 이 결정의 기록은 곧바로 만료
    msg_svc.list_requests({a, b, "$3:3"})                                      # 어느 읽기·쓰기든 정리한다
    left = [r["origin"] for r in store._read("approvals")[0]["items"] if approvals.is_resolved(r)]
    assert left == [f"{approvals.RESOLVED_PREFIX}msg:{rid[3:]}"]                # 만료된 두 번째 기록만 정리됐다
    with pytest.raises(ShareError) as ei:
        msg_svc.decide(rid2, "allow", None, HUMAN, {a, b, "$3:3"})
    assert ei.value.code == "not_found"                                         # 보존 기간이 지나면 기록이 없다


def test_msg_unknown_request_is_404(msgs):
    a, b, alive = msgs
    with pytest.raises(ShareError) as ei:
        msg_svc.decide("ms-nope", "allow", None, HUMAN, alive)
    assert ei.value.code == "not_found"


@pytest.mark.parametrize("decisions", [("allow",) * 6, ("deny",) * 6, ("allow", "deny") * 3])
def test_msg_concurrent_decisions_have_exactly_one_winner(msgs, decisions):
    a, b, alive = msgs
    rid = _pending(a, b, alive)
    gate = threading.Barrier(len(decisions))
    out: list = [None] * len(decisions)

    def run(i, d):
        gate.wait()
        try:
            out[i] = ("ok", msg_svc.decide(rid, d, None, HUMAN, alive))
        except ShareError as e:
            out[i] = ("err", e.code)

    ts = [threading.Thread(target=run, args=(i, d)) for i, d in enumerate(decisions)]
    [t.start() for t in ts]
    [t.join(timeout=30) for t in ts]
    assert all(o is not None for o in out)
    winners = [o for o in out if o[0] == "ok" and "replayed" not in o[1]]
    assert len(winners) == 1                                                   # 처음 처리한 쪽은 정확히 하나
    won = "allow" if winners[0][1]["grant"] else "deny"
    for (kind, v), d in zip(out, decisions):
        if d == won:
            assert kind == "ok"                                                # 같은 결정은 처음 결과를 받는다
        else:
            assert (kind, v) == ("err", "already_decided")                     # 다른 결정은 409
    grants = msg_svc.msg_grants({"grants": store._read("grants")[0]})
    assert len(grants) == (1 if won == "allow" else 0)


# --- N-1: 세션 공유 승인 (HTTP) ----------------------------------------------------------------------


def test_share_replay_same_result_without_new_audit_or_grant(web, share_on):
    from authz import audit
    human, agent, env = setup_pair(web)
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    first = approve(human, rid)
    assert first.status_code == 200 and "replayed" not in first.json()
    n_audit = len(audit.read_recent(1000, "share."))
    again = approve(human, rid)
    assert again.status_code == 200 and again.json()["replayed"] is True
    assert again.json()["grantId"] == first.json()["grantId"] and again.json()["grant"]["id"] == first.json()["grant"]["id"]
    assert len(audit.read_recent(1000, "share.")) == n_audit                   # 감사 줄이 늘지 않았다
    assert len(store._read("grants")[0]["items"]) == 1
    bad = human.post(f"/api/agent/share/requests/{rid}/decision", json={"decision": "deny"})
    assert bad.status_code == 409 and bad.json()["error"] == "already_decided"
    wide = approve(human, rid, "readwrite")                                    # 다른 수준의 재요청도 처음 결정과 다르다
    assert wide.status_code == 409
    assert aread(agent, env, "claude-a", "shell").status_code == 200           # 409 가 권한을 바꾸지 않았다


def test_share_deny_replay_and_cooldown(web, share_on):
    human, agent, env = setup_pair(web)
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    d = lambda x: human.post(f"/api/agent/share/requests/{rid}/decision", json={"decision": x})  # noqa: E731
    assert d("deny").status_code == 200
    r = d("deny")
    assert r.status_code == 200 and r.json()["replayed"] is True and r.json()["grant"] is None
    assert d("allow").status_code == 409
    assert aread(agent, env, "claude-a", "shell").status_code == 403           # 쿨다운 유지
    assert store._read("grants")[0]["items"] == []


def test_share_replay_needs_a_human(web, share_on):
    human, agent, env = setup_pair(web)
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    assert approve(human, rid).status_code == 200
    r = agent.post(f"/api/agent/share/requests/{rid}/decision", json={"decision": "allow"}, headers=env.ident("claude-a"))
    assert r.status_code in (401, 403)


def test_share_concurrent_double_click_has_one_winner(web, share_on):
    human, agent, env = setup_pair(web)
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    snap = agent_share.snapshot()
    decisions = ["allow", "deny", "allow", "deny", "allow", "deny"]
    gate = threading.Barrier(len(decisions))
    out: list = [None] * len(decisions)

    def run(i, d):
        gate.wait()
        try:
            out[i] = ("ok", share_svc.decide(rid, d, None, snap, HUMAN))
        except ShareError as e:
            out[i] = ("err", e.code)

    ts = [threading.Thread(target=run, args=(i, d)) for i, d in enumerate(decisions)]
    [t.start() for t in ts]
    [t.join(timeout=30) for t in ts]
    winners = [o for o in out if o[0] == "ok" and "replayed" not in o[1]]
    assert len(winners) == 1
    won = "allow" if winners[0][1]["grant"] else "deny"
    for (kind, v), d in zip(out, decisions):
        assert (kind == "ok") if d == won else (kind, v) == ("err", "already_decided")
    assert len(store._read("grants")[0]["items"]) == (1 if won == "allow" else 0)


def test_share_resolved_records_do_not_show_in_legacy_projection_or_verify(web, share_on):
    from authz import migrate
    human, agent, env = setup_pair(web)
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    approve(human, rid)
    assert any(approvals.is_resolved(a) for a in store._read("approvals")[0]["items"])
    data, _ = agent_share._load()
    assert data["requests"] == []                                              # 옛 파일(롤백 대상)에는 해결 기록이 없다
    assert migrate.verify_store()["clean"]
