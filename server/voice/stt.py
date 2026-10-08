"""데몬(전역 핫키)용 STT — 서버와 같은 엔진 계층(stt_service)을 쓴다.

데몬은 서버와 **별도 프로세스**라 자기 엔진 인스턴스를 따로 올린다(서버가 안 떠 있어도 동작해야 하므로).
선택·준비 상태는 서버와 같은 설정(voice.sttModel, VT_STT_MODEL)과 같은 캐시를 본다. 모델이 없으면 몰래
받지 않고 NotReady로 멈춘다(recorder가 알림으로 이유를 보여준다).
"""
import io
import wave

import numpy as np

from .config import MAX_RECORDING_SECONDS, SAMPLE_RATE, logger
import voice_handler
import stt_service  # server/ 는 config.py가 sys.path에 넣어 둔다

# transcribe에 넘길 오디오 상한. 녹음 상한(MAX_RECORDING_SECONDS)과 같게 두되, 방어적으로
# 여기서도 한 번 더 자른다 — 긴 오디오가 엔진 메모리를 폭주시키는 것을 차단.
_MAX_STT_SAMPLES = int(MAX_RECORDING_SECONDS * SAMPLE_RATE) if MAX_RECORDING_SECONDS > 0 else 0


def transcribe(audio_np: np.ndarray) -> str:
    """numpy int16 배열 → 텍스트. 모델이 없으면 stt_engines.NotReady."""
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
    wav = buf.getvalue()
    if voice_handler._is_silent_wav(wav):
        return ""
    text, _timing = stt_service.transcribe_wav(wav, None)
    return text
