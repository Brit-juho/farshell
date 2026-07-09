"""마이크 녹음 + STT 호출 + tmux 주입 통합."""
import threading
import time

import numpy as np
import sounddevice as sd

import platform_utils

from .config import MAX_RECORDING_SECONDS, SAMPLE_RATE, TTS_CONFIRM, logger
from .stt import transcribe
from .tmux_target import resolve_voice_target_pane, send_to_tmux

_recording = False
_audio_frames: list[np.ndarray] = []
_stream: sd.InputStream | None = None
_lock = threading.Lock()
# 토글 디바운스 — 미디어 키 중복 발생/경쟁 등 어떤 이중 이벤트에도 rapid on/off를 막는다.
_last_toggle = 0.0
TOGGLE_DEBOUNCE_SEC = 0.25


def is_recording() -> bool:
    return _recording


def start_recording():
    global _recording, _audio_frames, _stream
    with _lock:
        if _recording:
            return
        _audio_frames = []
        _recording = True

    # 상한 프레임 수 — MAX_RECORDING_SECONDS 초과 시 자동 종료(끄기 토글 유실 방어).
    max_frames = int(MAX_RECORDING_SECONDS * SAMPLE_RATE) if MAX_RECORDING_SECONDS > 0 else 0
    captured = 0

    def callback(indata, frames, time_info, status):
        nonlocal captured
        if not _recording:
            return
        _audio_frames.append(indata.copy())
        captured += len(indata)
        if max_frames and captured >= max_frames:
            # 콜백 스레드에서 직접 stop하면 sounddevice가 데드락날 수 있어 별도 스레드로.
            logger.warning(
                f"녹음 {MAX_RECORDING_SECONDS:.0f}s 상한 도달 — 강제 종료 (메모리 폭주 방어)"
            )
            threading.Thread(target=stop_recording_and_process, daemon=True).start()

    # 방어: desync로 남은 이전 스트림이 있으면 먼저 닫아 누수 차단.
    if _stream is not None:
        try:
            _stream.stop()
            _stream.close()
        except Exception:
            pass
        _stream = None

    _stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback
    )
    _stream.start()
    logger.info("🎙 녹음 시작")
    platform_utils.play_sound("start")


def stop_recording_and_process():
    global _recording, _stream, _audio_frames
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

    # 프레임 리스트를 로컬로 넘기고 전역 버퍼는 즉시 비운다 — 다음 녹음 전까지 GB 단위
    # 오디오를 붙들지 않도록. concatenate 결과(audio)만 STT까지 살아 있으면 충분.
    frames, _audio_frames = _audio_frames, []
    audio = np.concatenate(frames, axis=0)
    del frames
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
    """핫키와 이어폰 버튼 공통 진입점. 원자적 결정 + 디바운스."""
    global _last_toggle
    with _lock:
        now = time.monotonic()
        if now - _last_toggle < TOGGLE_DEBOUNCE_SEC:
            return  # 이중 이벤트 무시
        _last_toggle = now
        want_start = not _recording
    # 실제 start/stop은 각자 _lock을 다시 잡으므로 락 밖에서 호출(재진입 데드락 방지).
    if want_start:
        start_recording()
    else:
        threading.Thread(target=stop_recording_and_process, daemon=True).start()
