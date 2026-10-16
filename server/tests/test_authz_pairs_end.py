"""pair 끝내기 — 명시적 종료·회수·유휴·세션 소멸·최대 수명, 회수 saga(모든 단계에서 크래시 주입 뒤 sweep), 감사, 파일 권한, 이전 도구와의 공존.
`test_authz_pairs.py` 의 환경을 쓴다."""

from __future__ import annotations

import os
import re
import stat

import pytest

from authz import audit, engine, leases, pairs, saga, store
from authz import principals as P
from test_authz_pairs import (A, B, C, H, bundle, code_of, env, get, grants_of, joined, make_active, manage)  # noqa: F401


# --- 끝내기 · 회수 saga -------------------------------------------------------------------------


def test_explicit_end_by_member_and_human_revoke_are_distinct_and_idempotent(env):
    pid = make_active(env)
    r = pairs.end(A, pid)
    assert r["state"] == "ended" and r["already"] is False and r["cleanup"]["done"] is True
    assert pairs.end(A, pid)["already"] is True                                       # 멱등
    pid2 = make_active(env, sessions=["charlie"], dirs=[env.proj + "/src"])
    assert pairs.end(H, pid2)["state"] == "revoked" and get(pid2)["end_reason"] == "revoked_by_human"
    assert code_of(pairs.end, C, pid2)[0] == "not_found"                              # 구성원도 사람도 아닌 호출자
    assert code_of(pairs.end, P.cli(os.getuid(), 9), pid2)[0] == "not_found"


def residue(pid):
    gs = [g for g in grants_of(pid)]
    ls = [l for l in leases.read_items()[0] if l.get("pair_id") == pid]
    return gs, ls


def seed_stuff(env, monkeypatch, pid):
    """끝나면 정리돼야 하는 것들: 구성원 간 대기 메시지, pair 가 효력을 갖는 동안 만든 위임(가짜 장부)."""
    import agent_msg
    import delegation
    import queue_store
    m = agent_msg.receive(from_host="local", from_label="x", from_session="alpha", to_session="bravo", text="hello partner")
    keep = agent_msg.receive(from_host="local", from_label="x", from_session="charlie", to_session="bravo", text="unrelated")
    since = get(pid)["activated"]
    ledger = [{"id": "d1", "state": "running", "parent": {"key": "$1:101"}, "child": {"key": "$9:9"}, "created_at": since + 1},
              {"id": "d0", "state": "running", "parent": {"key": "$1:101"}, "child": {"key": "$9:8"}, "created_at": since - 100},
              {"id": "d2", "state": "running", "parent": {"key": "$3:103"}, "child": {"key": "$9:7"}, "created_at": since + 1}]
    closed = []
    monkeypatch.setattr(delegation, "list_all", lambda: [x for x in ledger if x["id"] not in closed])
    monkeypatch.setattr(delegation, "close", lambda did, **k: closed.append(did))
    return m, keep, closed, queue_store


def test_end_runs_the_saga_that_clears_grants_leases_messages_and_delegations(env, monkeypatch):
    pid = make_active(env, leases=True)
    m, keep, closed, queue_store = seed_stuff(env, monkeypatch, pid)
    gs, ls = residue(pid)
    assert gs and ls
    r = pairs.end(A, pid)
    assert r["cleanup"]["done"] is True
    gs, ls = residue(pid)
    assert gs == [] and ls == []                                                      # 회수된 grant 레코드까지 지워졌다
    import agent_msg
    assert agent_msg.get(m["id"])["status"] == "failed" and agent_msg.get(keep["id"])["status"] == "queued"
    assert closed == ["d1"]                                                           # pair 중에 만든 위임만(이전 것·남의 것은 그대로)
    assert not any((i.get("source") or {}).get("agentMsg") == m["id"] for i in queue_store.list_items())
    assert any((i.get("source") or {}).get("agentMsg") == keep["id"] for i in queue_store.list_items())
    assert get(pid).get("saga_done")
    snap = store.snapshot()
    assert not engine.decide(A, "session.read", "session:local/$2:102", {}, snap).allow


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("step", saga.step_names())
def test_saga_crash_at_every_step_then_sweep_is_clean(env, monkeypatch, step, when):
    pid = make_active(env, leases=True)
    m, keep, closed, queue_store = seed_stuff(env, monkeypatch, pid)
    saga.FAULT.update(step=step, when=when)
    with pytest.raises(saga.InjectedCrash):
        pairs.end(A, pid)
    assert get(pid)["state"] == "ended"                                               # 끝남 자체는 이미 저장됐다
    assert not engine.decide(A, "session.read", "session:local/$2:102", {}, store.snapshot()).allow     # 크래시 중에도 권한은 이미 닫혔다
    saga.FAULT.clear()
    done = saga.sweep()
    assert done, "sweep 이 이어서 끝내야 한다"
    gs, ls = residue(pid)
    assert gs == [] and ls == []
    import agent_msg
    assert agent_msg.get(m["id"])["status"] == "failed" and closed == ["d1"]
    rec = next(s for s in saga.list_all() if s["target"].get("pair") == pid)
    assert rec["state"] == "done" and set(rec["steps"]) == set(saga.step_names())
    assert saga.sweep() == []                                                         # 두 번째 sweep 은 할 일이 없다
    assert get(pid).get("saga_done")


