"""authz 이전 — 각 행, 손상 입력, 멱등, 백업·복원, CLI.

tmux 는 부르지 않는다 — 계획·적용에 tmux snapshot 을 직접 넘긴다(`tmux=`).
"""

from __future__ import annotations

import io
import json
import stat
import tarfile
import time

import pytest

from authz import __main__ as cli
from authz import engine, migrate, principals as P, scopes as S, store

NOW = time.time()
A, T, O, X = "$1:1001", "$2:1002", "$3:1003", "$9:9009"          # X 는 죽은 세션
TMUX = {A: {"name": "agent-a", "nick": "", "mode": ""},
        T: {"name": "target", "nick": "shell", "mode": "readwrite"},
        O: {"name": "other", "nick": "rw", "mode": "readwrite"}}
SECRET = "s3cr3t-" + "peer-value-not-real"


def ref(k):
    sid, created = k.split(":")
    return {"sid": sid, "created": int(created), "name": "n"}


@pytest.fixture
def sd(tmp_path, monkeypatch):
    d = tmp_path / "vt"
    d.mkdir()
    monkeypatch.setenv("VT_STATE_DIR", str(d))
    (d / "agent_grants.json").write_text(json.dumps({
        "grants": [
            {"id": "g-read", "agent": ref(A), "target": ref(T), "read": True, "write": False, "granted": NOW - 10},
            {"id": "g-rw", "agent": ref(A), "target": ref(O), "read": True, "write": True, "granted": NOW - 10},
            {"id": "g-dead", "agent": ref(X), "target": ref(T), "read": True, "write": False},
            {"id": "g-none", "agent": ref(A), "target": ref(T), "read": False, "write": False},
        ],
        "requests": [
            {"id": "r-pend", "agent": ref(A), "target": ref(T), "kind": "write", "state": "pending",
             "created": NOW - 5, "expires": NOW + 3600},
            {"id": "r-exp", "agent": ref(A), "target": ref(T), "kind": "read", "state": "pending",
             "created": NOW - 99999, "expires": NOW - 1},
            {"id": "r-deny", "agent": ref(A), "target": ref(O), "kind": "read", "state": "denied", "decided": NOW - 10},
            {"id": "r-old", "agent": ref(A), "target": ref(O), "kind": "write", "state": "denied", "decided": NOW - 400},
        ]}))
    (d / "hosts.json").write_text(json.dumps({
        "version": 1, "self": {"id": "me", "label": "M1"}, "peers": [],
        "grants": [{"id": "pv", "label": "PV", "secret": SECRET, "level": "view", "added": 1},
                   {"id": "pca", "label": "PCA", "secret": SECRET + "2", "level": "control",
                    "agentMessages": True, "added": 2}]}))
    (d / "delegations.json").write_text(json.dumps({"items": [
        {"id": "d1", "state": "running", "parent": {"key": A}, "child": {"key": O}}], "spawns": []}))
    (d / "agent_access.log").write_text('{"a":1}\n{"a":2}\n')
    (d / "peer_audit.log").write_text('{"p":1}\n')
    return d


def by_origin(items):
    return {x["origin"]: x for x in items}


# --- B1 각 행 ----------------------------------------------------------------------------


def test_agent_grants_rows(sd):
    p = migrate.plan(now=NOW, tmux=TMUX)
    g = by_origin(p["grants"])
    r = g["legacy:agent_grants:g-read"]
    assert (r["subject"], r["resource"], r["scopes"]) == (f"agent:local/{A}", f"session:local/{T}",
                                                          ["session.view", "session.read"])
    assert r["approved_by"].startswith("migrated:") and r["expires"] is None
    assert g["legacy:agent_grants:g-rw"]["scopes"] == ["session.view", "session.read", "session.input"]
    assert g["legacy:agent_grants:g-rw"]["sconds"] == {"session.input": {"modes": ["line"]}}
    assert g["legacy:agent_grants:g-rw"]["schema"] == 2 and "sconds" not in r
    assert "legacy:agent_grants:g-dead" not in g and "legacy:agent_grants:g-none" not in g
    assert p["dropped"] == {"dead_session": 1, "expired": 2, "empty": 1}
    a = by_origin(p["approvals"])
    assert a["legacy:agent_grants:req:r-pend"]["state"] == "pending"
    assert a["legacy:agent_grants:req:r-pend"]["scopes"][-1] == "session.input"
    d = a["legacy:agent_grants:req:r-deny"]
    assert d["state"] == "denied" and d["cooldown_until"] == pytest.approx(NOW - 10 + 300)
    assert "legacy:agent_grants:req:r-old" not in a and "legacy:agent_grants:req:r-exp" not in a


