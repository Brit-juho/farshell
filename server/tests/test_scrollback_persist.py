"""N13 — 스크롤백 영속화 회귀 (80-multihost-agents.md §3 수용 기준).

persist ON → 파일 생성·회전 / OFF → 파일 없음 / 입력 미기록을 확인한다.
입력 미기록은 이 모듈 자체가 출력 전용 API(append)만 제공한다는 사실로
설계상 보장되므로, 여기서는 그 계약(파라미터가 오직 PTY 출력 청크라는 것)과
pty_manager 쪽 호출 지점이 write()가 아니라 _flush_session()이라는 걸 함께 확인한다.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def sp(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_WORKSPACE_PATH", str(tmp_path / "workspace.json"))
    import scrollback_persist as _sp
    importlib.reload(_sp)
    import workspace
    importlib.reload(workspace)
    return _sp


def _enable(sp):
    import workspace
    workspace.save({"settings": {"scrollback.persist": True}})
    sp._enabled_cache.clear()  # 방금 켠 값이 캐시(3초)에 안 걸리게


def test_disabled_by_default_writes_nothing(sp, tmp_path):
    sp.append("s1", b"hello\n")
    assert not sp.scrollback_dir().exists() or not any(sp.scrollback_dir().iterdir())


def test_enabled_creates_log_file_with_0600(sp):
    _enable(sp)
    sp.append("s1", b"hello\n")
    p = sp._log_path("s1")
    assert p.is_file()
    assert p.read_bytes() == b"hello\n"
    assert oct(p.stat().st_mode)[-3:] == "600"


def test_enabled_appends_across_calls(sp):
    _enable(sp)
    sp.append("s1", b"line1\n")
    sp.append("s1", b"line2\n")
    assert sp._log_path("s1").read_bytes() == b"line1\nline2\n"


def test_rotation_moves_full_log_to_dot1(sp, monkeypatch):
    _enable(sp)
    monkeypatch.setattr(sp, "MAX_LOG_BYTES", 10)
    sp.append("s1", b"0123456789")  # 정확히 상한 — 다음 append 전에 회전
    sp.append("s1", b"next")
    p = sp._log_path("s1")
    rotated = p.with_suffix(p.suffix + ".1")
    assert rotated.read_bytes() == b"0123456789"
    assert p.read_bytes() == b"next"


def test_read_before_returns_latest_first_then_pages_back(sp):
    _enable(sp)
    sp.append("s1", b"A" * 100)
    sp.append("s1", b"B" * 100)
    r1 = sp.read_before("s1", before=None, limit=100)
    assert r1["data"] == b"B" * 100
    assert r1["total"] == 200
    assert r1["next_before"] == 100

    r2 = sp.read_before("s1", before=r1["next_before"], limit=100)
    assert r2["data"] == b"A" * 100
    assert r2["next_before"] is None  # 더 없음


def test_read_before_includes_rotated_generation(sp, monkeypatch):
    _enable(sp)
    monkeypatch.setattr(sp, "MAX_LOG_BYTES", 5)
    sp.append("s1", b"AAAAA")  # 회전됨 → .log.1
    sp.append("s1", b"BB")     # 새 .log
    r = sp.read_before("s1", before=None, limit=1000)
    assert r["data"] == b"AAAAABB"


def test_read_before_missing_session_is_empty(sp):
    r = sp.read_before("no-such-session", before=None, limit=100)
    assert r == {"data": b"", "next_before": None, "total": 0}


def test_disk_usage_bytes_reflects_written_data(sp):
    _enable(sp)
    sp.append("s1", b"x" * 50)
    assert sp.disk_usage_bytes() == 50


def test_cleanup_old_removes_stale_logs_only(sp, monkeypatch):
    _enable(sp)
    sp.append("s1", b"old")
    sp.append("s2", b"fresh")
    import os
    import time
    old_path = sp._log_path("s1")
    old_time = time.time() - 8 * 86400
    os.utime(old_path, (old_time, old_time))

    removed = sp.cleanup_old(days=7)
    assert removed == 1
    assert not old_path.exists()
    assert sp._log_path("s2").exists()


def test_pty_manager_only_calls_append_from_flush_not_write():
    """설계 계약 회귀 — write()(사용자 입력 경로)가 scrollback_persist를 호출하면
    비밀번호 등 입력이 디스크에 남는다. 소스에서 정적으로 확인한다."""
    import inspect
    import pty_manager
    write_src = inspect.getsource(pty_manager.PTYManager.write)
    flush_src = inspect.getsource(pty_manager.PTYManager._flush_session)
    assert "scrollback_persist" not in write_src
    assert "scrollback_persist.append" in flush_src
