"""녹음 중 처리(voice_live) + /voice/chunk 응답의 endpoint·partial + stt_service.transcribe_partial.

부분 인식 STT는 가짜(결정적)다. 실제 엔진 왕복은 여기서 하지 않는다."""
import asyncio
import json
import logging
import threading

import pytest
from fastapi.responses import JSONResponse

import stt_engines
import stt_service
import tmux_target
import voice_chunks as vc
import voice_handler
import voice_live
import voice_targets
import workspace
from routes import voice
from stt_engines import env as env_mod
from stt_fakes import MAC_ARM, make_engines
from test_voice_vad import mix, noise, pcm, tone
from shared_testkit import Req  # noqa: F401  (pytest 가 이름으로 쓰는 fixture/헬퍼)

TIMING = {"decode_ms": 1, "wait_ms": 0, "load_ms": 0, "stt_ms": 5, "total_ms": 7}
SPEECH = pcm(mix(tone(0.5), noise(0.5, seed=2)))     # 0.5초 = 16000바이트(클라이언트 청크 크기)
QUIET = pcm(noise(0.5, seed=3))
SECRET = "비밀 문장 ls -la"


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        vc.reset()
        voice._voice_done.clear(); voice._voice_inflight.clear(); voice._voice_fingerprints.clear()
        voice_targets._targets.clear()
    wipe()
    yield
    wipe()
    assert voice_live.pending_tasks() == 0              # 백그라운드 작업이 새지 않는다




def issue():
    owner = voice_targets.principal(Req())
    return voice_targets.issue(owner, {"key": "k", "pane": "%1", "name": "w", "tmux_id": "$1",
                                       "generation": "1", "boot": "b"})["token"]


def body(r):
    return json.loads(r.body) if isinstance(r, JSONResponse) else r


async def post(token, seq, data, final=False, vid="vid-1"):
    q = {"seq": str(seq), **({"final": "1"} if final else {})}
    return body(await voice.voice_chunk(Req(data, {"x-voice-id": vid, "x-voice-target": token}, q)))


@pytest.fixture
def env(monkeypatch):
    """opts: 녹음 시작에 읽힐 설정. partial: 부분 인식 가짜(호출 기록, gate로 멈출 수 있음). final: 최종 인식 가짜."""
    st = type("S", (), {})()
    st.opts = {"auto_stop": False, "partial": False}
    st.partial_calls = []
    st.partial_text = SECRET
    st.partial_gate = None          # threading.Event
    st.partial_error = None
    st.partial_none = False
    st.final_calls = []
    st.sent = []
    monkeypatch.setattr(voice_live, "read_options", lambda: dict(st.opts))

    def fake_partial(wav, lang):
        st.partial_calls.append(len(wav))
        if st.partial_gate is not None:
            st.partial_gate.wait(5)
        if st.partial_error:
            raise st.partial_error
        return None if st.partial_none else st.partial_text
    monkeypatch.setattr(stt_service, "transcribe_partial", fake_partial)

    async def fake_final(audio, input_format="webm", language=None):
        st.final_calls.append(len(audio))
        return "최종", dict(TIMING)
    monkeypatch.setattr(voice_handler, "transcribe_timed", fake_final)
    monkeypatch.setattr(voice_targets, "valid", lambda t: True)
    monkeypatch.setattr(tmux_target, "send_to_tmux", lambda pane, text: st.sent.append((pane, text)) or True)
    return st


async def settle():
    """부분 인식 작업(스레드 포함)이 끝날 때까지 이벤트 루프를 돌린다."""
    for _ in range(200):
        if voice_live.pending_tasks() == 0:
            return
        await asyncio.sleep(0.01)


# ── 설정 읽기 ───────────────────────────────────────────────────

def test_read_options_defaults_and_values(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "WS_PATH", tmp_path / "ws.json")
    assert voice_live.read_options() == {"auto_stop": False, "partial": True}
    (tmp_path / "ws.json").write_text(json.dumps({"settings": {"voice.autoStop": True, "voice.partialResults": False}}))
    assert voice_live.read_options() == {"auto_stop": True, "partial": False}
    (tmp_path / "ws.json").write_text(json.dumps({"settings": {"voice.autoStop": "true", "voice.partialResults": 0}}))
    assert voice_live.read_options() == {"auto_stop": False, "partial": True}     # 형식이 틀린 값은 기본값
    (tmp_path / "ws.json").write_text("{broken")
    assert voice_live.read_options() == {"auto_stop": False, "partial": True}