def test_hosts_and_delegation_rows_without_copying_secrets(sd):
    p = migrate.plan(now=NOW, tmux=TMUX)
    g = by_origin(p["grants"])
    assert g["legacy:hosts:pv"]["preset"] == "view" and "relay" not in g["legacy:hosts:pv"]["scopes"]
    assert g["legacy:hosts:pca"]["preset"] == "control" and "relay" in g["legacy:hosts:pca"]["scopes"]
    assert g["legacy:hosts:pca"]["sconds"]["relay"]["groups"] == list(S.ALL_RELAY_GROUPS)
    m = g["legacy:hosts:pca:agentMessages"]
    assert (m["subject"], m["resource"], m["scopes"]) == ("peer:pca/obo:agent:*", "msg:local/*", ["msg.send"])
    assert "legacy:hosts:pv:agentMessages" not in g
    assert g["legacy:delegations:d1:control"]["subject"] == f"agent:local/{A}"
    assert g["legacy:delegations:d1:report"]["subject"] == f"agent:local/{O}"
    assert g["legacy:delegations:d1:control"]["approved_by"] == "system:spawn"
    assert {x["id"] for x in p["peers"]} == {"pv", "pca"}
    migrate.apply(now=NOW, tmux=TMUX)
    for f in store.root().rglob("*.json"):
        assert SECRET not in f.read_text(), f


def test_tmux_share_snapshot_and_lower_level_on_mismatch(sd):
    with store.txn() as docs:
        docs["shares"]["items"][T] = {"nick": "shell", "level": "read", "origin": f"legacy:tmux:{T}"}
    p = migrate.plan(now=NOW, tmux=TMUX)
    assert p["shares"][T]["level"] == "read"           # tmux readwrite vs 저장소 read → 낮은 쪽
    assert p["shares"][O]["level"] == "readwrite" and A not in p["shares"]
    assert any("불일치" in w and "target" in w for w in p["warnings"])


def test_unreadable_tmux_keeps_everything(sd):
    p = migrate.plan(now=NOW, tmux=None)
    assert "legacy:agent_grants:g-dead" in by_origin(p["grants"])
    assert p["shares"] == {} and any("tmux 를 읽지 못함" in w for w in p["warnings"])


def test_engine_on_migrated_store_matches_old_effective(sd):
    migrate.apply(now=NOW, tmux=TMUX)
    snap = store.snapshot(NOW)
    me = P.agent(A)
    ctx = lambda mode: {"share": {"nick": "x", "mode": mode}, "prefer_live": False, "alive": set(TMUX)}  # noqa: E731
    assert engine.decide(me, "session.read", f"session:local/{T}", ctx("readwrite"), snap).allow
    line = lambda mode: {**ctx(mode), "mode": "line"}  # noqa: E731
    assert not engine.decide(me, "session.input", f"session:local/{T}", line("readwrite"), snap).allow
    assert engine.decide(me, "session.input", f"session:local/{O}", line("readwrite"), snap).allow
    assert not engine.decide(me, "session.input", f"session:local/{O}", line("read"), snap).allow
    assert engine.decide(me, "deleg.control", "deleg:local/d1", {"role": "parent"}, snap).allow
    assert not engine.decide(me, "deleg.control", "deleg:local/d1", {"role": "child"}, snap).allow
    assert not engine.decide(P.agent(O), "deleg.control", "deleg:local/d1", {"role": "parent"}, snap).allow
    assert engine.decide(P.agent(O), "deleg.control", "deleg:local/d1", {"role": "child"}, snap).allow


# --- 손상 입력 ---------------------------------------------------------------------------


def test_corrupt_legacy_file_is_skipped_with_a_warning(sd):
    (sd / "agent_grants.json").write_text("{broken")
    (sd / "delegations.json").write_text("[]")
    p = migrate.plan(now=NOW, tmux=TMUX)
    assert any("agent_grants.json" in w for w in p["warnings"])
    assert any("delegations.json" in w for w in p["warnings"])
    assert {o.split(":")[1] for o in by_origin(p["grants"])} == {"hosts"}
    r = migrate.apply(now=NOW, tmux=TMUX)
    assert r["grants"]["added"] == 3


def test_corrupt_authz_doc_is_backed_up_before_apply_overwrites(sd):
    store.root().mkdir(parents=True)
    store.doc_path("grants").write_text("not json")
    migrate.apply(now=NOW, tmux=TMUX)
    assert [b.read_text() for b in store.root().glob("grants.json.corrupt-*")] == ["not json"]
    assert store.snapshot().corrupt == frozenset()


# --- 멱등 ---------------------------------------------------------------------------------


