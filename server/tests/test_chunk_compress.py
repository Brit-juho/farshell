"""대화 사본 조각의 압축 저장 — 이름(원본 바이트의 sha256)은 그대로, 디스크에는 원래 형식 또는 `.z`(zstd)로."""
import hashlib
import json
import os
import tarfile

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
    # 이 파일의 시험은 조각을 실제로 쓰고 지운다 — 시험용 임시 폴더가 아니면 즉시 멈춘다(실제 ~/.vt를 건드리지 않게).
    assert str(db.directory().resolve()).startswith(str(tmp_path.resolve())), db.directory()
    return home


TEXT = (b'{"type":"assistant","message":{"content":[{"type":"text","text":"hello hello hello"}]}}\n' * 400)
NOISE = os.urandom(60 * 1024)


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _raw_write(digest, data):
    from session_history import archive
    archive.chunk_path(digest).write_bytes(data)       # 예전 방식으로 쌓인 원래 형식 조각


def test_new_chunks_are_stored_packed_and_read_back_identically(store):
    from session_history import archive
    d = archive.put_chunk(TEXT)
    assert d == _sha(TEXT), "이름은 원본 바이트의 sha256 그대로"
    assert archive.chunk_packed_path(d).exists() and not archive.chunk_path(d).exists()
    assert archive.chunk_packed_path(d).stat().st_size < len(TEXT) / 3
    assert archive.read_chunk(d) == TEXT
    assert archive.chunk_exists(d)


def test_incompressible_data_stays_in_the_original_form(store):
    from session_history import archive
    d = archive.put_chunk(NOISE)
    assert archive.chunk_path(d).exists() and not archive.chunk_packed_path(d).exists()
    assert archive.read_chunk(d) == NOISE


def test_putting_the_same_chunk_twice_stores_it_once_in_either_form(store):
    from session_history import archive
    d = archive.put_chunk(TEXT)
    assert archive.put_chunk(TEXT) == d
    folder = archive.chunk_path(d).parent
    assert sorted(p.name for p in folder.iterdir() if not p.name.startswith(".")) == [d + ".z"]
    e = _sha(b"legacy" * 1000)
    _raw_write(e, b"legacy" * 1000)
    assert archive.put_chunk(b"legacy" * 1000) == e, "원래 형식으로 이미 있으면 다시 쓰지 않는다"
    assert not archive.chunk_packed_path(e).exists()


def test_a_wrong_stored_chunk_is_refused(store):
    from session_history import archive
    d = _sha(TEXT)
    _raw_write(d, b"not what the name says")
    with pytest.raises(archive.Refused) as err:
        archive.put_chunk(TEXT)
    assert err.value.code == "chunk_corrupt"


