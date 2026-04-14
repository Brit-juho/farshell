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


def _convert_to_wav(audio_bytes: bytes, input_format: str = "webm") -> bytes:
    """ffmpeg으로 입력 오디오를 16kHz mono WAV로 변환."""
    # [H4] 허용 포맷 검증
    if input_format not in ALLOWED_AUDIO_FORMATS:
        raise ValueError(f"Unsupported audio format: {input_format!r}")
    with tempfile.NamedTemporaryFile(suffix=f".{input_format}", delete=False) as src:
        src.write(audio_bytes)
        src_path = src.name

    dst_path = src_path.rsplit(".", 1)[0] + ".wav"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", src_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", dst_path,
            ],
            capture_output=True,
            timeout=10,
        )
        return Path(dst_path).read_bytes()
    finally:
        Path(src_path).unlink(missing_ok=True)
        Path(dst_path).unlink(missing_ok=True)


STT_TIMEOUT = 30  # seconds


async def transcribe(audio_bytes: bytes, input_format: str = "webm",
                     language: Optional[str] = None) -> str:
    """음성 바이트 → 텍스트. webm/opus를 WAV로 변환 후 Whisper 실행.

    language=None 이면 자동 감지 (Whisper가 한/영/일 등 자동 판별).
    language="ko"/"en" 등으로 명시 지정 시 해당 언어로 고정.
    환경변수 RALPH_STT_LANG=ko 로 기본값 오버라이드 가능.
    """
    engine = _init_stt()
    if engine == "none":
        raise RuntimeError("STT engine not available")

    # 언어 결정: 명시 인자 > 환경변수 > 자동(None)
    import os as _os
    lang = language if language else _os.environ.get("RALPH_STT_LANG", "").strip() or None

    loop = asyncio.get_running_loop()
    wav_bytes = await loop.run_in_executor(None, _convert_to_wav, audio_bytes, input_format)

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
