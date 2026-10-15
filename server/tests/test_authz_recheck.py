"""입력 직전 재평가 — 승인~입력 사이에 grant·관계가 사라지면 아무것도 입력하지 않는다.

큐가 투입하기 직전(pane 락 안, 첫 키 전)에 에이전트 행동에서 비롯된 항목(위임 지시·결과, 에이전트 메시지)을 엔진으로 다시
판정하고, 공유 세션으로 가는 직접 입력도 같은 자리에서 다시 판정한다. 해당 기능의 `VT_AUTHZ_ENFORCE` 토큰이 꺼져 있으면
옛 동작 그대로다(재평가 없음, `send_to_tmux` 에 가드를 넘기지도 않는다).

"승인과 입력 사이의 회수"는 마지막 관문(`_is_waiting`)에 부수효과를 심어 흉내 낸다 — 판정을 지난 뒤, 입력하기 전이다.
"""

from __future__ import annotations

import pytest

import agent_msg
import agent_share
import delegation
import pane_input
import queue_runner
import queue_store
import tmux_target
from authz import audit, store
from authz import principals as P
from authz.errors import ShareError

from test_delegation import _caller, tm  # noqa: F401  (격리 가짜 tmux 픽스처)

PANE = "%9"
_REAL_SEND = tmux_target.send_to_tmux               # 위임 시험 픽스처가 가짜로 바꾸기 전의 진짜 구현


# --- 도우미 -----------------------------------------------------------------------------------


def drop_grant(gid: str) -> None:
    with store.txn() as d:
        d["grants"]["items"] = [g for g in d["grants"]["items"] if g.get("id") != gid]


def add_grant(**g) -> None:
    base = {"state": "active", "approved_by": "human:test", "expires": None, "conds": {}}
    with store.txn() as d:
        d["grants"]["items"].append({**base, **g})


def audit_rows(prefix: str) -> list[dict]:
    return list(reversed(audit.read_recent(1000, prefix)))


class Typed:
    """`tmux_target.send_to_tmux` 의 진짜 구현을 돌리되, tmux 로 나가는 호출만 기록한다."""

    def __init__(self, monkeypatch):
        self.keys: list = []
        self.guard_lock_held: list[bool] = []
        orig = tmux_target.tmux_runner.run                  # 위임 시험의 가짜 tmux(tm) 위에 얹는다 — send-keys 만 기록

        def run(args, *a, **kw):
            if args and args[0] == "send-keys":
                self.keys.append(("run", args))
            return orig(args, *a, **kw)
        monkeypatch.setattr(tmux_target.tmux_runner, "run", run)
        monkeypatch.setattr(tmux_target, "send_to_tmux", _REAL_SEND)
        monkeypatch.setattr(tmux_target, "_paste_to_pane", lambda pane, text: self.keys.append(("paste", text)) or True)
        monkeypatch.setattr(tmux_target, "_enter_gap_sec", lambda: 0.0)
        monkeypatch.setattr(queue_runner, "_resolve_pane", lambda item: (PANE, "auto"))
        monkeypatch.setattr(queue_runner, "_is_waiting", lambda *a, **k: False)


@pytest.fixture
def typed(tm, monkeypatch):
    return Typed(monkeypatch)


def only_item(session: str) -> dict:
    items = [x for x in queue_store.list_items() if (x.get("target") or {}).get("session") == session]
    assert len(items) == 1
    return items[0]


def hook_before_type(monkeypatch, fn) -> None:
    """마지막 관문(승인 대기 확인)에 부수효과를 심는다 — 관문 판정은 이미 지났고 입력은 아직이다."""
    monkeypatch.setattr(queue_runner, "_is_waiting", lambda *a, **k: fn() or False)


def spawn(tm, monkeypatch, typed=None, enforce="deleg"):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", enforce)
    parent = _caller(tm, "main")
    did = delegation.spawn(parent, task="x", title="kid")["delegation"]      # 자식을 띄우는 입력은 시험 대상이 아니다
    if typed is not None:
        typed.keys.clear()
    return parent, did


# --- 위임 항목 ---------------------------------------------------------------------------------


def test_followup_is_blocked_when_parent_grant_is_revoked_between_decision_and_typing(tm, typed, monkeypatch):
    parent, did = spawn(tm, monkeypatch, typed)
    queue_store.remove_pending_by_source(lambda s: True)                       # 첫 지시는 치운다
    delegation.send_to_child(parent.key, did, "다음 일")
    item = only_item("kid")
    hook_before_type(monkeypatch, lambda: drop_grant(f"dc-{did}"))              # 판정~입력 사이의 회수

    r = queue_runner.drain_once("kid", session_scoped=True)

    assert r["drained"] == 0 and r["error"] == "blocked" and r["reason"] == "grant_revoked"
    assert not any(k[0] == "paste" for k in typed.keys) and typed.keys == []    # 키가 하나도 나가지 않았다
    cur = only_item("kid")
    assert cur["id"] == item["id"] and cur["status"] == "blocked" and cur["blocked_reason"] == "grant_revoked"
    assert "exhausted" not in cur and "retry_at" not in cur                       # 영구 사유 — 자동 재시도하지 않는다
    rows = audit_rows("deleg.queue")
    assert rows and rows[0]["result"] == "denied" and rows[0]["reason"] == "grant_revoked"
    assert "다음 일" not in audit.path().read_text(encoding="utf-8")             # 감사에 내용이 없다


