"""N25(2.1.5 3/n) — tmux 세션의 붙여넣기 위임.

핵심 불변식: **우리가 마커를 씌우지 않는다** — `-p`만 주고 tmux가 그 pane의
진짜 화면 모드로 판단하게 둔다. 3.7+에서는 `-S`로 tmux의 vis(3) 이스케이프를
끈다(우리가 이미 위험 제어문자를 제거했고, vis(3)는 비ASCII를 오염시킬 수 있다).

실제 tmux 서버가 필요한 테스트는 **전용 소켓**(VT_TMUX_SOCKET)을 쓰고 끝나면
그 소켓만 kill-server한다 — 사용자 실세션과 섞지 않는다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid

import pytest

import tmux_paste

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux 미설치")


# ── 버전 파싱 (순수 로직, 서버 불필요) ──────────────────────────────────────

def test_supports_paste_sanitize_off_at_and_above_3_7():
    assert tmux_paste.supports_paste_sanitize_off((3, 7)) is True
    assert tmux_paste.supports_paste_sanitize_off((3, 8)) is True
    assert tmux_paste.supports_paste_sanitize_off((4, 0)) is True


def test_supports_paste_sanitize_off_below_3_7():
    assert tmux_paste.supports_paste_sanitize_off((3, 6)) is False
    assert tmux_paste.supports_paste_sanitize_off((2, 9)) is False


def test_supports_paste_sanitize_off_unknown_version_is_false():
    # 모르면 구버전 취급 — `-S`를 잘못 붙여 명령 자체가 실패하는 것보다
    # vis(3)를 한 번 더 태우는 쪽(구버전과 같은 동작)이 안전하다.
    assert tmux_paste.supports_paste_sanitize_off(None) is False


def test_tmux_version_parses_real_binary():
    v = tmux_paste.tmux_version()
    assert v is not None
    assert isinstance(v, tuple) and len(v) == 2
    assert v[0] >= 2  # 말이 되는 메이저 버전


# ── 실제 tmux 서버로 붙여넣기 왕복 ───────────────────────────────────────────

SOCKET = f"fsh-paste-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def isolated_tmux(monkeypatch):
    """전용 소켓 + 전용 세션. 끝나면 그 소켓만 kill-server."""
    monkeypatch.setenv("VT_TMUX_SOCKET", SOCKET)
    import importlib
    import tmux_runner
    importlib.reload(tmux_runner)
    monkeypatch.setattr(tmux_paste, "tmux_runner", tmux_runner)

    session = f"paste-test-{uuid.uuid4().hex[:8]}"
    out_file = f"/tmp/{session}.out"
    subprocess.run(
        ["tmux", "-L", SOCKET, "new-session", "-d", "-s", session,
         f"cat > {out_file}"],
        check=True, timeout=5,
    )
    time.sleep(0.4)
    yield session, out_file
    subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True, timeout=5)
    try:
        os.remove(out_file)
    except OSError:
        pass


def test_paste_via_tmux_delivers_text_to_the_pane(isolated_tmux):
    session, out_file = isolated_tmux
    ok = tmux_paste.paste_via_tmux(session, "hello from N25\n")
    assert ok is True
    time.sleep(0.4)
    with open(out_file, "rb") as f:
        data = f.read()
    assert b"hello from N25" in data


def test_paste_via_tmux_multiline_arrives_as_one_paste_not_split(isolated_tmux):
    """줄마다 즉시 실행되지 않는지 확인하려면 진짜 셸이 필요하다 — 여기선
    cat이 받은 바이트가 통으로 도착하는지만 본다(내용이 안 잘림)."""
    session, out_file = isolated_tmux
    text = "line1\nline2\nline3\n"
    ok = tmux_paste.paste_via_tmux(session, text)
    assert ok is True
    time.sleep(0.4)
    with open(out_file, "rb") as f:
        data = f.read()
    assert b"line1" in data and b"line2" in data and b"line3" in data


def test_paste_via_tmux_unknown_session_fails_without_raising(isolated_tmux):
    ok = tmux_paste.paste_via_tmux("no-such-session-xyz", "hi")
    assert ok is False


def test_paste_via_tmux_cleans_up_the_buffer_on_failure(isolated_tmux):
    """-d로 지우기 전에 paste-buffer가 실패하면 delete-buffer로 뒷정리한다
    — 사용자 버퍼 스택(prefix+])에 우리 임시 버퍼가 안 쌓여야 한다."""
    import tmux_runner
    tmux_paste.paste_via_tmux("no-such-session-xyz", "hi")
    rc, out, _ = tmux_runner.run(["list-buffers"])
    assert rc == 0
    assert b"vt-paste-" not in out


def test_paste_via_tmux_empty_text_is_a_noop_success(isolated_tmux):
    session, _ = isolated_tmux
    assert tmux_paste.paste_via_tmux(session, "") is True
