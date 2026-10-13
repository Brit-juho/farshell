"""Voice Handler — 입력 오디오 디코딩 + STT 호출 + TTS (Edge TTS / macOS say).

STT 엔진·모델 선택과 실행은 `stt_service`/`stt_engines`가 맡는다. 여기서는 업로드된 오디오를
16kHz mono WAV로 바꾸고(무음·길이 상한 처리) 한 번에 하나씩 엔진에 넘긴다.
TTS 우선순위: edge-tts → macOS say fallback
"""

import asyncio
import io
import logging
import os
import subprocess
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import stt_service
from i18n import tr

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# STT (엔진은 stt_service가 관리 — 이 함수들은 기존 호출부 호환용 얇은 위임)
# ---------------------------------------------------------------------------


def stt_available() -> bool:
    """이 서버에서 STT 엔진을 쓸 수 있는가 — 모델을 올리지 않는다(capabilities처럼 자주 불리는 경로용)."""
    return stt_service.available()


def stt_loaded() -> bool:
    return stt_service.loaded()


def preload_stt() -> Optional[str]:
    """선택된 모델을 미리 올려 첫 인식 지연을 없앤다. 못 올리면 None(이유는 stt_service.last_error())."""
    return stt_service.preload()


def unload_stt() -> bool:
    return stt_service.unload()


async def stt_idle_monitor() -> None:
    await stt_service.idle_monitor()


def _module_available(*names: str) -> bool:
    """모듈을 import하지 않고 설치 여부만 확인."""
    import importlib.util
    for n in names:
        try:
            if importlib.util.find_spec(n) is not None:
                return True
        except Exception:
            continue
    return False


def tts_available() -> bool:
    """TTS '설치 여부'만 확인 — import 없이 find_spec으로만 본다."""
    if _module_available("edge_tts"):
        return True
    import shutil
    return shutil.which("say") is not None  # macOS say fallback


# ---------------------------------------------------------------------------
# 입력 오디오 → 16kHz mono WAV
# ---------------------------------------------------------------------------

