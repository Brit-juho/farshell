"""`VT_AUTHZ_ENFORCE=deleg`: 에이전트 위임의 판정·파생 grant·감사를 엔진이 쥔다.

기존 `test_delegation.py` 는 무수정으로 꺼짐/켜짐 둘 다 통과한다(`VT_AUTHZ_ENFORCE=deleg pytest …`). 여기는 켜졌을 때만 성립하는 성질:

  - 위임 장부(`delegations.json`)는 소유 원장으로 계속 쓰이고, 엔진 grant 는 거기서 파생된다(dual-write, `--verify` 차이 없음).
  - 적대 시험: 형제·남의 자식은 제어 못 함, 자식은 부모 역할 못 함, 자식이 아닌 세션의 보고 거부, 회수된 grant 는 장부가 소유를
    말해도 거부, 쿼터·속도·깊이·killswitch(VT_AGENT_SPAWN, safe_mode).
  - H9: cwd·worktree 기준은 fsguard 루트(VT_BROWSE_ROOTS) 안일 때만 — 밖이면 403 `cwd_outside_roots`.
  - D13: fs grant 상속 규칙(공유 참조 / worktree 전용) 기록과 동반 회수. 강제는 5단계.
  - 감사는 authz hash chain 에 있고 내용이 없다. 변경은 감사·엔진 실패 시 닫힌다.
  - 플래그를 끄면 같은 상태로 이어지고(롤백), 다시 켜면 그 사이 옛 코드의 변경을 채택한다.
  - H3: `fsh delegate close` 는 켜짐에서 서버(주체 cli)를 거친다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import delegation
import queue_store
from authz import audit, engine, flags, migrate, store
from authz import principals as P

from test_delegation import _caller, _pending_for, tm  # noqa: F401  (격리 가짜 tmux 픽스처)

_CHAR = Path(__file__).resolve().parent / "authz_char"
if str(_CHAR) not in sys.path:
    sys.path.insert(0, str(_CHAR))


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg")
    monkeypatch.delenv("VT_SAFE_MODE", raising=False)


# --- 도우미 ---------------------------------------------------------------------------------


def grants() -> list[dict]:
    return store._read("grants")[0]["items"]


def gmap() -> dict:
    return {g["id"]: g for g in grants()}


def lines(prefix: str = "deleg.") -> list[dict]:
    return list(reversed(audit.read_merged(1000, prefix)))


def raw_audit() -> str:
    p = audit.path()
    return p.read_text(encoding="utf-8") if p.exists() else ""


def add_grant(**g) -> None:
    base = {"state": "active", "approved_by": "human:test", "expires": None, "conds": {}}
    with store.txn() as d:
        d["grants"]["items"].append({**base, **g})


def drop_grant(gid: str) -> None:
    with store.txn() as d:
        d["grants"]["items"] = [g for g in d["grants"]["items"] if g.get("id") != gid]


def spawn(tm, parent="main", title="kid", **kw):
    return delegation.spawn(_caller(tm, parent), task=kw.pop("task", "x"), title=title, **kw)["delegation"]


def code(fn):
    with pytest.raises(delegation.DelegationError) as e:
        fn()
    return e.value.code, e.value.status


def roots(monkeypatch, tmp_path):
    r = tmp_path / "roots"
    (r / "proj").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(r))
    return r


# --- 엔진이 판정권자 + dual-write -----------------------------------------------------------------


def test_engine_decides_and_ledger_stays_the_ownership_ledger(tm, monkeypatch):
    seen = []
    real = engine.decide
    monkeypatch.setattr(engine, "decide", lambda p, s, r, *a, **k: seen.append((p.kind, s)) or real(p, s, r, *a, **k))
    parent = _caller(tm, "main")
    did = spawn(tm)
    delegation.send_to_child(parent.key, did, "more")
    delegation.report(_caller(tm, "kid").key, "succeeded", "done")
    delegation.close(did, caller_key=parent.key)
    assert {("agent", "deleg.spawn"), ("agent", "deleg.control")} <= set(seen)
    item = delegation.get(did)                         # 장부는 계속 쓴다
    assert item["state"] == "closed" and item["parent"]["key"] == parent.key


def test_derived_grants_follow_b1_and_verify_is_clean(tm):
    parent, did = _caller(tm, "main"), spawn(tm)
    kid = _caller(tm, "kid")
    g = gmap()
    c, r = g[f"dc-{did}"], g[f"dr-{did}"]
    assert (c["subject"], c["resource"], c["scopes"]) == (f"agent:local/{parent.key}", f"deleg:local/{did}", ["deleg.control"])
    assert (r["subject"], r["resource"], r["scopes"]) == (f"agent:local/{kid.key}", f"deleg:local/{did}", ["deleg.control"])
    assert c["sconds"] == {"deleg.control": {"role": ["parent"]}} and r["sconds"] == {"deleg.control": {"role": ["child"]}}
    assert c["approved_by"] == r["approved_by"] == "system:spawn" and c["state"] == r["state"] == "active"
    assert migrate.verify_store(tmux=None)["clean"]
    delegation.close(did, caller_key=parent.key)
    g = gmap()
    assert g[f"dc-{did}"]["state"] == g[f"dr-{did}"]["state"] == "ended"      # 닫히면 grant 도 끝난다
    assert migrate.verify_store(tmux=None)["clean"]


def test_orphaned_delegation_ends_its_grants(tm):
    did = spawn(tm)
    tm.sessions.pop("main")
    assert delegation.sweep()["orphaned"] == [did]
    assert {gmap()[f"dc-{did}"]["state"], gmap()[f"dr-{did}"]["state"]} == {"ended"}
    assert any(r["action"] == "deleg.end" for r in lines())


def test_closed_delegation_keeps_the_public_contract(tm):
    """grant 가 끝나도 응답은 그대로다: 닫힌 위임 send=409, close=already, 자식 보고=409. 남은 404/403 도 그대로."""
    parent, did = _caller(tm, "main"), spawn(tm)
    kid = _caller(tm, "kid")
    delegation.close(did, caller_key=parent.key)
    assert code(lambda: delegation.send_to_child(parent.key, did, "x")) == ("delegation_closed", 409)
    assert delegation.close(did, caller_key=parent.key) == {"ok": True, "already": True}
    assert code(lambda: delegation.report(kid.key, "succeeded", "x")) == ("already_reported", 409)
    assert code(lambda: delegation.send_to_child(_caller(tm, "other").key, did, "x")) == ("not_found", 404)


# --- 적대 시험 ---------------------------------------------------------------------------------


def test_child_cannot_control_siblings_or_its_own_delegation(tm):
    a = _caller(tm, "main")
    d1, d2 = spawn(tm, title="k1"), spawn(tm, title="k2")
    k1, k2 = _caller(tm, "k1"), _caller(tm, "k2")
    for who in (k1, k2):                                     # 자식은 부모 역할을 못 한다 — 형제의 위임도, 자기 위임도
        for did in (d1, d2):
            assert code(lambda: delegation.send_to_child(who.key, did, "x")) == ("not_found", 404)
            assert code(lambda: delegation.close(did, caller_key=who.key)) == ("not_found", 404)
    assert delegation.get(d1)["state"] == delegation.get(d2)["state"] == "running"
    denied = [r for r in lines() if r["decision"] == "deny"]
    assert {r["principal"] for r in denied} == {f"agent:local/{k1.key}", f"agent:local/{k2.key}"}
    delegation.send_to_child(a.key, d1, "ok")                 # 진짜 부모는 된다


def test_other_parents_cannot_touch_my_children(tm):
    d = spawn(tm, parent="main")
    other = _caller(tm, "other")
    spawn(tm, parent="other", title="theirs")
    assert code(lambda: delegation.send_to_child(other.key, d, "x")) == ("not_found", 404)
    assert code(lambda: delegation.close(d, caller_key=other.key)) == ("not_found", 404)
    assert code(lambda: delegation.send_to_child(other.key, "nope", "x")) == ("not_found", 404)
    assert delegation.get(d)["state"] == "running"


def test_forged_report_from_a_non_child_is_rejected_and_audited(tm):
    did = spawn(tm)
    imposter = _caller(tm, "imposter")
    assert code(lambda: delegation.report(imposter.key, "succeeded", "SECRET-FORGED-SUMMARY")) == ("not_delegated", 403)
    assert code(lambda: delegation.report(_caller(tm, "main").key, "succeeded", "x")) == ("not_delegated", 403)  # 부모도 아니다
    assert delegation.get(did)["state"] == "running" and _pending_for("main") == []
    assert "SECRET-FORGED-SUMMARY" not in raw_audit()
    assert any(r["action"] == "deleg.report" and r["decision"] == "deny" and r["principal"].endswith(imposter.key)
               for r in lines())


def test_a_revoked_grant_beats_the_ledger(tm):
    """장부가 소유를 말해도 grant 가 회수(revoked)됐으면 엔진이 거부한다. 회수는 다시 파생해도 되살아나지 않는다."""
    parent, did = _caller(tm, "main"), spawn(tm)
    kid = _caller(tm, "kid")
    with store.txn() as d:
        for g in d["grants"]["items"]:
            if g["id"] in (f"dc-{did}", f"dr-{did}"):
                g.update(state="revoked", revoked_at=1.0, revoked_reason="admin")
    assert code(lambda: delegation.send_to_child(parent.key, did, "x")) == ("not_found", 404)
    assert code(lambda: delegation.close(did, caller_key=parent.key)) == ("not_found", 404)
    assert code(lambda: delegation.report(kid.key, "succeeded", "x")) == ("not_delegated", 403)
    spawn(tm, parent="p2", title="other")                        # 장부를 다시 쓴다(동기화)
    migrate.apply(tmux=None)                                     # 이전 도구도 되살리지 않는다
    assert {gmap()[f"dc-{did}"]["state"], gmap()[f"dr-{did}"]["state"]} == {"revoked"}
    assert migrate.verify_store(tmux=None)["clean"]
    assert code(lambda: delegation.report(kid.key, "succeeded", "x")) == ("not_delegated", 403)


def test_forged_ledger_ids_with_glob_characters_get_no_grants():
    item = {"id": "*", "state": "running", "parent": {"key": "$1:1"}, "child": {"key": "$2:2"}}
    assert migrate.translate_delegation(item) == []
    item = {"id": "ok1", "state": "running", "parent": {"key": "*"}, "child": {"key": "$2:2"}}
    assert [g["id"] for g in migrate.translate_delegation(item)] == ["dr-ok1"]
    item = {"id": "ok2", "state": "running", "parent": {"key": "$1:1"}, "child": {"key": "$2:2"},
            "fs": {"mode": "worktree", "path": "/x/*"}}
    assert [g["id"] for g in migrate.translate_delegation(item)] == ["dc-ok2", "dr-ok2"]       # 경로에 glob → fs 레코드 없음


def test_child_limit_depth_rate_and_closed_do_not_count(tm, monkeypatch):
    parent = _caller(tm, "main")
    ids = [spawn(tm, title=f"k{i}") for i in range(3)]
    assert code(lambda: spawn(tm, title="k4")) == ("child_limit", 429)
    delegation.close(ids[0], caller_key=parent.key)
    spawn(tm, title="k4")
    assert code(lambda: delegation.spawn(_caller(tm, "k1"), task="y")) == ("depth_limit", 403)
    delegation.close(ids[1], caller_key=parent.key)                   # 닫혀도 자식이었던 세션은 못 만든다
    assert code(lambda: delegation.spawn(_caller(tm, "k1"), task="y")) == ("depth_limit", 403)


def test_global_spawn_rate_limit(tm, monkeypatch):
    for i in range(delegation.SPAWN_PER_MIN):
        spawn(tm, parent=f"p{i}", title=f"c{i}")
    assert code(lambda: spawn(tm, parent="late", title="x")) == ("rate_limited", 429)


def test_killswitches(tm, monkeypatch):
    monkeypatch.setenv("VT_AGENT_SPAWN", "0")
    assert code(lambda: spawn(tm)) == ("spawn_disabled", 403)
    monkeypatch.delenv("VT_AGENT_SPAWN")
    monkeypatch.setenv("VT_SAFE_MODE", "1")
    assert code(lambda: spawn(tm)) == ("safe_mode", 403)
    assert delegation.role_of(_caller(tm, "main").key)["can_spawn"] is False
    assert delegation.list_all() == [] and "kid" not in tm.sessions
    monkeypatch.delenv("VT_SAFE_MODE")
    assert spawn(tm)
    assert any(r["action"] == "deleg.spawn" and r["reason"] == "safe_mode" for r in lines())


def test_safe_mode_is_only_a_killswitch_under_the_flag(tm, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    monkeypatch.setenv("VT_SAFE_MODE", "1")
    assert spawn(tm)                                             # 꺼짐: 지금처럼 safe_mode 를 보지 않는다
    assert delegation.role_of(_caller(tm, "main").key)["can_spawn"] is True


# --- H9: cwd · worktree -------------------------------------------------------------------------


def test_spawn_cwd_must_be_inside_the_browse_roots(tm, monkeypatch, tmp_path):
    r = roots(monkeypatch, tmp_path)
    did = spawn(tm, cwd=str(r / "proj"))
    assert delegation.get(did)["cwd"] == str((r / "proj").resolve())
    for bad in (str(tmp_path / "outside"), "/", "/etc"):
        assert code(lambda: spawn(tm, title="bad", cwd=bad)) == ("cwd_outside_roots", 403)
    (r / "link").symlink_to(tmp_path / "outside")                # 루트 안의 링크가 밖을 가리킨다
    assert code(lambda: spawn(tm, title="bad", cwd=str(r / "link"))) == ("cwd_outside_roots", 403)
    (r / "proj" / ".ssh").mkdir()                                # 루트 안이라도 거부 목록
    assert code(lambda: spawn(tm, title="bad", cwd=str(r / "proj" / ".ssh"))) == ("cwd_outside_roots", 403)
    assert [x["id"] for x in delegation.list_all()] == [did] and "bad" not in tm.sessions       # 아무것도 남지 않는다
    assert code(lambda: spawn(tm, title="rel", cwd="relative")) == ("bad_cwd", 400)             # 옛 오류는 그대로
    den = [x for x in lines("deleg.cwd")]
    assert den and all(x["decision"] == "deny" and x["scopes"] == ["fs.read"] for x in den)


def test_flag_off_still_accepts_any_directory(tm, monkeypatch, tmp_path):
    roots(monkeypatch, tmp_path)
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    assert delegation.get(spawn(tm, cwd=str(tmp_path / "outside")))["cwd"] == str(tmp_path / "outside")


def _fake_git(monkeypatch, top: str, wt: str):
    class R:
        pass

    def run(cmd, **kw):
        r = R()
        r.stdout = top if "--show-toplevel" in cmd else "deadbeef"
        return r

    import worktree
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(worktree, "create_worktree", lambda spec: {"worktree": {"path": wt}})


def test_worktree_base_must_be_inside_the_browse_roots(tm, monkeypatch, tmp_path):
    r = roots(monkeypatch, tmp_path)
    _fake_git(monkeypatch, str(tmp_path / "outside"), str(r / "wt"))
    assert code(lambda: spawn(tm, worktree_branch="feat")) == ("cwd_outside_roots", 403)
    assert delegation.list_all() == [] and "kid" not in tm.sessions
    _fake_git(monkeypatch, str(r / "proj"), str(r / "wt"))
    (r / "wt").mkdir()
    did = spawn(tm, worktree_branch="feat")
    assert delegation.get(did)["cwd"] == str(r / "wt")
    assert any("spawn_worktree_base" in (x["reason"] or "") and x["decision"] == "deny" for x in lines("deleg.cwd"))


# --- D13: fs grant 상속 규칙 (기록 + 동반 회수, 강제는 5단계) --------------------------------------------


def _fs_grant(parent_key: str, real: str, gid="fsg-1", scopes=("fs.write",)):
    add_grant(id=gid, subject=f"agent:local/{parent_key}", resource=f"fs:local{real}/*", scopes=list(scopes))


def test_no_worktree_child_shares_the_parent_fs_grant_and_it_cascades(tm, monkeypatch, tmp_path):
    r = roots(monkeypatch, tmp_path)
    real = str((r / "proj").resolve())
    parent = _caller(tm, "main")
    _fs_grant(parent.key, real)
    did = spawn(tm, cwd=real)
    kid = _caller(tm, "kid")
    rec = gmap()[f"di-{did}-fsg-1"]
    assert (rec["subject"], rec["inherit"], rec["inherit_from"], rec["approved_by"]) == (
        f"agent:local/{kid.key}", True, "fsg-1", "system:spawn")
    assert rec["resource"] == f"fs:local{real}/*" and set(rec["scopes"]) == {"fs.read", "fs.write"}
    assert delegation.get(did)["fs"]["from"][0]["grant"] == "fsg-1"      # 원장에 공유 참조가 적힌다
    assert migrate.verify_store(tmux=None)["clean"]
    # 부모 grant 가 회수되면 다음 동기화에서 상속분도 회수된다
    drop_grant("fsg-1")
    delegation.send_to_child(parent.key, did, "more")
    rec = gmap()[f"di-{did}-fsg-1"]
    assert rec["state"] == "revoked" and rec["revoked_reason"] == "parent_grant_ended"
    assert any(x["action"] == "deleg.cascade" and x["grant_id"] == f"di-{did}-fsg-1" for x in lines())
    assert migrate.verify_store(tmux=None)["clean"]
    migrate.apply(tmux=None)
    assert gmap()[f"di-{did}-fsg-1"]["state"] == "revoked"               # 이전 도구가 되살리지 않는다


def test_parent_without_fs_grant_has_nothing_to_inherit(tm, monkeypatch, tmp_path):
    r = roots(monkeypatch, tmp_path)
    did = spawn(tm, cwd=str(r / "proj"))
    assert "fs" not in delegation.get(did)
    assert not [g for g in grants() if g["id"].startswith(("di-", "dw-"))]
    # 부모가 다른 폴더의 grant 만 가졌다면 덮지 않으므로 상속 없음
    _fs_grant(_caller(tm, "main").key, str(r / "elsewhere"))
    did2 = spawn(tm, title="k2", cwd=str(r / "proj"))
    assert "fs" not in delegation.get(did2)


def test_worktree_child_gets_only_the_new_path_and_not_the_parents(tm, monkeypatch, tmp_path):
    r = roots(monkeypatch, tmp_path)
    real, wt = str((r / "proj").resolve()), str((r / "wt").resolve())
    (r / "wt").mkdir()
    parent = _caller(tm, "main")
    _fs_grant(parent.key, real)
    _fake_git(monkeypatch, real, wt)
    did = spawn(tm, worktree_branch="feat")
    kid = _caller(tm, "kid")
    mine = {g["id"]: g for g in grants() if g["subject"] == f"agent:local/{kid.key}"}
    assert set(mine) == {f"dr-{did}", f"dw-{did}"}                         # 부모의 폴더 grant 는 상속 안 함
    w = mine[f"dw-{did}"]
    assert (w["resource"], w["scopes"], w["approved_by"], w["inherit"]) == (f"fs:local{wt}/*", ["fs.write"], "system:spawn", False)
    assert delegation.get(did)["fs"] == {"mode": "worktree", "path": wt}
    assert migrate.verify_store(tmux=None)["clean"]


def test_nothing_else_is_inherited(tm, monkeypatch, tmp_path):
    """세션 읽기·입력, 메시지, 브라우저 등은 자식에게 가지 않는다 — 자식의 grant 는 report(+fs) 뿐이고 엔진도 그렇게 답한다."""
    r = roots(monkeypatch, tmp_path)
    parent = _caller(tm, "main")
    _fs_grant(parent.key, str((r / "proj").resolve()))
    add_grant(id="x-read", subject=f"agent:local/{parent.key}", resource="session:local/*", scopes=["session.read"])
    spawn(tm, cwd=str(r / "proj"))
    kid = _caller(tm, "kid")
    mine = {g["id"].split("-")[0] for g in grants() if g["subject"] == f"agent:local/{kid.key}"}
    assert mine == {"dr", "di"}
    snap = store.snapshot()
    for scope, res, ctx in (("session.read", f"session:local/{parent.key}", {}),
                            ("session.input", f"session:local/{parent.key}", {"mode": "line"}),
                            ("browser", "browser:adapter:cdp", {"op": "open"}),
                            ("deleg.control", "deleg:local/other", {"role": "parent"}),
                            ("fs.write", "fs:local/etc/passwd", {"op": "write"})):
        d = engine.decide(P.agent(kid.key), scope, res, ctx, snap)
        assert not d.allow, (scope, d.reason)


def test_closing_ends_inherited_and_worktree_grants_too(tm, monkeypatch, tmp_path):
    r = roots(monkeypatch, tmp_path)
    real = str((r / "proj").resolve())
    parent = _caller(tm, "main")
    _fs_grant(parent.key, real)
    did = spawn(tm, cwd=real)
    delegation.close(did, caller_key=parent.key)
    assert {g["state"] for g in grants() if g["id"].endswith(did) or g["id"].startswith(f"di-{did}")} == {"ended"}


# --- 감사 ----------------------------------------------------------------------------------------


def test_audit_has_sizes_and_sha8_never_content(tm):
    parent = _caller(tm, "main")
    did = spawn(tm, task="SECRET-TASK-TEXT 가나다")
    delegation.send_to_child(parent.key, did, "SECRET-SEND-TEXT")
    delegation.report(_caller(tm, "kid").key, "failed", "SECRET-SUMMARY-TEXT")
    delegation.close(did, caller_key=parent.key)
    raw = raw_audit()
    for secret in ("SECRET-TASK-TEXT", "SECRET-SEND-TEXT", "SECRET-SUMMARY-TEXT", "가나다"):
        assert secret not in raw
    by = {r["action"]: r for r in lines()}
    assert {"deleg.spawn", "deleg.send", "deleg.report", "deleg.close", "deleg.grant", "deleg.end"} <= set(by)
    assert by["deleg.send"]["sizes"] == {"chars": len("SECRET-SEND-TEXT")} and len(by["deleg.send"]["sha8"]) == 8
    assert by["deleg.report"]["sha8"] == audit.sha8("SECRET-SUMMARY-TEXT") and by["deleg.report"]["reason"] == "failed"
    assert by["deleg.spawn"]["principal"] == f"agent:local/{parent.key}" and by["deleg.spawn"]["result"] == "ok"
    assert by["deleg.grant"]["principal"] == "system:spawn"
    assert audit.verify()[0] is True


def test_legacy_agent_access_log_is_replaced_only_when_enforced(tm, monkeypatch):
    import agent_share
    spawn(tm)
    assert not agent_share._audit_path().exists()                 # 켜짐: 옛 로그를 쓰지 않는다
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    spawn(tm, parent="p2", title="k2")
    assert "delegate_spawn" in agent_share._audit_path().read_text(encoding="utf-8")


def test_audit_failure_stops_the_grant_change(tm, monkeypatch):
    """grant 발급 감사를 못 쓰면 위임은 만들어지지 않는다 — 장부·세션 모두 되돌린다."""
    real = audit.append

    def flaky(rec, **k):
        if rec.get("action") == "deleg.grant":
            raise OSError("disk full")
        return real(rec, **k)

    monkeypatch.setattr(audit, "append", flaky)
    assert code(lambda: spawn(tm)) == ("audit_unavailable", 503)
    assert delegation.list_all() == [] and "kid" in tm.killed
    assert not [g for g in grants() if g["id"].startswith(("dc-", "dr-"))]


def test_engine_failure_and_corrupt_store_fail_closed(tm, monkeypatch):
    did = spawn(tm)
    parent = _caller(tm, "main")
    real = engine.decide
    monkeypatch.setattr(engine, "decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert code(lambda: delegation.send_to_child(parent.key, did, "x")) == ("authz_unavailable", 503)
    assert code(lambda: spawn(tm, title="k2")) == ("authz_unavailable", 503)
    monkeypatch.setattr(engine, "decide", real)
    gpath = store.doc_path("grants")
    before = gpath.read_text()
    gpath.write_text("{not json")
    assert code(lambda: delegation.send_to_child(parent.key, did, "x")) == ("authz_unavailable", 503)
    assert code(lambda: delegation.close(did, caller_key=parent.key)) == ("authz_unavailable", 503)
    assert gpath.read_text() == "{not json"                      # 덮어쓰지 않는다
    assert delegation.get(did)["state"] == "running"
    assert delegation.list_all()                                  # 읽기는 막지 않는다
    gpath.write_text(before)
    delegation.send_to_child(parent.key, did, "x")


# --- 롤백 · 채택 ---------------------------------------------------------------------------------


def test_flag_off_is_an_instant_rollback_and_back_on_adopts(tm, monkeypatch):
    parent = _caller(tm, "main")
    d1 = spawn(tm, title="k1")
    gp = store.doc_path("grants")
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    before = (gp.read_bytes(), store.doc_path("meta").read_bytes())
    d2 = spawn(tm, title="k2")                                     # 옛 경로: 장부만 쓴다
    delegation.close(d1, caller_key=parent.key)
    delegation.send_to_child(parent.key, d2, "x")
    assert (gp.read_bytes(), store.doc_path("meta").read_bytes()) == before      # authz 저장소는 건드리지 않았다
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg")
    delegation.list_all()                                          # 락에 들어오는 순간 채택
    g = gmap()
    assert g[f"dc-{d1}"]["state"] == "ended" and g[f"dc-{d2}"]["state"] == "active" and f"dr-{d2}" in g
    assert any(r["action"] == "deleg.adopt" for r in lines())
    delegation.send_to_child(parent.key, d2, "again")              # 껐다 켠 뒤의 위임을 엔진이 인정한다
    assert migrate.verify_store(tmux=None)["clean"]


def test_restore_does_not_deadlock_with_the_ledger_lock(tm):
    spawn(tm)
    bk = migrate.backup(tmux=None)
    spawn(tm, parent="p2", title="k2")
    migrate.restore(bk["ts"])
    assert [x["child"]["name"] for x in delegation.list_all()] == ["kid"]
    assert migrate.verify_store(tmux=None)["clean"]


def test_concurrent_ops_keep_the_lock_order(tm):
    parent = _caller(tm, "main")
    dids = [spawn(tm, title=f"k{i}") for i in range(3)]
    errs = []

    def work(n):
        try:
            for i in range(4):
                delegation.send_to_child(parent.key, dids[n % 3], f"m{i}")
                delegation.list_all()
                delegation.sweep()
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert not errs and not any(t.is_alive() for t in ts)
    assert migrate.verify_store(tmux=None)["clean"]


# --- H3: fsh delegate close 는 서버(주체 cli)를 거친다 ---------------------------------------------------


class _SockScope:
    """TestClient 용 — unix socket 게이트(`authz.sock.Gate`)가 붙이는 scope 를 흉내 낸다."""

    def __init__(self, app, principal):
        self.app, self.p = app, principal

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope["fsh_sock"] = True
            scope["state"] = {**(scope.get("state") or {}), "fsh_principal": self.p}
        await self.app(scope, receive, send)


def _client(monkeypatch, principal):
    from starlette.testclient import TestClient

    import main

    async def _noop():
        return None
    monkeypatch.setattr(delegation, "sweep_loop", _noop)
    return TestClient(_SockScope(main.app, principal) if principal else main.app)


def test_close_by_cli_route_cli_principal_may_only_close(tm, monkeypatch):
    did = spawn(tm)
    cli = P.cli(os.getuid(), 4242)
    with _client(monkeypatch, cli) as c:
        r = c.post(f"/api/delegations/{did}/close-by-cli")
        assert r.status_code == 200 and r.json()["ok"] is True
        assert c.post("/api/delegations/nope/close-by-cli").status_code == 404
        assert c.post(f"/api/delegations/{did}/send", json={"text": "x"}).status_code == 403    # cli 는 보내기·식별 불가
    x = delegation.get(did)
    assert x["state"] == "closed" and x["close_reason"] == "closed_by_user"
    closes = [r for r in lines() if r["action"] == "deleg.close"]
    assert closes and closes[-1]["principal"] == cli.id
    # 승인·발급은 못 한다: cli 에게는 deleg.control(close 외), deleg.spawn, authz.admin 이 없다
    snap = store.snapshot()
    for scope, res, ctx in (("deleg.control", f"deleg:local/{did}", {"role": "parent", "op": "send"}),
                            ("deleg.spawn", "deleg-quota:x", {}), ("authz.admin", "authz:*", {}),
                            ("deleg.control", f"deleg:local/{did}", {"role": "child"})):
        assert not engine.decide(cli, scope, res, ctx, snap).allow, scope


def test_close_by_cli_route_requires_the_socket_and_a_cli_or_parent(tm, monkeypatch):
    did = spawn(tm)
    with _client(monkeypatch, None) as c:                               # TCP·기계 토큰 경로
        r = c.post(f"/api/delegations/{did}/close-by-cli")
        assert r.status_code == 403 and r.json()["error"] == "socket_required"
    parent, other = _caller(tm, "main"), _caller(tm, "other")
    with _client(monkeypatch, P.svc("hook", os.getuid(), 1)) as c:
        assert c.post(f"/api/delegations/{did}/close-by-cli").status_code == 403
    hook = P.from_socket({"uid": os.getuid(), "pid": 1}, row=type("R", (), {
        "sid": parent.sid, "created": parent.created, "name": "main", "pane_id": "%1"})(), svc_claim="hook")
    assert P.is_hook(hook) and hook.kind == "agent"
    with _client(monkeypatch, hook) as c:                               # 훅은 같은 agent 라도 위임을 닫을 수 없다
        assert c.post(f"/api/delegations/{did}/close-by-cli").status_code == 403
    with _client(monkeypatch, P.agent(other.key)) as c:                 # pane 안 프로세스는 소유 검사를 받는다
        assert c.post(f"/api/delegations/{did}/close-by-cli").status_code == 404
    assert delegation.get(did)["state"] == "running"
    with _client(monkeypatch, P.agent(parent.key)) as c:
        assert c.post(f"/api/delegations/{did}/close-by-cli").status_code == 200
    assert delegation.get(did)["state"] == "closed"


def test_cli_close_goes_through_the_server_when_enforced(tm, monkeypatch, capsys):
    import fsh_client
    did = spawn(tm)
    calls = []

    def fake(method, path, **kw):
        calls.append((method, path))
        return 200, json.dumps({"ok": True, "removed_queued": 0}).encode()

    monkeypatch.setattr(fsh_client, "socket_request", fake)
    assert delegation._cli(["close", did[:4]]) == 0
    assert calls == [("POST", f"/api/delegations/{did}/close-by-cli")]
    assert delegation.get(did)["state"] == "running"                    # 장부는 서버가 쓴다 — CLI 가 직접 쓰지 않았다


def test_cli_close_server_denial_is_not_bypassed_by_a_direct_write(tm, monkeypatch, capsys):
    import fsh_client
    did = spawn(tm)
    monkeypatch.setattr(fsh_client, "socket_request",
                        lambda *a, **k: (403, b'{"error":"forbidden","reason":"no"}'))
    assert delegation._cli(["close", did]) == 1
    assert delegation.get(did)["state"] == "running"
    monkeypatch.setattr(fsh_client, "socket_request", lambda *a, **k: (_ for _ in ()).throw(OSError("timed out")))
    assert delegation._cli(["close", did]) == 1                          # 붙은 뒤의 실패 — 직접 쓰기로 되풀이하지 않는다
    assert delegation.get(did)["state"] == "running"


def test_cli_close_falls_back_only_when_the_socket_is_unusable(tm, monkeypatch, capsys):
    import fsh_client
    did = spawn(tm)

    def down(*a, **k):
        raise fsh_client.SocketUnavailable("no socket")

    monkeypatch.setattr(fsh_client, "socket_request", down)
    assert delegation._cli(["close", did]) == 0
    x = delegation.get(did)
    assert x["state"] == "closed" and x["close_reason"] == "closed_by_user"
    closes = [r for r in lines() if r["action"] == "deleg.close"]
    assert closes[-1]["principal"].startswith("local:cli:uid")                 # 직접 경로도 cli 주체로 판정·감사된다
    did2 = spawn(tm, title="k2")
    monkeypatch.setattr(fsh_client, "socket_request", lambda *a, **k: (403, b'{"error":"socket_route_not_allowed"}'))
    assert delegation._cli(["close", did2]) == 0 and delegation.get(did2)["state"] == "closed"     # 옛 서버


def test_cli_close_keeps_the_direct_path_when_the_flag_is_off(tm, monkeypatch, capsys):
    import fsh_client
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    did = spawn(tm)
    monkeypatch.setattr(fsh_client, "socket_request",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("꺼짐에서는 서버를 부르지 않는다")))
    assert delegation._cli(["close", did]) == 0 and delegation.get(did)["state"] == "closed"


def test_human_close_by_user_is_decided_by_the_engine(tm, monkeypatch):
    did = spawn(tm)
    seen = []
    real = engine.decide
    monkeypatch.setattr(engine, "decide", lambda p, s, r, *a, **k: seen.append((p.kind, s)) or real(p, s, r, *a, **k))
    delegation.close(did, reason="closed_by_user", actor=P.human("dev1"))
    assert ("human", "deleg.control") in seen and delegation.get(did)["state"] == "closed"
    assert [r for r in lines() if r["action"] == "deleg.close"][-1]["principal"] == "human:dev:dev1"
