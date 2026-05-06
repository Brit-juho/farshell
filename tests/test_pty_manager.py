"""D7: pty_manager.py 단위 테스트 (safe_mode 라인 버퍼, pause/resume)."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

import asyncio
import pytest


def test_session_duplicate_raises():
    from pty_manager import PTYManager
    mgr = PTYManager()
    shell = "/bin/sh"
    session = mgr.create_session("test-dup", cmd=shell)
    with pytest.raises(ValueError):
        mgr.create_session("test-dup", cmd=shell)
    mgr.destroy_session("test-dup")


def test_pause_resume_flag():
    from pty_manager import PTYManager
    mgr = PTYManager()
    mgr.create_session("test-pr", cmd="/bin/sh")
    session = mgr._sessions["test-pr"]
    assert session._paused is False
    mgr.pause_read("test-pr")
    assert session._paused is True
    mgr.resume_read("test-pr")
    assert session._paused is False
    mgr.destroy_session("test-pr")


def test_pause_nonexistent_session_no_crash():
    from pty_manager import PTYManager
    mgr = PTYManager()
    mgr.pause_read("ghost-session")  # 존재하지 않아도 예외 없음
    mgr.resume_read("ghost-session")


def test_scrollback_returned_on_get():
    from pty_manager import PTYManager
    mgr = PTYManager()
    mgr.create_session("test-sb", cmd="/bin/sh")
    # 수동으로 scrollback에 데이터 추가 (실제 read loop 대신)
    mgr._sessions["test-sb"]._scrollback.append(b"hello\n")
    chunks = mgr.get_scrollback("test-sb")
    assert chunks == [b"hello\n"]
    mgr.destroy_session("test-sb")