# ── 자동 종료(endpoint) ─────────────────────────────────────────

def test_endpoint_after_speech_and_one_second_silence(env):
    env.opts = {"auto_stop": True, "partial": False}
    tok = issue()

    async def go():
        seq, out = 0, []
        for data in [QUIET, SPEECH, SPEECH] + [QUIET] * 3:
            out.append(await post(tok, seq, data)); seq += 1
        final = await post(tok, seq, b"", final=True)
        return out, final
    out, final = asyncio.run(go())
    assert [("endpoint" in r) for r in out] == [False, False, False, False, True, True]
    assert all(r["complete"] is False for r in out)
    assert final["complete"] is True and final["text"] == "최종" and "endpoint" not in final
    assert env.partial_calls == []


def test_no_endpoint_without_speech_or_when_off(env):
    tok = issue()

    async def go(vid, chunks):
        return [await post(tok, i, d, vid=vid) for i, d in enumerate(chunks)]
    env.opts = {"auto_stop": True, "partial": False}
    quiet = asyncio.run(go("vid-q", [QUIET] * 12))           # 6초 내내 조용 — 끝내지 않는다
    assert not any("endpoint" in r for r in quiet)
    env.opts = {"auto_stop": False, "partial": False}
    off = asyncio.run(go("vid-o", [QUIET, SPEECH, SPEECH] + [QUIET] * 4))
    assert not any("endpoint" in r for r in off)


def test_options_are_fixed_at_recording_start(env):
    env.opts = {"auto_stop": False, "partial": False}
    tok = issue()

    async def go():
        r = [await post(tok, 0, QUIET)]
        env.opts = {"auto_stop": True, "partial": True}       # 녹음 중에 바꿔도 이 녹음은 그대로
        for i, d in enumerate([SPEECH, SPEECH] + [QUIET] * 3, start=1):
            r.append(await post(tok, i, d))
        return r
    assert not any("endpoint" in r or "partial" in r for r in asyncio.run(go()))
    assert env.partial_calls == []


def test_out_of_order_chunks_feed_vad_in_seq_order(env):
    env.opts = {"auto_stop": True, "partial": False}
    tok = issue()

    async def go():
        order = [(0, QUIET), (2, SPEECH), (1, SPEECH), (4, QUIET), (3, QUIET), (5, QUIET)]
        return [await post(tok, s, d) for s, d in order]
    out = asyncio.run(go())
    assert "endpoint" not in out[0] and "endpoint" in out[-1]
    rec = vc.peek(voice_targets.principal(Req()), "vid-1")
    assert rec.live.fed_seq == 5


# ── 부분 인식 ───────────────────────────────────────────────────

def test_partial_appears_after_speech_and_is_not_injected_or_logged(env, caplog):
    env.opts = {"auto_stop": False, "partial": True}
    tok = issue()
    caplog.set_level(logging.DEBUG)

    async def go():
        out = []
        for i, d in enumerate([QUIET, SPEECH, SPEECH, SPEECH, SPEECH]):
            out.append(await post(tok, i, d))
            await settle()
        out.append(await post(tok, 5, SPEECH))
        final = await post(tok, 6, b"", final=True)
        return out, final
    out, final = asyncio.run(go())
    assert "partial" not in out[0]
    with_partial = [r for r in out if "partial" in r]
    assert with_partial and with_partial[-1]["partial"]["text"] == SECRET
    assert with_partial[-1]["partial"]["clipped"] is False and isinstance(with_partial[-1]["partial"]["seq"], int)
    assert env.sent == [("%1", "최종")]                     # 부분 텍스트는 pane에 들어가지 않는다
    assert final["text"] == "최종" and "partial" not in final
    assert SECRET not in caplog.text and "voice.partial" in caplog.text     # 길이·시간만 남는다


def test_partial_not_started_without_speech(env):
    env.opts = {"auto_stop": False, "partial": True}
    tok = issue()

    async def go():
        for i in range(10):
            await post(tok, i, QUIET); await settle()
    asyncio.run(go())
    assert env.partial_calls == []


