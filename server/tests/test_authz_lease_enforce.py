"""`VT_AUTHZ_ENFORCE=lease` — FarShell 이 대신 하는 쓰기의 강제, 위임 상속, 회수 saga.

플래그가 꺼져 있으면 같은 호출이 아무것도 막지 않는다(꺼짐 = 기존 동작)는 것도 여기서 쓰기 라우트마다 확인한다.
"""

from __future__ import annotations

import os
import subprocess

import pytest

import agent_msg
import delegation
import queue_store
from authz import audit, leases, saga, store
from authz import principals as P

from test_delegation import _caller, tm  # noqa: F401  (격리 가짜 tmux 픽스처)
from test_authz_leases import _Sock, grant

A = P.agent("$1:101", name="alpha")
B = P.agent("$2:102", name="bravo")
H = P.human("dev1")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    root = tmp_path.resolve()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    alive = {"v": None}
    monkeypatch.setattr(leases, "alive_keys", lambda fresh=False: alive["v"])
    leases.reset_rate()
    saga.FAULT.clear()

    class E:
        pass
    e = E()
    e.root, e.alive = root, alive
    return e


def on(monkeypatch, extra=""):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "lease" + extra)


def client(principal):
    from starlette.testclient import TestClient

    import main
    return TestClient(_Sock(main.app, principal))


def repo(root, name="r"):
    d = root / name
    d.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)
    (d / "a.txt").write_text("a")
    (d / "b.txt").write_text("b")
    return d


# --- 쓰기 라우트: 꺼짐/켜짐 ----------------------------------------------------------------------


def _holder(env, repo_dir):
    grant(A, env.root)
    return leases.acquire(A, str(repo_dir), mode="write")


@pytest.mark.parametrize("flag", [False, True])
def test_git_stage_unstage_commit_by_flag(env, monkeypatch, flag):
    r = repo(env.root)
    _holder(env, r)
    if flag:
        on(monkeypatch)
    want = 403 if flag else 200
    with client(B) as cb, client(A) as ca:
        s = cb.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt"]})
        assert s.status_code == want, s.text
        if flag:
            body = s.json()
            assert body["error"] == "lease_conflict" and body["holder"] == "alpha" and body["op"] == "git.stage"
        # 보유자 본인은 언제나 통과
        assert ca.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt", "b.txt"]}).status_code == 200
        u = cb.post("/api/git/unstage", json={"repo": str(r), "files": ["b.txt"]})
        assert u.status_code == want
        c = cb.post("/api/git/commit", json={"repo": str(r), "message": "m"})
        assert c.status_code == want, c.text
        if flag:
            assert c.json()["op"] == "git.commit" and c.json()["paths"] == 2        # 스테이지된 두 파일 모두가 대상
            assert subprocess.run(["git", "-C", str(r), "log", "--oneline"], capture_output=True).returncode != 0   # 커밋이 일어나지 않았다
        else:
            assert c.json()["committed"] is True


def test_commit_checks_every_staged_path_including_renames(env, monkeypatch):
    on(monkeypatch)
    r = repo(env.root)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "init"], check=True, capture_output=True)
    (r / "sub").mkdir()
    grant(A, env.root)
    leases.acquire(A, str(r / "a.txt"), mode="write")                      # 파일 하나만 lease
    subprocess.run(["git", "-C", str(r), "mv", "a.txt", "sub/moved name.txt"], check=True, capture_output=True)   # 이름 바꾸기 + 공백
    with client(B) as cb:
        c = cb.post("/api/git/commit", json={"repo": str(r), "message": "m"})
        assert c.status_code == 403 and c.json()["paths"] == 1                 # 옛 이름(a.txt 삭제)이 lease 에 걸린다
    with client(A) as ca:
        assert ca.post("/api/git/commit", json={"repo": str(r), "message": "m"}).status_code == 200


def test_unrelated_paths_and_released_leases_do_not_block(env, monkeypatch):
    on(monkeypatch)
    r = repo(env.root)
    grant(A, env.root)
    l = leases.acquire(A, str(r / "a.txt"))
    with client(B) as cb:
        assert cb.post("/api/git/stage", json={"repo": str(r), "files": ["b.txt"]}).status_code == 200
        assert cb.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt"]}).status_code == 403
        leases.release(A, l["id"])
        assert cb.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt"]}).status_code == 200