@pytest.mark.parametrize("damage", ["flip", "truncate", "other_content", "garbage"])
def test_a_damaged_packed_chunk_is_never_returned(store, damage):
    from session_history import archive
    d = archive.put_chunk(TEXT)
    path = archive.chunk_packed_path(d)
    blob = path.read_bytes()
    if damage == "flip":
        blob = blob[:20] + bytes([blob[20] ^ 0xFF]) + blob[21:]
    elif damage == "truncate":
        blob = blob[: len(blob) // 2]
    elif damage == "other_content":
        blob = archive.pack_bytes(b"x" * 5000)          # 정상 압축이지만 이름과 다른 내용
    else:
        blob = b"this is not zstd at all"
    path.write_bytes(blob)
    with pytest.raises(archive.Refused) as err:
        archive.read_chunk(d)
    assert err.value.code == "chunk_corrupt"


def test_a_missing_chunk_is_a_missing_file_error_in_both_forms(store):
    from session_history import archive
    with pytest.raises(OSError):
        archive.read_chunk(_sha(b"never stored"))
    assert not archive.chunk_exists(_sha(b"never stored"))


def test_the_raw_file_wins_when_both_forms_exist(store):
    from session_history import archive
    d = archive.put_chunk(TEXT)
    _raw_write(d, TEXT)
    assert archive.read_chunk(d) == TEXT
    archive.chunk_packed_path(d).write_bytes(b"garbage")      # 변환 도중 상태 — 원래 형식이 있으면 그것을 읽는다
    assert archive.read_chunk(d) == TEXT


def test_usage_counts_a_packed_chunk_under_its_digest(store):
    """`.z` 이름 때문에 어느 기록도 안 가리키는 조각으로 오해해 양쪽 용량에 다 세면 안 된다."""
    from session_history import archive
    from session_catalog.db import database
    d = archive.put_chunk(TEXT)
    size = archive.chunk_packed_path(d).stat().st_size
    with database(write=True) as db:
        db.execute("INSERT INTO manifests(id,conversation_id,file_id,generation,length,digest,detail,created) VALUES(?,?,?,?,?,?,?,?)",
                   ("m1", None, "f1", "g1", len(TEXT), d, json.dumps({"chunks": [{"digest": d, "size": len(TEXT)}]}), 1.0))
    usage = archive._count_usage()
    assert usage["local"] == size and usage["remote"] == 0


# ── 변환 도구 ───────────────────────────────────────────────────────────────────

def _seed(n=5):
    from session_history import archive
    out = []
    for i in range(n):
        data = (f'{{"i":{i},"text":"{"abc " * 500}"}}\n'.encode()) * 20
        d = _sha(data)
        _raw_write(d, data)
        out.append((d, data))
    noise = os.urandom(50 * 1024)
    _raw_write(_sha(noise), noise)
    out.append((_sha(noise), noise))
    return out


def test_preview_changes_nothing(store):
    from session_history import chunk_pack, archive
    chunks = _seed()
    before = sorted(p.name for p in archive.chunk_path(chunks[0][0]).parent.iterdir())
    info = chunk_pack.preview()
    assert info["estimated_saved_bytes"] > 0 and info["incompressible"] == 1 and info["unreadable"] == 0
    assert sorted(p.name for p in archive.chunk_path(chunks[0][0]).parent.iterdir()) == before


def test_apply_packs_every_compressible_chunk_and_keeps_all_readable(store):
    from session_history import chunk_pack, archive
    chunks = _seed()
    out = chunk_pack.apply()
    assert out["converted"] == 5 and out["kept_raw_incompressible"] == 1 and not out["failed"] and out["stopped"] is None
    for d, data in chunks:
        assert archive.read_chunk(d) == data
    for d, _ in chunks[:-1]:
        assert archive.chunk_packed_path(d).exists() and not archive.chunk_path(d).exists()
    assert archive.chunk_path(chunks[-1][0]).exists(), "압축해도 안 줄어드는 조각은 그대로"
    again = chunk_pack.apply()
    assert again["converted"] == 0 and again["kept_raw_incompressible"] == 1, "두 번 실행해도 같은 결과"
    s = chunk_pack.status()
    assert s["packed_chunks"] == 5 and s["raw_chunks"] == 1 and s["both_forms"] == 0


def test_a_failed_check_leaves_the_raw_file_in_place(store, monkeypatch):
    from session_history import chunk_pack, archive
    chunks = _seed(3)
    bad = chunks[1][0]
    real = chunk_pack._verify_packed
    monkeypatch.setattr(chunk_pack, "_verify_packed", lambda path, digest: False if digest == bad else real(path, digest))
    out = chunk_pack.apply()
    assert [f["digest"] for f in out["failed"]] == [bad] and out["converted"] == 2
    assert archive.chunk_path(bad).exists() and not archive.chunk_packed_path(bad).exists(), "검증 실패한 조각은 원본 유지, 압축본은 치움"
    assert archive.read_chunk(bad) == chunks[1][1]


def test_a_chunk_that_is_already_wrong_is_never_touched(store):
    from session_history import chunk_pack, archive
    d = _sha(TEXT)
    _raw_write(d, b"damaged bytes " * 100)
    out = chunk_pack.apply()
    assert out["failed"] == [{"digest": d, "why": "raw_mismatch"}] and out["converted"] == 0
    assert archive.chunk_path(d).read_bytes() == b"damaged bytes " * 100


def test_an_interrupted_run_loses_nothing(store, monkeypatch):
    """압축본을 쓴 직후 중단돼도(원본 삭제 전) 두 형식이 함께 있고 읽기는 정상 — 다음 실행이 정리한다."""
    from session_history import chunk_pack, archive
    chunks = _seed(3)

    def crash(*_a, **_k):
        raise RuntimeError("power loss")

    with monkeypatch.context() as patched:               # 이 안에서만 고친다(밖의 시험용 환경 설정은 그대로)
        patched.setattr(chunk_pack, "_sync_folder", crash)
        with pytest.raises(RuntimeError):
            chunk_pack.apply()
    for d, data in chunks:
        assert archive.read_chunk(d) == data
    assert chunk_pack.status()["both_forms"] == 3
    out = chunk_pack.apply()
    assert out["failed"] == [] and chunk_pack.status()["both_forms"] == 0
    for d, data in chunks:
        assert archive.read_chunk(d) == data


def test_limit_and_low_disk_stop_early_and_resume_later(store, monkeypatch):
    from session_history import chunk_pack, archive
    chunks = _seed(5)
    first = chunk_pack.apply(limit_bytes=1)
    assert first["stopped"] == "limit" and first["converted"] == 1, "한도는 조각 단위로 확인하므로 하나는 넘을 수 있다"
    with monkeypatch.context() as patched:
        patched.setattr(chunk_pack.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
        low = chunk_pack.apply()
    assert low["stopped"] == "low_disk" and low["converted"] == 0
    rest = chunk_pack.apply()
    assert rest["stopped"] is None and not rest["failed"] and first["converted"] + rest["converted"] == 5
    for d, data in chunks:
        assert archive.read_chunk(d) == data


def test_expand_restores_the_original_form_byte_for_byte(store):
    from session_history import chunk_pack, archive
    chunks = _seed()
    chunk_pack.apply()
    out = chunk_pack.expand()
    assert out["converted"] == 5 and not out["failed"]
    for d, data in chunks:
        assert archive.chunk_path(d).read_bytes() == data and not archive.chunk_packed_path(d).exists()


def test_a_second_run_at_the_same_time_is_refused(store):
    from session_history import chunk_pack
    with chunk_pack._lock():
        assert chunk_pack.main(["apply"]) == 3


def test_export_and_restore_work_with_packed_chunks(store, tmp_path):
    """내보내기는 압축 여부와 상관없이 이름과 같은 원본 바이트를 담고, 복원이 그것을 받는다."""
    from session_history import archive, backup
    from session_catalog.db import database
    d = archive.put_chunk(TEXT)
    assert archive.chunk_packed_path(d).exists()
    with database(write=True) as db:
        db.execute("INSERT INTO manifests(id,conversation_id,file_id,generation,length,digest,detail,created) VALUES(?,?,?,?,?,?,?,?)",
                   ("m1", None, "f1", "g1", len(TEXT), d, json.dumps({"chunks": [{"digest": d, "size": len(TEXT)}]}), 1.0))
    package = backup.export_package()
    with tarfile.open(package) as tar:
        member = tar.extractfile("chunks/" + d)
        assert member.read() == TEXT
