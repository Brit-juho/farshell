"""tmux_target.session_for_cwd() — 프롬프트 큐 세션 스코프 드레인이 쓰는 cwd→세션 매칭.

그리드 뷰의 카드 특정과 같은 규칙: 같은 cwd로 여러 세션이 떠 있으면(모호하면)
확신 있게 아무거나 고르지 않고 None을 돌려준다.
"""

import subprocess
import time
import uuid

import pytest

import tmux_runner
import tmux_sockets
import tmux_target

# ⚠ 소켓 이름은 **테스트마다 새로 만든다**(`test_tmux_paste.py`가 이미 쓰는
# 관용구). 고정 이름을 쓰면 픽스처의 `kill-server`와 다음 테스트의
# `new-session`이 경쟁한다 — `kill-server`는 요청만 보내고 바로 돌아오는데,
# 서버가 실제로 내려가기 전에 다음 `new-session`이 붙으면 exit 1로 죽는다.
# 2026-09-19 CI(python 3.11)에서 실제로 그렇게 깨졌다(같은 런의 3.13·3.14는
# 통과 — 느린 러너에서만 드러나는 경쟁이다).
SOCKET = ""          # 픽스처가 채운다
BASE: list[str] = []  # 〃


def _tmux(*args: str) -> str:
    r = subprocess.run(BASE + list(args), capture_output=True, text=True, timeout=5, check=True)
    return r.stdout.strip()


def _new_session(name: str, cwd) -> None:
    """세션을 만들고 tmux 가 그 pane 의 경로를 cwd 로 보고할 때까지 기다린다.

    Linux 의 tmux 는 pane_current_path 를 pane 의 foreground 프로세스(/proc/<pgrp>/cwd)에서 읽는다. 세션을 막
    만든 직후엔 셸이 아직 foreground 를 잡지 않아 경로가 비거나 다를 수 있다 — 2026-11-06 CI(master, 3.14)에서
    `session_for_cwd` 가 None 을 돌려 실패했다. 판정 규칙은 그대로 두고 전제(경로가 보고됨)만 기다린다."""
    _tmux("new-session", "-d", "-s", name, "-c", str(cwd), "-x", "80", "-y", "24")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _tmux("display-message", "-p", "-t", f"={name}:", "#{pane_current_path}") == str(cwd):
            return
        time.sleep(0.02)
    pytest.fail(f"tmux 가 5초 안에 {name} 의 경로를 {cwd} 로 보고하지 않았다")


@pytest.fixture
def tmux_server(monkeypatch, tmp_path):
    global SOCKET, BASE
    SOCKET = f"vt-test-session-for-cwd-{uuid.uuid4().hex[:8]}"
    BASE = ["tmux", "-L", SOCKET]
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", SOCKET)
    monkeypatch.setattr(tmux_runner, "VT_TMUX_CONF", None)
    yield tmp_path
    tmux_sockets.kill_server(SOCKET)


def test_session_for_cwd_returns_none_when_no_match(tmux_server):
    d = tmux_server / "dev"
    d.mkdir()
    _new_session("dev", d)
    assert tmux_target.session_for_cwd(str(tmux_server / "other")) is None


def test_session_for_cwd_matches_unique_session(tmux_server):
    d = tmux_server / "dev"
    d.mkdir()
    _new_session("dev", d)
    assert tmux_target.session_for_cwd(str(d)) == "dev"


def test_session_for_cwd_returns_none_when_ambiguous(tmux_server):
    """같은 cwd로 두 세션이 떠 있으면 틀린 쪽을 확신 있게 고르지 않는다."""
    d = tmux_server / "shared"
    d.mkdir()
    _new_session("a", d)
    _new_session("b", d)
    assert tmux_target.session_for_cwd(str(d)) is None


def test_session_for_cwd_none_input_returns_none(tmux_server):
    assert tmux_target.session_for_cwd(None) is None
    assert tmux_target.session_for_cwd("") is None
