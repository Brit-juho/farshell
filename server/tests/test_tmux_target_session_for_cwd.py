"""tmux_target.session_for_cwd() — 프롬프트 큐 세션 스코프 드레인이 쓰는 cwd→세션 매칭.

그리드 뷰의 카드 특정과 같은 규칙: 같은 cwd로 여러 세션이 떠 있으면(모호하면)
확신 있게 아무거나 고르지 않고 None을 돌려준다.
"""

import subprocess

import pytest

import tmux_runner
import tmux_target

SOCKET = "vt-test-session-for-cwd"
BASE = ["tmux", "-L", SOCKET]


def _tmux(*args: str) -> str:
    r = subprocess.run(BASE + list(args), capture_output=True, text=True, timeout=5, check=True)
    return r.stdout.strip()


@pytest.fixture
def tmux_server(monkeypatch, tmp_path):
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
