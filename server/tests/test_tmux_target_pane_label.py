"""V3-G: pane_label() — pane_id를 사람이 읽는 "session:window.pane" 라벨로 변환.

녹음 시작 시 데스크톱 알림에 쓰인다(voice/recorder.py:_notify_target). 실제 tmux
서버(전용 소켓)를 띄워 왕복으로 검증한다 — 사용자의 실제 `-L fsh` 소켓은 건드리지 않는다.
"""

import subprocess
import uuid

import pytest

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
def tmux_server(monkeypatch):
    global SOCKET, BASE
    SOCKET = f"vt-test-pane-label-{uuid.uuid4().hex[:8]}"
    BASE = ["tmux", "-L", SOCKET]
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
