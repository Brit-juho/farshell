"""모델 고정(pins) — 표 정합성, 해시 검증, 손상 처리, 옛 캐시 재사용. 네트워크·실제 모델을 쓰지 않는다."""
import hashlib
import os
import re

import pytest

from stt_engines import catalog, hfcache, pins
from stt_engines.pins import Pin, PinError

REPO = "o/r"
GOOD = {"a.bin": b"hello" * 10, "c.json": b"{}"}


def _sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def fx(monkeypatch, tmp_path):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    pin = Pin(REPO, "r" * 40, {n: (len(d), _sha(d)) for n, d in GOOD.items()})
    monkeypatch.setitem(pins.PINS, REPO, pin)
    root = tmp_path / "hub"
    root.mkdir()
    return pin, root


def _snap(root, rev, files):
    entry = hfcache.repo_dir(REPO, root)
    snap = entry / "snapshots" / rev
    snap.mkdir(parents=True, exist_ok=True)
    for n, d in files.items():
        blob = entry / "blobs" / f"{rev}-{n}-{_sha(d)[:8]}"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(d)
        if not os.path.lexists(snap / n):
            os.symlink(blob, snap / n)
    return snap


def test_table_is_well_formed():
    assert PINS_OK()


def PINS_OK():
    for repo, p in pins.PINS.items():
        assert re.fullmatch(r"[0-9a-f]{40}", p.revision), repo
        for n, (size, sha) in p.files.items():
            assert size > 0 and re.fullmatch(r"[0-9a-f]{64}", sha), (repo, n)
    return True


def test_every_catalog_repo_is_pinned_and_covers_its_files():
    for m in catalog.CATALOG:
        if not m.repo:
            continue
        p = pins.pin_for(m.repo)
        assert p is not None, m.id
        for n in m.files:
            assert n in p.files, (m.id, n)
    mlx = pins.pin_for("mlx-community/Qwen3-ASR-0.6B-8bit")
    assert "config.json" in mlx.files and "model.safetensors" in mlx.files


def test_unpinned_repo_returns_none(fx):
    assert pins.pin_for("x/unknown") is None
    assert pins.verified_snapshot("x/unknown") is None
    assert pins.complete_snapshot("x/unknown") is None


def test_verified_snapshot_ok_and_marker_avoids_rehash(fx, monkeypatch):
    pin, root = fx
    snap = _snap(root, pin.revision, GOOD)
    assert pins.verified_snapshot(REPO, root) == snap
    calls = []
    orig = pins.sha256_file
    monkeypatch.setattr(pins, "sha256_file", lambda p: (calls.append(p), orig(p))[1])
    assert pins.verified_snapshot(REPO, root) == snap
    assert calls == []


def test_marker_invalidated_when_file_changes(fx):
    pin, root = fx
    snap = _snap(root, pin.revision, GOOD)
    assert pins.verified_snapshot(REPO, root) == snap
    real = (snap / "a.bin").resolve()
    real.write_bytes(b"X" * len(GOOD["a.bin"]))          # 같은 크기, 다른 내용
    os.utime(real, ns=(1, 1))
    with pytest.raises(PinError):
        pins.verified_snapshot(REPO, root)


def test_hash_mismatch_in_pinned_revision_deletes_only_bad_file(fx):
    pin, root = fx
    snap = _snap(root, pin.revision, GOOD)
    (snap / "a.bin").resolve().write_bytes(b"Z" * len(GOOD["a.bin"]))
    with pytest.raises(PinError) as e:
        pins.verified_snapshot(REPO, root)
    assert "a.bin" in str(e.value)
    assert not (snap / "a.bin").exists() and not os.path.lexists(snap / "a.bin")
    assert (snap / "c.json").exists()
    assert pins.complete_snapshot(REPO, root) is None


def test_legacy_revision_with_good_hashes_is_reused(fx):
    pin, root = fx
    old = _snap(root, "o" * 40, GOOD)
    assert pins.complete_snapshot(REPO, root) == old
    assert pins.verified_snapshot(REPO, root) == old


def test_legacy_revision_with_bad_hash_is_left_alone(fx):
    pin, root = fx
    old = _snap(root, "o" * 40, {"a.bin": b"Q" * len(GOOD["a.bin"]), "c.json": GOOD["c.json"]})
    assert pins.verified_snapshot(REPO, root) is None      # 쓰지 않는다
    assert (old / "a.bin").exists()                        # 하지만 사용자 캐시는 지우지 않는다


def test_drop_keeps_blob_shared_with_other_snapshot(fx):
    pin, root = fx
    old = _snap(root, "o" * 40, GOOD)
    new = _snap(root, pin.revision, GOOD)
    shared = old / "a.bin"
    os.unlink(new / "a.bin")                               # 새 스냅샷이 옛 blob을 같이 가리키게
    os.symlink(shared.resolve(), new / "a.bin")
    shared.resolve().write_bytes(b"Z" * len(GOOD["a.bin"]))
    with pytest.raises(PinError):
        pins.verified_snapshot(REPO, root)
    assert shared.resolve().exists()                       # 옛 스냅샷이 쓰는 blob은 남는다


def test_incomplete_snapshot_is_not_complete(fx):
    pin, root = fx
    _snap(root, pin.revision, {"a.bin": GOOD["a.bin"]})
    assert pins.complete_snapshot(REPO, root) is None
    assert pins.verified_snapshot(REPO, root) is None


def test_download_uses_pinned_revision_and_verifies(fx, monkeypatch):
    pin, root = fx
    seen = {}

    def fake_dl(repo, **kw):
        seen.update(kw, repo=repo)
        _snap(root, kw["revision"], GOOD)
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_dl)
    snap = pins.download_pinned(REPO, root)
    assert seen["revision"] == pin.revision and seen["repo"] == REPO
    assert sorted(seen["allow_patterns"]) == sorted(GOOD)
    assert snap.name == pin.revision


def test_download_with_bad_content_raises_and_removes(fx, monkeypatch):
    pin, root = fx

    def fake_dl(repo, **kw):
        _snap(root, kw["revision"], {"a.bin": b"E" * len(GOOD["a.bin"]), "c.json": GOOD["c.json"]})
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_dl)
    with pytest.raises(PinError):
        pins.download_pinned(REPO, root)
    assert pins.complete_snapshot(REPO, root) is None


def test_download_skips_network_when_cache_verified(fx, monkeypatch):
    pin, root = fx
    _snap(root, "o" * 40, GOOD)
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda *a, **k: pytest.fail("네트워크를 쓰면 안 된다"))
    assert pins.download_pinned(REPO, root).name == "o" * 40


def test_download_removes_truncated_file_before_fetch(fx, monkeypatch):
    pin, root = fx
    snap = _snap(root, pin.revision, {"a.bin": b"short", "c.json": GOOD["c.json"]})

    def fake_dl(repo, **kw):
        assert not os.path.lexists(snap / "a.bin")         # 잘린 파일은 먼저 치워져 HF가 다시 받는다
        _snap(root, kw["revision"], GOOD)
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_dl)
    assert pins.download_pinned(REPO, root).name == pin.revision
