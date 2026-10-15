"""`VT_AUTHZ_ENFORCE=msg`: 에이전트 간 메시지의 판정·승인·감사를 엔진이 쥔다.

기존 `test_agent_msg.py`·authz_char 행렬은 꺼짐/켜짐 둘 다 통과한다(켠 쪽의 의도한 차이는 `test_char_agent_msg_enforced.py`).
여기는 켜졌을 때만 성립하는 성질:

  - 보낸 쪽 = 인증된 호출자. 본문이 다른 칸을 주장해도 무시하고 감사에 `claim_mismatch` 만 남긴다. 확인 못 하면 403.
  - 관계(위임 부모↔자식·형제, **열린** 위임만)는 승인 없이, 그 밖은 쌍별 grant — 202 → 사람 승인 → 전송 → 철회 → 다시 202.
    거절 쿨다운·대기 한도/만료·에이전트는 승인할 수 없음.
  - 받는 쪽(peer): hosts.json 의 등급×agentMessages 결과가 옛 검사와 같다. 분당 한도·hop 상한도 같다.
  - 꺼 두면 옛 동작(롤백), 감사는 내용 없이 해시 체인에, `fsh migrate-auth --verify` 는 깨끗하다.
"""

from __future__ import annotations

import json
import time

import pytest

from authz_harness import MACHINE, TMUX_ENV, World, Case

SECRET_TEXT = "SECRET-BODY-" + "7f3a9c"


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")


# --- 도우미 -----------------------------------------------------------------------------------


def hdrs(world, name: str, *, pid: int | None = None, tmux: str = TMUX_ENV) -> tuple:
    """`name` 세션의 pane 안에서 도는 에이전트가 보내는 식별 헤더(MCP 가 싣는 것과 같다)."""
    s = world.tmux.sessions[name]
    return (("Authorization", f"Bearer {MACHINE}"), ("X-Fsh-Pane", s.pane), ("X-Fsh-Tmux", tmux),
            ("X-Fsh-Pid", str(pid if pid is not None else s.pane_pid)))


def send(world, frm: str, to: str, text: str = "hi", **extra):
    body = {"to": f"local/{to}", "text": text, **extra}
    return world.run(Case(id="adhoc", group="adhoc", caller="none", method="POST", route="/api/agent/message",
                          path="/api/agent/message", body=body, headers=hdrs(world, frm)))


def raw_send(world, frm: str, to: str, text: str = "hi", **extra):
    """JSON 본문까지 보는 호출(상태·본문)."""
    from starlette.testclient import TestClient
    import main
    world.set_mode("password")
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.post("/api/agent/message", json={"to": f"local/{to}", "text": text, **extra}, headers=dict(hdrs(world, frm)))
    return r.status_code, r.json()


def human(world, method: str, path: str, body=None):
    r = world.run(Case(id="adhoc", group="adhoc", caller="session", method=method, route=path, path=path, body=body))
    return r


def human_json(world, method: str, path: str, body=None):
    from starlette.testclient import TestClient
    import auth
    import main
    world.set_mode("password")
    c = TestClient(main.app, raise_server_exceptions=False)
    ck = auth.make_session("")
    r = c.request(method, path, json=body, headers={"Cookie": f"vt_session={ck}"})
    return r.status_code, r.json()


def audit_lines(prefix="msg."):
    from authz import audit
    return list(reversed(audit.read_recent(1000, prefix)))


def raw_audit() -> str:
    from authz import audit
    p = audit.path()
    return p.read_text(encoding="utf-8") if p.exists() else ""


def ledger(world, *items):
    import vt_paths
    (vt_paths.state_dir() / "delegations.json").write_text(json.dumps({"items": list(items), "spawns": []}), encoding="utf-8")


