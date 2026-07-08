"""D7: pty_manager.py 단위 테스트 (pause/resume, scrollback)."""

import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

import pytest


@pytest.fixture
def loop():
    """asyncio event loop — create_session이 loop 안에서 실행돼야 함."""
    _loop = asyncio.new_event_loop()
    yield _loop
    # _read_loop 등 pending task를 취소하고 정리한다. 이렇게 하지 않으면
    # read 스레드(select 기반)가 닫힌 loop에 결과를 전달하려다 인터프리터가
    # 크래시할 수 있다.
    pending = asyncio.all_tasks(_loop)
    for t in pending:
        t.cancel()
    if pending:
        _loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    # 기본 executor의 read 스레드를 join한 뒤 loop을 닫는다.
    _loop.run_until_complete(_loop.shutdown_default_executor())
    _loop.close()


def make_manager_with_session(loop, session_id="test-sess"):
    """event loop 안에서 세션 생성 후 반환."""
    from pty_manager import PTYManager

    async def _setup():
        mgr = PTYManager()
        mgr.create_session(session_id, cmd="/bin/sh")
        return mgr

    return loop.run_until_complete(_setup())


def test_pause_resume_flag(loop):
    from pty_manager import PTYManager
    mgr = make_manager_with_session(loop, "test-pr")
    session = mgr._sessions["test-pr"]
    assert session._paused is False

    mgr.pause_read("test-pr", requester_id=1)
    assert session._paused is True

    mgr.resume_read("test-pr", requester_id=1)
    assert session._paused is False

    mgr.destroy_session("test-pr")


def test_multiple_pausers_need_all_to_resume(loop):
    """Codex: WS A + WS B 둘 다 pause한 경우 하나만 resume해도 여전히 paused."""
    mgr = make_manager_with_session(loop, "test-multi")
    mgr.pause_read("test-multi", requester_id=1)
    mgr.pause_read("test-multi", requester_id=2)
    mgr.resume_read("test-multi", requester_id=1)
    assert mgr._sessions["test-multi"]._paused is True  # 아직 paused
    mgr.resume_read("test-multi", requester_id=2)
    assert mgr._sessions["test-multi"]._paused is False  # 이제 해제
    mgr.destroy_session("test-multi")


def test_pause_nonexistent_session_no_crash():
    """존재하지 않는 세션에 pause/resume 호출해도 예외 없음."""
    from pty_manager import PTYManager
    mgr = PTYManager()
    mgr.pause_read("ghost-session", requester_id=99)
    mgr.resume_read("ghost-session", requester_id=99)


def test_scrollback_returned_on_get(loop):
    mgr = make_manager_with_session(loop, "test-sb")
    mgr._sessions["test-sb"]._scrollback.append(b"hello\n")
    chunks = mgr.get_scrollback("test-sb")
    assert chunks == [b"hello\n"]
    mgr.destroy_session("test-sb")