def test_guard_runs_under_the_pane_lock_before_the_first_key(tm, typed, monkeypatch):
    parent, did = spawn(tm, monkeypatch, typed)
    real = queue_runner._recheck_for

    def spy(item):
        g = real(item)
        if g is None:
            return None

        def wrapped():
            typed.guard_lock_held.append(pane_input._lock_for(PANE).locked())
            assert typed.keys == []                                              # 첫 키 전이다
            return g()
        return wrapped
    monkeypatch.setattr(queue_runner, "_recheck_for", spy)

    r = queue_runner.drain_once("kid", session_scoped=True)

    assert r["drained"] == 1 and typed.guard_lock_held == [True]
    assert any(k[0] == "paste" for k in typed.keys)


def test_allowed_item_is_typed_normally(tm, typed, monkeypatch):
    spawn(tm, monkeypatch, typed)
    r = queue_runner.drain_once("kid", session_scoped=True)
    assert r["drained"] == 1 and any(k[0] == "paste" for k in typed.keys)
    assert audit_rows("deleg.queue") == []


def test_result_to_parent_is_blocked_after_the_delegation_is_closed(tm, typed, monkeypatch):
    parent, did = spawn(tm, monkeypatch, typed)
    delegation.report(_caller(tm, "kid").key, "succeeded", "끝")
    assert only_item("main")["source"]["kind"] == "reported"
    delegation.close(did, caller_key=parent.key)                                   # 결과가 입력되기 전에 부모가 닫음

    r = queue_runner.drain_once("main", session_scoped=True)

    assert r["drained"] == 0 and r["reason"] == "delegation_closed" and typed.keys == []
    assert only_item("main")["blocked_reason"] == "delegation_closed"


def test_item_is_blocked_when_the_relationship_is_gone(tm, typed, monkeypatch):
    parent, did = spawn(tm, monkeypatch, typed)
    delegation.report(_caller(tm, "kid").key, "succeeded", "끝")
    with delegation._locked():
        data = delegation._read()
        data["items"] = [x for x in data["items"] if x["id"] != did]               # 장부에서 사라진 위임
        delegation._write(data)

    r = queue_runner.drain_once("main", session_scoped=True)

    assert r["reason"] == "relationship_gone" and typed.keys == []


def test_gone_notice_still_arrives_after_the_child_is_gone(tm, typed, monkeypatch):
    """소멸 알림은 위임이 닫힌 뒤에 오는 게 정상이다 — 관계(장부에 있다)만 본다."""
    parent, did = spawn(tm, monkeypatch, typed)
    queue_store.remove_pending_by_source(lambda s: True)
    del tm.sessions["kid"]
    delegation.sweep()
    assert only_item("main")["source"]["kind"] == "gone"

    r = queue_runner.drain_once("main", session_scoped=True)

    assert r["drained"] == 1


def test_engine_failure_closes_and_is_retriable(tm, typed, monkeypatch):
    parent, did = spawn(tm, monkeypatch, typed)
    from authz import engine

    def boom(*a, **k):
        raise RuntimeError("engine down")
    monkeypatch.setattr(engine, "decide", boom)

    r = queue_runner.drain_once("kid", session_scoped=True)

    assert r["drained"] == 0 and r["reason"] == "authz_unavailable" and typed.keys == []
    assert only_item("kid")["status"] == "blocked" and "retry_at" in only_item("kid")   # 일시 사유 — 백오프 뒤 재시도


def test_flag_off_is_the_old_behavior(tm, monkeypatch):
    """꺼짐: 재평가 없음 — 가드를 넘기지도 않는다(예전 시그니처 그대로)."""
    monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    parent = _caller(tm, "main")
    did = delegation.spawn(parent, task="x", title="kid")["delegation"]
    calls = []
    monkeypatch.setattr(queue_runner, "_resolve_pane", lambda item: (PANE, "auto"))
    monkeypatch.setattr(queue_runner, "_is_waiting", lambda *a, **k: False)
    monkeypatch.setattr(queue_runner.tmux_target, "send_to_tmux", lambda pane, text, preface=None: calls.append(text) or True)

    r = queue_runner.drain_once("kid", session_scoped=True)

    assert r["drained"] == 1 and len(calls) == 1 and did
    assert not (store.root()).exists()                                             # 엔진 저장소를 건드리지 않았다


