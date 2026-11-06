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


# --- 일괄 삭제(2026-09-23, 첨부 탭 「선택 삭제」) -------------------------------


def test_delete_many_removes_all_given_ids(store, tmp_path):
    a = _upload(store, tmp_path, name="a.txt")
    b = _upload(store, tmp_path, name="b.txt")
    c = _upload(store, tmp_path, name="c.txt")
    fp_a, fp_b = store.real_path_for(a["id"]), store.real_path_for(b["id"])
    result = store.delete_many([a["id"], b["id"]])
    assert sorted(result["deleted"]) == sorted([a["id"], b["id"]])
    assert result["missing"] == []
    assert not fp_a.exists()
    assert not fp_b.exists()
    assert store.get_item(a["id"]) is None
    assert store.get_item(b["id"]) is None
    assert store.get_item(c["id"]) is not None  # 나머지는 그대로


def test_delete_many_reports_missing_ids_without_failing(store, tmp_path):
    a = _upload(store, tmp_path, name="a.txt")
    result = store.delete_many([a["id"], "nope-1", "nope-2"])
    assert result["deleted"] == [a["id"]]
    assert sorted(result["missing"]) == ["nope-1", "nope-2"]


def test_delete_many_dedupes_repeated_ids(store, tmp_path):
    a = _upload(store, tmp_path, name="a.txt")
    result = store.delete_many([a["id"], a["id"]])
    assert result["deleted"] == [a["id"]]
    assert result["missing"] == []


def test_delete_many_empty_list_is_a_noop(store, tmp_path):
    a = _upload(store, tmp_path, name="a.txt")
    result = store.delete_many([])
    assert result == {"deleted": [], "missing": []}
    assert store.get_item(a["id"]) is not None


def test_delete_many_removes_share_records_with_the_file(store, tmp_path):
    a = _upload(store, tmp_path, name="a.txt")
    store.add_share(a["id"], "device", 3600, False)
    assert store.get_item(a["id"])["shares"]
    result = store.delete_many([a["id"]])
    assert result["deleted"] == [a["id"]]
    assert store.get_item(a["id"]) is None  # 파일과 함께 공유 레코드도 사라진다


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



def test_set_pin_turns_protection_on_and_off(store, tmp_path, monkeypatch):
    """고정을 켜는 길(set_pin)이 실제로 정리에서 빼고, 끄면 다시 정리 대상이 된다."""
    monkeypatch.setattr(store, "TTL_SECONDS", 1)
    item = _upload(store, tmp_path)
    assert store.set_pin(item["id"], True)["pin"] is True
    items = store._read_unlocked()
    for x in items:
        x["created"] = time.time() - 10
    store._write_unlocked(items)
    store.cleanup()
    assert store.get_item(item["id"])["pin"] is True

    assert store.set_pin(item["id"], False)["pin"] is False
    store.cleanup()
    assert store.get_item(item["id"]) is None


def test_set_pin_unknown_id_returns_none(store):
    assert store.set_pin("nope", True) is None


# --- 공유 다운로드 판정(claim_download) ------------------------------------------


def _pin_share_with_cookie(store, tmp_path, once=False):
    import hashlib
    item = _upload(store, tmp_path)
    share = store.add_share(item["id"], "pin", 3600, once, pin="123456")
    cookie_hash = hashlib.sha256(b"cookie-secret").hexdigest()
    store.update_share(item["id"], share["shareId"], dl_cookie_hash=cookie_hash, dl_cookie_exp=time.time() + 60)
    return item, share, cookie_hash


def test_claim_download_consumes_the_pin_cookie(store, tmp_path):
    item, share, cookie_hash = _pin_share_with_cookie(store, tmp_path)
    state, got, fp = store.claim_download(item["id"], share["shareId"], cookie_hash)
    assert state == "ok" and got["id"] == item["id"] and fp.read_bytes() == b"hello"
    # 같은 쿠키는 두 번 통하지 않는다 — 소비 표시가 저장소에 남았다.
    assert store.claim_download(item["id"], share["shareId"], cookie_hash)[0] == "denied"
    _, after = store.get_item_and_share(item["id"], share["shareId"])
    assert "dl_cookie_hash" not in after and after["views"] == 1


