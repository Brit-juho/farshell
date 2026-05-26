"""마이크 녹음 + STT 호출 + tmux 주입 통합."""
import threading

import numpy as np
import sounddevice as sd

import platform_utils

from .config import SAMPLE_RATE, TTS_CONFIRM, logger
from .stt import transcribe
from .tmux_target import resolve_voice_target_pane, send_to_tmux

_recording = False
_audio_frames: list[np.ndarray] = []
_stream: sd.InputStream | None = None
_lock = threading.Lock()


def is_recording() -> bool:
    return _recording


def start_recording():
    global _recording, _audio_frames, _stream
    with _lock:
        if _recording:
            return
        _audio_frames = []
        _recording = True

    def callback(indata, frames, time_info, status):
        if _recording:
            _audio_frames.append(indata.copy())

    _stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback
    )
    _stream.start()
    logger.info("🎙 녹음 시작")
    platform_utils.play_sound("start")


def stop_recording_and_process():
    global _recording, _stream
    with _lock:
        if not _recording:
            return
        _recording = False

    if _stream:
        _stream.stop()
        _stream.close()
        _stream = None

    platform_utils.play_sound("stop")

    if not _audio_frames:
        logger.info("녹음 데이터 없음")
        return

    audio = np.concatenate(_audio_frames, axis=0)
    logger.info(f"녹음 종료: {len(audio)/SAMPLE_RATE:.1f}초")

    text = transcribe(audio)
    if not text:
        logger.info("인식된 텍스트 없음")
        return

    logger.info(f"STT: {text}")

    pane, mode = resolve_voice_target_pane()
    if not pane:
        logger.warning("활성 tmux pane 없음")
        if TTS_CONFIRM:
            platform_utils.tts_speak("tmux 세션이 없습니다")
        return

    if send_to_tmux(pane, text):
        logger.info(f"→ tmux {pane} ({mode}): {text}")
        if TTS_CONFIRM:
            platform_utils.tts_speak(text)


def toggle_recording():
    """핫키와 이어폰 버튼 공통 진입점."""
    if _recording:
        threading.Thread(target=stop_recording_and_process, daemon=True).start()
    else:
        start_recording()
