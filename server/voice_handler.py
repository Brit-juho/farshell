"""Voice Handler — STT (Whisper) + TTS (Kokoro / Edge TTS).

STT 우선순위: mlx-whisper (Apple Silicon) → faster-whisper → 에러
TTS 우선순위: Kokoro → edge-tts → macOS say fallback
음성 입력: webm/opus → ffmpeg으로 WAV 변환 → Whisper
"""

import asyncio
import io
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# STT Engine
# ---------------------------------------------------------------------------

_stt_engine: Optional[str] = None
_whisper_model = None


def _init_stt() -> str:
    """사용 가능한 STT 엔진을 감지하고 모델을 로드한다."""
    global _stt_engine, _whisper_model

    if _stt_engine is not None:
        return _stt_engine

    # 1) mlx-whisper (Apple Silicon, 가장 빠름)
    try:
        import mlx_whisper  # noqa: F401
        _stt_engine = "mlx-whisper"
        logger.info("STT engine: mlx-whisper")
        return _stt_engine
    except ImportError:
        pass

    # 2) faster-whisper
    try:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel("base", compute_type="int8")
        _stt_engine = "faster-whisper"
        logger.info("STT engine: faster-whisper")
        return _stt_engine
    except ImportError:
        pass

    _stt_engine = "none"
    logger.warning("No STT engine available. Install mlx-whisper or faster-whisper.")
    return _stt_engine


ALLOWED_AUDIO_FORMATS = {"webm", "wav", "ogg", "mp3", "m4a"}

# 입력이 이미 16kHz mono int16 WAV인지 빠르게 판별 (ffmpeg/pyav 우회)
def _is_already_target_wav(audio_bytes: bytes) -> bool:
    if len(audio_bytes) < 44 or audio_bytes[:4] != b"RIFF" or audio_bytes[8:12] != b"WAVE":
        return False
    try:
        # fmt 청크: 24~28 sample rate, 22~24 channels, 34~36 bit depth
        import struct as _s
        chans = _s.unpack("<H", audio_bytes[22:24])[0]
        rate = _s.unpack("<I", audio_bytes[24:28])[0]
        bits = _s.unpack("<H", audio_bytes[34:36])[0]
        return chans == 1 and rate == 16000 and bits == 16
    except Exception:
        return False


def _convert_to_wav_pyav(audio_bytes: bytes, input_format: str) -> Optional[bytes]:
    """Phase 9 #10: pyav로 in-process 디코딩 (ffmpeg subprocess 회피).

    실패 시 None을 반환해 호출자가 ffmpeg fallback을 쓰도록 한다.
    """
    try:
        import av  # type: ignore
        import io as _io
        import wave as _wave
    except ImportError:
        return None

    try:
        container = av.open(_io.BytesIO(audio_bytes), format=input_format)
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        pcm = bytearray()
        for frame in container.decode(stream):
            for f in resampler.resample(frame):
                pcm.extend(bytes(f.planes[0]))
        # resampler flush
        for f in resampler.resample(None):
            pcm.extend(bytes(f.planes[0]))
        container.close()

        buf = _io.BytesIO()
        with _wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(bytes(pcm))
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"pyav decode failed, falling back to ffmpeg: {e}")
        return None


def _convert_to_wav_ffmpeg(audio_bytes: bytes, input_format: str) -> bytes:
    """ffmpeg subprocess fallback."""
    with tempfile.NamedTemporaryFile(suffix=f".{input_format}", delete=False) as src:
        src.write(audio_bytes)
        src_path = src.name
    dst_path = src_path.rsplit(".", 1)[0] + ".wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src_path,
             "-ar", "16000", "-ac", "1", "-f", "wav", dst_path],
            capture_output=True, timeout=10,
        )
        return Path(dst_path).read_bytes()
    finally:
        Path(src_path).unlink(missing_ok=True)
        Path(dst_path).unlink(missing_ok=True)


def _convert_to_wav(audio_bytes: bytes, input_format: str = "webm") -> bytes:
    """입력 오디오를 16kHz mono int16 WAV로 변환.

    우선순위: (0) 이미 target wav면 그대로 → (1) pyav in-process → (2) ffmpeg subprocess.
    """
    # [H4] 허용 포맷 검증
    if input_format not in ALLOWED_AUDIO_FORMATS:
        raise ValueError(f"Unsupported audio format: {input_format!r}")

    # (0) wav 패스스루 — local_mic이 만든 wav는 이미 16kHz mono int16
    if input_format == "wav" and _is_already_target_wav(audio_bytes):
        return audio_bytes

    # (1) pyav 시도
    out = _convert_to_wav_pyav(audio_bytes, input_format)
    if out is not None:
        return out

    # (2) ffmpeg fallback
    return _convert_to_wav_ffmpeg(audio_bytes, input_format)


STT_TIMEOUT = 30  # seconds

# 무음/저에너지 입력은 Whisper가 "You?" / "Thanks for watching" 등 환각을 내기 쉽다.
# WAV 16-bit PCM 평균 절대값 임계값(0~32767). 600은 약 -34dBFS — 일반 발화 대비 충분히 낮다.
SILENCE_RMS_THRESHOLD = 600