def test_claim_download_rejects_wrong_expired_or_missing_cookie(store, tmp_path):
    item, share, cookie_hash = _pin_share_with_cookie(store, tmp_path)
    assert store.claim_download(item["id"], share["shareId"], "0" * 64)[0] == "denied"
    assert store.claim_download(item["id"], share["shareId"], None)[0] == "denied"   # pin 공유를 device 경로로
    store.update_share(item["id"], share["shareId"], dl_cookie_exp=time.time() - 1)
    assert store.claim_download(item["id"], share["shareId"], cookie_hash)[0] == "denied"
    assert store.claim_download(item["id"], "no-such-share", cookie_hash)[0] == "gone"


def test_claim_download_device_mode_and_once(store, tmp_path):
    item = _upload(store, tmp_path)
    share = store.add_share(item["id"], "device", 3600, True)
    # device 공유에 쿠키 해시를 들고 오면 모드가 엇갈린 것이다.
    assert store.claim_download(item["id"], share["shareId"], "x" * 64)[0] == "denied"
    assert store.claim_download(item["id"], share["shareId"])[0] == "ok"
    # once — 첫 다운로드가 같은 락 안에서 공유를 지웠다.
    assert store.claim_download(item["id"], share["shareId"])[0] == "gone"


def test_claim_download_is_atomic_across_threads(store, tmp_path):
    """같은 쿠키로 동시에 와도 정확히 하나만 통과한다."""
    import threading
    item, share, cookie_hash = _pin_share_with_cookie(store, tmp_path)
    out = []
    gate = threading.Barrier(20)

    def go():
        gate.wait()
        out.append(store.claim_download(item["id"], share["shareId"], cookie_hash)[0])

    ts = [threading.Thread(target=go) for _ in range(20)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert out.count("ok") == 1 and out.count("denied") == 19


# --- 이어받기(claim_resume) — 소비한 쿠키는 원래 기한까지 Range 이어받기에만 쓰인다 ----------------------


def test_claim_resume_needs_a_claimed_cookie_within_its_original_expiry(store, tmp_path):
    item, share, cookie_hash = _pin_share_with_cookie(store, tmp_path)
    # 아직 소비 전 — 이어받기로는 새 다운로드를 열 수 없다.
    assert store.claim_resume(item["id"], share["shareId"], cookie_hash)[0] == "denied"
    assert store.claim_download(item["id"], share["shareId"], cookie_hash)[0] == "ok"
    state, got, fp = store.claim_resume(item["id"], share["shareId"], cookie_hash)
    assert state == "ok" and fp.read_bytes() == b"hello"
    assert store.claim_resume(item["id"], share["shareId"], "0" * 64)[0] == "denied"
    assert store.claim_resume(item["id"], share["shareId"], "")[0] == "denied"
    # 조회수는 다운로드 한 번으로만 센다.
    assert store.get_item_and_share(item["id"], share["shareId"])[1]["views"] == 1


def test_claim_resume_ends_at_the_cookie_expiry_and_on_revoke(store, tmp_path, monkeypatch):
    item, share, cookie_hash = _pin_share_with_cookie(store, tmp_path)
    assert store.claim_download(item["id"], share["shareId"], cookie_hash)[0] == "ok"
    real = time.time
    monkeypatch.setattr(store.time, "time", lambda: real() + 61)
    assert store.claim_resume(item["id"], share["shareId"], cookie_hash)[0] == "denied"
    store.cleanup()
    assert "dl_resumes" not in store.get_item(item["id"])   # 지난 기록은 정리 때 걷힌다
    monkeypatch.setattr(store.time, "time", real)

    item2, share2, hash2 = _pin_share_with_cookie(store, tmp_path)
    assert store.claim_download(item2["id"], share2["shareId"], hash2)[0] == "ok"
    store.remove_share(item2["id"], share2["shareId"])
    assert store.claim_resume(item2["id"], share2["shareId"], hash2)[0] == "denied", "취소한 공유를 이어받았다"


def test_claim_resume_works_for_a_consumed_once_share(store, tmp_path):
    item, share, cookie_hash = _pin_share_with_cookie(store, tmp_path, once=True)
    assert store.claim_download(item["id"], share["shareId"], cookie_hash)[0] == "ok"
    assert store.get_item_and_share(item["id"], share["shareId"])[1] is None   # once — 공유는 지워졌다
    assert store.claim_resume(item["id"], share["shareId"], cookie_hash)[0] == "ok"
    assert store.claim_download(item["id"], share["shareId"], cookie_hash)[0] == "gone"


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


# --- CLI (N23, 50-files-share.md §6) ------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """CLI 테스트용 — share 발급은 auth.sign_payload가 필요하므로 서명키도 심는다."""
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    import auth as _auth
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "cli-test-key")
    import file_store
    importlib.reload(file_store)
    return file_store


