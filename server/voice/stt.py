"""STT 엔진 — mlx-whisper 우선, faster-whisper fallback."""
import io
import wave

import numpy as np

from .config import MAX_RECORDING_SECONDS, SAMPLE_RATE, logger

_whisper_model = None

# transcribe에 넘길 오디오 상한. 녹음 상한(MAX_RECORDING_SECONDS)과 같게 두되, 방어적으로
# 여기서도 한 번 더 자른다 — faster-whisper 긴 오디오 → CTranslate2 네이티브 메모리 폭주 차단.
_MAX_STT_SAMPLES = int(MAX_RECORDING_SECONDS * SAMPLE_RATE) if MAX_RECORDING_SECONDS > 0 else 0


def init_whisper():
    global _whisper_model
    if _whisper_model is not None:
        return
    try:
        import mlx_whisper  # noqa: F401
        _whisper_model = "mlx"
        logger.info("STT: mlx-whisper")
        return
    except ImportError:
        pass
    from faster_whisper import WhisperModel
    _whisper_model = WhisperModel("base", compute_type="int8")
    logger.info("STT: faster-whisper (base)")


def transcribe(audio_np: np.ndarray) -> str:
    """numpy int16 배열 → 텍스트."""
    init_whisper()

    if _MAX_STT_SAMPLES and len(audio_np) > _MAX_STT_SAMPLES:
        logger.warning(
            f"STT 입력 {len(audio_np)/SAMPLE_RATE:.0f}s → {_MAX_STT_SAMPLES/SAMPLE_RATE:.0f}s로 절단 (메모리 폭주 방어)"
        )
        audio_np = audio_np[:_MAX_STT_SAMPLES]

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_np.tobytes())
    wav_bytes = buf.getvalue()

    if _whisper_model == "mlx":
        import mlx_whisper
        result = mlx_whisper.transcribe(
            wav_bytes, path_or_hf_repo="mlx-community/whisper-base"
        )
        return result.get("text", "").strip()
    else:
        buf.seek(0)
        segments, _ = _whisper_model.transcribe(buf, language="ko")
        return " ".join(seg.text for seg in segments).strip()
