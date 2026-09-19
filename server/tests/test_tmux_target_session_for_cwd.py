"""tmux_target.session_for_cwd() — 프롬프트 큐 세션 스코프 드레인이 쓰는 cwd→세션 매칭.

그리드 뷰의 카드 특정과 같은 규칙: 같은 cwd로 여러 세션이 떠 있으면(모호하면)
확신 있게 아무거나 고르지 않고 None을 돌려준다.
"""

import subprocess
import uuid

import pytest

import tmux_runner
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


@pytest.fixture
def tmux_server(monkeypatch, tmp_path):
    global SOCKET, BASE
    SOCKET = f"vt-test-session-for-cwd-{uuid.uuid4().hex[:8]}"
    BASE = ["tmux", "-L", SOCKET]
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", SOCKET)
    monkeypatch.setattr(tmux_runner, "VT_TMUX_CONF", None)
    yield tmp_path
    subprocess.run(BASE + ["kill-server"], capture_output=True, timeout=5)


def test_session_for_cwd_returns_none_when_no_match(tmux_server):
    d = tmux_server / "dev"
    d.mkdir()
    _tmux("new-session", "-d", "-s", "dev", "-c", str(d), "-x", "80", "-y", "24")
    assert tmux_target.session_for_cwd(str(tmux_server / "other")) is None


def test_session_for_cwd_matches_unique_session(tmux_server):
    d = tmux_server / "dev"
    d.mkdir()
    _tmux("new-session", "-d", "-s", "dev", "-c", str(d), "-x", "80", "-y", "24")
    assert tmux_target.session_for_cwd(str(d)) == "dev"


def test_session_for_cwd_returns_none_when_ambiguous(tmux_server):
    """같은 cwd로 두 세션이 떠 있으면 틀린 쪽을 확신 있게 고르지 않는다."""
    d = tmux_server / "shared"
    d.mkdir()
    _tmux("new-session", "-d", "-s", "a", "-c", str(d), "-x", "80", "-y", "24")
    _tmux("new-session", "-d", "-s", "b", "-c", str(d), "-x", "80", "-y", "24")
    assert tmux_target.session_for_cwd(str(d)) is None


def test_session_for_cwd_none_input_returns_none(tmux_server):
    assert tmux_target.session_for_cwd(None) is None
    assert tmux_target.session_for_cwd("") is None
