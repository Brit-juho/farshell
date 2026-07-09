"""Voice Handler — STT (Whisper) + TTS (Kokoro / Edge TTS).

STT 우선순위: mlx-whisper (Apple Silicon) → faster-whisper → 에러
TTS 우선순위: Kokoro → edge-tts → macOS say fallback
음성 입력: webm/opus → ffmpeg으로 WAV 변환 → Whisper
"""

import asyncio
import io
import logging
import os
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# STT Engine
# ---------------------------------------------------------------------------

_stt_engine: Optional[str] = None
_whisper_model = None
# 마지막 STT 사용 시각(monotonic). idle 언로드 판단용.
_last_stt_use: float = 0.0
# STT 모델을 마지막 사용 후 이 시간(초) 지나면 언로드. 기본 0(끔) — macOS에선 파이썬
# 객체를 해제해도 CTranslate2 네이티브 메모리가 OS로 반환되지 않아(malloc arena) RSS가
# 안 줄어든다(실측). 진짜 회수는 '애초에 안 켜면 안 로드'로 해결(capabilities가 더는
# 모델을 로드하지 않음). 이 옵션은 Linux/서브프로세스 워커 등에서만 의미. VT_STT_IDLE_SEC로 opt-in.
STT_IDLE_UNLOAD_SEC = float(os.environ.get("VT_STT_IDLE_SEC", "0"))


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


def stt_loaded() -> bool:
    """STT 모델이 현재 메모리에 상주 중인지."""
    return _stt_engine not in (None, "none")


def _module_available(*names: str) -> bool:
    """모듈을 import(=모델 로드) 하지 않고 설치 여부만 확인."""
    import importlib.util
    for n in names:
        try:
            if importlib.util.find_spec(n) is not None:
                return True
        except Exception:
            continue
    return False


def stt_available() -> bool:
    """STT '설치 여부'만 확인 — 모델을 로드하지 않는다.

    ⚠️ _init_stt()는 faster-whisper 모델을 로드(~400MB)하므로, /api/capabilities처럼
    페이지 로드마다 불리는 경로에서 쓰면 터미널만 쓰는 사용자도 400MB를 물게 된다.
    설치 여부 체크는 반드시 이 함수(find_spec, 로드 없음)를 쓸 것.
    """
    return _module_available("mlx_whisper", "faster_whisper")


def tts_available() -> bool:
    """TTS '설치 여부'만 확인 — 무거운 import(kokoro→torch 등)를 피한다."""
    if _module_available("kokoro", "edge_tts"):
        return True
    import shutil
    return shutil.which("say") is not None  # macOS say fallback


def preload_stt() -> str:
    """음성 모드 진입 시 미리 로드해 첫 STT 지연을 없앤다."""
    return _init_stt()


def unload_stt() -> bool:
    """STT 모델을 메모리에서 내린다. 다음 transcribe 때 lazy 재로드된다.

    idle 타임아웃(STT_IDLE_UNLOAD_SEC)이 STT_TIMEOUT(30s)보다 훨씬 크므로, 언로드 시점엔
    진행 중인 transcribe가 없다(마지막 사용이 수 분 전). 따라서 별도 락 없이 안전하다.
    """
    global _stt_engine, _whisper_model
    if _stt_engine in (None, "none"):
        return False
    _whisper_model = None            # faster-whisper 모델 해제
    _stt_engine = None               # 다음 호출 시 _init_stt 재실행
    try:
        # mlx-whisper는 load_models.load_model이 lru_cache라 별도 해제 필요.
        import mlx_whisper
        mlx_whisper.load_models.load_model.cache_clear()
    except Exception:
        pass
    import gc
    gc.collect()
    logger.info("STT 모델 언로드 — 메모리 회수")
    return True


async def stt_idle_monitor() -> None:
    """마지막 STT 사용 후 STT_IDLE_UNLOAD_SEC 지나면 모델을 언로드하는 백그라운드 루프."""
    if STT_IDLE_UNLOAD_SEC <= 0:
        return
    while True:
        await asyncio.sleep(30)
        if stt_loaded() and _last_stt_use > 0 and \
                time.monotonic() - _last_stt_use > STT_IDLE_UNLOAD_SEC:
            unload_stt()


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

# faster-whisper/CTranslate2는 긴 오디오를 처리하면 RSS가 급증하고 그 네이티브 메모리를
# OS로 반환하지 않는다(실측: 60s 오디오 1회 → +800MB, 안 줄어듦). 터미널 음성 명령은
# 길어야 수 초이므로, transcribe 직전에 이 상한으로 잘라 메모리 폭주를 원천 차단한다.
# 모바일(/voice/input)·로컬믹·데몬 모든 경로가 이 지점을 통과한다. VT_STT_MAX_SEC로 조정(0=무제한).
try:
    MAX_STT_SECONDS = float(os.environ.get("VT_STT_MAX_SEC", "60"))
except ValueError:
    MAX_STT_SECONDS = 60.0


def _truncate_wav(wav_bytes: bytes, max_sec: float) -> bytes:
    """16kHz mono int16 WAV을 앞에서 max_sec초까지만 남기고 자른다.

    _is_already_target_wav를 통과한(16k/mono/16bit) 입력에만 쓴다. 상한 이하면 원본 그대로.
    """
    if max_sec <= 0 or len(wav_bytes) <= 44:
        return wav_bytes
    max_pcm = int(max_sec * 16000) * 2  # samples * 2바이트(int16)
    if len(wav_bytes) - 44 <= max_pcm:
        return wav_bytes
    pcm = wav_bytes[44:44 + max_pcm]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    logger.warning(
        f"STT 입력 {(len(wav_bytes)-44)/32000:.0f}s → {max_sec:.0f}s로 절단 (메모리 폭주 방어)"
    )
    return buf.getvalue()

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
    global _last_stt_use
    _last_stt_use = time.monotonic()
    engine = _init_stt()
    if engine == "none":
        raise RuntimeError("STT engine not available")

    # 언어 결정: 명시 인자 > 환경변수 > 자동(None)
    import os as _os
    lang = language if language else _os.environ.get("VT_STT_LANG", "").strip() or None

    loop = asyncio.get_running_loop()
    wav_bytes = await loop.run_in_executor(None, _convert_to_wav, audio_bytes, input_format)

    # 메모리 폭주 방어 — CTranslate2에 긴 오디오를 넘기지 않도록 상한으로 절단.
    wav_bytes = _truncate_wav(wav_bytes, MAX_STT_SECONDS)

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