def test_other_flag_does_not_recheck_delegation_items(tm, typed, monkeypatch):
    parent, did = spawn(tm, monkeypatch, typed, enforce="deleg")
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share")                                # deleg 토큰이 없다
    assert queue_runner._recheck_for(only_item("kid")) is None


def test_plain_queue_items_are_never_rechecked(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "share,deleg,msg")
    assert queue_runner._recheck_for({"id": "x", "text": "ls"}) is None
    assert queue_runner._recheck_for({"id": "x", "text": "ls", "source": {"other": 1}}) is None


# --- 에이전트 메시지 -----------------------------------------------------------------------------

AK, TK = "$1:1001", "$2:1002"


def send_msg(monkeypatch, *, grant=True):
    """AK 가 TK 에게 보낸 메시지를 엔진 허용 뒤 큐에 넣는다."""
    from authz import msg_svc
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")
    if grant:
        add_grant(id="gm-t1", origin="authz:msg:grant:t1", kind="msg", subject=f"agent:local/{AK}",
                  resource=f"msg:local/{TK}", scopes=["msg.send"], subject_session=AK, resource_session=TK, schema=2)
    v = msg_svc.authorize_local(AK, "a", TK, None)
    assert v.allow and v.ticket is not None
    return agent_msg.receive(from_host="local", from_label="me", from_session="a", to_session="b", text="안녕", authz=v.ticket)


def test_agent_message_is_blocked_when_the_grant_is_revoked_before_typing(tm, typed, monkeypatch):
    msg = send_msg(monkeypatch)
    assert only_item("b")["source"]["authz"] == {"from": AK, "to": TK}
    hook_before_type(monkeypatch, lambda: drop_grant("gm-t1"))

    r = queue_runner.drain_once("b", session_scoped=True)

    assert r["drained"] == 0 and r["reason"] == "grant_revoked" and typed.keys == []
    assert only_item("b")["blocked_reason"] == "grant_revoked"
    m = agent_msg.get(msg["id"])
    assert m["status"] == agent_msg.STATUS_BLOCKED and m["reason"] == "grant_revoked"      # 메시지 상태도 맞춘다
    rows = audit_rows("msg.queue")
    assert rows and rows[0]["result"] == "denied" and rows[0]["reason"] == "grant_revoked"
    assert "안녕" not in audit.path().read_text(encoding="utf-8")


def test_agent_message_is_typed_while_the_grant_stands(tm, typed, monkeypatch):
    send_msg(monkeypatch)
    r = queue_runner.drain_once("b", session_scoped=True)
    assert r["drained"] == 1 and any(k[0] == "paste" for k in typed.keys)


def test_agent_message_flag_off_has_no_clue_and_no_recheck(tm, monkeypatch):
    monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    agent_msg.receive(from_host="local", from_label="me", from_session="a", to_session="b", text="hi")
    item = only_item("b")
    assert set(item["source"]) == {"agentMsg"}
    assert queue_runner._recheck_for(item) is None


def test_peer_message_is_blocked_when_the_allow_switch_is_turned_off(tm, typed, monkeypatch):
    import host_store
    from authz import msg_svc
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")
    grant = {"id": "m1", "label": "m1", "level": "control", "agentMessages": True}
    v = msg_svc.authorize_peer(grant)
    assert v.allow
    agent_msg.receive(from_host="m1", from_label="m1", from_session="x", to_session="b", text="원격", authz=v.ticket)
    assert only_item("b")["source"]["authz"] == {"peer": "m1"}
    monkeypatch.setattr(host_store, "find_grant", lambda pid: {**grant, "agentMessages": False})

    r = queue_runner.drain_once("b", session_scoped=True)

    assert r["reason"] == "grant_revoked" and typed.keys == []


def test_peer_message_is_blocked_when_the_peer_is_gone(tm, typed, monkeypatch):
    import host_store
    from authz import msg_svc
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "msg")
    grant = {"id": "m1", "label": "m1", "level": "control", "agentMessages": True}
    agent_msg.receive(from_host="m1", from_label="m1", from_session="x", to_session="b", text="원격",
                      authz=msg_svc.authorize_peer(grant).ticket)
    monkeypatch.setattr(host_store, "find_grant", lambda pid: None)

    assert queue_runner.drain_once("b", session_scoped=True)["reason"] == "grant_revoked"


# --- 공유 세션 직접 입력 ---------------------------------------------------------------------------


class Agent:
    sid, created, name, pane_id = "$1", 1001, "a", "%1"
    key = "$1:1001"


def agent_sess():
    return agent_share.Sess("$1", 1001, "a", "%1", "claude", None)


def target(mode="readwrite"):
    return agent_share.Sess("$2", 1002, "tgt", PANE, "bash", {"nick": "shell", "mode": mode})