def deleg(did: str, parent, child, state="running") -> dict:
    return {"id": did, "state": state, "agent": "claude", "created_at": time.time(), "task_preview": "x", "cwd": "/tmp",
            "outbox": [],
            "parent": {"key": f"{parent.sid}:{parent.created}", "name": parent.name, "pane": parent.pane},
            "child": {"key": f"{child.sid}:{child.created}", "name": child.name, "pane": child.pane}}


def stored_messages():
    import agent_msg
    return agent_msg.list_messages("", 100)


# --- 관계: 부모↔자식, 형제, 닫힌 위임 ------------------------------------------------------------


def test_parent_and_child_send_without_approval(world):
    assert send(world, "agent-a", "child-b") == {"status": 200, "error": None}      # 부모 → 자식
    assert send(world, "child-b", "agent-a") == {"status": 200, "error": None}      # 자식 → 부모
    assert [m["from"]["session"] for m in stored_messages()] == ["agent-a", "child-b"]


def test_unrelated_sender_gets_202_and_nothing_is_queued(world):
    import queue_store
    status, body = raw_send(world, "agent-a", "target", SECRET_TEXT)
    assert status == 202 and body["state"] == "pending" and body["requestId"].startswith("ms-")
    assert "승인" in body["reason"]
    assert stored_messages() == [] and queue_store.list_items() == []
    # 같은 쌍의 되풀이는 같은 요청 하나
    s2, b2 = raw_send(world, "agent-a", "target", "again")
    assert s2 == 202 and b2["requestId"] == body["requestId"]
    st, reqs = human_json(world, "GET", "/api/agent/share/requests")
    assert st == 200 and [(r["id"], r["kind"], r["agent"]["name"], r["target"]["name"]) for r in reqs["requests"]] == \
        [(body["requestId"], "msg", "agent-a", "target")]


def test_siblings_may_message_each_other(world):
    sib = world.tmux.add("child-c", 4747)
    ledger(world, deleg("d1", world.agent_a, world.child), deleg("d2", world.agent_a, sib))
    assert send(world, "child-b", "child-c") == {"status": 200, "error": None}
    assert send(world, "child-c", "child-b") == {"status": 200, "error": None}
    # 남의 자식(형제가 아님)은 아니다
    ledger(world, deleg("d1", world.agent_a, world.child), deleg("d2", world.other, sib))
    assert send(world, "child-b", "child-c")["status"] == 202


def test_child_of_other_parent_is_not_related_to_parent(world):
    sib = world.tmux.add("child-c", 4747)
    ledger(world, deleg("d1", world.agent_a, world.child), deleg("d2", world.other, sib))
    assert send(world, "agent-a", "child-c")["status"] == 202         # 남의 자식에게 보내려면 승인
    assert send(world, "child-c", "agent-a")["status"] == 202


@pytest.mark.parametrize("state", ["closed", "orphaned"])
def test_closed_delegation_no_longer_relates(world, state):
    assert send(world, "agent-a", "child-b")["status"] == 200
    ledger(world, deleg("dseed00001", world.agent_a, world.child, state=state))
    assert send(world, "agent-a", "child-b")["status"] == 202
    assert send(world, "child-b", "agent-a")["status"] == 202


def test_pair_relation_hook_is_a_stub_returning_false(world):
    from authz import msg_svc
    assert msg_svc.pair_related("$1:1001", "$2:1002") is False
    assert msg_svc.relation("$1:1001", "$2:1002", items=[]) is None


def test_relation_function_rules():
    from authz import msg_svc

    class S:
        def __init__(self, k): self.k = k

    def it(p, c, st="running"):
        return {"state": st, "parent": {"key": p}, "child": {"key": c}}
    items = [it("P", "A"), it("P", "B"), it("Q", "C", "closed"), it("Q", "D", "reported")]
    r = msg_svc.relation
    assert r("P", "A", items) == "parent_child" and r("A", "P", items) == "parent_child"
    assert r("A", "B", items) == "sibling" and r("B", "A", items) == "sibling"
    assert r("A", "C", items) is None and r("Q", "C", items) is None and r("Q", "D", items) == "parent_child"
    assert r("A", "A", items) is None and r("", "A", items) is None