def test_read_only_non_exclusive_lease_does_not_block_writes(env, monkeypatch):
    on(monkeypatch)
    r = repo(env.root)
    grant(A, env.root, scopes=("fs.read", "fs.write"))
    leases.acquire(A, str(r), mode="read")
    with client(B) as cb:
        assert cb.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt"]}).status_code == 200
        leases.acquire(A, str(r), mode="read", exclusive=True)
        assert cb.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt"]}).status_code == 403


def test_human_write_is_allowed_with_audit_and_notice(env, monkeypatch):
    on(monkeypatch)
    r = repo(env.root)
    l = _holder(env, r)
    events = []

    async def fake_emit(msg):
        events.append(msg)
    from authz import events as authz_events
    monkeypatch.setattr(authz_events, "emit", fake_emit)
    with client(H) as ch:
        assert ch.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt"]}).status_code == 200
    assert events == [{"type": "lease_notice", "id": l["id"], "kind": "override"}]           # 내용 없는 알림
    ov = [x for x in audit.read_recent(10, "lease.override")]
    assert ov and ov[0]["detail"]["lease"] == l["id"] and "a.txt" not in str(ov[0])


def test_unidentified_callers_are_blocked_by_any_write_lease(env, monkeypatch):
    on(monkeypatch)
    r = repo(env.root)
    _holder(env, r)
    anon = P.cli(os.getuid(), 99)                                           # 보유자가 아닌 비human
    with client(anon) as c:
        assert c.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt"]}).status_code == 403


def test_corrupt_lease_store_denies_non_humans_only(env, monkeypatch):
    on(monkeypatch)
    r = repo(env.root)
    _holder(env, r)
    store.doc_path("leases").write_text("{bad")
    with client(B) as cb, client(H) as ch:
        x = cb.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt"]})
        assert x.status_code == 503 and x.json()["error"] == "lease_unavailable"
        assert ch.post("/api/git/stage", json={"repo": str(r), "files": ["a.txt"]}).status_code == 200


@pytest.mark.parametrize("flag", [False, True])
def test_upload_and_insert_target_the_session_workdir(env, monkeypatch, flag):
    r = repo(env.root)
    _holder(env, r)
    monkeypatch.setattr(leases, "session_cwd", lambda s: str(r))
    import file_store
    import tmux_target
    monkeypatch.setenv("VT_STATE_DIR", str(env.root / "vt"))
    monkeypatch.setattr(tmux_target, "session_pane", lambda s: "%1")
    monkeypatch.setattr(tmux_target, "type_to_tmux", lambda pane, text: True)
    monkeypatch.setattr(file_store, "real_path_for", lambda fid: env.root / "att.bin")
    if flag:
        on(monkeypatch)
    want = 403 if flag else 200
    with client(B) as cb:
        up = cb.post("/api/upload?session_id=sess", files={"file": ("x.txt", b"hello")})
        assert up.status_code == want, up.text
        ins = cb.post("/api/files/abc/insert", json={"session": "sess"})
        assert ins.status_code == want, ins.text
        if flag:
            assert ins.json()["op"] == "insert" and up.json()["op"] == "upload"
        # 세션이 없거나 작업 폴더를 모르면 대상이 없으니 막지 않는다
        monkeypatch.setattr(leases, "session_cwd", lambda s: None)
        assert cb.post("/api/files/abc/insert", json={"session": "sess"}).status_code == 200


