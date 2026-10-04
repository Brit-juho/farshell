"""voice_handler — 입력 오디오 디코딩·무음 판정·절단·transcribe_timed. ffmpeg가 없으면 디코딩 테스트는 건너뛴다."""
import array
import asyncio
import io
import math
import shutil
import subprocess
import wave

import pytest

import stt_service
import voice_handler
from routes import voice as routes_voice
from stt_engines.base import NotReady

RATE = 16000


def make_wav(samples, rate=RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(array.array("h", samples).tobytes())
    return buf.getvalue()


def tone(sec, amp=8000, freq=440, rate=RATE):
    return [int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(int(sec * rate))]


def wav_info(b: bytes):
    with wave.open(io.BytesIO(b)) as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()


# ── 디코딩 (ffmpeg로 샘플 인코딩) ──────────────────────────────────

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")


def encode(tmp_path, wav: bytes, ext: str, codec_args: list, rate=None, ch=2) -> bytes:
    rate = rate or (48000 if "libopus" in codec_args else 44100)       # opus는 44.1k를 못 받는다(브라우저 녹음은 48k)
    src = tmp_path / "src.wav"
    src.write_bytes(wav)
    dst = tmp_path / f"out.{ext}"
    r = subprocess.run(["ffmpeg", "-y", "-i", str(src), "-ar", str(rate), "-ac", str(ch), *codec_args, str(dst)],
                       capture_output=True, timeout=30)
    if r.returncode != 0 or not dst.exists():
        pytest.skip(f"ffmpeg가 {ext} 인코딩을 못 한다: {r.stderr[-120:]!r}")
    return dst.read_bytes()


FORMATS = [
    ("webm", "webm", ["-c:a", "libopus"]),
    ("ogg", "ogg", ["-c:a", "libopus"]),
    ("mp4", "mp4", ["-c:a", "aac"]),
    ("mp4", "m4a", ["-c:a", "aac"]),
    ("mp3", "mp3", ["-c:a", "libmp3lame"]),
]


@needs_ffmpeg
@pytest.mark.parametrize("fmt,ext,args", FORMATS)
def test_convert_to_16k_mono_int16(tmp_path, fmt, ext, args):
    src = make_wav(tone(2.0))
    data = encode(tmp_path, src, ext, args)
    out = voice_handler._convert_to_wav(data, fmt)
    ch, width, rate, frames = wav_info(out)
    assert (ch, width, rate) == (1, 2, 16000)
    assert frames / 16000 > 2.0 - 0.3                            # 잘리지 않았다(길이 정밀 검사는 아래 테스트)
    assert not voice_handler._is_silent_wav(out)


@needs_ffmpeg
@pytest.mark.parametrize("fmt,ext,args", FORMATS[2:])
def test_convert_duration_exact_for_unaligned_frames(tmp_path, fmt, ext, args):
    out = voice_handler._convert_to_wav(encode(tmp_path, make_wav(tone(2.0)), ext, args), fmt)
    assert abs(wav_info(out)[3] / 16000 - 2.0) < 0.3


@needs_ffmpeg
def test_convert_wav_other_rate_resampled(tmp_path):
    src = make_wav(tone(1.0, rate=44100), rate=44100)
    out = voice_handler._convert_to_wav(src, "wav")
    assert wav_info(out)[:3] == (1, 2, 16000)
    assert wav_info(out)[3] / 16000 >= 1.0


@needs_ffmpeg
@pytest.mark.parametrize("fmt,ext,args", FORMATS[:3])
def test_convert_ffmpeg_fallback_when_pyav_fails(tmp_path, monkeypatch, fmt, ext, args):
    data = encode(tmp_path, make_wav(tone(1.5)), ext, args)
    monkeypatch.setattr(voice_handler, "_convert_to_wav_pyav", lambda b, f: None)
    out = voice_handler._convert_to_wav(data, fmt)
    ch, width, rate, frames = wav_info(out)
    assert (ch, width, rate) == (1, 2, 16000) and abs(frames / 16000 - 1.5) < 0.3


@needs_ffmpeg
def test_ffmpeg_fallback_output_is_detected_as_speech(tmp_path, monkeypatch):
    """ffmpeg가 만든 WAV는 헤더에 LIST 청크가 붙어 44바이트가 아닐 수 있다 — 그래도 말소리가 무음으로 오판되면 안 된다."""
    data = encode(tmp_path, make_wav(tone(1.5, amp=9000)), "webm", ["-c:a", "libopus"])
    monkeypatch.setattr(voice_handler, "_convert_to_wav_pyav", lambda b, f: None)
    out = voice_handler._convert_to_wav(data, "webm")
    assert not voice_handler._is_silent_wav(out)


def test_wav_passthrough_when_already_target():
    src = make_wav(tone(0.5))
    assert voice_handler._convert_to_wav(src, "wav") is src


def test_convert_rejects_unknown_format():
    with pytest.raises(ValueError):
        voice_handler._convert_to_wav(b"x", "exe")


# ── Content-Type → 형식 ────────────────────────────────────────────

@pytest.mark.parametrize("ct,fmt", [
    ("audio/webm", "webm"), ("audio/webm;codecs=opus", "webm"), ("AUDIO/WEBM", "webm"),
    ("audio/wav", "wav"), ("audio/x-wav", "wav"), ("audio/wave", "wav"),
    ("audio/ogg", "ogg"), ("audio/ogg; codecs=opus", "ogg"),
    ("audio/mp4", "mp4"), ("audio/x-m4a", "mp4"), ("audio/m4a", "mp4"), ("audio/aac", "mp4"),
    ("audio/mp4;codecs=mp4a.40.2", "mp4"),
    ("audio/mpeg", "mp3"), ("audio/mp3", "mp3"),
    ("application/octet-stream", "webm"), ("", "webm"), ("text/plain", "webm"),
])
def test_audio_format_mapping(ct, fmt):
    assert routes_voice._audio_format(ct) == fmt
    assert fmt in voice_handler.ALLOWED_AUDIO_FORMATS


# ── 무음 판정 ──────────────────────────────────────────────────────

def test_silent_wav_cases():
    assert voice_handler._is_silent_wav(b"") and voice_handler._is_silent_wav(b"x" * 44)
    assert voice_handler._is_silent_wav(make_wav([0] * RATE))
    assert voice_handler._is_silent_wav(make_wav([0, 3] * 8000))
    assert not voice_handler._is_silent_wav(make_wav(tone(1.0)))


def test_small_noise_is_silent():
    noise = [(i * 7919) % 201 - 100 for i in range(RATE * 2)]          # 평균 절대값 약 50 ≪ 600
    assert voice_handler._is_silent_wav(make_wav(noise))


def test_leading_silence_then_speech_is_not_silent():
    """회귀 방지: 말하기 전 3초 무음이 있어도(예전엔 앞 2초만 봐서 버렸다) 뒤의 말소리는 무음이 아니다."""
    wav = make_wav([0] * (RATE * 3) + tone(2.0, amp=9000))
    assert not voice_handler._is_silent_wav(wav)


def test_very_short_speech_after_long_silence_detected():
    wav = make_wav([0] * (RATE * 3) + tone(1.5, amp=20000))
    assert not voice_handler._is_silent_wav(wav)


# ── 절단 ───────────────────────────────────────────────────────────

def test_truncate_wav():
    wav = make_wav(tone(3.0))
    out = voice_handler._truncate_wav(wav, 1.0)
    assert wav_info(out) == (1, 2, 16000, 16000)
    assert out[44:] == wav[44:44 + 32000]                                # 앞부분을 남긴다
    assert voice_handler._truncate_wav(wav, 5.0) is wav                  # 상한 이하면 원본
    assert voice_handler._truncate_wav(wav, 3.0) is wav
    assert voice_handler._truncate_wav(wav, 0) is wav                    # 0=무제한
    assert voice_handler._truncate_wav(wav, -1) is wav
    tiny = b"x" * 44
    assert voice_handler._truncate_wav(tiny, 1.0) is tiny


# ── transcribe_timed ───────────────────────────────────────────────

@pytest.fixture
def svc(monkeypatch):
    calls = []

    def fake(wav, lang):
        calls.append((len(wav), lang))
        return "안녕", {"wait_ms": 1, "load_ms": 2, "stt_ms": 3}
    monkeypatch.setattr(stt_service, "transcribe_wav", fake)
    monkeypatch.delenv("VT_STT_LANG", raising=False)
    return calls


def run(coro):
    return asyncio.run(coro)


def test_timed_silent_skips_engine(svc):
    text, t = run(voice_handler.transcribe_timed(make_wav([0] * RATE), "wav"))
    assert text == "" and svc == []
    assert set(t) == {"decode_ms", "wait_ms", "load_ms", "stt_ms", "total_ms"}
    assert t["wait_ms"] == t["load_ms"] == t["stt_ms"] == 0


def test_timed_speech_calls_engine_with_timing(svc):
    text, t = run(voice_handler.transcribe_timed(make_wav(tone(1.0)), "wav", "ko"))
    assert text == "안녕" and svc == [(44 + 32000, "ko")]
    assert set(t) == {"decode_ms", "wait_ms", "load_ms", "stt_ms", "total_ms"}
    assert (t["wait_ms"], t["load_ms"], t["stt_ms"]) == (1, 2, 3) and t["total_ms"] >= t["decode_ms"] >= 0


def test_timed_lang_from_env(svc, monkeypatch):
    monkeypatch.setenv("VT_STT_LANG", "en")
    run(voice_handler.transcribe_timed(make_wav(tone(1.0)), "wav"))
    run(voice_handler.transcribe_timed(make_wav(tone(1.0)), "wav", "ko"))
    assert [c[1] for c in svc] == ["en", "ko"]


def test_timed_truncates_long_audio(svc, monkeypatch):
    monkeypatch.setattr(voice_handler, "MAX_STT_SECONDS", 1.0)
    run(voice_handler.transcribe_timed(make_wav(tone(3.0)), "wav"))
    assert svc == [(44 + 32000, None)]


@pytest.mark.parametrize("exc", [stt_service.Busy("밀림"), NotReady("모델을 받아 주세요")], ids=["busy", "not_ready"])
def test_timed_engine_errors_propagate(monkeypatch, exc):
    def boom(wav, lang):
        raise exc
    monkeypatch.setattr(stt_service, "transcribe_wav", boom)
    with pytest.raises(type(exc)):
        run(voice_handler.transcribe_timed(make_wav(tone(1.0)), "wav"))


def test_timed_timeout_is_runtime_error(monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(stt_service, "transcribe_wav", lambda w, l: (gate.wait(3), ("x", {}))[1])
    monkeypatch.setattr(voice_handler, "STT_TOTAL_TIMEOUT", 0.05)
    try:
        with pytest.raises(RuntimeError, match="초과"):
            run(voice_handler.transcribe_timed(make_wav(tone(1.0)), "wav"))
    finally:
        gate.set()


def test_transcribe_wrapper_returns_text_only(svc):
    assert run(voice_handler.transcribe(make_wav(tone(1.0)), "wav")) == "안녕"