# --- H2 / H17 ---------------------------------------------------------------------------------


def test_forged_body_sender_is_ignored_and_audited_without_content(world):
    status, body = raw_send(world, "agent-a", "child-b", SECRET_TEXT, pane="%3", tmux=TMUX_ENV, cwd="/x")
    assert status == 200
    (m,) = stored_messages()
    assert m["from"]["session"] == "agent-a"               # 본문이 주장한 other 가 아니라 확인된 호출자
    claim = [r for r in audit_lines() if r["action"] == "msg.claim"]
    assert len(claim) == 1 and claim[0]["reason"] == "claim_mismatch" and claim[0]["decision"] == "deny"
    assert claim[0]["detail"] == {"claimed_pane": "%3", "actual_pane": world.agent_a.pane}
    assert SECRET_TEXT not in raw_audit()


def test_forged_sender_cannot_borrow_anothers_relation(world):
    """target 의 칸을 주장해도(관계 없음) 호출자는 agent-a — 부모→자식 관계가 호출자 기준으로 판정된다."""
    assert send(world, "agent-a", "child-b", pane=world.target.pane)["status"] == 200
    # 반대로: 관계 없는 호출자가 agent-a 의 칸을 주장하면 호출자는 other 이고 pid 체인이 agent-a 에 닿지 않으면 403
    r = world.run(Case(id="adhoc", group="adhoc", caller="none", method="POST", route="/api/agent/message",
                       path="/api/agent/message", body={"to": "local/child-b", "text": "hi"},
                       headers=hdrs(world, "agent-a", pid=world.other.pane_pid)))
    assert r == {"status": 403, "error": "identity_unverified"}


@pytest.mark.parametrize("what", ["no_headers", "foreign_pid", "foreign_tmux", "no_pane"])
def test_unverified_sender_is_rejected_403(world, what):
    import queue_store
    h = {"no_headers": (("Authorization", f"Bearer {MACHINE}"),),
         "foreign_pid": hdrs(world, "agent-a", pid=99999),
         "foreign_tmux": hdrs(world, "agent-a", tmux="/private/tmp/tmux-501/default,1,0"),
         "no_pane": (("Authorization", f"Bearer {MACHINE}"), ("X-Fsh-Tmux", TMUX_ENV))}[what]
    r = world.run(Case(id="adhoc", group="adhoc", caller="none", method="POST", route="/api/agent/message",
                       path="/api/agent/message", body={"to": "local/child-b", "text": SECRET_TEXT, "pane": "%1",
                                                        "tmux": TMUX_ENV}, headers=h))
    assert r == {"status": 403, "error": "identity_unverified"}
    assert stored_messages() == [] and queue_store.list_items() == []
    (line,) = [x for x in audit_lines() if x["action"] == "msg.send"]
    assert line["result"] == "unverified" and line["principal"] == "agent:unverified" and line["decision"] == "deny"
    assert SECRET_TEXT not in raw_audit()


def test_unverified_sender_is_rejected_for_remote_targets_too(world):
    r = world.run(Case(id="adhoc", group="adhoc", caller="machine", method="POST", route="/api/agent/message",
                       path="/api/agent/message", body={"to": "m5/dev", "text": "hi", "pane": "%1", "tmux": TMUX_ENV}))
    assert r == {"status": 403, "error": "identity_unverified"}
    assert not [n for n in world.net if n[0] == "send_bytes"]


def test_missing_targets_are_404_before_the_sender_is_checked(world):
    nohdr = (("Authorization", f"Bearer {MACHINE}"),)
    for to, err in (("local/ghost", "session_not_found"), ("ghost/dev", "host_not_found")):
        r = world.run(Case(id="adhoc", group="adhoc", caller="none", method="POST", route="/api/agent/message",
                           path="/api/agent/message", body={"to": to, "text": "hi"}, headers=nohdr))
        assert r == {"status": 404, "error": err}


