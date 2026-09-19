"""D7: pty_manager.py 단위 테스트 (pause/resume, scrollback)."""

import asyncio

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


def test_output_batching_coalesces_rapid_reads(loop):
    """R3: BATCH_WINDOW_SEC 안에 들어온 여러 _on_readable 호출이 broadcast 1번으로 합쳐지는지."""
    mgr = make_manager_with_session(loop, "test-batch")
    session = mgr._sessions["test-batch"]

    received = []
    session._subscribers.add(received.append)

    async def _run():
        # os.read를 실제로 하면 fd에 데이터가 없어 BlockingIOError로 조기 반환하니,
        # _on_readable 내부의 os.read 호출 지점만 우회해 배치 로직 자체를 검증한다.
        import pty_manager as pm
        orig_read = pm.os.read
        pm.os.read = lambda fd, n: b"chunk"
        try:
            mgr._on_readable("test-batch")
            mgr._on_readable("test-batch")
            mgr._on_readable("test-batch")
        finally:
            pm.os.read = orig_read
        # flush 전이므로 아직 구독자에게 아무것도 안 갔어야 한다.
        assert received == []
        await asyncio.sleep(mgr.BATCH_WINDOW_SEC * 3)

    loop.run_until_complete(_run())
    # 3번의 read가 한 번의 broadcast로 합쳐졌는가 — 그게 이 테스트의 전부다.
    #
    # **내용을 통째로 비교하면 안 된다.** 이 세션은 진짜 PTY라 그 안의 셸이
    # 프롬프트를 뱉고, 그 바이트도 같은 배치 창에 합류한다(리눅스 컨테이너에서
    # 루트 프롬프트 `# `가 붙어 실패한 것으로 2026-09-19에 확인). 그건 결함이
    # 아니라 배치가 제대로 동작한다는 증거다 — 합쳐진 게 하나뿐인지와,
    # 우리가 넣은 3개가 앞에 붙어 있는지만 본다.
    assert len(received) == 1, f"broadcast가 한 번으로 안 합쳐졌다: {received}"
    assert received[0].startswith(b"chunkchunkchunk"), received[0]
    mgr.destroy_session("test-batch")


# ── N27: 붙여넣기 페이로드가 키 입력용 필터를 타면 안 된다 ──────────────────
# 두 필터(자동응답 제거 · 부팅 0.5초 ESC 드롭)는 사용자가 친 키를 방어하려고
# 있는 것이다. bracketed paste 사이의 바이트는 키가 아니라 **내용**이라,
# 여기에 적용하면 붙여넣은 텍스트가 조용히 변조되거나 통째로 사라진다.

PS = b"\x1b[200~"
PE = b"\x1b[201~"


def test_split_paste_segments_marks_paste_region():
    from pty_manager import split_paste_segments

    segs, in_paste = split_paste_segments(b"ab" + PS + b"xy" + PE + b"cd", False)
    assert segs == [(False, b"ab"), (True, PS + b"xy" + PE), (False, b"cd")]
    assert in_paste is False


def test_split_paste_segments_carries_state_across_chunks():
    """붙여넣기는 WS 한 프레임에 다 안 들어온다 — 마커가 갈라지면 뒷조각
    전체가 키 입력으로 오인된다."""
    from pty_manager import split_paste_segments

    segs, in_paste = split_paste_segments(b"ab" + PS + b"xy", False)
    assert segs == [(False, b"ab"), (True, PS + b"xy")]
    assert in_paste is True

    segs2, in_paste2 = split_paste_segments(b"zw" + PE + b"q", True)
    assert segs2 == [(True, b"zw" + PE), (False, b"q")]
    assert in_paste2 is False


def test_paste_payload_keeps_bytes_that_look_like_auto_replies(loop):
    """붙여넣는 텍스트 안의 `ESC[...c`/`ESC[...R`가 삭제되던 버그."""
    mgr = make_manager_with_session(loop, "test-paste-filter")
    session = mgr._sessions["test-paste-filter"]
    written = []
    mgr._write_raw = lambda s, d: written.append(d)

    payload = PS + b"echo \x1b[0c and \x1b[1;1R done" + PE
    mgr.write("test-paste-filter", payload)
    assert written == [payload], "붙여넣기 내용은 한 바이트도 바뀌면 안 된다"

    # 같은 바이트를 키 입력으로 보내면 예전대로 걸러낸다(방어선은 그대로다).
    written.clear()
    mgr.write("test-paste-filter", b"echo \x1b[0c hi")
    assert written == [b"echo  hi"]
    assert session is not None


def test_paste_survives_boot_grace_period(loop):
    """bracketed paste는 ESC로 시작한다 — 세션이 붙자마자 붙여넣으면 부팅
    grace(0.5초)에 걸려 통째로 사라지던 버그."""
    import time

    mgr = make_manager_with_session(loop, "test-paste-boot")
    session = mgr._sessions["test-paste-boot"]
    session._start_monotonic = time.monotonic()   # 방금 부팅한 상태로
    written = []
    mgr._write_raw = lambda s, d: written.append(d)

    payload = PS + b"hello" + PE
    mgr.write("test-paste-boot", payload)
    assert written == [payload]

    # 붙여넣기가 아닌 ESC 시퀀스는 여전히 막는다.
    written.clear()
    mgr.write("test-paste-boot", b"\x1b[>0;95;0c")
    assert written == []


def test_keystrokes_around_a_paste_are_still_filtered(loop):
    """한 프레임에 키 입력과 붙여넣기가 섞여 와도 각자 규칙을 받는다."""
    import time

    mgr = make_manager_with_session(loop, "test-paste-mixed")
    mgr._sessions["test-paste-mixed"]._start_monotonic = time.monotonic() - 10  # grace 지남
    written = []
    mgr._write_raw = lambda s, d: written.append(d)

    mgr.write("test-paste-mixed", b"a\x1b[0c" + PS + b"\x1b[0c" + PE + b"b")
    assert written == [b"a" + PS + b"\x1b[0c" + PE + b"b"]
