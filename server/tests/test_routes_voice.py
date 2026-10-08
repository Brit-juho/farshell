"""routes/voice.py — local_mic 503, /voice/input(재전송 멱등 포함), notify, capabilities의 stt 항목."""

import asyncio
import json

import pytest
from fastapi.responses import JSONResponse

import stt_service
import voice_handler
from routes import system as system_routes
from routes import voice
from stt_engines.base import NotReady


def test_local_mic_endpoints_report_unavailable_without_optional_dependencies(monkeypatch):
    """core 프로필에서도 서버는 기동하고, local-mic만 503으로 명확히 거절한다."""

    start = asyncio.run(voice.local_mic_start())
    stop = asyncio.run(voice.local_mic_stop(None))

    for response in (start, stop):
        assert isinstance(response, JSONResponse)
        assert response.status_code == 410
        assert b"local_mic_removed" in response.body


def test_notify_clients_count(monkeypatch):
    monkeypatch.setattr(voice, "notify_clients", set())
    assert asyncio.run(voice.notify_web_clients()) == {"web_clients": 0}
    monkeypatch.setattr(voice, "notify_clients", {object(), object()})
    assert asyncio.run(voice.notify_web_clients()) == {"web_clients": 2}


# ── /voice/input ───────────────────────────────────────────────────

class _Req:
    """voice_input이 쓰는 headers·query_params·body()만 흉내 낸다(헤더 키는 소문자)."""
    def __init__(self, body=b"audio", headers=None, query=None):
        self._body, self.headers, self.query_params = body, headers or {}, query or {}

    async def body(self):
        return self._body


class _PTY:
    def __init__(self, sessions=()):
        self.sessions = {s: object() for s in sessions}
        self.writes = []

    def write(self, sid, data):
        self.writes.append((sid, data))


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(voice, "_cancel_tts_playback", lambda: 0)        # 실제 pkill 방지
    voice._voice_done.clear()
    voice._voice_inflight.clear()
    pty = _PTY(["s1"])
    monkeypatch.setattr(voice, "pty_mgr", pty)
    yield pty
    voice._voice_done.clear()
    voice._voice_inflight.clear()


TIMING = {"decode_ms": 1, "wait_ms": 0, "load_ms": 0, "stt_ms": 5, "total_ms": 7}


def fake_transcribe(monkeypatch, text="ls -la", exc=None, gate=None):
    calls = []

    async def fn(audio, input_format="webm", language=None):
        calls.append((audio, input_format, language))
        if gate is not None:
            await gate.wait()
        if exc is not None:
            raise exc
        return text, dict(TIMING)
    monkeypatch.setattr(voice_handler, "transcribe_timed", fn)
    return calls


def post(req):
    return asyncio.run(voice.voice_input(req))


def test_input_success_injects_into_session(monkeypatch, isolated):
    calls = fake_transcribe(monkeypatch)
    r = post(_Req(b"abc", {"content-type": "audio/mp4"}, {"session_id": "s1", "lang": "ko"}))
    assert r == {"text": "ls -la", "injected": True, "session_id": "s1", "timing": TIMING}
    assert isolated.writes == [("s1", b"ls -la\n")]
    assert calls == [(b"abc", "mp4", "ko")]


def test_input_session_from_header_and_unknown_session(monkeypatch, isolated):
    fake_transcribe(monkeypatch)
    r = post(_Req(headers={"x-session-id": "s1"}))
    assert r["injected"] is True and r["session_id"] == "s1"
    r = post(_Req(query={"session_id": "ghost"}))
    assert r["injected"] is False and r["text"] == "ls -la" and len(isolated.writes) == 1
    r = post(_Req())
    assert r["injected"] is False and r["session_id"] == ""


def test_input_empty_text_not_injected(monkeypatch, isolated):
    fake_transcribe(monkeypatch, text="")
    r = post(_Req(query={"session_id": "s1"}))
    assert r["text"] == "" and r["injected"] is False and isolated.writes == []


def test_input_not_ready_is_200_with_error(monkeypatch, isolated):
    fake_transcribe(monkeypatch, exc=NotReady("모델을 받아 주세요"))
    r = post(_Req(query={"session_id": "s1"}))
    assert r == {"text": "", "error": "모델을 받아 주세요", "injected": False}
    assert isolated.writes == []