ALLOWED_AUDIO_FORMATS = {"webm", "wav", "ogg", "mp3", "m4a", "mp4"}

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
        # mp4/m4a는 컨테이너 이름이 여러 개라 형식을 지정하지 않고 판별에 맡긴다(iPhone Safari는 mp4(AAC)로 보낸다).
        container = av.open(_io.BytesIO(audio_bytes), format=input_format if input_format in ("webm", "ogg", "wav", "mp3") else None)
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        pcm = bytearray()
        # 프레임 버퍼는 정렬 패딩이 붙어 있을 수 있다 — 실제 샘플 수(f.samples × 2바이트 mono s16)만 쓴다.
        # 안 자르면 AAC·mp3처럼 프레임이 딱 안 떨어지는 입력(iPhone Safari의 mp4)에서 길이가 늘어난다(2초 → 2.57초).
        deadline = time.monotonic() + 10
        for frame in container.decode(stream):
            if time.monotonic() > deadline:
                raise ValueError(tr("오디오 디코딩 시간이 초과됐습니다"))
            for f in resampler.resample(frame):
                pcm.extend(bytes(f.planes[0])[: f.samples * 2])
                if len(pcm) >= int((MAX_STT_SECONDS or 60) * 32000):
                    break
            if len(pcm) >= int((MAX_STT_SECONDS or 60) * 32000):
                break
        pcm = pcm[:int((MAX_STT_SECONDS or 60) * 32000)]
        # resampler flush
        for f in resampler.resample(None):
            pcm.extend(bytes(f.planes[0])[: f.samples * 2])
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
            ["ffmpeg", "-y", "-i", src_path, "-t", str(MAX_STT_SECONDS or 60),
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

# 엔진에 긴 오디오를 넘기면 메모리가 급증한다(과거 CTranslate2는 60s 오디오 1회에 +800MB를 쥐고 안 돌려줬다).
# 터미널 음성 명령은 길어야 수 초이므로, 인식 직전에 이 상한으로 잘라 폭주를 원천 차단한다.
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
    """WAV 헤더(44바이트) 이후를 16-bit signed PCM으로 보고 **전체**의 평균 절대값을 잰다.

    예전에는 앞 2초만 봐서, 말하기 전에 2초 넘게 말이 없으면 무음으로 버렸다. 4샘플마다 훑어 비용을 줄인다.
    """
    if len(wav_bytes) <= 44:
        return True
    try:
        import array
        pcm = wav_bytes[44:44 + (len(wav_bytes) - 44) // 2 * 2]
        samples = array.array("h")
        samples.frombytes(pcm)
        if len(samples) == 0:
            return True
        picked = samples[::4]
        avg_abs = sum(abs(x) for x in picked) / len(picked)
        return avg_abs < SILENCE_RMS_THRESHOLD
    except Exception:
        return False


# 엔진 대기+로드+인식을 합친 상한. 첫 로드(수 초~수십 초)와 줄 서기를 감안한 여유값이다.
STT_TOTAL_TIMEOUT = 120


async def transcribe_timed(audio_bytes: bytes, input_format: str = "webm",
                           language: Optional[str] = None) -> tuple:
    """음성 바이트 → (텍스트, 단계별 시간 ms). 모델이 없으면 stt_engines.NotReady, 줄이 길면 stt_service.Busy.

    language=None이면 엔진이 자동 판별한다. 환경변수 VT_STT_LANG=ko로 기본값을 정할 수 있다.
    """
    t0 = time.perf_counter()
    lang = language if language else os.environ.get("VT_STT_LANG", "").strip() or None
    loop = asyncio.get_running_loop()
    wav_bytes = await loop.run_in_executor(None, _convert_to_wav, audio_bytes, input_format)
    t1 = time.perf_counter()

    # 메모리 폭주 방어 — 엔진에 긴 오디오를 넘기지 않도록 상한으로 절단.
    wav_bytes = _truncate_wav(wav_bytes, MAX_STT_SECONDS)

    # 무음 차단 — 모델이 무음에서 지어내는 글자(hallucination) 방지.
    if _is_silent_wav(wav_bytes):
        logger.info("STT: silent input, skipping transcription")
        return "", {"decode_ms": round((t1 - t0) * 1000), "wait_ms": 0, "load_ms": 0, "stt_ms": 0,
                    "total_ms": round((time.perf_counter() - t0) * 1000)}

    try:
        text, t = await asyncio.wait_for(
            loop.run_in_executor(None, stt_service.transcribe_wav, wav_bytes, lang),
            timeout=STT_TOTAL_TIMEOUT)
    except asyncio.TimeoutError as e:
        raise RuntimeError(tr("음성 인식 시간이 초과됐습니다")) from e
    timing = {"decode_ms": round((t1 - t0) * 1000), **t, "total_ms": round((time.perf_counter() - t0) * 1000)}
    logger.info("voice.stt timing %s", timing)
    return text, timing


async def transcribe(audio_bytes: bytes, input_format: str = "webm",
                     language: Optional[str] = None) -> str:
    """음성 바이트 → 텍스트(타이밍 없이). local_mic 등 기존 호출부용."""
    text, _ = await transcribe_timed(audio_bytes, input_format, language)
    return text


# ---------------------------------------------------------------------------
# TTS Engine
# ---------------------------------------------------------------------------

_tts_engine: Optional[str] = None


def _init_tts() -> str:
    """사용 가능한 TTS 엔진을 감지."""
    global _tts_engine

    if _tts_engine is not None:
        return _tts_engine

    # 1) edge-tts
    try:
        import edge_tts  # noqa: F401
        _tts_engine = "edge-tts"
        logger.info("TTS engine: edge-tts")
        return _tts_engine
    except ImportError:
        pass

    # 2) macOS say fallback
    import shutil
    if shutil.which("say"):
        _tts_engine = "macos-say"
        logger.info("TTS engine: macOS say (fallback)")
        return _tts_engine

    _tts_engine = "none"
    logger.warning("No TTS engine available.")
    return _tts_engine


@dataclass(frozen=True)
class SynthesizedAudio:
    data: bytes
    media_type: str
    engine: str


def completion_sound() -> SynthesizedAudio:
    import math
    import struct
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(5000 * math.sin(2 * math.pi * 660 * i / 16000)
                             * (1 - i / 3200))) for i in range(3200)))
    return SynthesizedAudio(buf.getvalue(), "audio/wav", "tone")


async def synthesize_result(text: str, voice: str = "ko-KR-SunHiNeural", engine: str = "auto") -> SynthesizedAudio:
    """결과의 실제 MIME을 반환한다. local 선택은 외부 서비스로 보내지 않는다."""
    if engine not in ("auto", "edge", "local"):
        raise ValueError(tr("지원하지 않는 TTS 엔진입니다"))
    selected = _init_tts() if engine == "auto" else ("edge-tts" if engine == "edge" else "macos-say")
    if selected == "edge-tts":
        try:
            import edge_tts
            buf = io.BytesIO()
            async for chunk in edge_tts.Communicate(text, voice).stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return SynthesizedAudio(buf.getvalue(), "audio/mpeg", "edge-tts")
        except Exception:
            if engine == "edge":
                raise
            logger.warning("edge-tts 실패 — 로컬 음성 합성으로 대체")
    import shutil
    if shutil.which("say"):
        return SynthesizedAudio(await _macos_say(text), "audio/wav", "macos-say")
    raise RuntimeError(tr("로컬 TTS 엔진을 사용할 수 없습니다"))


async def synthesize(text: str, voice: str = "ko-KR-SunHiNeural") -> bytes:
    return (await synthesize_result(text, voice)).data


async def _macos_say(text: str) -> bytes:
    """macOS say 명령으로 TTS."""
    loop = asyncio.get_running_loop()

    def _say():
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run(
                ["say", "-v", "Yuna", "--file-format=WAVE", "--data-format=LEI16@22050", "-o", tmp_path, text],
                capture_output=True,
                check=True,
                timeout=30,
            )
            return Path(tmp_path).read_bytes()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    return await loop.run_in_executor(None, _say)