def test_self_send_is_400(world):
    assert send(world, "agent-a", "agent-a") == {"status": 400, "error": "self"}


def test_machine_token_h2_cases_change_with_the_flag(world):
    """matrix 의 H2 사례(`spoof_other`): 본문 pane 만 주장한 machine/agent_foreign 은 켠 상태에서 403."""
    for caller in ("machine", "agent_foreign"):
        r = world.run(Case(id="adhoc", group="adhoc", caller=caller, method="POST", route="/api/agent/message",
                           path="/api/agent/message",
                           body={"to": "local/target", "text": "hi", "pane": "%3", "tmux": TMUX_ENV}))
        assert r == {"status": 403, "error": "identity_unverified"}


# --- grant 수명: 요청 → 202 → 승인 → 전송 → 철회 → 다시 202 ------------------------------------------


def test_grant_lifecycle_request_approve_send_revoke_pending_again(world):
    status, body = raw_send(world, "agent-a", "target", SECRET_TEXT)
    rid = body["requestId"]
    assert status == 202
    # 에이전트는 승인할 수 없다 — 사람 전용 라우트는 기계 토큰·pane 을 거부한다
    forged = world.run(Case(id="adhoc", group="adhoc", caller="none", method="POST",
                            route="/api/agent/share/requests/{request_id}/decision",
                            path=f"/api/agent/share/requests/{rid}/decision", body={"decision": "allow"},
                            headers=hdrs(world, "agent-a")))
    assert forged["status"] == 403
    assert raw_send(world, "agent-a", "target")[0] == 202
    # 사람이 승인
    st, dec = human_json(world, "POST", f"/api/agent/share/requests/{rid}/decision", {"decision": "allow", "level": "read"})
    assert st == 200 and dec["ok"] and dec["grant"]["kind"] == "msg" and dec["grantId"].startswith("gm-")
    gid = dec["grantId"]
    assert human_json(world, "GET", "/api/agent/share/requests")[1]["requests"] == []
    # 승인된 쌍은 보낼 수 있다(반대 방향·다른 쌍은 아니다)
    status, ok = raw_send(world, "agent-a", "target", SECRET_TEXT)
    assert status == 200 and ok["to"] == "local/target"
    assert raw_send(world, "target", "agent-a")[0] == 202
    assert raw_send(world, "agent-a", "other")[0] == 202
    st, gl = human_json(world, "GET", "/api/agent/share/grants?kind=msg")
    assert [(g["id"], g["agent"]["name"], g["target"]["name"]) for g in gl["grants"]] == [(gid, "agent-a", "target")]
    assert human_json(world, "GET", "/api/agent/share/grants")[1]["grants"] == []        # 기본 목록(세션 공유)은 그대로
    # 철회하면 다시 보류
    st, rv = human_json(world, "DELETE", f"/api/agent/share/grants/{gid}")
    assert st == 200 and rv["ok"]
    status, again = raw_send(world, "agent-a", "target")
    assert status == 202 and again["requestId"] != rid
    acts = [(r["action"], r["result"]) for r in audit_lines()]
    assert ("msg.decide", "allowed") in acts and ("msg.revoke", "ok") in acts and ("msg.send", "ok") in acts
    assert ("msg.request", "pending") in acts
    assert SECRET_TEXT not in raw_audit()
    from authz import audit
    assert audit.verify()[0]


