"""카탈로그 저장 형식 v1 → v2 올리기(세션 로직 점검 단계 2, S2).

- 기존 저장소는 처음 열 때 한 번 올리고, 올리기 전 사본(`catalog.sqlite3.v1.bak`, 0600)을 남긴다.
- 새로 만든 저장소와 올린 저장소의 `sqlite_master`가 글자까지 같다(백업 복원이 그걸 비교한다).
- 옛(v1) 백업도 복원할 때 같은 방식으로 올린다.
- 더 새 버전이 만든 저장소(구버전으로 되돌린 경우의 반대)는 건드리지 않는다.
"""

import os
import sqlite3
import stat
import tarfile

import pytest


@pytest.fixture
def state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    return home / "state"


def _v1_store(path):
    """v1 형식 저장소를 손으로 만든다(옛 서버가 남긴 것과 같은 모양)."""
    from session_catalog.db import DDL
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript("BEGIN;" + DDL + "PRAGMA user_version=1; COMMIT;")
    db.execute("INSERT INTO meta VALUES('host','h')")
    rows = [
        # (세션 id, title, 마지막 런타임 이름, 런타임 시작·종료) — 자동 이름 / 사람이 바꾼 이름 / 런타임 없음
        ("a" * 32, "farshell", "farshell", 100.0, 300.0),
        ("b" * 32, "내 작업", "sample-repo", 100.0, 400.0),
        ("c" * 32, "orphan", None, None, None),
        # 옛 자동 이름(`server`)이 남았는데 사람이 tmux에서 `far1`로 바꿨다 → 자동, 보이는 이름은 far1.
        ("d" * 32, "server-1", "far1", 100.0, 500.0),
        # 옛 이어서 시작 이름은 보이는 이름으로 쓰지 않는다.
        ("e" * 32, "server", "fsh-" + "0" * 32, 100.0, 500.0),
    ]
    for sid, title, rname, started, ended in rows:
        db.execute("INSERT INTO sessions(id,host,title,origin,created,updated) VALUES(?,?,?,?,?,?)", (sid, "h", title, "local", 50.0, 60.0))
        if rname:
            db.execute("INSERT INTO runtimes VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (sid[:31] + "r", sid, "e", f"${sid[0]}:1", rname, "stopped", started, ended, ended, "{}"))
    db.commit()
    db.close()
    os.chmod(path, 0o600)


def test_existing_v1_store_is_upgraded_with_a_private_copy_and_backfill(state):
    from session_catalog import db
    path = state / "session-catalog" / "catalog.sqlite3"
    _v1_store(path)
    with db.database() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 2
        rows = {r["id"][0]: dict(r) for r in con.execute("SELECT id,last_activity,name_source FROM sessions")}
    assert rows["a"] == {"id": "a" * 32, "last_activity": 300.0, "name_source": "auto"}
    assert rows["b"]["last_activity"] == 400.0 and rows["b"]["name_source"] == "human"
    assert rows["c"]["last_activity"] == 60.0 and rows["c"]["name_source"] == "auto"
    with db.database() as con:
        titles = {r["id"][0]: (r["title"], r["name_source"]) for r in con.execute("SELECT id,title,name_source FROM sessions")}
    assert titles["d"] == ("far1", "auto")
    assert titles["e"] == ("server", "auto")
    assert titles["b"] == ("내 작업", "human")
    copy = path.with_name("catalog.sqlite3.v1.bak")
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    with sqlite3.connect(copy) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 1
        assert old.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 5


def test_fresh_and_upgraded_stores_have_the_same_schema(state, tmp_path):
    from session_catalog import db
    with db.database():
        pass
    fresh = sqlite3.connect(state / "session-catalog" / "catalog.sqlite3")
    query = "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    assert list(fresh.execute(query)) == db.expected_schema()
    fresh.close()
    assert not (state / "session-catalog" / "catalog.sqlite3.v1.bak").exists(), "빈 새 저장소는 사본을 만들 필요가 없다"


def test_a_store_from_a_newer_version_is_left_alone(state):
    from session_catalog import db
    path = state / "session-catalog" / "catalog.sqlite3"
    _v1_store(path)
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA user_version=99")
    with pytest.raises(db.Refused, match="unsupported_store_version"):
        with db.database():
            pass
    with sqlite3.connect(path) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 99


def test_an_old_v1_backup_is_upgraded_on_restore(state, tmp_path):
    from session_history import backup
    v1 = tmp_path / "pkg" / "catalog.sqlite3"
    _v1_store(v1)
    package = tmp_path / "old.tar"
    with tarfile.open(package, "w") as tar:
        tar.add(v1, arcname="catalog.sqlite3", recursive=False)
    destination = tmp_path / "restored-state"
    assert backup.restore_package(package, destination)["state"] == "restored"
    with sqlite3.connect(destination / "session-catalog" / "catalog.sqlite3") as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 2
        assert con.execute("SELECT name_source FROM sessions WHERE title='내 작업'").fetchone()[0] == "human"


def test_runtime_inserts_name_their_columns():
    """열 이름 없이 값만 넣는 INSERT는 열을 더하는 순간 깨진다 — 런타임 INSERT는 열 이름을 적는다."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("session_catalog/lifecycle.py", "session_catalog/inventory.py"):
        assert "INSERT INTO runtimes VALUES" not in (root / rel).read_text()
