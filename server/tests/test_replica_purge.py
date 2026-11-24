"""다른 맥에서 복제로 받은 사본 정리(session_history/replica_purge.py) — 자기 대화는 한 바이트도 안 건드리고,
복제가 켜져 있으면 거부하고, 지우기 전에 DB 백업을 만든다."""
import json

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    from session_catalog import db
    db.snapshot()
    assert str(db.directory().resolve()).startswith(str(tmp_path.resolve())), db.directory()
    return home


def _chunk(text):
    from session_history import archive
    return archive.put_chunk((text * 400).encode())


def _detail(*digests):
    return json.dumps({"chunks": [{"digest": d, "size": 100} for d in digests]})


def _seed(origin_q=True):
    """이 맥: 조각 A(자기 것)·S(자기 것이면서 R도 같은 내용). 출처 R: 대화·메시지·manifest(S,R1,R2)·pending(R3). 출처 Q: S,Q1."""
    from session_catalog.db import database, host_id, uid, set_setting
    chunks = {n: _chunk(n * 3) for n in ("A", "S", "R1", "R2", "R3", "Q1")}
    with database(write=True) as db:
        me = host_id(db)
        R, Q = uid(), uid()
        db.execute("INSERT INTO manifests(id,conversation_id,file_id,generation,length,digest,detail,created) VALUES(?,?,?,?,?,?,?,?)",
                   ("local-1", None, "lf", "g", 1, "d", _detail(chunks["A"], chunks["S"]), 1.0))
        rec = lambda mid, *d: json.dumps({"id": mid, "file_id": "rf", "generation": "g", "created": 1.0, "detail": _detail(*d)})
        rows = [(R, "c1", "conversation", json.dumps({"id": "c1"})),
                (R, "m1", "message", json.dumps({"conversation_id": "c1", "body": "hello from the other mac"})),
                (R, "m2", "message", json.dumps({"conversation_id": "c1", "body": "second remote message"})),
                (R, "rm1", "manifest", rec("rm1", chunks["S"], chunks["R1"], chunks["R2"])),
                (R, "rp1", "pending", json.dumps({"manifest": json.loads(rec("rp1", chunks["R3"]))}))]
        if origin_q:
            rows += [(Q, "qm1", "manifest", rec("qm1", chunks["S"], chunks["Q1"])),
                     (Q, "qc1", "message", json.dumps({"conversation_id": "qc", "body": "from a third mac"}))]
        for r in rows:
            db.execute("INSERT INTO replicas(origin,id,kind,payload,received) VALUES(?,?,?,?,?)", (*r, 1.0))
        db.execute("INSERT INTO replication_grants VALUES(?,?,?,?,?,?)", ("peer-r", "receive", "pk", 1, 1, 1.0))
        set_setting(db, "replication-origin:peer-r", R)
        db.execute("INSERT INTO replica_checkpoints VALUES(?,?,?)", ("peer-r", 7, 1.0))
    return me, R, Q, chunks


def _off():
    from session_catalog.db import database
    with database(write=True) as db:
        db.execute("UPDATE replication_grants SET enabled=0")


def _exists(digest):
    from session_history import archive
    return archive.chunk_path(digest).exists() or archive.chunk_packed_path(digest).exists()


def _count(table, where="1=1"):
    from session_catalog.db import database
    with database() as db:
        return db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def test_preview_reports_and_changes_nothing(store):
    from session_history import replica_purge as rp
    from session_catalog.db import directory
    me, R, Q, chunks = _seed()
    before = (sorted(p.name for p in (directory() / "chunks").iterdir()), _count("replicas"))
    found = rp.plan()
    assert set(found["origins"]) == {R, Q} and found["receiving"] == ["peer-r"]
    assert found["chunks_only_remote"] == 4 and found["chunks_shared_kept"] == 1     # R1,R2,R3,Q1 / S
    assert found["chunk_bytes"] > 0
    only_r = rp.plan(R)
    assert set(only_r["origins"]) == {R} and only_r["chunks_only_remote"] == 3 and only_r["chunks_shared_kept"] == 1
    assert (sorted(p.name for p in (directory() / "chunks").iterdir()), _count("replicas")) == before


def test_apply_is_refused_while_this_mac_still_receives(store):
    from session_history import replica_purge as rp
    from session_catalog.db import Refused
    _, _, _, chunks = _seed()
    with pytest.raises(Refused) as err:
        rp.apply(grace=0)
    assert err.value.code == "replication_still_on"
    assert _count("replicas") == 7 and all(_exists(d) for d in chunks.values())


