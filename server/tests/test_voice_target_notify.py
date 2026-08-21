"""V3-G: recorder._notify_target() — 녹음 시작 시 타깃 pane을 발화 전에 알린다.

실제 tmux/오디오 장치는 몽키패치로 대체한다. resolve_voice_target_pane/pane_label의
실제 tmux 왕복은 test_tmux_target_pane_label.py가 이미 다룬다 — 여기서는 그 둘의
결과를 recorder가 올바른 알림 문구로 조합하는지만 본다.
"""

import pytest

# 이 테스트는 실제로 sounddevice/numpy 기반 recorder를 import한다. 코어 설치는
# 의도적으로 해당 선택 의존성을 포함하지 않으므로, 그 프로필에서는 수집 대상에서 뺀다.
pytest.importorskip("sounddevice")

import voice.recorder as recorder


@pytest.fixture(autouse=True)
def _notify_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(recorder.platform_utils, "notify", lambda title, msg="": calls.append((title, msg)))
    return calls


def test_no_pane_skips_notification(monkeypatch, _notify_spy):
    monkeypatch.setattr(recorder, "resolve_voice_target_pane", lambda: (None, "none"))
    recorder._notify_target()
    assert _notify_spy == []


def test_auto_mode_shows_label_without_lock_prefix(monkeypatch, _notify_spy):
    monkeypatch.setattr(recorder, "resolve_voice_target_pane", lambda: ("%3", "auto"))
    monkeypatch.setattr(recorder, "pane_label", lambda pane: "dev:0.0")
    recorder._notify_target()
    assert len(_notify_spy) == 1
    title, msg = _notify_spy[0]
    assert "dev:0.0" in msg
    assert "🔒" not in msg


def test_lock_mode_shows_lock_prefix(monkeypatch, _notify_spy):
    monkeypatch.setattr(recorder, "resolve_voice_target_pane", lambda: ("%3", "lock:dev"))
    monkeypatch.setattr(recorder, "pane_label", lambda pane: "dev:0.0")
    recorder._notify_target()
    title, msg = _notify_spy[0]
    assert "🔒" in msg
    assert "dev:0.0" in msg


def test_label_lookup_failure_falls_back_to_raw_pane_id(monkeypatch, _notify_spy):
    monkeypatch.setattr(recorder, "resolve_voice_target_pane", lambda: ("%7", "auto"))
    monkeypatch.setattr(recorder, "pane_label", lambda pane: None)
    recorder._notify_target()
    title, msg = _notify_spy[0]
    assert "%7" in msg


def test_notify_exception_does_not_propagate(monkeypatch, _notify_spy):
    def boom():
        raise RuntimeError("tmux gone")
    monkeypatch.setattr(recorder, "resolve_voice_target_pane", boom)
    recorder._notify_target()  # 예외를 던지면 안 된다 — 녹음 시작 자체를 막으면 안 되므로
    assert _notify_spy == []


def test_start_recording_respects_voice_target_notify_toggle(monkeypatch):
    """VOICE_TARGET_NOTIFY=False면 _notify_target 자체가 호출되지 않아야 한다."""
    called = []
    monkeypatch.setattr(recorder, "_notify_target", lambda: called.append(True))
    monkeypatch.setattr(recorder, "VOICE_TARGET_NOTIFY", False)
    # 실제 오디오 장치를 열지 않도록 InputStream을 스텁.
    monkeypatch.setattr(recorder.platform_utils, "play_sound", lambda *_: None)

    class _FakeStream:
        def __init__(self, *a, **k):
            pass
        def start(self):
            pass
        def stop(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(recorder.sd, "InputStream", _FakeStream)
    try:
        recorder.start_recording()
        assert called == [], "토글이 꺼져 있으면 알림을 시도조차 하면 안 된다"
    finally:
        # 전역 상태 원복 — 이 프로세스에서 뒤에 도는 다른 테스트에 안 새도록.
        recorder._recording = False
        recorder._stream = None