def _is_silent_wav(wav_bytes: bytes) -> bool:
    """WAV 헤더(44바이트) 이후를 16-bit signed PCM으로 보고 평균 절대값 측정."""
    if len(wav_bytes) <= 44:
        return True
    try:
        import struct as _struct
        pcm = wav_bytes[44:]
        n = len(pcm) // 2
        if n == 0:
            return True
        # 너무 길면 앞 32K 샘플(약 2초@16kHz)만 검사 — 충분
        sample_n = min(n, 32000)
        samples = _struct.unpack(f"<{sample_n}h", pcm[: sample_n * 2])
        avg_abs = sum(abs(s) for s in samples) / sample_n
        return avg_abs < SILENCE_RMS_THRESHOLD
    except Exception:
        return False


async def transcribe(audio_bytes: bytes, input_format: str = "webm",
                     language: Optional[str] = None) -> str:
    """음성 바이트 → 텍스트. webm/opus를 WAV로 변환 후 Whisper 실행.

    language=None 이면 자동 감지 (Whisper가 한/영/일 등 자동 판별).
    language="ko"/"en" 등으로 명시 지정 시 해당 언어로 고정.
    환경변수 VT_STT_LANG=ko 로 기본값 오버라이드 가능.
    """
    engine = _init_stt()
    if engine == "none":
        raise RuntimeError("STT engine not available")

    # 언어 결정: 명시 인자 > 환경변수 > 자동(None)
    import os as _os
    lang = language if language else _os.environ.get("VT_STT_LANG", "").strip() or None

    loop = asyncio.get_running_loop()
    wav_bytes = await loop.run_in_executor(None, _convert_to_wav, audio_bytes, input_format)

    # 무음 차단 — Whisper hallucination 방지 (TEST_REPORT.md Bug #8).
    if _is_silent_wav(wav_bytes):
        logger.info("STT: silent input, skipping transcription")
        return ""

    if engine == "mlx-whisper":
        import mlx_whisper

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name
        try:
            def _run():
                kwargs = {"language": lang} if lang else {}
                return mlx_whisper.transcribe(tmp_path, **kwargs)
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=STT_TIMEOUT,
            )
            if not lang and result.get("language"):
                logger.debug(f"STT detected language: {result['language']}")
            return result.get("text", "").strip()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    elif engine == "faster-whisper":
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(wav_bytes)
            tmp_path = tmp.name
        try:
            def _run():
                kwargs = {"language": lang} if lang else {}
                return _whisper_model.transcribe(tmp_path, **kwargs)
            segments, info = await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=STT_TIMEOUT,
            )
            if not lang and hasattr(info, "language"):
                logger.debug(f"STT detected language: {info.language}")
            text = "".join(seg.text for seg in segments).strip()
            return text
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    return ""


# ---------------------------------------------------------------------------
# TTS Engine
# ---------------------------------------------------------------------------

_tts_engine: Optional[str] = None


def _init_tts() -> str:
    """사용 가능한 TTS 엔진을 감지."""
    global _tts_engine

    if _tts_engine is not None:
        return _tts_engine

    # 1) Kokoro
    try:
        import kokoro  # noqa: F401
        _tts_engine = "kokoro"
        logger.info("TTS engine: kokoro")
        return _tts_engine
    except ImportError:
        pass

    # 2) edge-tts
    try:
        import edge_tts  # noqa: F401
        _tts_engine = "edge-tts"
        logger.info("TTS engine: edge-tts")
        return _tts_engine
    except ImportError:
        pass

    # 3) macOS say fallback
    import shutil
    if shutil.which("say"):
        _tts_engine = "macos-say"
        logger.info("TTS engine: macOS say (fallback)")
        return _tts_engine

    _tts_engine = "none"
    logger.warning("No TTS engine available.")
    return _tts_engine


async def synthesize(text: str, voice: str = "ko-KR-SunHiNeural") -> bytes:
    """텍스트 → 음성 바이트 (MP3 또는 WAV)."""
    engine = _init_tts()

    if engine == "kokoro":
        import kokoro
        loop = asyncio.get_running_loop()
        audio = await loop.run_in_executor(None, lambda: kokoro.synthesize(text))
        return audio

    elif engine == "edge-tts":
        import edge_tts
        try:
            communicate = edge_tts.Communicate(text, voice)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()
        except Exception as e:
            logger.warning(f"edge-tts failed: {e}, falling back to macOS say")
            return await _macos_say(text)

    elif engine == "macos-say":
        return await _macos_say(text)

    raise RuntimeError("TTS engine not available")


async def _macos_say(text: str) -> bytes:
    """macOS say 명령으로 TTS."""
    loop = asyncio.get_running_loop()

    def _say():
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run(
                ["say", "-v", "Yuna", "-o", tmp_path, text],
                capture_output=True,
                timeout=30,
            )
            return Path(tmp_path).read_bytes()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    return await loop.run_in_executor(None, _say)
