"""HF 캐시 도우미 — 용량 집계·삭제·조회."""
import os

import pytest

from stt_engines import hfcache


def _mk_repo(root, repo, files, shared=None, inside_blobs=True, rev="abc123"):
    """files: {이름: bytes}. shared=True면 가중치 실파일을 폴더 밖 root/blobs 에 둔다(xet 방식)."""
    entry = hfcache.repo_dir(repo, root)
    snap = entry / "snapshots" / rev
    snap.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        if shared:
            blob = root / "blobs" / f"{repo.replace('/', '_')}-{name}"
            blob.parent.mkdir(parents=True, exist_ok=True)
        else:
            blob = entry / "blobs" / f"h-{name}"
            blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(data)
        os.symlink(blob, snap / name)
    return entry


def test_dir_size_counts_link_target_once(tmp_path):
    entry = _mk_repo(tmp_path, "o/r", {"a.bin": b"x" * 100})
    # 같은 실파일을 가리키는 링크가 여럿이어도 한 번만
    os.symlink(entry / "blobs" / "h-a.bin", entry / "snapshots" / "abc123" / "alias.bin")
    assert hfcache.dir_size(entry) == 100


def test_dir_size_includes_outside_blobs(tmp_path):
    entry = _mk_repo(tmp_path, "o/r", {"w.gguf": b"y" * 500, "c.json": b"z" * 5}, shared=True)
    assert hfcache.dir_size(entry) == 505


def test_dir_size_missing_dir(tmp_path):
    assert hfcache.dir_size(tmp_path / "nope") == 0


def test_dir_size_broken_link_ignored(tmp_path):
    entry = _mk_repo(tmp_path, "o/r", {"a": b"1234"})
    os.symlink(tmp_path / "gone", entry / "snapshots" / "abc123" / "dangling")
    assert hfcache.dir_size(entry) == 4


def test_delete_repo_removes_outside_blob(tmp_path):
    entry = _mk_repo(tmp_path, "o/r", {"w": b"x" * 300}, shared=True)
    blob = tmp_path / "blobs" / "o_r-w"
    freed = hfcache.delete_repo("o/r", tmp_path)
    assert not entry.exists() and not blob.exists()
    assert freed == 300


def test_delete_repo_inside_blobs_freed_once(tmp_path):
    _mk_repo(tmp_path, "o/r", {"w": b"x" * 40})
    assert hfcache.delete_repo("o/r", tmp_path) == 40


def test_delete_repo_keeps_blob_used_by_other_repo(tmp_path):
    _mk_repo(tmp_path, "o/r1", {"w": b"x" * 300}, shared=True)
    e2 = hfcache.repo_dir("o/r2", tmp_path)
    (e2 / "snapshots" / "r").mkdir(parents=True)
    shared_blob = tmp_path / "blobs" / "o_r1-w"
    os.symlink(shared_blob, e2 / "snapshots" / "r" / "w")
    hfcache.delete_repo("o/r1", tmp_path)
    assert shared_blob.exists()
    assert (e2 / "snapshots" / "r" / "w").resolve() == shared_blob.resolve()


def test_delete_repo_never_touches_outside_cache(tmp_path):
    root = tmp_path / "hub"
    root.mkdir()
    external = tmp_path / "external.bin"
    external.write_bytes(b"keep" * 10)
    entry = hfcache.repo_dir("o/r", root)
    snap = entry / "snapshots" / "r"
    snap.mkdir(parents=True)
    os.symlink(external, snap / "w")
    hfcache.delete_repo("o/r", root)
    assert external.exists() and not entry.exists()


def test_delete_repo_missing_returns_zero(tmp_path):
    assert hfcache.delete_repo("o/none", tmp_path) == 0


def test_delete_repo_files_only_named(tmp_path):
    entry = _mk_repo(tmp_path, "o/r", {"a.gguf": b"a" * 10, "mmproj-a.gguf": b"m" * 20, "other.gguf": b"o" * 7})
    freed = hfcache.delete_repo_files("o/r", ["a.gguf", "mmproj-a.gguf"], tmp_path)
    snap = entry / "snapshots" / "abc123"
    assert freed == 30
    assert not (snap / "a.gguf").exists() and not (snap / "mmproj-a.gguf").exists()
    assert (snap / "other.gguf").resolve().read_bytes() == b"o" * 7
    assert entry.exists()


