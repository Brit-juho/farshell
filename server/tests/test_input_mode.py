"""N25(2.1.5 1/n) — pane 입력 모드 판정.

이 파일의 절반은 **실측을 회귀로 고정한 것**이다. 2026-09-14에 직접 실행해
얻은 세 가지 사실 위에 붙여넣기 경로 전체가 서 있어서, 플랫폼이나 파이썬이
바뀌어 그 사실이 깨지면 여기서 먼저 터져야 한다:

1. master fd의 termios가 슬레이브의 ICANON을 실시간으로 비춘다
2. 정규 모드 한 줄 한계(macOS 1024, 개행 포함)를 넘기면 **줄 전체가 사라진다**
3. tmux 안에서는 1·2가 모두 무의미하다 → 위임

3번은 tmux 서버를 띄워야 해서 여기 두지 않는다(2.1.5 3/n에서 전용 소켓으로).
"""

from __future__ import annotations

import os
import pty
import select
import sys
import termios
import time

import pytest

import input_mode


# ── 순수 로직: DECSET 2004 추적 ────────────────────────────────────────────

def test_scan_picks_up_set_and_reset():
    assert input_mode.scan_bracketed_mode(b"\x1b[?2004h", None) is True
    assert input_mode.scan_bracketed_mode(b"\x1b[?2004l", True) is False


def test_scan_keeps_previous_value_when_nothing_seen():
    """모르는 것(None)과 꺼진 것(False)은 다르다 — 아무것도 못 봤으면 유지."""
    assert input_mode.scan_bracketed_mode(b"hello world", None) is None
    assert input_mode.scan_bracketed_mode(b"hello world", True) is True
    assert input_mode.scan_bracketed_mode(b"hello world", False) is False


def test_scan_last_one_wins():
    """한 조각에 여러 번 들어 있으면 마지막이 지금 상태다(vim이 떴다 지는 경우)."""
    data = b"\x1b[?2004h" + b"text" + b"\x1b[?2004l" + b"\x1b[?2004h"
    assert input_mode.scan_bracketed_mode(data, None) is True
    assert input_mode.scan_bracketed_mode(b"\x1b[?2004h.\x1b[?2004l", None) is False


def test_scan_ignores_other_dec_modes():
    """1049(대체 화면)·1000(마우스) 같은 이웃 모드에 반응하면 안 된다."""
    assert input_mode.scan_bracketed_mode(b"\x1b[?1049h\x1b[?1000h", None) is None
    # 20044는 2004가 아니다 — 숫자 경계를 안 보면 걸린다.
    assert input_mode.scan_bracketed_mode(b"\x1b[?20044h", None) is None


def test_scan_handles_combined_parameters():
    """앱들은 `ESC[?1049;2004h`처럼 묶어 보내기도 한다."""
    assert input_mode.scan_bracketed_mode(b"\x1b[?1049;2004h", None) is True


# ── 실 PTY: 실측을 회귀로 ──────────────────────────────────────────────────

def _spawn(cmd):
    pid, fd = pty.fork()
    if pid == 0:                      # 자식 — 여기서 예외가 나면 테스트가 아니라 프로세스를 죽인다
        try:
            os.execvp(cmd[0], cmd)
        finally:
            os._exit(127)
    return pid, fd


def _write_all(fd, data, timeout=2.0):
    sent = 0
    end = time.monotonic() + timeout
    while sent < len(data) and time.monotonic() < end:
        _, w, _ = select.select([], [fd], [], 0.2)
        if not w:
            continue
        sent += os.write(fd, data[sent:sent + 4096])
    return sent


@pytest.fixture
def shell_pty():
    pid, fd = _spawn(["/bin/sh"])
    time.sleep(0.4)
    yield fd
    # 순서가 중요하다: fd를 먼저 닫아 슬레이브에 SIGHUP이 가게 하고, 그 다음
    # 죽인다. 그리고 **waitpid를 블로킹으로 부르지 않는다** — 자식(sh)이 자기
    # 자식(cat)을 남긴 채 죽으면 여기서 그대로 멈춘다(실제로 멈췄다).
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    for _ in range(20):
        try:
            if os.waitpid(pid, os.WNOHANG)[0] != 0:
                break
        except OSError:
            break
        time.sleep(0.05)


def test_master_fd_sees_the_slave_switching_to_canonical_mode(shell_pty):
    """실측 1 — 서버가 추측할 필요가 없다는 것의 근거 전체."""
    before = input_mode.is_canonical(shell_pty)
    os.write(shell_pty, b"cat > /dev/null\n")
    time.sleep(0.6)
    during = input_mode.is_canonical(shell_pty)
    os.write(shell_pty, b"\x04")          # EOF로 cat 종료
    time.sleep(0.5)
    after = input_mode.is_canonical(shell_pty)

    assert during is True, "정규 모드로 들어간 것이 master fd에서 보여야 한다"
    assert after == before, "cat이 끝나면 원래 모드로 돌아와야 한다"