def share_setup(monkeypatch, tmp_path, *, write=True, enforce="share"):
    from authz import approvals, shares
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", enforce)
    t = target()
    legacy = {"id": "g1", "agent": {"sid": "$1", "created": 1001, "name": "a"}, "target": {"sid": "$2", "created": 1002},
              "read": True, "write": write, "granted": 1.0}
    with store.txn() as d:
        d["grants"]["items"].append(approvals.engine_grant(legacy, approved_by="human:test"))
        d["shares"]["items"][t.key] = shares.entry(t.key, "shell", "readwrite")
    monkeypatch.setattr(agent_share, "snapshot", lambda rows=None: {t.key: t, AK: agent_sess()})
    return t


def tmux_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_share.tmux_runner, "run", lambda args, **kw: calls.append(args) or (0, b"", b""))
    return calls


def test_share_send_is_refused_when_the_grant_is_revoked_before_typing(monkeypatch, tmp_path):
    t = share_setup(monkeypatch, tmp_path)
    calls = tmux_calls(monkeypatch)
    me = Agent()
    agent_share.recheck_send(me, t)                              # 지금은 통과
    drop_grant("ag-g1")                                          # authorize 후, 입력 전에 회수

    with pytest.raises(ShareError) as e:
        agent_share.send_keys(t, "ls", True, lambda: agent_share.recheck_send(me, t))

    assert e.value.code == "grant_revoked" and e.value.status == 403
    assert calls == []                                           # tmux 로 아무것도 나가지 않았다


def test_share_send_is_refused_when_the_share_is_lowered_before_typing(monkeypatch, tmp_path):
    t = share_setup(monkeypatch, tmp_path)
    calls = tmux_calls(monkeypatch)
    lowered = agent_share.Sess(t.sid, t.created, t.name, t.pane_id, t.command, {"nick": "shell", "mode": "read"})
    monkeypatch.setattr(agent_share, "snapshot", lambda rows=None: {t.key: lowered, AK: agent_sess()})
    with store.txn() as d:
        from authz import shares
        d["shares"]["items"][t.key] = shares.entry(t.key, "shell", "read")

    with pytest.raises(ShareError) as e:
        agent_share.send_keys(t, "ls", True, lambda: agent_share.recheck_send(Agent(), t))

    assert e.value.code == "grant_revoked" and calls == []


def test_share_send_is_refused_when_the_session_is_gone(monkeypatch, tmp_path):
    t = share_setup(monkeypatch, tmp_path)
    calls = tmux_calls(monkeypatch)
    monkeypatch.setattr(agent_share, "snapshot", lambda rows=None: {AK: agent_sess()})
    with pytest.raises(ShareError) as e:
        agent_share.send_keys(t, "ls", True, lambda: agent_share.recheck_send(Agent(), t))
    assert e.value.code == "grant_revoked" and calls == []


def test_share_send_goes_through_while_allowed_and_holds_the_pane_lock(monkeypatch, tmp_path):
    t = share_setup(monkeypatch, tmp_path)
    calls = tmux_calls(monkeypatch)
    held = []
    me = Agent()
    agent_share.send_keys(t, "ls", True, lambda: held.append(pane_input._lock_for(PANE).locked()) or agent_share.recheck_send(me, t))
    assert held == [True] and len(calls) == 2                    # 텍스트 + Enter


def test_share_recheck_does_not_count_the_rate_limit(monkeypatch, tmp_path):
    t = share_setup(monkeypatch, tmp_path)
    agent_share._rate.clear()
    agent_share.recheck_send(Agent(), t)
    assert agent_share._rate == {} or not any(agent_share._rate.values())


def test_share_recheck_is_a_noop_when_the_flag_is_off(monkeypatch, tmp_path):
    t = share_setup(monkeypatch, tmp_path)
    monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    drop_grant("ag-g1")
    agent_share.recheck_send(Agent(), t)                          # 올리지 않는다


def test_share_route_passes_no_guard_when_the_flag_is_off(monkeypatch):
    """라우트 소스가 꺼짐에서 예전과 같은 호출(가드 인자 없음)을 한다."""
    import inspect
    from routes import agent_share as r
    src = inspect.getsource(r.agent_send)
    assert "if agent_share.enforced():" in src and "agent_share.send_keys, target, text, enter)" in src


def test_engine_error_in_share_recheck_closes(monkeypatch, tmp_path):
    t = share_setup(monkeypatch, tmp_path)
    calls = tmux_calls(monkeypatch)
    from authz import engine
    monkeypatch.setattr(engine, "decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(ShareError) as e:
        agent_share.send_keys(t, "ls", True, lambda: agent_share.recheck_send(Agent(), t))
    assert e.value.code == "authz_unavailable" and e.value.status == 503 and calls == []
