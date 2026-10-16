"""pair 역할 교체(handoff)와 관계 — 한 driver 불변식(스레드), 세대 번호, 2분 만료, 사람의 개입, 메시지·lease 관계. `test_authz_pairs.py` 의 환경을 쓴다."""

from __future__ import annotations

import threading

import pytest

from authz import engine, leases, msg_svc, pairs, store
from authz.errors import ShareError
from test_authz_pairs import (A, B, C, H, bundle, code_of, env, get, grants_of, make_active, manage)  # noqa: F401


# --- handoff ------------------------------------------------------------------------------------


def roles(pid):
    return {m["seat"]: m["role"] for m in get(pid)["members"]}


def drivers(pid):
    return [m for m in get(pid)["members"] if m["role"] == "driver"]


def test_handoff_offer_accept_swaps_roles_grants_and_generation(env):
    pid = make_active(env, leases=True)
    raw = pid[3:]
    assert get(pid)["generation"] == 0
    v = pairs.handoff_offer(A, pid)
    assert v["state"] == "handoff_pending" and v["handoff"]["expiresIn"] == 120 and v["generation"] == 0
    assert roles(pid) == {"initiator": "driver", "joiner": "navigator"}            # 수락 전에는 그대로
    v = pairs.handoff_accept(B, pid)
    assert v["state"] == "active" and v["generation"] == 1 and v["role"] == "driver"
    assert roles(pid) == {"initiator": "navigator", "joiner": "driver"}
    gs = {g["id"]: g for g in grants_of(pid)}
    assert set(gs[f"pp-{raw}-j-d0"]["scopes"]) == {"fs.read", "fs.write"}         # 새 driver 는 쓰기를, 옛 driver 는 읽기만
    assert set(gs[f"pp-{raw}-i-d0"]["scopes"]) == {"fs.read"}
    assert f"pp-{raw}-i-q" not in gs and f"pp-{raw}-j-q" in gs                      # spawn 쿼터도 따라간다
    snap = store.snapshot()
    res = f"fs:local{env.proj}/x"
    assert engine.decide(B, "fs.write", res, {"op": "write"}, snap).allow and not engine.decide(A, "fs.write", res, {"op": "write"}, snap).allow
    # 세션 입력: 새 driver B 는 A 의 세션이 번들에 없으므로 영향 없음 — 번들에 있는 bravo 에 대한 A 의 입력은 이제 막힌다
    assert not engine.decide(A, "session.input", "session:local/$2:102", {"mode": "line"}, snap).allow
    # lease 는 새 driver 로 옮겨졌다
    held = [l for l in leases.active_now(alive=None) if l.get("pair_id") == pid]
    assert [l["holder"] for l in held] == [B.id]
    assert code_of(pairs.handoff_accept, B, pid)[0] == "no_offer"                  # 같은 제안을 두 번 받을 수 없다
    pairs.handoff_offer(B, pid)
    assert pairs.handoff_accept(A, pid)["generation"] == 2                          # 다시 돌려받으면 세대 +1


def test_only_the_navigator_can_accept_and_only_the_driver_can_offer(env):
    pid = make_active(env)
    assert code_of(pairs.handoff_offer, B, pid)[:2] == ("not_driver", 403)
    assert code_of(pairs.handoff_offer, C, pid)[:2] == ("not_found", 404)
    pairs.handoff_offer(A, pid)
    assert code_of(pairs.handoff_accept, A, pid)[:2] == ("not_navigator", 403)       # 제안한 사람이 스스로 수락할 수 없다
    assert code_of(pairs.handoff_accept, C, pid)[0] == "not_found"
    assert roles(pid)["initiator"] == "driver" and get(pid)["generation"] == 0


def test_offer_times_out_after_two_minutes_back_to_the_previous_state(env):
    pid = make_active(env)
    pairs.handoff_offer(A, pid)
    env.clock.advance(119)
    assert pairs.status(A, pid)["state"] == "handoff_pending"
    env.clock.advance(2)
    assert code_of(pairs.handoff_accept, B, pid)[:2] == ("no_offer", 409)
    s = pairs.status(A, pid)
    assert s["state"] == "active" and s["handoff"] is None and s["generation"] == 0 and roles(pid)["initiator"] == "driver"
    pairs.handoff_offer(A, pid)                                                    # 새 제안은 다시 할 수 있다
    env.clock.advance(125)
    assert pairs.sweep(env.clock()) == [] and get(pid)["state"] == "active"          # sweep 도 원상복구한다