def test_peer_file_drop_is_blocked_before_storing(env, monkeypatch):
    """fs.drop — 라우트 함수를 직접 부른다(HMAC 관문은 peer 시험이 본다). 저장 전에 막혀야 한다."""
    import asyncio

    import file_store
    import routes.peer as peer_routes
    on(monkeypatch)
    r = repo(env.root)
    _holder(env, r)
    monkeypatch.setattr(leases, "session_cwd", lambda s: str(r))
    stored = []
    monkeypatch.setattr(file_store, "find_by_origin", lambda o: None)
    monkeypatch.setattr(peer_routes, "_store_peer_file", lambda *a: stored.append(a) or {"id": "x"})

    class Req:
        headers = {"x-peer-file-name": "f.txt", "x-peer-file-id": "1", "x-peer-file-session": "sess"}
        scope = {"type": "http"}
        state = type("S", (), {})()
        cookies, query_params, client, url = {}, {}, None, None
    fn = getattr(peer_routes.peer_file, "__wrapped__", peer_routes.peer_file)
    out = asyncio.run(fn(Req(), {"id": "pg"}, b"data"))
    assert out[0].status_code == 403 and stored == []


# --- 위임 ---------------------------------------------------------------------------------------


@pytest.fixture
def world(env, tm, monkeypatch):
    env.alive["v"] = None
    monkeypatch.setattr(leases, "alive_keys", lambda fresh=False: {v["key"] for v in tm.sessions.values()})
    on(monkeypatch)
    env.tm = tm
    return env


def pcaller(world, name):
    c = _caller(world.tm, name)
    return c, P.agent(c.key, name=name)


def test_spawn_cwd_inside_another_holders_lease_is_refused(world):
    c, p = pcaller(world, "main")
    _o, other = pcaller(world, "other")
    grant(other, world.root)
    leases.acquire(other, str(world.root))
    with pytest.raises(delegation.DelegationError) as e:
        delegation.spawn(c, task="x", title="kid")
    assert (e.value.code, e.value.status) == ("lease_conflict", 403)
    assert delegation.list_all() == []