def test_max_canon_matches_the_measured_limit(shell_pty):
    """플랫폼별 실측값. `>= 256` 같은 하한으로 두면 안 된다 — **리눅스의 진짜
    값이 255**여서 그 단언이 리눅스에서만 깨졌다(2026-09-19, Docker
    python:3.11-slim으로 재현). 255는 POSIX `_POSIX_MAX_CANON`이고 glibc가
    `_PC_MAX_CANON`으로 그대로 돌려준다.

    값을 정확히 못 박는 것이 하한보다 낫다: 폴백(`FALLBACK_MAX_CANON`=1024)이
    잘못 걸리면 리눅스에서 1024가 나오는데, 하한 단언은 그걸 통과시킨다."""
    limit = input_mode.max_canon(shell_pty)
    assert limit > 0, f"말이 안 되는 값이면 폴백이 잘못 걸린 것이다: {limit}"
    if sys.platform == "darwin":
        assert limit == 1024, "macOS 실측값(개행 포함 1024)"
    elif sys.platform.startswith("linux"):
        assert limit == 255, "리눅스 실측값(_POSIX_MAX_CANON)"


@pytest.mark.skipif(sys.platform != "darwin", reason="한계 동작을 실측한 것은 macOS뿐")
def test_canonical_overflow_loses_the_whole_line(tmp_path, shell_pty):
    """실측 2 — **잘리는 게 아니라 통째로 사라진다.**

    N26이 "미리 거절하고 알린다"여야 하는 이유. 여기가 깨지면(= 넘겨도 일부가
    도착하면) 그 정책의 전제가 바뀌므로 다시 판단해야 한다.
    """
    out = tmp_path / "canon.txt"
    os.write(shell_pty, f"cat > {out}\n".encode())
    time.sleep(0.5)
    limit = input_mode.max_canon(shell_pty)
    assert input_mode.is_canonical(shell_pty) is True

    # 한계 - 1바이트 본문 + 개행 = 정확히 한계 → 통과한다
    body = b"x" * (limit - 1) + b"\n"
    assert _write_all(shell_pty, body) == len(body)
    time.sleep(0.6)
    assert out.stat().st_size == limit

    # 한 바이트만 더 크면 앞부분까지 전부 사라진다(파일 크기가 안 늘어난다)
    too_long = b"y" * limit + b"\n"
    assert _write_all(shell_pty, too_long) == len(too_long), "os.write는 성공을 보고한다"
    time.sleep(0.6)
    assert out.stat().st_size == limit, "한계를 넘긴 줄은 한 바이트도 도착하지 않는다"


# ── 판정 결과 조립 ────────────────────────────────────────────────────────

def test_tmux_session_delegates_instead_of_guessing():
    """tmux 안에서는 바깥 신호가 둘 다 죽는다 — 값을 지어내지 않고 위임한다."""
    mode = input_mode.pane_input_mode(0, tmux=True, bracketed=True)
    assert mode["delegate"] == "tmux"
    assert mode["icanon"] is None and mode["bracketed"] is None and mode["max_line"] is None


def test_plain_session_reports_what_it_measured(shell_pty):
    mode = input_mode.pane_input_mode(shell_pty, bracketed=None)
    assert mode["delegate"] is None
    assert mode["icanon"] in (True, False)
    assert mode["bracketed"] is None, "못 본 값을 False로 단정하지 않는다"


def test_max_line_only_matters_in_canonical_mode(shell_pty):
    os.write(shell_pty, b"cat > /dev/null\n")
    time.sleep(0.6)
    canon = input_mode.pane_input_mode(shell_pty)
    assert canon["icanon"] is True and canon["max_line"] == input_mode.max_canon(shell_pty)
    os.write(shell_pty, b"\x04")
    time.sleep(0.5)
    raw = input_mode.pane_input_mode(shell_pty)
    if raw["icanon"] is False:
        assert raw["max_line"] is None, "raw 모드에는 줄 한계가 없다"


def test_bad_fd_reports_unknown_instead_of_raising():
    """세션이 방금 죽은 것과 붙여넣기가 경쟁할 수 있다 — 여기서 터지면 안 된다."""
    assert input_mode.is_canonical(-1) is None
    assert input_mode.max_canon(-1) == input_mode.FALLBACK_MAX_CANON
    mode = input_mode.pane_input_mode(-1)
    assert mode["icanon"] is None