def test_input_busy_is_429(monkeypatch, isolated):
    fake_transcribe(monkeypatch, exc=stt_service.Busy("밀림"))
    r = post(_Req(query={"session_id": "s1"}))
    assert isinstance(r, JSONResponse) and r.status_code == 429
    assert json.loads(r.body) == {"text": "", "error": "밀림", "injected": False}
    assert isolated.writes == []


def test_input_generic_error_returns_error_field(monkeypatch):
    fake_transcribe(monkeypatch, exc=RuntimeError("터짐"))
    r = post(_Req())
    assert r["text"] == "" and r["error"] == "터짐" and r["injected"] is False


def test_input_content_type_to_decoder_format(monkeypatch):
    calls = fake_transcribe(monkeypatch)
    for ct in ["audio/webm;codecs=opus", "audio/ogg", "audio/wav", "audio/mp4", ""]:
        post(_Req(b"x", {"content-type": ct}))
    assert [c[1] for c in calls] == ["webm", "ogg", "wav", "mp4", "webm"]


# ── X-Voice-Id 멱등 ────────────────────────────────────────────────

def test_voice_id_resend_is_duplicate_and_writes_once(monkeypatch, isolated):
    calls = fake_transcribe(monkeypatch)
    h = {"x-voice-id": "rec-123_A"}
    a = post(_Req(headers=h, query={"session_id": "s1"}))
    b = post(_Req(headers=h, query={"session_id": "s1"}))
    assert "duplicate" not in a and b["duplicate"] is True
    assert {k: v for k, v in b.items() if k != "duplicate"} == a
    assert len(calls) == 1 and isolated.writes == [("s1", b"ls -la\n")]


def test_voice_id_different_ids_both_processed(monkeypatch, isolated):
    calls = fake_transcribe(monkeypatch)
    post(_Req(headers={"x-voice-id": "a1"}, query={"session_id": "s1"}))
    post(_Req(headers={"x-voice-id": "a2"}, query={"session_id": "s1"}))
    assert len(calls) == 2 and len(isolated.writes) == 2


@pytest.mark.parametrize("exc", [NotReady("준비 안 됨"), RuntimeError("터짐")])
def test_voice_id_failure_not_remembered(monkeypatch, isolated, exc):
    calls = fake_transcribe(monkeypatch, exc=exc)
    h = {"x-voice-id": "rec1"}
    assert post(_Req(headers=h, query={"session_id": "s1"}))["error"]
    fake_transcribe(monkeypatch)                                         # 이제 성공
    r = post(_Req(headers=h, query={"session_id": "s1"}))
    assert r["text"] == "ls -la" and "duplicate" not in r and r["injected"] is True
    assert isolated.writes == [("s1", b"ls -la\n")] and voice._voice_inflight == {}


def test_voice_id_empty_result_not_remembered(monkeypatch):
    calls = fake_transcribe(monkeypatch, text="")
    h = {"x-voice-id": "rec1"}
    post(_Req(headers=h))
    post(_Req(headers=h))
    assert len(calls) == 2


def test_voice_id_inflight_request_joins(monkeypatch, isolated):
    async def go():
        gate = asyncio.Event()
        calls = fake_transcribe(monkeypatch, gate=gate)
        h = {"x-voice-id": "rec1"}
        t1 = asyncio.create_task(voice.voice_input(_Req(headers=h, query={"session_id": "s1"})))
        await asyncio.sleep(0.05)
        t2 = asyncio.create_task(voice.voice_input(_Req(headers=h, query={"session_id": "s1"})))
        await asyncio.sleep(0.05)
        assert not t1.done() and not t2.done() and len(calls) == 1
        gate.set()
        return await t1, await t2, calls
    a, b, calls = asyncio.run(go())
    assert b["duplicate"] is True and "duplicate" not in a and b["text"] == a["text"]
    assert len(calls) == 1 and isolated.writes == [("s1", b"ls -la\n")]
    assert voice._voice_inflight == {}


def test_voice_id_inflight_waiter_gets_failure_then_retry_reprocesses(monkeypatch):
    async def go():
        gate = asyncio.Event()
        fake_transcribe(monkeypatch, exc=NotReady("준비 안 됨"), gate=gate)
        h = {"x-voice-id": "rec1"}
        t1 = asyncio.create_task(voice.voice_input(_Req(headers=h)))
        await asyncio.sleep(0.02)
        t2 = asyncio.create_task(voice.voice_input(_Req(headers=h)))
        await asyncio.sleep(0.02)
        gate.set()
        return await t1, await t2
    a, b = asyncio.run(go())
    assert a["error"] == b["error"] == "준비 안 됨" and "rec1" not in voice._voice_done