def test_spawn_is_untouched_when_the_flag_is_off(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    c, p = pcaller(world, "main")
    _o, other = pcaller(world, "other")
    grant(other, world.root)
    leases.acquire(other, str(world.root))
    did = delegation.spawn(c, task="x", title="kid")["delegation"]
    assert [l for l in leases.active_now() if l.get("deleg") == did] == []


def test_worktree_child_gets_an_exclusive_system_lease_released_on_close(world, monkeypatch):
    c, p = pcaller(world, "main")
    wt = world.root / "wt-feature"
    wt.mkdir()
    monkeypatch.setattr(delegation, "_prepare_worktree", lambda *a, **k: str(wt))
    # 부모는 원래 폴더 lease 를 가졌다 — worktree 자식은 그것을 상속하지 않는다
    grant(p, world.root)
    pl = leases.acquire(p, str(world.root / "orig"), mode="write")
    r = delegation.spawn(c, task="x", title="kid", worktree_branch="feat/x")
    did = r["delegation"]
    mine = [l for l in leases.active_now() if l.get("deleg") == did]
    assert len(mine) == 1
    l = mine[0]
    assert l["glob"] == str(wt) and l["mode"] == "write" and l["exclusive"] is True
    assert l["approved_via"] == "system" and l["grant_id"] is None and l["holder_name"] == r["child"] and not l.get("derived_from")
    assert pl["id"] in {x["id"] for x in leases.active_now()}                       # 부모의 lease 는 그대로
    # 자식은 자기 worktree 에서만 쓸 수 있다
    child = P.agent(l["holder_key"], name=r["child"])
    assert leases.enforce_write_sync(child, [str(wt / "f.py")], op="t")[0] is None
    assert leases.enforce_write_sync(child, [str(world.root / "orig" / "f.py")], op="t")[0]["error"] == "lease_conflict"
    # 부모도 자식의 exclusive worktree 에는 쓸 수 없다(상속은 한 방향이 아니다 — 폴더 lease 는 같은 폴더 자식만)
    assert leases.enforce_write_sync(p, [str(wt / "f.py")], op="t")[0]["error"] == "lease_conflict"
    delegation.close(did, caller_key=c.key)
    assert [x for x in leases.active_now() if x.get("deleg") == did] == []
    assert pl["id"] in {x["id"] for x in leases.active_now()}
    acts = [x["action"] for x in audit.read_recent(30, "lease.")]
    assert "lease.acquire" in acts and "lease.revoke" in acts
    ac = [x for x in audit.read_recent(30, "lease.acquire") if x["detail"]["via"] == "system"]
    assert ac and ac[0]["principal"].startswith("agent:")


def test_worktree_lease_conflict_fails_the_spawn_cleanly(world, monkeypatch):
    c, p = pcaller(world, "main")
    _o, other = pcaller(world, "other")
    wt = world.root / "wt-x"
    wt.mkdir()
    monkeypatch.setattr(delegation, "_prepare_worktree", lambda *a, **k: str(wt))
    grant(other, world.root)
    leases.acquire(other, str(wt / "*"))
    with pytest.raises(delegation.DelegationError) as e:
        delegation.spawn(c, task="x", title="kid", worktree_branch="feat/x")
    assert e.value.code == "lease_conflict"
    assert delegation.list_all() == [] and world.tm.killed == ["kid"]


def test_same_folder_child_shares_the_parents_lease_and_it_cascades(world):
    c, p = pcaller(world, "main")
    grant(p, world.root)
    pl = leases.acquire(p, str(world.root), mode="write", exclusive=True)
    r = delegation.spawn(c, task="x", title="kid")
    did = r["delegation"]
    derived = [l for l in leases.active_now() if l.get("deleg") == did]
    assert len(derived) == 1 and derived[0]["derived_from"] == pl["id"]
    d = derived[0]
    assert d["glob"] == pl["glob"] and d["mode"] == "write" and d["exclusive"] and d["approved_via"] == "system"
    assert d["expires"] == pytest.approx(pl["expires"], abs=1)
    child = P.agent(d["holder_key"], name="kid")
    f = str(world.root / "f.py")
    assert leases.enforce_write_sync(child, [f], op="t")[0] is None               # 자식은 부모 lease 안에서 쓴다
    assert leases.enforce_write_sync(p, [f], op="t")[0] is None
    _s, stranger = pcaller(world, "stranger")
    assert leases.enforce_write_sync(stranger, [f], op="t")[0]["error"] == "lease_conflict"
    # 형제 자식도 같은 lease 를 나눠 쓴다
    r2 = delegation.spawn(_caller(world.tm, "main"), task="y", title="kid2")
    d2 = [l for l in leases.active_now() if l.get("deleg") == r2["delegation"]][0]
    assert leases.enforce_write_sync(P.agent(d2["holder_key"], name="kid2"), [f], op="t")[0] is None
    # 파생 lease 는 직접 갱신하지 못한다(부모를 갱신하면 따라간다)
    with pytest.raises(leases.LeaseError) as e:
        leases.renew(child, d["id"])
    assert e.value.code == "derived_lease"
    leases.renew(p, pl["id"], ttl=3000)
    assert [l for l in leases.active_now() if l["id"] == d["id"]][0]["expires"] == pytest.approx(
        [l for l in leases.active_now() if l["id"] == pl["id"]][0]["expires"], abs=1)
    # 위임을 닫으면 그 자식의 몫만 사라지고 부모는 남는다. 부모를 놓으면 남은 형제 몫도 사라진다
    delegation.close(did, caller_key=c.key)
    ids = {l["id"] for l in leases.active_now()}
    assert d["id"] not in ids and pl["id"] in ids and d2["id"] in ids
    leases.release(p, pl["id"])
    assert leases.active_now() == []
    assert leases.enforce_write_sync(child, [f], op="t")[0] is None               # 아무도 안 잡았으니 막을 것도 없다


def test_no_inheritance_when_the_parent_holds_no_lease_over_the_folder(world):
    c, p = pcaller(world, "main")
    grant(p, world.root)
    leases.acquire(p, str(world.root / "elsewhere"), mode="write")
    did = delegation.spawn(c, task="x", title="kid")["delegation"]
    assert [l for l in leases.active_now() if l.get("deleg") == did] == []


def test_child_session_death_ends_its_derived_lease(world):
    c, p = pcaller(world, "main")
    grant(p, world.root)
    pl = leases.acquire(p, str(world.root))
    r = delegation.spawn(c, task="x", title="kid")
    assert len(leases.active_now()) == 2
    world.tm.sessions.pop("kid")
    assert [l["id"] for l in leases.active_now()] == [pl["id"]]


# --- saga -------------------------------------------------------------------------------------


def build_saga_world(world, monkeypatch):
    """에이전트 main 이 grant·lease·열린 위임(자식 lease 포함)·큐 항목·미전달 메시지를 가진 상태."""
    c, p = pcaller(world, "main")
    grant(p, world.root, gid="g-main")
    pl = leases.acquire(p, str(world.root))
    did = delegation.spawn(c, task="work", title="kid")["delegation"]
    monkeypatch.setattr(agent_msg, "check_rate", lambda *a: None)
    m = agent_msg.receive(from_host="local", from_label="local", from_session="main", to_session="other", text="hi")
    assert m["status"] == "queued"
    return {"c": c, "p": p, "pl": pl, "did": did, "mid": m["id"], "target": {"grant_ids": ["g-main"], "key": c.key, "names": ["main"]}}


def residue(w) -> dict:
    items, _ = leases.read_items()
    g = next((x for x in store._read("grants")[0]["items"] if x["id"] == "g-main"), None)
    d = delegation.get(w["did"])
    queued_msg = [x for x in queue_store.list_items() if (x.get("source") or {}).get("agentMsg") == w["mid"]]
    queued_task = [x for x in queue_store.list_items() if (x.get("source") or {}).get("delegation") == w["did"]
                   and x["source"].get("kind") == "task"]
    return {"leases": len(items), "grant": g.get("state") if g else None, "deleg": d["state"] if d else None,
            "msg": agent_msg.get(w["mid"])["status"], "queued": len(queued_msg) + len(queued_task)}


CLEAN = {"leases": 0, "grant": "revoked", "deleg": "closed", "msg": "failed", "queued": 0}


def test_saga_runs_every_step_in_order_and_leaves_nothing(world, monkeypatch):
    w = build_saga_world(world, monkeypatch)
    before = residue(w)
    assert before["leases"] == 2 and before["grant"] == "active" and before["msg"] == "queued"
    rec = saga.start("principal", w["target"])
    assert rec["state"] == "done" and set(rec["steps"]) == set(saga.step_names())
    assert residue(w) == CLEAN
    d = delegation.get(w["did"])
    assert d["close_reason"] == "revoked"
    assert [x for x in audit.read_recent(10, "authz.saga")][0]["result"] == "done"
    assert saga.start("principal", w["target"])["id"] != rec["id"]                      # 끝난 saga 는 새로 돈다(멱등: 두 번째도 깨끗)
    assert residue(w) == CLEAN


STEPS = saga.step_names()


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("step", STEPS)
def test_saga_crash_at_every_step_then_sweep_is_clean(world, monkeypatch, step, when):
    w = build_saga_world(world, monkeypatch)
    saga.FAULT.update(step=step, when=when)
    with pytest.raises(saga.InjectedCrash):
        saga.start("principal", w["target"])
    rec = saga.list_all()[-1]
    assert rec["state"] == "running"                                                    # 죽었으니 끝나지 않았다
    assert set(rec["steps"]) == set(STEPS[:STEPS.index(step)])                         # 죽은 단계 앞까지만 기록됐다(뒤에 죽어도 그 단계는 기록 전)
    assert saga.sweep() == [rec["id"]]                                                  # lifespan sweep 이 이어한다
    after = saga.get(rec["id"])
    assert after["state"] == "done" and set(after["steps"]) == set(STEPS)
    assert residue(w) == CLEAN
    assert saga.sweep() == []


def test_saga_step_failure_is_retried_by_sweep(world, monkeypatch):
    w = build_saga_world(world, monkeypatch)
    real = saga._close_delegations
    calls = {"n": 0}

    def flaky(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(t)
    monkeypatch.setattr(saga, "_STEPS", [(n, flaky if n == "close_delegations" else f) for n, f in saga._STEPS])
    rec = saga.start("principal", w["target"])
    assert rec["state"] == "running" and "close_delegations" in rec["error"] and rec["attempts"] == 1
    assert "drop_queued" in rec["steps"] and "close_delegations" not in rec["steps"]
    assert saga.sweep(now=rec["updated"] + 1) == []                                      # 방금 실패 — 잠시 늦춘다
    assert saga.sweep(now=rec["updated"] + 10) == [rec["id"]]
    assert saga.get(rec["id"])["state"] == "done" and residue(w) == CLEAN


def test_saga_verify_failure_restarts_from_the_top(world, monkeypatch):
    w = build_saga_world(world, monkeypatch)
    real = saga._release_leases
    first = {"v": True}

    def racing(t):                                   # 정리 도중 누군가 다시 lease 를 만든다
        real(t)
        if first["v"]:
            first["v"] = False
            with store.txn() as d:                    # grant 가 이미 거둬진 뒤라 정상 경로로는 못 잡는다 — 레코드를 직접 되살린다
                d["leases"]["items"].append({**w["pl"], "id": "ls-again", "grant_id": "g-main", "holder": w["p"].id,
                                             "holder_key": w["p"].attrs["key"], "glob": str(world.root / "again"), "state": "active",
                                             "resource": "fs:local" + str(world.root / "again"), "derived_from": None, "deleg": None})
    monkeypatch.setattr(saga, "_STEPS", [(n, racing if n == "release_leases" else f) for n, f in saga._STEPS])
    # 새 lease 는 grant g-main 이 근거라 verify 의 대상이다 → 한 바퀴 더 돌아 지운다
    rec = saga.start("principal", w["target"])
    assert rec["state"] == "running" and "leases=1" in rec["error"] and rec["steps"] == {}
    assert saga.sweep(now=rec["updated"] + 10) == [rec["id"]]
    assert saga.get(rec["id"])["state"] == "done" and residue(w) == CLEAN


def test_saga_gives_up_visibly_after_max_attempts(world, monkeypatch):
    w = build_saga_world(world, monkeypatch)
    monkeypatch.setattr(saga, "_STEPS", [("close_delegations", lambda t: (_ for _ in ()).throw(RuntimeError("x")))])
    rec = saga.start("principal", w["target"])
    for _ in range(saga.MAX_ATTEMPTS):
        rec = saga.run(rec["id"]) if rec["state"] == "running" else rec
    assert rec["state"] == "stuck" and saga.pending() and saga.pending()[0]["id"] == rec["id"]


def test_registration_list_lets_later_phases_add_steps(world, monkeypatch):
    seen = []
    saga.register("pairs", lambda t: seen.append(t.get("key")))
    try:
        assert saga.step_names().index("pairs") == saga.step_names().index("verify") - 1
        w = build_saga_world(world, monkeypatch)
        saga.start("principal", w["target"])
        assert seen == [w["c"].key]
    finally:
        saga.unregister("pairs")
    assert "pairs" not in saga.step_names()


def test_saga_ignores_grants_made_after_it_started(world, monkeypatch):
    w = build_saga_world(world, monkeypatch)
    rec = saga.start("principal", {"key": w["c"].key}, run_now=False)
    grant(w["p"], world.root, gid="g-new")
    with store.txn() as d:
        next(x for x in d["grants"]["items"] if x["id"] == "g-new")["created"] = rec["created"] + 5
    saga.run(rec["id"])
    assert saga.get(rec["id"])["state"] == "done"
    g = {x["id"]: x for x in store._read("grants")[0]["items"]}
    assert g["g-new"]["state"] == "active" and g["g-main"]["state"] == "revoked"


def test_peer_saga_cancels_that_peers_undelivered_messages(world, monkeypatch):
    monkeypatch.setattr(agent_msg, "check_rate", lambda *a: None)
    m = agent_msg.receive(from_host="peer1", from_label="p", from_session="s", to_session="x", text="hi")
    other = agent_msg.receive(from_host="peer2", from_label="p", from_session="s", to_session="x", text="hi")
    rec = saga.after_peer_deny("peer1")
    assert rec["state"] == "done"
    assert agent_msg.get(m["id"])["status"] == "failed" and agent_msg.get(other["id"])["status"] == "queued"
    assert not [x for x in queue_store.list_items() if (x.get("source") or {}).get("agentMsg") == m["id"]]


# --- 기존 회수 경로 연결 -----------------------------------------------------------------------


@pytest.mark.parametrize("flag", [False, True])
def test_grant_revoke_route_runs_the_saga_only_when_enabled(world, monkeypatch, flag):
    import main
    import routes.authz as authz_routes
    human_gate = authz_routes.human_router.dependencies[0].dependency      # 라우터에 실제로 걸린 사람 관문(다른 시험이 auth 를 다시 불러와도 같은 객체)
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "lease" if flag else "")
    p = P.agent("$5:105", name="five")
    world.tm.add("five")
    gid = "gg-test1"
    with store.txn() as d:
        d["grants"]["items"].append({
            "id": gid, "origin": "authz:grant:test1", "kind": "generic", "subject": p.id, "resource": f"fs:local{world.root}/*",
            "scopes": ["fs.write"], "sconds": {"fs.write": {"ops": ["write"]}}, "schema": 2, "state": "active",
            "approved_by": "human:test", "approved_via": "manual", "expires": None, "conds": {}})
    world.alive["v"] = None
    l = leases.acquire(p, str(world.root / "x"))
    assert l["id"]
    main.app.dependency_overrides[human_gate] = lambda: None
    try:
        from starlette.testclient import TestClient
        with TestClient(main.app) as c:
            res = c.delete(f"/api/authz/grants/{gid}")
        assert res.status_code == 200, res.text
    finally:
        main.app.dependency_overrides.clear()
    assert leases.active_now() == []                                                    # 근거 grant 가 사라져 lease 도 무효
    if flag:
        s = saga.list_all()
        assert len(s) == 1 and s[0]["kind"] == "grant" and s[0]["state"] == "done" and s[0]["target"]["grant_ids"] == [gid]
        assert leases.read_items()[0] == []                                              # 파일에서도 정리됐다
    else:
        assert saga.list_all() == [] and not store.doc_path("sagas").exists()           # 꺼짐: saga 문서조차 만들지 않는다
        assert len(leases.read_items()[0]) == 1                                          # 기존 경로만 — 정리는 sweep 의 몫


def test_panic_and_host_deny_start_sagas_only_when_enabled(world, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    assert saga.after_panic() is None and saga.after_peer_deny("p") is None and saga.after_grant_revoke(["x"]) is None
    on(monkeypatch)
    w = build_saga_world(world, monkeypatch)
    rec = saga.after_panic()
    assert rec["state"] == "done" and rec["target"] == {"all": True}
    assert residue(w)["leases"] == 0 and residue(w)["deleg"] == "closed"
    # 호스트 deny-* 는 host_grant_svc.change 가 부른다
    import host_store
    from authz import host_grant_svc
    calls = []
    monkeypatch.setattr(saga, "after_peer_deny", lambda pid, actor=None: calls.append(pid))
    monkeypatch.setattr(host_store, "find_grant", lambda pid: {"id": "pc", "label": "pc", "level": "control"})
    monkeypatch.setattr(host_store, "normalize_id", lambda x: x)
    monkeypatch.setattr(host_grant_svc, "_current", lambda a, g: False)
    monkeypatch.setattr(host_grant_svc, "_apply", lambda a, pid: {"id": pid})
    monkeypatch.setattr(host_grant_svc, "mirror", lambda *a, **k: None)
    monkeypatch.setattr(host_store, "audit", lambda *a, **k: None)
    host_grant_svc.change(H, "pc", "deny-control")
    assert calls == ["pc"]


def test_lifespan_sweep_resumes_a_saga_left_running(world, monkeypatch):
    import asyncio
    w = build_saga_world(world, monkeypatch)
    saga.FAULT.update(step="release_leases", when="before")
    with pytest.raises(saga.InjectedCrash):
        saga.start("principal", w["target"])
    assert residue(w)["leases"] == 1                                                    # 위임 몫은 닫을 때 이미 놓였고, 부모 몫이 남았다
    # 서버 재시작 = 시작 직후 sweep_loop 의 첫 바퀴
    async def one_round():
        t = asyncio.create_task(saga.sweep_loop(interval=3600))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if not saga.pending():
                break
        t.cancel()
    asyncio.run(one_round())
    assert residue(w) == CLEAN