def test_cli_ls_empty(cli_env, capsys):
    rc = cli_env._cli(["ls"])
    assert rc == 0
    assert "파일이 없습니다" in capsys.readouterr().out


def test_cli_add_copies_file_and_keeps_original(cli_env, tmp_path, capsys):
    src = tmp_path / "orig.txt"
    src.write_text("hello")
    rc = cli_env._cli(["add", str(src)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "추가됨" in out and "orig.txt" in out
    assert src.is_file()  # add는 복사 — 원본 유지
    assert len(cli_env.list_items()) == 1


def test_cli_add_missing_file_errors(cli_env, capsys):
    rc = cli_env._cli(["add", "/no/such/file.txt"])
    assert rc == 1
    assert "없습니다" in capsys.readouterr().err


def test_cli_add_with_share_issues_token(cli_env, tmp_path, capsys):
    src = tmp_path / "a.txt"
    src.write_text("x")
    rc = cli_env._cli(["add", str(src), "--share", "1h"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "/s/v1." in out
    item = cli_env.list_items()[0]
    assert len(item["shares"]) == 1


def test_cli_rm_by_id_prefix(cli_env, tmp_path, capsys):
    src = tmp_path / "a.txt"
    src.write_text("x")
    cli_env._cli(["add", str(src)])
    fid = cli_env.list_items()[0]["id"]
    rc = cli_env._cli(["rm", fid[:4]])
    assert rc == 0
    assert cli_env.list_items() == []


def test_cli_rm_ambiguous_prefix_fails(cli_env, tmp_path, capsys, monkeypatch):
    src = tmp_path / "a.txt"
    src.write_text("x")
    cli_env._cli(["add", str(src)])
    src2 = tmp_path / "b.txt"
    src2.write_text("y")
    cli_env._cli(["add", str(src2)])
    rc = cli_env._cli(["rm", ""])  # 빈 접두 — 둘 다 매치
    assert rc == 1
    assert "특정되는" in capsys.readouterr().err


def test_cli_share_and_unshare(cli_env, tmp_path, capsys):
    src = tmp_path / "a.txt"
    src.write_text("x")
    cli_env._cli(["add", str(src)])
    fid = cli_env.list_items()[0]["id"]
    rc = cli_env._cli(["share", fid, "--ttl", "7d", "--once"])
    assert rc == 0
    assert len(cli_env.list_items()[0]["shares"]) == 1
    rc = cli_env._cli(["unshare", fid])
    assert rc == 0
    assert cli_env.list_items()[0]["shares"] == []


def test_cli_share_bad_ttl_errors(cli_env, tmp_path, capsys):
    src = tmp_path / "a.txt"
    src.write_text("x")
    cli_env._cli(["add", str(src)])
    fid = cli_env.list_items()[0]["id"]
    rc = cli_env._cli(["share", fid, "--ttl", "5min"])
    assert rc == 2
    assert "알 수 없는" in capsys.readouterr().err


def test_cli_insert_requires_tmux_target(cli_env, tmp_path, capsys, monkeypatch):
    src = tmp_path / "a.txt"
    src.write_text("x")
    cli_env._cli(["add", str(src)])
    fid = cli_env.list_items()[0]["id"]
    import tmux_target
    monkeypatch.setattr(tmux_target, "resolve_voice_target_pane", lambda: (None, "none"))
    rc = cli_env._cli(["insert", fid])
    assert rc == 1
    assert "찾지 못했습니다" in capsys.readouterr().err


def test_cli_insert_types_path_into_resolved_pane(cli_env, tmp_path, capsys, monkeypatch):
    src = tmp_path / "a.txt"
    src.write_text("x")
    cli_env._cli(["add", str(src)])
    fid = cli_env.list_items()[0]["id"]
    import tmux_target
    monkeypatch.setattr(tmux_target, "resolve_voice_target_pane", lambda: ("%3", "auto"))
    calls = []
    monkeypatch.setattr(tmux_target, "type_to_tmux", lambda pane, text: calls.append((pane, text)) or True)
    rc = cli_env._cli(["insert", fid])
    assert rc == 0
    assert calls[0][0] == "%3"
