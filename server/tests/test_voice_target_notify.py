"""V3-G: recorder._notify_target() — 녹음 시작 시 타깃 pane을 발화 전에 알린다.

실제 tmux/오디오 장치는 몽키패치로 대체한다. resolve_voice_target_pane/pane_label의
실제 tmux 왕복은 test_tmux_target_pane_label.py가 다룬다. 안내는 고정된 대상만 사용해야 한다.
"""

import pytest

# 이 테스트는 실제로 sounddevice/numpy 기반 recorder를 import한다. 코어 설치는
# 의도적으로 해당 선택 의존성을 포함하지 않으므로, 그 프로필에서는 수집 대상에서 뺀다.
pytest.importorskip("sounddevice")

import voice.recorder as recorder


@pytest.fixture(autouse=True)
def _notify_spy(monkeypatch):
    calls = []
    monkeypatch.setattr(recorder, "_target", None)
    monkeypatch.setattr(recorder.platform_utils, "notify", lambda title, msg="": calls.append((title, msg)))
    return calls


def test_no_pane_skips_notification(monkeypatch, _notify_spy):
    monkeypatch.setattr(recorder, "resolve_voice_target_pane", lambda: (None, "none"))
    recorder._notify_target()
    assert _notify_spy == []


def test_auto_mode_shows_label_without_lock_prefix(monkeypatch, _notify_spy):
    monkeypatch.setattr(recorder, "_target", {"pane": "%3", "mode": "auto"})
    monkeypatch.setattr(recorder, "pane_label", lambda pane: "dev:0.0")
    recorder._notify_target()
    assert len(_notify_spy) == 1
    title, msg = _notify_spy[0]
    assert "dev:0.0" in msg
    assert "🔒" not in msg


def test_lock_mode_shows_lock_prefix(monkeypatch, _notify_spy):
    monkeypatch.setattr(recorder, "_target", {"pane": "%3", "mode": "lock:dev"})
    monkeypatch.setattr(recorder, "pane_label", lambda pane: "dev:0.0")
    recorder._notify_target()
    title, msg = _notify_spy[0]
    assert "🔒" in msg
    assert "dev:0.0" in msg


def test_label_lookup_failure_falls_back_to_raw_pane_id(monkeypatch, _notify_spy):
    monkeypatch.setattr(recorder, "_target", {"pane": "%7", "mode": "auto"})
    monkeypatch.setattr(recorder, "pane_label", lambda pane: None)
    recorder._notify_target()
    title, msg = _notify_spy[0]
    assert "%7" in msg


def test_notify_exception_does_not_propagate(monkeypatch, _notify_spy):
    monkeypatch.setattr(recorder, "_target", {"pane": "%3"})
    def boom(*args):
        raise RuntimeError("tmux gone")
    monkeypatch.setattr(recorder, "pane_label", boom)
    recorder._notify_target()  # 예외를 던지면 안 된다 — 녹음 시작 자체를 막으면 안 되므로
    assert _notify_spy == []


def test_start_recording_respects_voice_target_notify_toggle(monkeypatch):
    """VOICE_TARGET_NOTIFY=False면 _notify_target 자체가 호출되지 않아야 한다."""
    called = []
    monkeypatch.setattr(recorder, "resolve_voice_target_pane", lambda: ("%3", "auto"))
    monkeypatch.setattr(recorder.server_client, "independent", lambda: False)
    monkeypatch.setattr(recorder.server_client, "target", lambda pane: {"pane": pane, "token": "test"})
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
        assert recorder.is_recording()
        assert called == [], "토글이 꺼져 있으면 알림을 시도조차 하면 안 된다"
    finally:
        # 전역 상태 원복 — 이 프로세스에서 뒤에 도는 다른 테스트에 안 새도록.
        recorder._recording = False
        recorder._stream = None


def test_input_stream_failure_resets_recording_and_resumes_queue(monkeypatch):
    monkeypatch.setattr(recorder, '_recording', False)
    monkeypatch.setattr(recorder, '_processing', False)
    monkeypatch.setattr(recorder, '_stream', None)
    monkeypatch.setattr(recorder, 'resolve_voice_target_pane', lambda: ('%3', 'auto'))
    monkeypatch.setattr(recorder.server_client, 'independent', lambda: False)
    monkeypatch.setattr(recorder.server_client, 'target', lambda pane: {'pane': pane, 'token': 'test'})
    resumed = []
    monkeypatch.setattr(recorder.server_client, 'request', lambda *args: resumed.append(args))
    def fail(**kw): raise RuntimeError('device unavailable')
    monkeypatch.setattr(recorder.sd, 'InputStream', fail)
    recorder.start_recording()
    assert not recorder.is_recording() and recorder._stream is None
    assert resumed and resumed[0][0] == '/voice/cancel'


def test_daemon_does_not_record_or_pause_tts_when_server_model_is_unready(monkeypatch):
    from voice import server_client
    calls = []
    def request(path, *args, **kw):
        calls.append(path)
        return {'token': 'test'} if path == '/voice/target' else {'loaded': False, 'error': 'model missing'}
    monkeypatch.setattr(server_client, 'request', request)
    with pytest.raises(RuntimeError, match='model missing'):
        server_client.target('%3')
    assert calls == ['/voice/target', '/voice/stt/preload']
