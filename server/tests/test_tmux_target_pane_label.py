"""V3-G: pane_label() — pane_id를 사람이 읽는 "session:window.pane" 라벨로 변환.

녹음 시작 시 데스크톱 알림에 쓰인다(voice/recorder.py:_notify_target). 실제 tmux
서버(전용 소켓)를 띄워 왕복으로 검증한다 — 사용자의 실제 `-L fsh` 소켓은 건드리지 않는다.
"""

import subprocess

import pytest

import tmux_target


SOCKET = "vt-test-pane-label"
BASE = ["tmux", "-L", SOCKET]


def _tmux(*args: str) -> str:
    r = subprocess.run(BASE + list(args), capture_output=True, text=True, timeout=5, check=True)
    return r.stdout.strip()


@pytest.fixture
def tmux_server(monkeypatch):
    monkeypatch.setattr(tmux_target, "TMUX_BASE", BASE)
    _tmux("new-session", "-d", "-s", "dev", "-x", "80", "-y", "24")
    yield
    subprocess.run(BASE + ["kill-server"], capture_output=True, timeout=5)


def test_pane_label_formats_session_window_pane(tmux_server):
    pane_id = _tmux("display-message", "-p", "-t", "dev", "#{pane_id}")
    label = tmux_target.pane_label(pane_id)
    assert label == "dev:0.0"


def test_pane_label_reflects_session_name(tmux_server):
    _tmux("new-session", "-d", "-s", "ops", "-x", "80", "-y", "24")
    pane_id = _tmux("display-message", "-p", "-t", "ops", "#{pane_id}")
    assert tmux_target.pane_label(pane_id) == "ops:0.0"


def test_pane_label_returns_none_for_nonexistent_pane(tmux_server):
    assert tmux_target.pane_label("%9999") is None


def test_pane_label_returns_none_when_no_tmux_server(monkeypatch):
    # 서버 자체가 없는 소켓 — display-message가 실패해야 한다.
    monkeypatch.setattr(tmux_target, "TMUX_BASE", ["tmux", "-L", "vt-test-nonexistent-socket"])
    assert tmux_target.pane_label("%0") is None


def test_resolve_voice_target_pane_auto_mode_label_roundtrip(tmux_server, monkeypatch):
    # lock 없이 AUTO로 잡힌 pane도 pane_label로 정상 변환되는지 — recorder._notify_target이
    # 쓰는 조합(resolve_voice_target_pane + pane_label) 그대로.
    monkeypatch.setattr(tmux_target, "read_voice_target_lock", lambda: None)
    pane, mode = tmux_target.resolve_voice_target_pane()
    assert mode == "auto"
    assert pane is not None
    label = tmux_target.pane_label(pane)
    assert label is not None and ":" in label