def test_stale_offer_is_rejected(env):
    pid = make_active(env)
    pairs.handoff_offer(A, pid)
    with store.txn() as d:                                                          # 제안 뒤에 세대가 바뀐 상황
        next(x for x in d["pairs"]["items"] if x["id"] == pid)["generation"] = 5
    assert code_of(pairs.handoff_accept, B, pid)[0] == "stale_offer"
    assert get(pid)["state"] == "active" and get(pid)["handoff"] is None


def test_human_override_swaps_without_accept(env):
    pid = make_active(env)
    v = pairs.handoff_offer(H, pid)                                                 # 지켜보는 사람 — 수락 없이 곧바로
    assert v["state"] == "active" and v["generation"] == 1 and roles(pid) == {"initiator": "navigator", "joiner": "driver"}
    # 사람은 어떤 역할에서도 막히지 않는다(사람 기본 권한) — navigator 인 쪽의 세션에도 입력할 수 있다
    snap = store.snapshot()
    assert engine.decide(H, "session.input", "session:local/$2:102", {"mode": "line"}, snap).allow
    assert not engine.decide(A, "session.input", "session:local/$2:102", {"mode": "line"}, snap).allow


def test_human_member_driver_offers_and_agent_accepts(env):
    """사람이 만든 pair: 사람 driver + 에이전트 navigator → 사람이 제안하고 에이전트가 수락, 에이전트는 driver 가 된다."""
    v, _ = pairs.create(H, bundle(env, sessions=["alpha"], role="driver"))
    c = pairs.issue_code(H, v["id"])
    pairs.join(B, c["code"])
    pairs.decide(pairs.request_id_of(v["id"]), "allow", H, alive=None)
    gs = grants_of(v["id"])
    assert {g["subject"] for g in gs} == {B.id}                                      # 사람 구성원에게는 따로 grant 를 쓰지 않는다
    assert not any("session.input" in g["scopes"] for g in gs)                       # B 는 navigator
    assert pairs.handoff_offer(H, v["id"])["state"] == "handoff_pending"              # 사람 driver 는 제안한다
    assert pairs.handoff_accept(B, v["id"])["isDriver"] is True
    assert any("session.input" in g["scopes"] for g in grants_of(v["id"]))
    assert code_of(pairs.handoff_accept, B, v["id"])[0] == "no_offer"


def test_exactly_one_driver_under_concurrent_handoffs(env):
    pid = make_active(env)
    errors, barrier = [], threading.Barrier(8)

    def worker(p, n):
        try:
            barrier.wait()
            for _ in range(n):
                for fn in (pairs.handoff_offer, pairs.handoff_accept):
                    try:
                        fn(p, pid)
                    except ShareError:
                        pass
                    ds = drivers(pid)
                    if len(ds) != 1:
                        errors.append(f"drivers={len(ds)}")
        except BaseException as e:  # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=worker, args=(p, 6)) for p in (A, B, A, B, A, B, A, B)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert not errors, errors[:3]
    assert len(drivers(pid)) == 1
    pr = get(pid)
    assert pr["state"] in ("active", "handoff_pending")
    # 경쟁 중에 교체가 한 번이라도 일어났는지는 스케줄링에 달렸다(오래된 파이썬에서는 0일 수 있다) — 그래서 끝난 뒤 정해진 교체를 한 번 더 해
    # 세대가 정확히 1 오르는지 본다. 마지막 driver 는 세대의 홀짝과 맞는다.
    g0 = pr["generation"]
    for who in (A, B):
        try:
            pairs.handoff_offer(who, pid)        # driver 만 성공한다(이미 제안 중이면 같은 제안)
        except ShareError:
            pass
    for who in (A, B):
        try:
            pairs.handoff_accept(who, pid)       # 제안받은 쪽만 성공한다
        except ShareError:
            pass
    pr = get(pid)
    assert pr["generation"] == g0 + 1
    assert (drivers(pid)[0]["seat"] == "joiner") == (pr["generation"] % 2 == 1)
    gs = {g["id"]: g for g in grants_of(pid)}
    drv = drivers(pid)[0]
    assert sum(1 for g in gs.values() if "fs.write" in g["scopes"]) == 1 and next(g for g in gs.values() if "fs.write" in g["scopes"])["subject"] == drv["principal"]