def test_saga_verify_catches_a_late_arrival(env, monkeypatch):
    """정리 도중에 새로 들어온 구성원 간 메시지가 있으면 verify 가 잡고 saga 는 처음부터 다시 돈다."""
    import agent_msg
    pid = make_active(env)
    seed_stuff(env, monkeypatch, pid)
    real = pairs.saga_messages
    state = {"n": 0}

    def late(t):
        real(t)
        if state["n"] == 0:
            state["n"] = 1
            agent_msg.receive(from_host="local", from_label="x", from_session="bravo", to_session="alpha", text="too late")
    monkeypatch.setattr(pairs, "saga_messages", late)
    r = pairs.end(A, pid)
    assert r["cleanup"]["done"] is False                                              # 첫 시도는 verify 에서 멈췄다
    assert saga.sweep(now=env.clock() + 400)
    msgs = [x for x in agent_msg.list_messages(limit=50) if x.get("status") == "queued" and x["from"]["session"] == "bravo"]
    assert msgs == []


def test_saga_for_other_kinds_is_untouched_by_the_pair_steps(env):
    rec = saga.start("grant", {"grant_ids": ["g-none"]}, run_now=True)
    assert rec["state"] == "done"


# --- 끝내기 트리거 ------------------------------------------------------------------------------


def test_idle_two_hours_ends_the_pair(env, monkeypatch):
    pid = make_active(env)
    monkeypatch.setattr(pairs, "_activity", lambda: {})                               # tmux 는 읽히지만 활동 기록이 없다
    env.clock.advance(7199)
    assert pairs.sweep(env.clock()) == []
    env.clock.advance(2)
    assert pairs.sweep(env.clock()) == [pid]
    assert get(pid)["state"] == "ended" and get(pid)["end_reason"] == "idle" and residue(pid) == ([], [])


def test_session_activity_keeps_the_pair_alive_and_env_overrides_the_limit(env, monkeypatch):
    pid = make_active(env)
    monkeypatch.setenv("VT_PAIR_IDLE_MIN", "10")
    act = {"$1:101": env.clock() - 1000, "$2:102": env.clock() - 1000}
    monkeypatch.setattr(pairs, "_activity", lambda: act)
    env.clock.advance(1000)
    assert pairs.sweep(env.clock()) == [pid]                                           # 활동이 17분 전 → 10분 제한 밖
    pid2 = make_active(env, sessions=["charlie"], dirs=[env.proj + "/src"])
    act["$1:101"] = env.clock() - 5                                                    # 5초 전에 활동 → 10분 제한 안
    assert pid2 not in pairs.sweep(env.clock())
    assert get(pid2)["state"] == "active"


def test_unreadable_tmux_never_ends_a_pair(env, monkeypatch):
    pid = make_active(env)
    monkeypatch.setattr(pairs, "_activity", lambda: None)
    env.alive["v"] = None
    env.clock.advance(3 * 3600)
    assert pairs.sweep(env.clock()) == []
    assert get(pid)["state"] == "active"


def test_member_session_gone_ends_the_pair(env):
    pid = make_active(env, leases=True)
    env.alive["v"] = {"$1:101", "$3:103"}                                              # bravo 가 사라졌다
    assert pairs.sweep(env.clock()) == [pid]
    assert get(pid)["end_reason"] == "member_gone" and residue(pid) == ([], [])


def test_max_lifetime_expires_the_pair(env, monkeypatch):
    pid = make_active(env)
    monkeypatch.setattr(pairs, "_activity", lambda: None)
    env.clock.advance(8 * 3600 + 1)
    assert pairs.sweep(env.clock()) == [pid] and get(pid)["state"] == "expired" and get(pid)["end_reason"] == "max_lifetime"


