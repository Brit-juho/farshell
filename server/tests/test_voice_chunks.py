"""voice_chunks 수집기 + /voice/chunk 라우트 — 순서 뒤바뀜·중복·상한·소유자·만료·취소·final 선도착."""

import asyncio
import json
import wave
import io

import pytest
from fastapi.responses import JSONResponse

import stt_service
import tmux_target
import voice_chunks as vc
import voice_handler
import voice_targets
from routes import voice
from shared_testkit import Req  # noqa: F401  (pytest 가 이름으로 쓰는 fixture/헬퍼)

TIMING = {"decode_ms": 1, "wait_ms": 0, "load_ms": 0, "stt_ms": 5, "total_ms": 7}
CH = b"\x01\x00" * 100          # 200바이트(짝수) 조각


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    vc.reset()
    voice._voice_done.clear(); voice._voice_inflight.clear(); voice._voice_fingerprints.clear()
    voice_targets._targets.clear()
    yield
    vc.reset()
    voice._voice_done.clear(); voice._voice_inflight.clear(); voice._voice_fingerprints.clear()
    voice_targets._targets.clear()


def chunk(i):
    return bytes([i % 250 + 1, 0]) * 100


# ── 수집기 ──────────────────────────────────────────────────────

def acc(seq, data=None, final=False, vid="v1", owner="o", token="t", now=None):
    return vc.accept(vid, owner, token, seq, chunk(seq) if data is None else data, final, now)


def test_in_order_complete():
    assert acc(0).kind == "ack" and acc(1).ack_seq == 1
    out = acc(2, final=True)
    assert out.kind == "complete" and out.ack_seq == 2
    assert vc.take_pcm(out.rec) == chunk(0) + chunk(1) + chunk(2)


def test_out_of_order_is_held_then_joined():
    assert acc(2).ack_seq == -1
    assert acc(1).ack_seq == -1
    out = acc(0)
    assert out.ack_seq == 2 and out.kind == "ack"
    out = acc(3, final=True)
    assert vc.take_pcm(out.rec) == b"".join(chunk(i) for i in range(4))


def test_final_first_then_missing_seq_completes():
    assert acc(0).kind == "ack"
    out = acc(3, final=True)
    assert out.kind == "ack" and out.ack_seq == 0          # 빈 seq 1·2가 있어 보류
    assert acc(2).kind == "ack"
    done = acc(1)
    assert done.kind == "complete" and vc.take_pcm(done.rec) == b"".join(chunk(i) for i in range(4))


def test_duplicate_seq_ignored_with_ack_and_conflict_rejected():
    acc(0); acc(1)
    again = acc(0)
    assert again.kind == "ack" and again.ack_seq == 1
    with pytest.raises(vc.ChunkError) as e:
        acc(0, data=b"\x09\x00" * 100)
    assert e.value.status == 409 and e.value.code == "seq_conflict"


def test_duplicate_pending_seq_ignored():
    acc(3); acc(3)
    rec = vc._recordings[("o", "v1")]
    assert rec.total_bytes == 200 and rec.pending_bytes == 200


@pytest.mark.parametrize("raw", ["", "-1", "1.5", "abc", " 1", "1e3", "٣", "9999999", "4000"])
def test_bad_seq(raw):
    with pytest.raises(vc.ChunkError) as e:
        vc.parse_seq(raw)
    assert e.value.status == 400


