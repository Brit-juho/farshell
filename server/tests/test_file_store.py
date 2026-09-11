"""N19 — file_store.py 단위 테스트 (50-files-share.md §1 수용 기준).

id 접근만 되는지 · TTL/상한 정리 순서 · 공유 중/고정 파일 제외 · 마이그레이션 ·
고아 정리를 확인한다. HTTP 레이어(routes/files.py)는 test_routes_files.py로 분리.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    """매 테스트마다 격리된 ~/.vt를 가리키도록 file_store를 재로드."""
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    import file_store
    importlib.reload(file_store)
    return file_store


def _upload(store, tmp_path, name="a.txt", content=b"hello", **kw):
    import uuid
    src = tmp_path / f"src-{uuid.uuid4().hex}"
    src.write_bytes(content)
    return store.add_from_upload(src, name, len(content), **kw)


# --- id 접근만 -----------------------------------------------------------------


def test_real_path_for_returns_none_for_unknown_id(store):
    assert store.real_path_for("nope") is None


def test_add_from_upload_stores_by_id_prefixed_path(store, tmp_path):
    item = _upload(store, tmp_path, name="report.pdf")
    fp = store.real_path_for(item["id"])
    assert fp is not None
    assert fp.name.startswith(item["id"] + "__")
    assert fp.name.endswith("report.pdf")
    assert fp.read_bytes() == b"hello"


def test_add_from_upload_sanitizes_traversal_in_name(store, tmp_path):
    item = _upload(store, tmp_path, name="../../etc/passwd")
    # 세그먼트만 남는다 — 저장 경로 탈출 불가
    assert "/" not in item["name"] and ".." not in item["name"]
    fp = store.real_path_for(item["id"])
    assert fp.parent == store.files_dir()


def test_get_item_and_list_items_roundtrip(store, tmp_path):
    item = _upload(store, tmp_path)
    assert store.get_item(item["id"])["name"] == "a.txt"
    assert any(x["id"] == item["id"] for x in store.list_items())


def test_delete_removes_meta_and_disk(store, tmp_path):
    item = _upload(store, tmp_path)
    fp = store.real_path_for(item["id"])
    assert store.delete(item["id"]) is True
    assert not fp.exists()
    assert store.get_item(item["id"]) is None
    assert store.delete(item["id"]) is False  # 이미 지워짐 → False


# --- TTL/상한 정리 순서 ---------------------------------------------------------


def test_cleanup_removes_ttl_expired_files(store, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "TTL_SECONDS", 1)
    item = _upload(store, tmp_path)
    # created를 과거로 밀어 TTL을 넘긴 것처럼 만든다.
    items = store._read_unlocked()
    for x in items:
        x["created"] = time.time() - 10
    store._write_unlocked(items)

    result = store.cleanup()
    assert result["removed_ttl"] == 1
    assert store.get_item(item["id"]) is None


def test_cleanup_evicts_oldest_first_when_over_cap(store, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "MAX_TOTAL_BYTES", 10)  # 5바이트 파일 3개 중 1개가 초과분
    monkeypatch.setattr(store, "TTL_SECONDS", 10**12)  # TTL 정리가 먼저 다 지워버리지 않게
    old = _upload(store, tmp_path, name="old.txt", content=b"aaaaa")
    mid = _upload(store, tmp_path, name="mid.txt", content=b"bbbbb")
    new = _upload(store, tmp_path, name="new.txt", content=b"ccccc")
    created_by_id = {old["id"]: 1.0, mid["id"]: 2.0, new["id"]: 3.0}
    items = store._read_unlocked()
    for x in items:
        x["created"] = created_by_id[x["id"]]
    store._write_unlocked(items)

    result = store.cleanup()
    remaining = {x["id"] for x in store.list_items()}
    assert new["id"] in remaining and mid["id"] in remaining
    assert old["id"] not in remaining
    assert result["removed_cap"] == 1


def test_cleanup_excludes_shared_files(store, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "TTL_SECONDS", 1)
    item = _upload(store, tmp_path)
    items = store._read_unlocked()
    for x in items:
        x["created"] = time.time() - 10
        x["shares"] = [{"shareId": "s1", "exp": time.time() + 3600}]  # 만료 안 된 공유
    store._write_unlocked(items)

    store.cleanup()
    assert store.get_item(item["id"]) is not None


def test_cleanup_excludes_pinned_files(store, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "TTL_SECONDS", 1)
    item = _upload(store, tmp_path)
    items = store._read_unlocked()
    for x in items:
        x["created"] = time.time() - 10
        x["pin"] = True
    store._write_unlocked(items)

    store.cleanup()
    assert store.get_item(item["id"]) is not None


# --- 마이그레이션 / 고아 정리 ---------------------------------------------------


def test_migrate_legacy_moves_files_and_creates_entries(store, tmp_path, monkeypatch):
    legacy = tmp_path / "legacy-uploads"
    legacy.mkdir()
    (legacy / "old-upload.txt").write_bytes(b"legacy content")
    monkeypatch.setattr(store, "LEGACY_UPLOAD_DIR", legacy)

    moved = store.migrate_legacy()
    assert moved == 1
    assert not (legacy / "old-upload.txt").exists()
    items = store.list_items()
    assert len(items) == 1
    fp = store.real_path_for(items[0]["id"])
    assert fp.read_bytes() == b"legacy content"


def test_migrate_legacy_is_noop_without_legacy_dir(store, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "LEGACY_UPLOAD_DIR", tmp_path / "does-not-exist")
    assert store.migrate_legacy() == 0


def test_reconcile_orphans_removes_meta_without_disk_file(store, tmp_path):
    item = _upload(store, tmp_path)
    store.real_path_for(item["id"]).unlink()
    result = store.reconcile_orphans()
    assert result["removed_meta"] == 1
    assert store.get_item(item["id"]) is None


def test_reconcile_orphans_removes_disk_file_without_meta(store, tmp_path):
    _upload(store, tmp_path)
    orphan = store.files_dir() / "ghost__orphan.txt"
    orphan.write_bytes(b"x")
    result = store.reconcile_orphans()
    assert result["removed_disk"] == 1
    assert not orphan.exists()