def test_deny_starts_a_cooldown_then_expires(world, monkeypatch):
    _, body = raw_send(world, "agent-a", "target")
    st, dec = human_json(world, "POST", f"/api/agent/share/requests/{body['requestId']}/decision", {"decision": "deny"})
    assert st == 200 and dec["grant"] is None
    status, denied = raw_send(world, "agent-a", "target")
    assert status == 403 and denied["error"] == "request_denied" and 0 < denied["retryAfter"] <= 300
    assert stored_messages() == []
    # 쿨다운이 끝나면 새 요청
    from authz import msg_svc
    real = time.time
    monkeypatch.setattr(msg_svc.time, "time", lambda: real() + 301)
    status, fresh = raw_send(world, "agent-a", "target")
    assert status == 202 and fresh["requestId"] != body["requestId"]


def test_pending_requests_are_limited_and_expire(world, monkeypatch):
    from authz import msg_svc, store
    from authz.errors import ShareError
    a = f"{world.agent_a.sid}:{world.agent_a.created}"
    alive = {a} | {f"$9{i}:{2000 + i}" for i in range(25)}
    for i in range(msg_svc.MAX_PENDING_PER_AGENT):
        msg_svc.request_access(a, "agent-a", f"$9{i}:{2000 + i}", f"t{i}", alive)
    with pytest.raises(ShareError) as e:
        msg_svc.request_access(a, "agent-a", "$920:2020", "t20", alive)
    assert e.value.code == "too_many_requests" and e.value.status == 429
    real = time.time
    monkeypatch.setattr(msg_svc.time, "time", lambda: real() + msg_svc.REQUEST_TTL + 5)
    assert msg_svc.list_requests(alive) == []                                             # 24시간 뒤 만료
    assert not [a for a in store._read("approvals")[0]["items"] if msg_svc.is_request(a)]


def test_agent_cannot_decide_or_revoke_even_with_pane_headers(world):
    _, body = raw_send(world, "agent-a", "target")
    for method, path, b in (("POST", f"/api/agent/share/requests/{body['requestId']}/decision", {"decision": "allow"}),
                            ("DELETE", "/api/agent/share/grants/gm-x", None)):
        r = world.run(Case(id="adhoc", group="adhoc", caller="none", method=method, route=path, path=path, body=b,
                           headers=hdrs(world, "agent-a")))
        assert r["status"] == 403
    assert raw_send(world, "agent-a", "target")[0] == 202


def test_decision_is_human_only_even_for_elevated_checks(world):
    from authz import msg_svc, principals as P
    from authz.errors import ShareError
    _, body = raw_send(world, "agent-a", "target")
    for actor in (None, P.legacy_token(), P.agent("$1:1001"), P.cli(501, 1), P.peer("pc")):
        with pytest.raises(ShareError) as e:
            msg_svc.decide(body["requestId"], "allow", None, actor, None)
        assert e.value.code == "human_session_required"
    assert len([r for r in human_json(world, "GET", "/api/agent/share/requests")[1]["requests"]]) == 1


def test_session_exit_drops_the_grant(world):
    _, body = raw_send(world, "agent-a", "target")
    human_json(world, "POST", f"/api/agent/share/requests/{body['requestId']}/decision", {"decision": "allow"})
    assert raw_send(world, "agent-a", "target")[0] == 200
    world.tmux.run(["kill-session", "-t", "target"])
    new = world.tmux.add("target", 4343)                    # 같은 이름의 새 세션 — key 가 다르다
    assert raw_send(world, "agent-a", "target")[0] == 202
    assert new.created != world.target.created


# --- 받는 쪽(peer): 등급 × agentMessages 패리티 ---------------------------------------------------


def _peer_post(world, caller: str):
    return world.run(Case(id="adhoc", group="adhoc", caller=caller, method="POST", route="/api/peer/agent/message",
                          path="/api/peer/agent/message",
                          body={"to": "target", "text": "hi", "from": "dev", "id": "m1"}))