def test_apply_is_idempotent_and_syncs_legacy_changes(sd):
    r1 = migrate.apply(now=NOW, tmux=TMUX)
    assert r1["grants"]["added"] == 7 and r1["logs"]["copied"] == 2
    before = store.doc_path("grants").read_bytes()
    with store.txn() as docs:      # 새 엔진이 만든 레코드(legacy 아님)는 이전이 건드리지 않는다
        docs["grants"]["items"].append({"id": "native", "origin": "authz:approval:z", "subject": "x"})
    r2 = migrate.apply(now=NOW + 60, tmux=TMUX)
    assert r2["grants"] == {"added": 0, "updated": 0, "removed": 0, "same": 7}
    assert r2["logs"] == {"copied": 0, "skipped": 2, "lines": {}}
    items = store.snapshot().grants
    assert any(x["id"] == "native" for x in items)
    assert [x for x in items if x["id"] != "native"] == json.loads(before)["items"]
    # 옛 저장소에서 철회되면 다음 apply 가 지운다
    ag = json.loads((sd / "agent_grants.json").read_text())
    ag["grants"] = [g for g in ag["grants"] if g["id"] != "g-rw"]
    (sd / "agent_grants.json").write_text(json.dumps(ag))
    assert not migrate.verify_store(now=NOW, tmux=TMUX)["clean"]
    r3 = migrate.apply(now=NOW + 120, tmux=TMUX)
    assert r3["grants"]["removed"] == 1
    assert migrate.verify_store(now=NOW, tmux=TMUX)["clean"]


def test_files_are_private(sd):
    migrate.apply(now=NOW, tmux=TMUX)
    for f in store.root().rglob("*"):
        mode = stat.S_IMODE(f.stat().st_mode)
        assert mode == (0o700 if f.is_dir() else 0o600), (f, oct(mode))


# --- 백업·복원 -----------------------------------------------------------------------------


def test_backup_manifest_and_restore_roundtrip(sd):
    r = migrate.apply(now=NOW, tmux=TMUX)
    ts = r["backup"]
    man = json.loads((migrate.backup_dir() / f"{ts}.manifest.json").read_text())
    assert {"agent_grants.json", "hosts.json", "delegations.json", "agent_access.log", "peer_audit.log",
            "tmux_shares.json"} <= set(man["files"])
    assert not any(n.startswith("authz/") for n in man["files"])          # 이전 전 백업 — authz 문서 없음
    original = (sd / "hosts.json").read_bytes()
    (sd / "hosts.json").write_text("{}")
    out = migrate.restore(ts, now=NOW + 5)
    assert (sd / "hosts.json").read_bytes() == original
    assert "authz/grants.json" in out["removed"] and not store.doc_path("grants").exists()
    # 복원 전 상태도 백업돼 있어 되돌릴 수 있다
    migrate.restore(out["pre_restore_backup"], now=NOW + 10)
    assert (sd / "hosts.json").read_text() == "{}" and store.doc_path("grants").exists()


def test_restore_refuses_tampered_or_hostile_backups(sd):
    m = migrate.backup(now=NOW, tmux=TMUX)
    tgz = migrate.backup_dir() / f"{m['ts']}.tgz"
    tgz.write_bytes(tgz.read_bytes() + b"x")
    with pytest.raises(migrate.MigrateError, match="sha256"):
        migrate.restore(m["ts"])
    with pytest.raises(migrate.MigrateError, match="잘못된"):
        migrate.restore("../etc")
    with pytest.raises(migrate.MigrateError, match="없습니다"):
        migrate.restore("19990101T000000Z")
    # manifest 와 sha 가 맞아도 허용되지 않은 이름(경로 탈출)은 거부
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("../evil.json")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"{}"))
    raw = buf.getvalue()
    (migrate.backup_dir() / "evil.tgz").write_bytes(raw)
    (migrate.backup_dir() / "evil.manifest.json").write_text(json.dumps({
        "ts": "evil", "tgz_sha256": migrate._sha(raw), "files": {"../evil.json": {"sha256": migrate._sha(b"{}"), "size": 2}}}))
    with pytest.raises(migrate.MigrateError, match="허용되지 않은"):
        migrate.restore("evil")
    assert not (sd.parent / "evil.json").exists()


# --- CLI ---------------------------------------------------------------------------------


def test_cli_dry_run_writes_nothing(sd, monkeypatch, capsys):
    monkeypatch.setattr(migrate, "tmux_snapshot", lambda: TMUX)
    assert cli.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "legacy:agent_grants:g-read" in out and SECRET not in out
    assert not store.root().exists()


def test_cli_apply_status_verify_finalize(sd, monkeypatch, capsys):
    monkeypatch.setattr(migrate, "tmux_snapshot", lambda: TMUX)
    monkeypatch.setattr(cli, "_replay_matrix", lambda: ("skipped:test", None))
    assert cli.main(["--apply"]) == 0
    assert cli.main(["--status"]) == 0
    assert "이전: 됨" in capsys.readouterr().out
    assert cli.main(["--verify"]) == 0
    assert "일치" in capsys.readouterr().out
    assert cli.main(["--finalize"]) == 2
    assert cli.main(["--restore", "nope"]) == 1
    assert cli.main(["--json", "--status"]) == 0