def test_odd_empty_and_big_chunks():
    with pytest.raises(vc.ChunkError) as e:
        acc(0, data=b"\x01\x00\x02")
    assert e.value.code == "odd_length"
    with pytest.raises(vc.ChunkError) as e:
        acc(0, data=b"")
    assert e.value.code == "empty_chunk"
    with pytest.raises(vc.ChunkError) as e:
        acc(0, data=b"\x00\x00" * (vc.MAX_CHUNK_BYTES // 2 + 1))
    assert e.value.status == 413
    # 검증 실패는 녹음을 만들지 않는다
    assert not vc._recordings


def test_total_cap_is_strict_and_discards():
    big = b"\x01\x00" * (vc.MAX_CHUNK_BYTES // 2)
    n = vc.MAX_TOTAL_BYTES // vc.MAX_CHUNK_BYTES
    for i in range(n):
        acc(i, data=big)
    with pytest.raises(vc.ChunkError) as e:
        acc(n, data=b"\x01\x00")
    assert e.value.status == 413 and e.value.code == "too_long"
    assert not vc._recordings
    with pytest.raises(vc.ChunkError) as e:                 # 폐기된 id는 새 녹음으로 되살아나지 않는다
        acc(n + 1)
    assert e.value.status == 410


def test_exactly_cap_is_accepted():
    big = b"\x01\x00" * (vc.MAX_CHUNK_BYTES // 2)
    n = vc.MAX_TOTAL_BYTES // vc.MAX_CHUNK_BYTES
    for i in range(n - 1):
        acc(i, data=big)
    out = acc(n - 1, data=big, final=True)
    assert out.kind == "complete" and len(vc.take_pcm(out.rec)) == vc.MAX_TOTAL_BYTES


def test_pending_limits():
    for i in range(1, vc.MAX_PENDING_CHUNKS + 1):
        acc(i, data=b"\x01\x00")
    with pytest.raises(vc.ChunkError) as e:
        acc(vc.MAX_PENDING_CHUNKS + 1, data=b"\x01\x00")
    assert e.value.code == "too_many_pending"
    vc.reset()
    with pytest.raises(vc.ChunkError) as e:                 # 너무 먼 seq
        acc(vc.MAX_PENDING_CHUNKS + 5, data=b"\x01\x00")
    assert e.value.code == "too_many_pending"
    vc.reset()
    big = b"\x01\x00" * (vc.MAX_CHUNK_BYTES // 2)
    with pytest.raises(vc.ChunkError):                      # 30초분 보류 초과(5초 x 7)
        for i in range(1, 8):
            acc(i, data=big)


def test_chunk_after_final_and_conflicting_finals():
    acc(0); acc(1, final=True)                              # 완결
    vc.reset()
    acc(0); acc(2)
    with pytest.raises(vc.ChunkError) as e:
        acc(1, final=True)                                  # 이미 seq 2가 있다
    assert e.value.code == "final_conflict"
    vc.reset()
    acc(0); acc(5, final=True)
    with pytest.raises(vc.ChunkError) as e:
        acc(6)
    assert e.value.code == "after_final"
    vc.reset()
    acc(0); acc(5, final=True)
    with pytest.raises(vc.ChunkError) as e:
        acc(4, final=True)
    assert e.value.code == "final_conflict"


def test_empty_final_marker_ok_but_empty_recording_rejected():
    acc(0)
    out = acc(1, data=b"", final=True)
    assert out.kind == "complete" and vc.take_pcm(out.rec) == chunk(0)
    vc.reset()
    with pytest.raises(vc.ChunkError) as e:
        acc(0, data=b"", final=True)
    assert e.value.code == "empty_recording"


def test_target_mismatch_for_same_recording():
    acc(0)
    with pytest.raises(vc.ChunkError) as e:
        acc(1, token="other")
    assert e.value.status == 409 and e.value.code == "target_mismatch"


def test_owners_are_isolated_and_new_start_replaces_old():
    acc(0, owner="a", vid="v1")
    acc(0, owner="b", vid="v1")                             # 같은 id라도 다른 소유자는 별개
    assert len(vc._recordings) == 2
    acc(0, owner="a", vid="v2")                             # a의 새 녹음 — v1 폐기
    assert ("a", "v1") not in vc._recordings
    with pytest.raises(vc.ChunkError) as e:
        acc(1, owner="a", vid="v1")
    assert e.value.status == 410
    assert acc(1, owner="b", vid="v1").ack_seq == 1


def test_idle_ttl_expires_and_tombstones_late_chunk():
    acc(0, now=1000.0)
    with pytest.raises(vc.ChunkError) as e:
        acc(1, now=1000.0 + vc.IDLE_TTL + 1)
    assert e.value.status == 410 and e.value.code == "voice_expired"
    assert not vc._recordings
    # 묘비도 시간이 지나면 사라진다
    vc.sweep(1000.0 + vc.IDLE_TTL + 1 + vc.DONE_TTL + 1)
    assert not vc._tombstones


def test_sweep_frees_without_requests():
    acc(0, now=50.0); acc(0, owner="z", now=50.0)
    assert vc.sweep(50.0 + vc.IDLE_TTL + 1) == 2
    assert not vc._recordings and not vc._by_owner


def test_cancel_is_idempotent_and_blocks_late_chunks():
    acc(0)
    assert vc.cancel("v1", "o") == "cancelled"
    assert vc.cancel("v1", "o") == "cancelled"
    with pytest.raises(vc.ChunkError) as e:
        acc(1)
    assert e.value.status == 410
    assert vc.cancel("x", "someone-else") == "cancelled"    # 남의 id는 내 소유자 키로만 보인다
    acc(0, owner="o2", vid="v1")                            # 다른 소유자의 같은 id는 영향 없음


def test_cancel_cannot_touch_other_owner():
    acc(0, owner="a")
    vc.cancel("v1", "b")
    assert ("a", "v1") in vc._recordings


def test_recordings_cap():
    for i in range(vc.MAX_RECORDINGS):
        acc(0, owner=f"o{i}")
    with pytest.raises(vc.ChunkError) as e:
        acc(0, owner="extra")
    assert e.value.status == 429


def test_to_wav_header():
    w = wave.open(io.BytesIO(vc.to_wav(chunk(0))))
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 100)


# ── 라우트 ──────────────────────────────────────────────────────



def issue(owner_headers=None):
    owner = voice_targets.principal(Req(headers=owner_headers))
    return voice_targets.issue(owner, {"key": "k", "pane": "%1", "name": "w", "tmux_id": "$1", "generation": "1", "boot": "b"})["token"]


def post(token, seq, data=CH, final=False, vid="vid-1", headers=None, query=None):
    h = {"x-voice-id": vid, "x-voice-target": token, **(headers or {})}
    q = {"seq": str(seq), **({"final": "1"} if final else {}), **(query or {})}
    return asyncio.run(voice.voice_chunk(Req(data, h, q)))


def body(r):
    return json.loads(r.body) if isinstance(r, JSONResponse) else r


@pytest.fixture
def stt(monkeypatch):
    calls = []
    sent = []

    async def fn(audio, input_format="webm", language=None):
        calls.append((audio, input_format))
        return "ls -la", dict(TIMING)
    monkeypatch.setattr(voice_handler, "transcribe_timed", fn)
    monkeypatch.setattr(voice_targets, "valid", lambda t: True)
    monkeypatch.setattr(tmux_target, "send_to_tmux", lambda pane, text: sent.append((pane, text)) or True)
    return calls, sent


def test_route_roundtrip_out_of_order_and_retransmit(stt):
    calls, sent = stt
    tok = issue()
    assert body(post(tok, 0)) == {"ackSeq": 0, "complete": False}
    assert body(post(tok, 0)) == {"ackSeq": 0, "complete": False}          # 응답 유실 후 재전송
    assert body(post(tok, 2, chunk(2), final=True)) == {"ackSeq": 0, "complete": False}
    r = body(post(tok, 1, chunk(1)))
    assert r["complete"] is True and r["text"] == "ls -la" and r["injected"] is True and r["ackSeq"] == 2
    assert len(calls) == 1 and calls[0][1] == "wav"
    w = wave.open(io.BytesIO(calls[0][0]))
    assert w.readframes(w.getnframes()) == CH + chunk(1) + chunk(2)
    assert sent == [("%1", "ls -la")]
    # 완료 뒤 final 재전송 — 다시 입력하지 않고 같은 결과
    r2 = body(post(tok, 2, chunk(2), final=True))
    assert r2["duplicate"] is True and r2["text"] == "ls -la"
    assert len(calls) == 1 and len(sent) == 1
    assert all(r.state == "done" and r.parts == [] and not r.pending for r in vc._recordings.values())   # 조각 메모리는 놓았다


def test_route_final_in_single_request(stt):
    calls, sent = stt
    tok = issue()
    r = body(post(tok, 0, final=True))
    assert r["complete"] is True and r["ackSeq"] == 0 and len(calls) == 1


def test_route_validation_errors(stt):
    tok = issue()
    def code(r): return (r.status_code, body(r)["code"])
    assert code(post(tok, 0, vid="bad id!")) == (400, "bad_voice_id")
    assert code(post("", 0)) == (400, "no_target")
    assert code(post(tok, "x")) == (400, "bad_seq")
    assert code(post(tok, "-1")) == (400, "bad_seq")
    assert code(post(tok, 0, data=b"\x01\x00\x02")) == (400, "odd_length")
    assert code(post(tok, 0, data=b"")) == (400, "empty_chunk")
    assert code(post(tok, 0, query={"final": "yes"})) == (400, "bad_final")
    assert code(post(tok, 0, data=b"\x00\x00" * (vc.MAX_CHUNK_BYTES // 2 + 1))) == (413, "chunk_too_large")
    assert code(post(tok, 0, headers={"content-length": str(vc.MAX_CHUNK_BYTES + 2)})) == (413, "chunk_too_large")
    assert code(post("nope", 0)) == (409, "target_expired")


def test_route_foreign_principal_cannot_use_token(stt):
    tok = issue()
    r = post(tok, 0, headers={"authorization": "Bearer tok-b"})
    assert r.status_code == 409 and not vc._recordings


def test_route_expired_target_token(stt, monkeypatch):
    tok = issue()
    voice_targets._targets[tok]["expires"] = 0
    assert post(tok, 0).status_code == 409 and not vc._recordings


def test_route_stt_busy_keeps_chunks_for_retry(monkeypatch, stt):
    tok = issue()
    async def busy(*a, **k):
        raise stt_service.Busy("밀림")
    monkeypatch.setattr(voice_handler, "transcribe_timed", busy)
    post(tok, 0)
    r = post(tok, 1, chunk(1), final=True)
    assert r.status_code == 429
    calls = []
    async def ok(audio, input_format="webm", language=None):
        calls.append(audio); return "ok", dict(TIMING)
    monkeypatch.setattr(voice_handler, "transcribe_timed", ok)
    r = body(post(tok, 1, chunk(1), final=True))                      # 같은 final 재전송
    assert r["text"] == "ok" and len(calls) == 1
    w = wave.open(io.BytesIO(calls[0]))
    assert w.readframes(w.getnframes()) == CH + chunk(1)


def test_route_concurrent_final_joins_inflight(monkeypatch, stt):
    calls, sent = stt
    tok = issue()
    gate = {}
    async def slow(audio, input_format="webm", language=None):
        calls.append(audio)
        await gate["e"].wait()
        return "once", dict(TIMING)
    monkeypatch.setattr(voice_handler, "transcribe_timed", slow)
    async def go():
        gate["e"] = asyncio.Event()
        await voice.voice_chunk(Req(CH, {"x-voice-id": "vid-1", "x-voice-target": tok}, {"seq": "0"}))
        mk = lambda: Req(chunk(1), {"x-voice-id": "vid-1", "x-voice-target": tok}, {"seq": "1", "final": "1"})
        t1 = asyncio.create_task(voice.voice_chunk(mk()))
        await asyncio.sleep(0.05)
        t2 = asyncio.create_task(voice.voice_chunk(mk()))
        await asyncio.sleep(0.05)
        gate["e"].set()
        return await t1, await t2
    a, b = asyncio.run(go())
    assert a["text"] == b["text"] == "once" and b["duplicate"] is True and "duplicate" not in a
    assert len(calls) == 1 and len(sent) == 1


def test_route_cancel(stt):
    tok = issue()
    post(tok, 0)
    r = asyncio.run(voice.voice_chunk_cancel(Req(b"", {"x-voice-id": "vid-1"})))
    assert r == {"cancelled": True, "state": "cancelled"}
    assert post(tok, 1).status_code == 410
    r = asyncio.run(voice.voice_chunk_cancel(Req(json.dumps({"voice_id": "vid-1"}).encode())))
    assert r["cancelled"] is True                                  # 멱등
    bad = asyncio.run(voice.voice_chunk_cancel(Req(b"{}")))
    assert bad.status_code == 400


def test_route_legacy_input_unchanged(stt):
    calls, sent = stt
    r = asyncio.run(voice.voice_input(Req(b"abc", {"content-type": "audio/mp4"}, {"session_id": "none"})))
    assert r["text"] == "ls -la" and calls[0] == (b"abc", "mp4")
