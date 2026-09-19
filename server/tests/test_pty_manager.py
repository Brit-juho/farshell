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
    # **received 전체를 비교하면 안 된다.** 이 세션은 진짜 PTY라 그 안의 셸이
    # 프롬프트를 뱉는데, 그게 언제 어디로 갈지는 환경마다 다르다(2026-09-19
    # 실측: macOS는 아예 안 옴 · 리눅스 컨테이너는 같은 배치에 합류해
    # `b'chunkchunkchunk# '` · CI는 별도 broadcast로 `[b'chunkchunkchunk', b'$ ']`).
    # 셋 다 배치가 올바로 동작한 모습이다.
    #
    # 환경과 무관하게 성립하는 불변식만 본다: **우리가 넣은 3개가 한 덩어리로
    # 뭉쳐 단 하나의 broadcast에만 나타난다.** 배치가 깨지면 `chunk`를 담은
    # broadcast가 3개가 되므로 이 단언이 바로 잡는다.
    with_chunk = [b for b in received if b"chunk" in b]
    assert len(with_chunk) == 1, f"chunk가 여러 broadcast로 쪼개졌다: {received}"
    assert b"chunkchunkchunk" in with_chunk[0], received
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


# ── destroy_session 이 시스템 프로세스·표준 스트림을 건드리지 않는가 ──────────
#
# 2026-09-19 사고의 회귀 가드. 가짜 세션이 `pid=1, fd=1`로 만들어진 채 앱
# lifespan 종료의 `destroy_all()`을 타자, `os.getpgid(1)`이 1이고 그게 우리
# 프로세스 그룹과 달라 group kill이 "안전"으로 판정돼 **`killpg(1, SIGKILL)`**
# 이 나갔다. GitHub 러너 VM이 그대로 죽어 2026-09-07부터 server 잡이 매번
# 45분을 돌다 사라졌고, 그렇게 죽은 잡은 로그조차 업로드되지 않아 원인이
# 10일 넘게 보이지 않았다. macOS(EPERM)와 컨테이너(pytest 자신이 pgid 1)에서는
# 재현되지 않는다 — 그래서 실측이 아니라 **불변식**으로 고정한다.

@pytest.mark.parametrize("pid", [0, 1, -1])
def test_destroy_session_never_signals_system_pids(loop, monkeypatch, pid):
    from pty_manager import PTYManager, PTYSession

    mgr = PTYManager()
    mgr._sessions["fake"] = PTYSession(session_id="fake", pid=pid, fd=-1)

    killed: list = []
    monkeypatch.setattr("pty_manager.os.kill", lambda *a: killed.append(("kill", a)))
    monkeypatch.setattr("pty_manager.os.killpg", lambda *a: killed.append(("killpg", a)))
    monkeypatch.setattr("pty_manager.os.waitpid", lambda *a: killed.append(("waitpid", a)))

    mgr.destroy_session("fake")

    assert killed == [], f"pid={pid}에 시그널이 나갔다: {killed}"


@pytest.mark.parametrize("fd", [0, 1, 2])
def test_destroy_session_never_closes_standard_streams(loop, monkeypatch, fd):
    """fd 1을 닫으면 서버가 자기 stdout을 잃는다 — 로그가 그 자리에서 끊긴다."""
    from pty_manager import PTYManager, PTYSession

    mgr = PTYManager()
    mgr._sessions["fake"] = PTYSession(session_id="fake", pid=0, fd=fd)

    closed: list = []
    monkeypatch.setattr("pty_manager.os.close", lambda f: closed.append(f))

    mgr.destroy_session("fake")

    assert closed == [], f"표준 스트림 fd={fd}를 닫았다"