def test_one_partial_in_flight_and_final_discards_it(env):
    env.opts = {"auto_stop": False, "partial": True}
    env.partial_gate = threading.Event()
    tok = issue()

    async def go():
        for i, d in enumerate([QUIET, SPEECH, SPEECH, SPEECH, SPEECH, SPEECH, SPEECH]):
            await post(tok, i, d)
            await asyncio.sleep(0.02)
        assert len(env.partial_calls) == 1                   # 진행 중이면 새로 시작하지 않는다
        assert voice_live.pending_tasks() == 1
        final = await post(tok, 7, b"", final=True)
        assert voice_live.pending_tasks() == 0              # final이 진행 중 작업을 정리했다
        env.partial_gate.set()                                # 엔진 스레드가 늦게 끝나도
        await asyncio.sleep(0.05)
        return final
    final = asyncio.run(go())
    assert final["complete"] is True and final["text"] == "최종"
    rec = vc.peek(voice_targets.principal(Req()), "vid-1")
    assert rec.state == "done" and rec.live is None


def test_cancel_during_partial_cleans_up(env):
    env.opts = {"auto_stop": False, "partial": True}
    env.partial_gate = threading.Event()
    tok = issue()

    async def go():
        for i, d in enumerate([QUIET, SPEECH, SPEECH, SPEECH, SPEECH]):
            await post(tok, i, d); await asyncio.sleep(0.02)
        assert voice_live.pending_tasks() == 1
        r = await voice.voice_chunk_cancel(Req(b"", {"x-voice-id": "vid-1"}))
        await asyncio.sleep(0.01)                             # 취소 요청이 작업에 전달되는 한 바퀴
        assert voice_live.pending_tasks() == 0              # 엔진 스레드는 아직 막혀 있어도 작업은 끝났다
        env.partial_gate.set()
        await asyncio.sleep(0.05)
        return r
    assert asyncio.run(go())["state"] == "cancelled"
    assert vc.peek(voice_targets.principal(Req()), "vid-1") is None


def test_replaced_recording_cleans_up_partial(env):
    env.opts = {"auto_stop": False, "partial": True}
    env.partial_gate = threading.Event()
    tok = issue()

    async def go():
        for i, d in enumerate([QUIET, SPEECH, SPEECH, SPEECH, SPEECH]):
            await post(tok, i, d); await asyncio.sleep(0.02)
        assert voice_live.pending_tasks() == 1
        await post(tok, 0, QUIET, vid="vid-2")               # 같은 사용자의 새 녹음이 이전 것을 폐기
        await asyncio.sleep(0.01)
        assert voice_live.pending_tasks() == 0
        env.partial_gate.set(); await asyncio.sleep(0.05)
    asyncio.run(go())


def test_partial_failure_or_busy_keeps_recording_and_retries(env):
    env.opts = {"auto_stop": False, "partial": True}
    tok = issue()

    async def go():
        env.partial_error = RuntimeError("engine exploded")
        out = []
        for i, d in enumerate([QUIET, SPEECH, SPEECH, SPEECH, SPEECH]):
            out.append(await post(tok, i, d)); await settle()
        failed_calls = len(env.partial_calls)
        env.partial_error = None
        env.partial_none = True                               # 엔진이 바빠 건너뜀
        out.append(await post(tok, 5, SPEECH)); await settle()
        busy_calls = len(env.partial_calls)
        env.partial_none = False
        out.append(await post(tok, 6, SPEECH)); await settle()
        out.append(await post(tok, 7, SPEECH))
        final = await post(tok, 8, b"", final=True)
        return out, failed_calls, busy_calls, final
    out, failed_calls, busy_calls, final = asyncio.run(go())
    assert failed_calls >= 1 and busy_calls > failed_calls   # 실패·건너뜀 뒤 다음 조각에서 다시 시도
    assert all(r["complete"] is False for r in out)
    assert out[-1]["partial"]["text"] == SECRET
    assert final["text"] == "최종"


def test_partial_window_is_bounded_and_marked_clipped(env, monkeypatch):
    env.opts = {"auto_stop": False, "partial": True}
    monkeypatch.setattr(voice_live, "PARTIAL_WINDOW_SEC", 2)
    tok = issue()

    async def go():
        out = []
        for i in range(12):
            out.append(await post(tok, i, SPEECH if i else QUIET)); await settle()
        return out
    out = asyncio.run(go())
    assert max(env.partial_calls) <= 2 * vc.BYTES_PER_SEC + 44
    assert out[-1]["partial"]["clipped"] is True