# --- 관계: 메시지 · lease -----------------------------------------------------------------------


def test_active_pair_members_are_related_for_messages_and_nobody_else(env):
    assert msg_svc.relation("$1:101", "$2:102") is None
    pid = make_active(env)
    assert msg_svc.relation("$1:101", "$2:102") == "pair" and msg_svc.relation("$2:102", "$1:101") == "pair"
    assert msg_svc.relation("$1:101", "$3:103") is None and msg_svc.pair_related("$2:102", "$3:103") is False
    pairs.handoff_offer(A, pid)                                                      # handoff 진행 중에도 같은 pair
    assert msg_svc.relation("$1:101", "$2:102") == "pair"
    pairs.end(A, pid)
    assert msg_svc.relation("$1:101", "$2:102") is None                              # 끝나면 관계도 끝


def test_pair_members_message_without_approval_when_msg_is_enforced(env, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "pair,msg")
    # 아직 합류 전 — 관계 없음 → 거부(grant 가 필요)
    manage(A)
    v, _ = pairs.create(A, bundle(env))
    c = pairs.issue_code(A, v["id"])
    pairs.join(B, c["code"])
    assert msg_svc.authorize_local("$2:102", "bravo", "$1:101", None).allow is False
    pairs.decide(pairs.request_id_of(v["id"]), "allow", H, alive=None)
    with store.txn() as d:                                                           # 관계만으로 허용되는지 보려고 pair 의 msg grant 를 지운다
        d["grants"]["items"] = [g for g in d["grants"]["items"] if not g["id"].endswith(("-i-m", "-j-m"))]
    r = msg_svc.authorize_local("$2:102", "bravo", "$1:101", None)
    assert r.allow and r.relation == "pair"
    assert msg_svc.authorize_local("$3:103", "charlie", "$1:101", None).allow is False


def test_same_pair_leases_do_not_conflict_and_others_do(env):
    pid = make_active(env)
    manage(C)
    from test_authz_leases import grant as lgrant
    for p in (A, B, C):
        lgrant(p, env.root)
    la = leases.acquire(A, env.proj + "/src", mode="write", alive=None)
    lb = leases.acquire(B, env.proj + "/src", mode="write", alive=None)               # 같은 pair, 둘 다 non-exclusive → 충돌 아님
    assert la["id"] != lb["id"]
    with pytest.raises(ShareError) as e:
        leases.acquire(C, env.proj + "/src", mode="write", alive=None)                  # pair 밖은 충돌
    assert e.value.code == "lease_conflict"
    with pytest.raises(ShareError) as e:
        leases.acquire(B, env.proj + "/src", mode="write", exclusive=True, alive=None)   # exclusive 면 같은 pair 여도 충돌
    assert e.value.code == "lease_conflict"
    assert pairs.same_pair_holders(A.id, B.id) and not pairs.same_pair_holders(A.id, C.id)
    pairs.end(A, pid)
    assert not pairs.same_pair_holders(A.id, B.id)                                       # 끝나면 예외도 끝


def test_same_pair_lease_exception_is_off_when_the_switch_is_off(env, monkeypatch):
    make_active(env)
    from test_authz_leases import grant as lgrant
    for p in (A, B):
        lgrant(p, env.root)
    leases.acquire(A, env.proj + "/src", mode="write", alive=None)
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    with pytest.raises(ShareError) as e:
        leases.acquire(B, env.proj + "/src", mode="write", alive=None)
    assert e.value.code == "lease_conflict"


def test_bundle_leases_go_to_the_driver_and_conflict_at_approval_is_atomic(env):
    from test_authz_leases import grant as lgrant
    lgrant(C, env.root)
    leases.acquire(C, env.proj, mode="write", alive=None)                               # 묶음 폴더를 다른 세션이 이미 쓰는 중
    manage(A)
    v, _ = pairs.create(A, bundle(env, leases=True))
    c = pairs.issue_code(A, v["id"])
    pairs.join(B, c["code"])
    e = code_of(pairs.decide, pairs.request_id_of(v["id"]), "allow", H, alive=None)
    assert e[:2] == ("lease_conflict", 409)
    assert get(v["id"])["state"] == "joined" and grants_of(v["id"]) == []             # 아무것도 반쯤 쓰이지 않았다