def test_unjoined_pair_and_unapproved_request_expire(env):
    manage(A)
    v, _ = pairs.create(A, bundle(env))
    pairs.issue_code(A, v["id"])
    env.clock.advance(3601)
    assert pairs.sweep(env.clock()) == [v["id"]] and get(v["id"])["state"] == "expired"
    pid, _ = joined(env, sessions=["charlie"], dirs=[env.proj + "/src"])
    env.clock.advance(24 * 3600 + 1)
    assert pid in pairs.sweep(env.clock()) and get(pid)["end_reason"] == "approval_expired"


def test_panic_ends_every_live_pair(env):
    p1 = make_active(env)
    p2, _ = joined(env, sessions=["charlie"], dirs=[env.proj + "/src"])
    assert pairs.end_all(H, "panic") == 2
    assert get(p1)["state"] == "revoked" and get(p2)["state"] == "revoked" and residue(p1) == ([], []) and residue(p2) == ([], [])


def test_revoking_one_pair_grant_from_the_permissions_screen_ends_the_pair(env):
    from authz import inbox
    pid = make_active(env)
    gid = get(pid)["grants"][0]
    g = inbox.revoke(gid, None, None, H)
    assert g["kind"] == "pair" and get(pid)["state"] == "revoked" and residue(pid) == ([], [])


def test_list_for_scopes_agents_to_their_own_pairs(env):
    pid = make_active(env)
    assert [x["id"] for x in pairs.list_for(A, everyone=False)] == [pid]
    assert [x["id"] for x in pairs.list_for(B, everyone=False)] == [pid]
    assert pairs.list_for(C, everyone=False) == []
    assert [x["id"] for x in pairs.list_for(H, everyone=True)] == [pid]
    v = pairs.list_for(B, everyone=False)[0]
    assert v["role"] == "navigator" and "$1:101" not in str(v) and A.id not in str(v)   # 세션 key·주체 id 는 내보내지 않는다
    assert pairs.active_count() == 1


# --- 감사 · 권한 비트 ----------------------------------------------------------------------------


def test_audit_records_pair_events_without_content(env):
    pid = make_active(env, leases=True)
    pairs.handoff_offer(A, pid)
    pairs.handoff_accept(B, pid)
    pairs.end(A, pid)
    txt = audit.path().read_text(encoding="utf-8")
    for action in ("pair.create", "pair.code", "pair.join", "pair.approve", "pair.handoff_offer", "pair.handoff", "pair.end"):
        assert f'"action": "{action}"' in txt, action
    mine = "\n".join(l for l in txt.splitlines() if '"action": "pair.' in l)             # (lease 감사 줄은 lease 의 기존 규칙을 따른다)
    assert "alpha" not in mine and "bravo" not in mine and env.proj not in mine         # 세션 이름·경로 없음
    assert not re.search(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}", mine) and "phrase" not in mine   # 코드·확인 문구 없음
    ok, bad, n = audit.verify()
    assert ok and bad is None and n > 5


def test_store_and_audit_files_are_private_under_umask_022(env, monkeypatch):
    old = os.umask(0o022)
    try:
        os.chmod(env.tmp, 0o755)
        pid = make_active(env, leases=True)
        pairs.handoff_offer(A, pid)
        pairs.handoff_accept(B, pid)
        pairs.end(A, pid)
        seen = 0
        for root in (store.root(), audit.path().parent):
            for p in [root, *root.rglob("*")]:
                if p.is_symlink() or p.is_socket():
                    continue
                want = 0o700 if p.is_dir() else 0o600
                assert stat.S_IMODE(os.stat(p).st_mode) == want, (p, oct(stat.S_IMODE(os.stat(p).st_mode)))
                seen += 1
        assert seen and store.doc_path("pairs").exists()
    finally:
        os.umask(old)


# --- 기존 도구와의 공존 --------------------------------------------------------------------------


def test_migrate_verify_is_clean_with_an_active_pair_and_pair_grants_survive_apply(env, monkeypatch):
    from authz import migrate
    pid = make_active(env, leases=True)
    v = migrate.verify_store(tmux=None)
    assert v["clean"] is True, v["diff"]
    before = sorted(get(pid)["grants"])
    migrate.apply(tmux=None)
    assert sorted(g["id"] for g in grants_of(pid)) == before                     # legacy: 가 아닌 레코드는 이전 도구가 건드리지 않는다
    assert migrate.verify_store(tmux=None)["clean"] is True