def test_delete_repo_files_outside_blobs(tmp_path):
    entry = _mk_repo(tmp_path, "o/r", {"a": b"a" * 10, "b": b"b" * 5}, shared=True)
    freed = hfcache.delete_repo_files("o/r", ["a"], tmp_path)
    assert freed == 10
    assert not (tmp_path / "blobs" / "o_r-a").exists()
    assert (tmp_path / "blobs" / "o_r-b").exists()
    assert (entry / "snapshots" / "abc123" / "b").exists()


def test_delete_repo_files_keeps_blob_shared_with_other_repo(tmp_path):
    _mk_repo(tmp_path, "o/r", {"a": b"a" * 10}, shared=True)
    blob = tmp_path / "blobs" / "o_r-a"
    e2 = hfcache.repo_dir("o/r2", tmp_path)
    (e2 / "snapshots" / "x").mkdir(parents=True)
    os.symlink(blob, e2 / "snapshots" / "x" / "a")
    assert hfcache.delete_repo_files("o/r", ["a"], tmp_path) == 0
    assert blob.exists()


def test_delete_repo_files_cleans_empty_repo(tmp_path):
    entry = _mk_repo(tmp_path, "o/r", {"a": b"a" * 10, "b": b"b" * 5})
    freed = hfcache.delete_repo_files("o/r", ["a", "b"], tmp_path)
    assert freed == 15
    assert not entry.exists()


def test_delete_repo_files_cleans_empty_repo_outside_blobs(tmp_path):
    entry = _mk_repo(tmp_path, "o/r", {"a": b"a" * 10}, shared=True)
    assert hfcache.delete_repo_files("o/r", ["a"], tmp_path) == 10
    assert not entry.exists()


def test_delete_repo_files_missing_repo(tmp_path):
    assert hfcache.delete_repo_files("o/none", ["a"], tmp_path) == 0


def test_delete_repo_files_ignores_link_outside_cache(tmp_path):
    root = tmp_path / "hub"
    root.mkdir()
    external = tmp_path / "ext.bin"
    external.write_bytes(b"x" * 9)
    entry = hfcache.repo_dir("o/r", root)
    snap = entry / "snapshots" / "r"
    snap.mkdir(parents=True)
    os.symlink(external, snap / "a")
    hfcache.delete_repo_files("o/r", ["a"], root)
    assert external.exists()


def test_cached_file(tmp_path):
    _mk_repo(tmp_path, "o/r", {"a.gguf": b"1"})
    p = hfcache.cached_file("o/r", "a.gguf", tmp_path)
    assert p is not None and p.name == "a.gguf" and p.resolve().is_file()
    assert hfcache.cached_file("o/r", "zzz", tmp_path) is None
    assert hfcache.cached_file("o/none", "a.gguf", tmp_path) is None


def test_cached_file_incomplete_dangling_link(tmp_path):
    entry = _mk_repo(tmp_path, "o/r", {"a": b"1"})
    (entry / "blobs" / "h-a").unlink()          # 받는 도중/손상: 링크만 남음
    assert hfcache.cached_file("o/r", "a", tmp_path) is None


def test_cached_snapshot(tmp_path):
    assert hfcache.cached_snapshot("o/r", tmp_path) is None
    entry = _mk_repo(tmp_path, "o/r", {"a": b"1"}, rev="old")
    assert hfcache.cached_snapshot("o/r", tmp_path).name == "old"
    new = entry / "snapshots" / "new"
    new.mkdir()
    os.utime(entry / "snapshots" / "old", (1, 1))
    assert hfcache.cached_snapshot("o/r", tmp_path).name == "new"


def test_cached_snapshot_empty_snapshots_dir(tmp_path):
    (hfcache.repo_dir("o/r", tmp_path) / "snapshots").mkdir(parents=True)
    assert hfcache.cached_snapshot("o/r", tmp_path) is None


def test_repo_dir_name():
    assert hfcache.repo_dir("a/b", hfcache.Path("/x")).name == "models--a--b"
