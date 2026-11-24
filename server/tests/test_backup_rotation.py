"""일일 카탈로그 백업 보관 개수 — 가장 최근 것만 남기고, 새 백업은 확인된 뒤에만 옛것을 지운다."""
import sqlite3
import time

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
    return home


def _files(folder):
    return sorted(p.name for p in folder.glob("*.sqlite3"))


def test_only_the_newest_backup_is_kept_and_it_is_a_valid_database(store):
    from session_history import backup
    from session_catalog.db import directory
    assert backup.KEEP_SNAPSHOTS == 1
    first = backup.snapshot()
    time.sleep(0.01)
    second = backup.snapshot()
    time.sleep(0.01)
    third = backup.snapshot()
    folder = directory() / "backups"
    assert _files(folder) == [third.name], "이전 백업은 지워지고 방금 만든 것만 남는다"
    assert first.name != third.name and second.name != third.name
    con = sqlite3.connect(third)
    try:
        assert con.execute("pragma quick_check").fetchone()[0] == "ok"
        assert con.execute("select count(*) from meta").fetchone()[0] >= 1
    finally:
        con.close()


def test_old_extra_backups_from_the_seven_era_are_cleaned_up_by_the_next_run(store):
    """예전(7개 보관) 때 쌓인 여러 개도 다음 백업 때 한꺼번에 정리된다 — 방금 만든 것은 남는다."""
    from session_history import backup
    from session_catalog.db import directory
    folder = directory() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        (folder / f"{1000 + i}.sqlite3").write_bytes(b"old")
    fresh = backup.snapshot()
    assert _files(folder) == [fresh.name]


def test_a_failed_new_backup_never_deletes_the_old_one(store, monkeypatch):
    from session_history import backup
    from session_catalog.db import directory
    kept = backup.snapshot()
    monkeypatch.setattr(backup.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        backup.snapshot()
    assert _files(directory() / "backups") == [kept.name], "새 백업이 실패하면 기존 백업은 그대로"