def test_voice_id_cancelled_first_request_cleans_inflight(monkeypatch):
    async def go():
        gate = asyncio.Event()
        fake_transcribe(monkeypatch, gate=gate)
        t1 = asyncio.create_task(voice.voice_input(_Req(headers={"x-voice-id": "rec1"})))
        await asyncio.sleep(0.02)
        t1.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t1
    asyncio.run(go())
    assert voice._voice_inflight == {}


@pytest.mark.parametrize("bad", ["a b", "한글", "a;b", "a/b", "a\tb", "<x>"])
def test_voice_id_non_alnum_ignored(monkeypatch, bad):
    calls = fake_transcribe(monkeypatch)
    assert voice._voice_id(_Req(headers={"x-voice-id": bad})) == ""
    post(_Req(headers={"x-voice-id": bad}))
    r = post(_Req(headers={"x-voice-id": bad}))
    assert len(calls) == 2 and "duplicate" not in r and voice._voice_done == {}


def test_voice_id_sanitize_and_truncate():
    assert voice._voice_id(_Req(headers={"x-voice-id": "  ab-c_9 "})) == "ab-c_9"
    assert voice._voice_id(_Req(headers={"x-voice-id": "a" * 100})) == "a" * 64
    assert voice._voice_id(_Req()) == ""


def test_voice_id_memory_bounded(monkeypatch):
    fake_transcribe(monkeypatch)
    for i in range(voice._VOICE_ID_MAX + 10):
        post(_Req(headers={"x-voice-id": f"id{i}"}))
    assert len(voice._voice_done) == voice._VOICE_ID_MAX
    assert "id0" not in voice._voice_done and f"id{voice._VOICE_ID_MAX + 9}" in voice._voice_done


def test_voice_id_expires(monkeypatch):
    calls = fake_transcribe(monkeypatch)
    h = {"x-voice-id": "rec1"}
    post(_Req(headers=h))
    ts, res = voice._voice_done["rec1"]
    voice._voice_done["rec1"] = (ts - voice._VOICE_ID_TTL - 1, res)
    post(_Req(headers={"x-voice-id": "rec2"}))                           # 기록 시점에 만료분을 치운다
    assert "rec1" not in voice._voice_done


def test_voice_id_busy_still_429(monkeypatch):
    fake_transcribe(monkeypatch, exc=stt_service.Busy("밀림"))
    r = post(_Req(headers={"x-voice-id": "rec1"}))
    assert isinstance(r, JSONResponse) and r.status_code == 429


# ── capabilities ───────────────────────────────────────────────────

def test_capabilities_stt_uses_new_service(monkeypatch):
    monkeypatch.setattr(system_routes.tunnel, "get_tunnel_status", lambda: {})
    monkeypatch.setattr(system_routes.tailscale, "get_status_dict", lambda: {})
    monkeypatch.setattr(stt_service, "available", lambda env=None: False)
    monkeypatch.setattr(stt_service, "loaded", lambda: False)
    p = system_routes._capabilities_payload()
    assert p["stt"] == "none" and p["stt_loaded"] is False

    monkeypatch.setattr(stt_service, "available", lambda env=None: True)
    assert system_routes._capabilities_payload()["stt"] == "available"

    monkeypatch.setattr(stt_service, "loaded", lambda: True)
    monkeypatch.setattr(stt_service, "loaded_engine_id", lambda: "mlx_qwen3")
    p = system_routes._capabilities_payload()
    assert p["stt"] == "mlx_qwen3" and p["stt_loaded"] is True and p["voice"] is True


def test_capabilities_does_not_load_model(monkeypatch):
    monkeypatch.setattr(system_routes.tunnel, "get_tunnel_status", lambda: {})
    monkeypatch.setattr(system_routes.tailscale, "get_status_dict", lambda: {})
    boom = []
    monkeypatch.setattr(stt_service, "preload", lambda: boom.append(1))
    monkeypatch.setattr(stt_service, "transcribe_wav", lambda *a: boom.append(2))
    system_routes._capabilities_payload()
    assert boom == []