def test_apply_removes_only_the_other_macs_copies(store):
    from session_history import replica_purge as rp, search
    from session_catalog.db import directory
    me, R, Q, chunks = _seed()
    search.refresh()
    assert (directory() / "search.sqlite3").exists()
    _off()
    out = rp.apply(R, grace=0)
    assert out["rows_deleted"] == 5 and not out["nothing_to_do"] and out["backup"]
    assert _count("replicas", f"origin='{R}'") == 0 and _count("replicas", f"origin='{Q}'") == 2, "다른 출처 Q는 그대로"
    assert not any(_exists(chunks[n]) for n in ("R1", "R2", "R3")), "받은 사본에만 쓰이던 조각은 지운다"
    assert _exists(chunks["A"]) and _exists(chunks["S"]), "이 맥 자기 대화가 쓰는 조각(같은 내용 포함)은 남긴다"
    assert _exists(chunks["Q1"]), "남는 출처가 쓰는 조각도 남긴다"
    assert _count("replica_checkpoints") == 0 and out["checkpoints"] == ["peer-r"]
    assert not (directory() / "search.sqlite3").exists(), "검색 목차는 지우고 서버가 다시 만든다"
    assert _count("manifests") == 1, "이 맥 자기 기록은 그대로"
    assert (directory() / "backups" / out["backup"]).exists(), "지우기 전에 만든 DB 백업"
    assert not (directory() / "chunks" / rp.TRASH).exists(), "휴지통 폴더는 비운다"


def test_apply_without_an_origin_removes_every_other_mac_and_is_repeatable(store):
    from session_history import replica_purge as rp
    me, R, Q, chunks = _seed()
    _off()
    first = rp.apply(grace=0)
    assert first["rows_deleted"] == 7 and _count("replicas") == 0
    assert _exists(chunks["A"]) and _exists(chunks["S"]) and not _exists(chunks["Q1"])
    again = rp.apply(grace=0)
    assert again["nothing_to_do"] is True


def test_a_chunk_a_local_manifest_starts_using_during_the_wait_is_put_back(store, monkeypatch):
    """유예 중 진행 중이던 자기 수집이 manifest를 확정해 그 조각을 쓰게 됐다면 지우지 않고 되돌린다."""
    from session_history import replica_purge as rp
    from session_catalog.db import database
    me, R, Q, chunks = _seed()
    _off()

    def commit_during_the_wait(_seconds):
        assert not _exists(chunks["R1"]), "유예 동안 조각은 휴지통에 있다"
        with database(write=True) as db:
            db.execute("INSERT INTO manifests(id,conversation_id,file_id,generation,length,digest,detail,created) VALUES(?,?,?,?,?,?,?,?)",
                       ("local-2", None, "lf2", "g", 1, "d", _detail(chunks["R1"]), 2.0))

    monkeypatch.setattr(rp.time, "sleep", commit_during_the_wait)
    out = rp.apply(R, grace=1)
    assert out["chunks"]["restored"] == 1 and _exists(chunks["R1"])
    assert not _exists(chunks["R2"]) and not _exists(chunks["R3"])


def test_packed_and_raw_chunks_are_both_removed(store):
    from session_history import replica_purge as rp, archive
    me, R, Q, chunks = _seed(origin_q=False)
    raw_digest = __import__("hashlib").sha256(b"raw-only " * 400).hexdigest()
    archive.chunk_path(raw_digest).write_bytes(b"raw-only " * 400)               # 압축 안 된(원래 형식) 조각
    from session_catalog.db import database
    with database(write=True) as db:
        db.execute("INSERT INTO replicas VALUES(?,?,?,?,?)", (R, "rm2", "manifest",
                   json.dumps({"id": "rm2", "file_id": "rf2", "generation": "g", "created": 2.0, "detail": _detail(raw_digest)}), 1.0))
    assert archive.chunk_packed_path(chunks["R1"]).exists()
    _off()
    rp.apply(R, grace=0)
    assert not archive.chunk_path(raw_digest).exists() and not _exists(chunks["R1"])


def test_cli_status_preview_apply_and_vacuum(store, capsys):
    from session_history import replica_purge as rp
    from session_catalog.db import directory
    _seed()
    assert rp.main(["status"]) == 0
    text = capsys.readouterr().out
    assert "다른 맥에서 복제로 받은 사본" in text and "복제를 받는 설정이 켜져 있습니다" in text
    assert rp.main(["apply", "--grace", "0"]) == 3, "복제가 켜져 있으면 거부(종료 코드 3)"
    _off()
    assert rp.main(["preview", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["receiving"] == []
    assert rp.main(["apply", "--grace", "0"]) == 0
    assert "삭제" in capsys.readouterr().out
    before = (directory() / "catalog.sqlite3").stat().st_size
    assert rp.main(["vacuum", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["before"] == before and 0 < out["after"] <= before