def test_peer_receive_parity_over_all_levels(world, monkeypatch):
    import host_store
    host_store.set_grant_agent("pv", True)         # view + agentMessages (등급이 먼저 막는다)
    callers = ("peer_view", "peer_control", "peer_control_agent")
    results = {}
    for flag in ("", "msg"):
        monkeypatch.setenv("VT_AUTHZ_ENFORCE", flag)
        results[flag] = [_peer_post(world, c) for c in callers]
    assert results[""] == results["msg"]
    assert results["msg"][0]["status"] == 403 and results["msg"][1] == {"status": 403, "error": "agent_messages_off"}
    assert results["msg"][2]["status"] == 200


def test_peer_engine_decision_equals_agent_messages_switch(world):
    from authz import msg_svc
    for level in ("view", "control"):
        for agent in (False, True):
            g = {"id": "px", "level": level, "agentMessages": agent}
            assert msg_svc.authorize_peer(g).allow is agent


def test_peer_sender_identity_comes_from_the_grant_not_the_body(world):
    assert _peer_post(world, "peer_control_agent")["status"] == 200
    (m,) = stored_messages()
    assert m["from"]["host"] == "pca" and m["from"]["session"] == "dev"


def test_peer_body_hash_signature_still_required(world):
    r = world.run(Case(id="adhoc", group="adhoc", caller="peer_forged", method="POST", route="/api/peer/agent/message",
                       path="/api/peer/agent/message", body={"to": "target", "text": "hi", "id": "m1"}))
    assert r["status"] == 401


# --- 속도·hop 패리티 ------------------------------------------------------------------------------


def _flood(world, monkeypatch, flag: str, n: int, **body):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", flag)
    import agent_msg
    agent_msg._rate.clear()
    out = []
    for i in range(n):
        out.append(world.run(Case(id="adhoc", group="adhoc", caller="peer_control_agent", method="POST",
                                  route="/api/peer/agent/message", path="/api/peer/agent/message",
                                  body={"to": "target", "text": f"m{i}", "from": "dev", **body})))
    return out


def test_rate_limit_parity_peer(world, monkeypatch):
    import agent_msg
    off = _flood(world, monkeypatch, "", agent_msg.RATE_PER_MIN + 3)
    on = _flood(world, monkeypatch, "msg", agent_msg.RATE_PER_MIN + 3)
    assert off == on
    assert [r["status"] for r in on] == [200] * agent_msg.RATE_PER_MIN + [429] * 3
    assert on[-1]["error"] == "rate_limited"


def test_rate_limit_parity_local(world, monkeypatch):
    import agent_msg
    out = {}
    for flag in ("", "msg"):
        monkeypatch.setenv("VT_AUTHZ_ENFORCE", flag)
        agent_msg._rate.clear()
        out[flag] = [send(world, "agent-a", "child-b", f"m{i}") for i in range(agent_msg.RATE_PER_MIN + 2)]
    assert out[""] == out["msg"]
    assert out["msg"][-1] == {"status": 429, "error": "rate_limited"}


def test_rate_limit_follows_the_module_constant(world, monkeypatch):
    import agent_msg
    monkeypatch.setattr(agent_msg, "RATE_PER_MIN", 2)
    on = _flood(world, monkeypatch, "msg", 4)
    assert [r["status"] for r in on] == [200, 200, 429, 429]


def test_hop_limit_parity(world, monkeypatch):
    import agent_msg
    off = _flood(world, monkeypatch, "", 1, hop=agent_msg.MAX_HOPS)
    on = _flood(world, monkeypatch, "msg", 1, hop=agent_msg.MAX_HOPS)
    assert off == on == [{"status": 429, "error": "hop_limit"}]


def test_text_errors_keep_their_order_before_the_rate_limit(world, monkeypatch):
    import agent_msg
    monkeypatch.setattr(agent_msg, "RATE_PER_MIN", 1)
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")
    assert _peer_post(world, "peer_control_agent")["status"] == 200
    r = world.run(Case(id="adhoc", group="adhoc", caller="peer_control_agent", method="POST",
                       route="/api/peer/agent/message", path="/api/peer/agent/message",
                       body={"to": "target", "text": "  ", "from": "dev"}))
    assert r == {"status": 400, "error": "empty"}                # 빈 글이 429 보다 먼저