def test_duty_factor_spaces_out_slow_partials(env, monkeypatch):
    env.opts = {"auto_stop": False, "partial": True}
    tok = issue()
    clock = iter([0.0, 2.0] + [10.0 + i for i in range(100)])   # 첫 부분 인식이 2초 걸린 것처럼
    monkeypatch.setattr(voice_live.time, "perf_counter", lambda: next(clock))

    async def go():
        for i in range(10):
            await post(tok, i, SPEECH if i else QUIET); await settle()
    asyncio.run(go())
    rec = vc.peek(voice_targets.principal(Req()), "vid-1")
    assert rec.live._interval_bytes >= 6 * vc.BYTES_PER_SEC   # 2초 × 3 = 오디오 6초마다
    assert len(env.partial_calls) == 1


def test_live_error_does_not_break_chunk_ack(env, monkeypatch):
    env.opts = {"auto_stop": True, "partial": True}
    tok = issue()

    def boom(self, rec):
        raise ValueError("bug")
    monkeypatch.setattr(voice_live.Live, "advance", boom)
    r = asyncio.run(post(tok, 0, SPEECH))
    assert r == {"ackSeq": 0, "complete": False}


# ── stt_service.transcribe_partial ─────────────────────────────

@pytest.fixture
def svc(monkeypatch, tmp_path):
    calls = []
    engines = make_engines(calls)
    monkeypatch.setattr(stt_engines, "_instances", dict(engines))
    env_mod.override(MAC_ARM)
    monkeypatch.delenv("VT_STT_MODEL", raising=False)
    monkeypatch.setattr(workspace, "WS_PATH", tmp_path / "ws.json")
    monkeypatch.setattr(stt_service, "_waiting", 0)
    monkeypatch.setattr(stt_service, "_last_error", "")
    monkeypatch.setattr(stt_service, "_last_timing", None)
    e = engines["mlx_qwen3"]
    e.present.add("qwen3-asr-0.6b-mlx")
    yield e, calls
    env_mod.override(None)


def test_partial_never_loads_model(svc):
    e, calls = svc
    assert stt_service.transcribe_partial(b"w", None) is None
    assert [c[1] for c in calls] == []                       # 올리지 않는다
    e.load(stt_service.selection().spec)
    assert stt_service.transcribe_partial(b"w", None) == "안녕하세요"
    assert stt_service._last_timing is None and stt_service._waiting == 0


def test_partial_skips_when_lock_busy_or_queue_waiting(svc, monkeypatch):
    e, _ = svc
    e.load(stt_service.selection().spec)
    with stt_service._run_lock:                              # 다른 인식이 엔진을 쓰는 중
        assert stt_service.transcribe_partial(b"w", None) is None
    monkeypatch.setattr(stt_service, "_waiting", 1)          # 줄 선 요청이 있음
    assert stt_service.transcribe_partial(b"w", None) is None
    monkeypatch.setattr(stt_service, "_waiting", 0)
    assert stt_service.transcribe_partial(b"w", None) == "안녕하세요"


def test_partial_does_not_block_final_queue_count(svc):
    """부분 인식이 엔진을 쓰는 동안 최종 인식은 줄을 서서(Busy 아님) 끝나면 바로 돈다."""
    e, _ = svc
    e.load(stt_service.selection().spec)
    e.gate = threading.Event()
    out = {}
    tp = threading.Thread(target=lambda: out.setdefault("p", stt_service.transcribe_partial(b"w", None)))
    tp.start()
    for _ in range(100):
        if e.active:
            break
        threading.Event().wait(0.01)
    assert stt_service._waiting == 0                          # 부분 인식은 줄 수에 안 들어간다
    tf = threading.Thread(target=lambda: out.setdefault("f", stt_service.transcribe_wav(b"w", None)[0]))
    tf.start()
    e.gate.set()
    tp.join(5); tf.join(5)
    assert out == {"p": "안녕하세요", "f": "안녕하세요"} and e.max_active == 1


def test_partial_engine_error_propagates_without_touching_last_error(svc, monkeypatch):
    e, _ = svc
    e.load(stt_service.selection().spec)
    monkeypatch.setattr(e, "transcribe", lambda wav, lang: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        stt_service.transcribe_partial(b"w", None)
    assert stt_service.last_error() == "" and not stt_service._run_lock.locked()
