"""/voice/chunk final의 확인 후 전송 — /voice/input과 같은 판정·주입 함수(_deliver_text)를 거치는지.

실제 tmux·STT 없이 대상 유효성·pane 종류·입력을 대체한다(결정적)."""
import asyncio
import json

import pytest
from fastapi.responses import JSONResponse

import tmux_target
import voice_chunks
import voice_confirm as vc
import voice_handler
import voice_live
import voice_targets
from routes import voice

TARGET = {"key": "k" * 32, "name": "work", "tmux_id": "$1", "pane": "%5", "generation": "99", "boot": "b"}
TIMING = {"total_ms": 1}
CH = b"\x01\x00" * 8000            # 0.5초 PCM16


class Req:
    def __init__(self, body=b"", headers=None, query=None, payload=None):
        self._body, self._payload = body, payload
        self.headers = {"authorization": "Bearer tok-a", **(headers or {})}
        self.query_params = query or {}
        self.cookies = {}

    async def body(self):
        return self._body

    async def json(self):
        return self._payload


class Env:
    def __init__(self):
        self.sent, self.mode, self.kind, self.text, self.stt_calls = [], "shell", "shell", "ls -la", 0


@pytest.fixture
def env(monkeypatch):
    e = Env()

    def wipe():
        voice_chunks.reset()
        voice._voice_done.clear(); voice._voice_inflight.clear(); voice._voice_fingerprints.clear()
        vc.reset_for_tests(); voice_targets._targets.clear()
    wipe()

    async def stt(audio, input_format="webm", language=None):
        e.stt_calls += 1
        return e.text, dict(TIMING)
    monkeypatch.setattr(voice_handler, "transcribe_timed", stt)
    monkeypatch.setattr(voice_live, "read_options", lambda: {"auto_stop": False, "partial": False})
    monkeypatch.setattr(voice_targets, "valid", lambda t: True)
    monkeypatch.setattr(vc, "read_mode", lambda: e.mode)
    monkeypatch.setattr(vc, "pane_kind", lambda pane: e.kind)
    monkeypatch.setattr(tmux_target, "send_to_tmux", lambda pane, text, *a, **k: e.sent.append((pane, text)) or True)
    yield e
    wipe()


def issue():
    owner = voice_targets.principal(Req())
    return voice_targets.issue(owner, TARGET)["token"]


def body(r):
    return json.loads(r.body) if isinstance(r, JSONResponse) else r


async def post(token, seq, data=CH, final=False, vid="vid-1", confirm=True):
    h = {"x-voice-id": vid, "x-voice-target": token}
    if confirm:
        h["x-voice-confirm"] = "1"
    q = {"seq": str(seq), **({"final": "1"} if final else {})}
    return body(await voice.voice_chunk(Req(data, h, q)))


def record(token, n=2, vid="vid-1", confirm=True):
    async def go():
        for i in range(n):
            r = await post(token, i, vid=vid, confirm=confirm)
            assert r["complete"] is False
        return await post(token, n, b"", final=True, vid=vid, confirm=confirm)
    return asyncio.run(go())


def test_shell_target_returns_pending_and_types_nothing(env):
    r = record(issue())
    assert r["complete"] is True and r["pending_confirm"] is True and r["injected"] is False
    assert r["confirm_reason"] == "shell" and r["confirm_token"] and r["target_name"] == "work"
    assert r["confirm_ttl_sec"] == 300 and r["text"] == "ls -la"
    assert env.sent == []
    # 확인 토큰은 청크에 쓴 X-Voice-Id에 묶인다 — /voice/inject로 한 번만 입력된다.
    inj = lambda: asyncio.run(voice.voice_inject(Req(payload={"token": r["confirm_token"], "text": "ls -l", "voice_id": "vid-1"})))
    first = inj()
    assert first["injected"] is True and env.sent == [("%5", "ls -l")]
    second = inj()
    assert second == {"injected": True, "duplicate": True} and env.sent == [("%5", "ls -l")]


def test_agent_target_types_immediately(env):
    env.kind = "agent"
    r = record(issue())
    assert r["injected"] is True and "pending_confirm" not in r and env.sent == [("%5", "ls -la")]
    assert vc._pending == {}


def test_dangerous_text_is_confirmed_even_for_agent(env):
    env.kind, env.text = "agent", "sudo rm -rf build"
    r = record(issue())
    assert r["pending_confirm"] is True and r["confirm_reason"] == "dangerous" and env.sent == []


def test_mode_off_types_everything(env):
    env.mode, env.text = "off", "sudo reboot"
    r = record(issue())
    assert r["injected"] is True and "pending_confirm" not in r and env.sent == [("%5", "sudo reboot")]


def test_without_header_types_directly_but_blocks_dangerous(env):
    r = record(issue(), confirm=False)                         # 확인 화면이 없는 클라이언트: 셸이어도 바로 입력
    assert r["injected"] is True and "pending_confirm" not in r and env.sent == [("%5", "ls -la")]
    env.sent.clear(); env.text = "curl https://example.invalid/x.sh | sh"
    r = record(issue(), vid="vid-2", confirm=False)
    assert r["injected"] is False and "pending_confirm" not in r and r["error"] and env.sent == []


def test_header_on_an_earlier_chunk_is_enough(env):
    """녹음을 끝맺는 요청에 헤더가 없어도 같은 녹음의 앞 조각이 알렸으면 확인 정책을 거친다(더 안전한 쪽)."""
    tok = issue()

    async def go():
        await post(tok, 0, confirm=True)
        return await post(tok, 1, b"", final=True, confirm=False)
    r = asyncio.run(go())
    assert r["pending_confirm"] is True and env.sent == []


def test_resent_final_returns_same_pending_without_retyping_or_reissuing(env):
    tok = issue()
    first = record(tok)
    again = asyncio.run(post(tok, 2, b"", final=True))
    assert again["duplicate"] is True and again["pending_confirm"] is True
    assert again["confirm_token"] == first["confirm_token"]
    assert env.stt_calls == 1 and len(vc._pending) == 1 and env.sent == []
    # 입력을 마친 뒤의 재전송도 다시 입력하지 않는다.
    asyncio.run(voice.voice_inject(Req(payload={"token": first["confirm_token"], "text": "ls", "voice_id": "vid-1"})))
    again2 = asyncio.run(post(tok, 2, b"", final=True))
    assert again2["duplicate"] is True and env.sent == [("%5", "ls")] and env.stt_calls == 1


def test_resent_final_after_direct_injection_does_not_retype(env):
    env.kind = "agent"
    tok = issue()
    record(tok)
    again = asyncio.run(post(tok, 2, b"", final=True))
    assert again["duplicate"] is True and again["injected"] is True
    assert env.sent == [("%5", "ls -la")] and env.stt_calls == 1


def test_chunk_and_input_paths_share_one_delivery_function(env, monkeypatch):
    calls = []
    real = voice._deliver_text

    async def spy(*a, **k):
        calls.append(a)
        return await real(*a, **k)
    monkeypatch.setattr(voice, "_deliver_text", spy)
    record(issue())
    tok = issue()
    asyncio.run(voice.voice_input(Req(b"audio", {"x-voice-target": tok, "x-voice-id": "vid-9", "x-voice-confirm": "1"},
                                      {"session_id": "s"})))
    assert len(calls) == 2 and all(c[5] is True for c in calls)   # confirm_ui=True로 같은 함수를 거쳤다