# --- 롤백 · shadow · migrate ---------------------------------------------------------------------


def test_flag_off_restores_the_old_behaviour_and_on_keeps_the_grant(world, monkeypatch):
    _, body = raw_send(world, "agent-a", "target")
    human_json(world, "POST", f"/api/agent/share/requests/{body['requestId']}/decision", {"decision": "allow"})
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    # 꺼짐: 옛 동작 — 본문 주장으로 보낸 쪽을 정하고 누구에게나 보낸다
    r = world.run(Case(id="adhoc", group="adhoc", caller="machine", method="POST", route="/api/agent/message",
                       path="/api/agent/message", body={"to": "local/other", "text": "x", "pane": "%1", "tmux": TMUX_ENV}))
    assert r == {"status": 200, "error": None}
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")
    assert raw_send(world, "agent-a", "target")[0] == 200       # 켜면 grant 그대로
    assert raw_send(world, "agent-a", "other")[0] == 202


def test_msg_flag_leaves_share_and_deleg_stores_alone(world, monkeypatch):
    """`msg` 만 켜면 공유·위임 저장소 파일은 만들지도 바꾸지도 않는다(독립 플래그)."""
    import vt_paths
    raw_send(world, "agent-a", "target")
    d = vt_paths.state_dir()
    assert not (d / "agent_grants.json").exists()
    ledger_before = (d / "delegations.json").read_text()
    raw_send(world, "agent-a", "child-b")
    assert (d / "delegations.json").read_text() == ledger_before


def test_shadow_has_no_diff_for_enforced_sites(world, monkeypatch):
    from authz import shadow
    monkeypatch.setenv("VT_AUTHZ_SHADOW", "1")
    before = shadow.STATS.as_dict()["mismatches"]
    for flag in ("", "msg"):
        monkeypatch.setenv("VT_AUTHZ_ENFORCE", flag)
        for c in ("peer_view", "peer_control", "peer_control_agent"):
            _peer_post(world, c)
    send(world, "agent-a", "child-b")
    assert shadow.STATS.as_dict()["mismatches"] == before
    assert shadow.STATS.as_dict()["bySite"].get("peer.agent_message", {}).get("mismatches", 0) == 0


def test_migrate_verify_stays_clean_with_msg_records(world):
    from authz import migrate, store
    migrate.apply(tmux=None)
    assert migrate.verify_store(tmux=None)["clean"]
    _, body = raw_send(world, "agent-a", "target")
    human_json(world, "POST", f"/api/agent/share/requests/{body['requestId']}/decision", {"decision": "allow"})
    raw_send(world, "agent-a", "other")                         # 보류 요청도 하나 남긴다
    assert migrate.verify_store(tmux=None)["clean"]             # 메시지 레코드는 legacy 원천이 아니라 비교 대상이 아니다
    migrate.apply(tmux=None)                                    # 다시 이전해도 지우지 않는다
    assert migrate.verify_store(tmux=None)["clean"]
    docs = store._read("grants")[0]["items"], store._read("approvals")[0]["items"]
    assert [g for g in docs[0] if g.get("origin", "").startswith("authz:msg:grant:")]
    assert [a for a in docs[1] if a.get("origin", "").startswith("authz:msg:req:")]


# --- 감사: 내용 없음 --------------------------------------------------------------------------------


def test_audit_has_lengths_and_sha8_but_never_the_text(world):
    from authz import audit
    raw_send(world, "agent-a", "child-b", SECRET_TEXT)
    raw_send(world, "agent-a", "target", SECRET_TEXT)
    _peer_post(world, "peer_control_agent")
    ok = [r for r in audit_lines() if r["action"] == "msg.send" and r["result"] == "ok"]
    assert ok and ok[0]["sizes"] == {"chars": len(SECRET_TEXT)} and ok[0]["sha8"] == audit.sha8(SECRET_TEXT)
    assert ok[0]["principal"] == f"agent:local/{world.agent_a.sid}:{world.agent_a.created}"
    assert ok[0]["reason"] == "parent_child"
    pend = [r for r in audit_lines() if r["action"] == "msg.request"]
    assert pend and pend[0]["decision"] == "pending" and pend[0]["sizes"] == {"chars": len(SECRET_TEXT)}
    assert SECRET_TEXT not in raw_audit() and "hi" not in [r.get("text") for r in audit_lines()]
    assert audit.verify()[0]


def test_engine_is_the_decider(world, monkeypatch):
    from authz import engine
    seen = []
    real = engine.decide
    monkeypatch.setattr(engine, "decide", lambda p, s, r, *a, **k: seen.append((p.kind, s, r)) or real(p, s, r, *a, **k))
    send(world, "agent-a", "child-b")
    _peer_post(world, "peer_control_agent")
    kinds = {(k, s) for k, s, _r in seen}
    assert ("agent", "msg.send") in kinds and ("peer", "msg.send") in kinds
    assert any(r == f"msg:local/{world.child.sid}:{world.child.created}" for _k, _s, r in seen)


def test_engine_failure_fails_closed_503(world, monkeypatch):
    from authz import engine
    monkeypatch.setattr(engine, "decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = send(world, "agent-a", "child-b")
    assert r == {"status": 503, "error": "authz_unavailable"}
    assert stored_messages() == []


def test_corrupt_store_fails_closed_for_grant_decisions(world):
    """F1 — authz 저장소가 손상되면 agent 판정은 grant 를 믿지 않는다: 기본 규칙(관계)만 남고, grant 가 필요한 쪽은 503."""
    from authz import store
    with store.txn() as d:
        d["grants"]["items"].append({"id": "x", "subject": "nobody", "resource": "msg:*", "scopes": ["msg.send"],
                                     "state": "active", "expires": None, "conds": {}})
    store.doc_path("grants").write_text("{not json")
    assert send(world, "agent-a", "child-b") == {"status": 200, "error": None}       # 관계는 저장소와 무관
    assert send(world, "agent-a", "target") == {"status": 503, "error": "authz_unavailable"}
    assert _peer_post(world, "peer_control_agent") == {"status": 503, "error": "authz_unavailable"}


# --- MCP ----------------------------------------------------------------------------------------


def test_mcp_send_message_shows_the_pending_text(monkeypatch):
    import mcp_farshell as m
    monkeypatch.setattr(m, "_api", lambda method, path, body=None, headers=None: (
        202, {"state": "pending", "requestId": "ms-abc", "reason": "사용자 승인 대기 중(메시지 보내기) — 승인되면 같은 호출을 다시 하세요"}))
    text, err = m.tool_send_message({"to": "target", "text": "hi"})
    assert err is False and "사용자 승인 대기 중" in text and "같은 호출을 다시 하세요" in text and "ms-abc" in text


def test_mcp_send_message_carries_identity_headers(monkeypatch):
    import fsh_client
    import mcp_farshell as m
    seen = {}
    monkeypatch.setattr(fsh_client, "call", lambda method, path, **kw: seen.update(kw=kw, path=path) or (200, b'{"ok": true, "id": "i", "conv": "c", "to": "local/x"}'))
    monkeypatch.setenv("TMUX_PANE", "%7")
    monkeypatch.setenv("TMUX", "/tmp/x,1,0")
    text, err = m.tool_send_message({"to": "x", "text": "hi"})
    assert err is False
    h = seen["kw"]["headers"]
    import urllib.parse
    assert urllib.parse.unquote(h["X-Fsh-Pane"]) == "%7" and "X-Fsh-Pid" in h and "X-Fsh-Tmux" in h
