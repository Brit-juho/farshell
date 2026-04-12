#!/usr/bin/env python3
"""Voice Daemon — macOS 글로벌 핫키 + 이어폰 버튼으로 음성 입력 → tmux 주입.

서버 없이 독립 동작. tmux + STT(faster-whisper) + sounddevice만 사용.
- Ctrl+Shift+V 토글: 녹음 시작/종료
- 이어폰 Play/Pause 버튼: 녹음 토글 (macOS Media Key)
→ STT → 활성 tmux pane에 타이핑.

실행:
    /opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python server/voice_daemon.py
"""

import io
import logging
import os
import subprocess
import sys
import threading
import wave

import numpy as np
import sounddevice as sd
from pynput import keyboard
import platform_utils

# ---------------------------------------------------------------------------
# 이어폰 미디어 버튼 (macOS NSEvent 기반)
# ---------------------------------------------------------------------------

_media_listener_thread: threading.Thread | None = None


def _start_media_key_listener(toggle_fn):
    """macOS NSSystemDefined 이벤트로 이어폰 Play/Pause 버튼 감지.

    pyobjc-framework-Cocoa 필요:
        pip install pyobjc-framework-Cocoa
    """
    if not platform_utils.IS_MACOS:
        return

    try:
        from AppKit import NSEvent, NSApplication, NSApp
        from Foundation import NSObject
        import AppKit
    except ImportError:
        logger.warning("pyobjc-framework-Cocoa 미설치 → 이어폰 버튼 비활성")
        logger.warning("  pip install pyobjc-framework-Cocoa")
        return

    # NSSystemDefined subtype 8 = 미디어 키
    _MEDIA_PLAY_PAUSE = 16
    _MEDIA_NEXT = 17
    _MEDIA_PREV = 18

    def _monitor():
        try:
            mask = AppKit.NSSystemDefinedMask
            tap = AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask,
                lambda event: _handle(event),
            )
            AppKit.NSRunLoop.mainRunLoop().run()
        except Exception as e:
            logger.error(f"미디어 키 모니터 오류: {e}")

    def _handle(event):
        try:
            if event.type() != 14:  # NSSystemDefined = 14
                return
            if event.subtype() != 8:
                return
            data1 = event.data1()
            key_code = (data1 & 0xFFFF0000) >> 16
            key_flags = data1 & 0x0000FFFF
            key_down = ((key_flags & 0xFF00) >> 8) == 0xA
            if not key_down:
                return
            if key_code == _MEDIA_PLAY_PAUSE:
                logger.info("🎧 이어폰 Play/Pause → 녹음 토글")
                toggle_fn()
        except Exception as e:
            logger.debug(f"미디어 이벤트 처리 오류: {e}")

    global _media_listener_thread
    _media_listener_thread = threading.Thread(target=_monitor, daemon=True)
    _media_listener_thread.start()
    logger.info("🎧 이어폰 버튼 감지 활성 (Play/Pause → 녹음 토글)")

logging.basicConfig(
    level=logging.INFO,
    format="[voice-daemon] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000
HOTKEY = {keyboard.Key.ctrl_l, keyboard.Key.shift, keyboard.KeyCode.from_char("v")}
TTS_CONFIRM = True  # STT 결과를 macOS say로 읽어줄지

# ---------------------------------------------------------------------------
# STT 엔진 (faster-whisper)
# ---------------------------------------------------------------------------

_whisper_model = None


def _init_whisper():
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
    _init_whisper()

    # WAV 바이트로 변환
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
        # faster-whisper
        buf.seek(0)
        segments, _ = _whisper_model.transcribe(buf, language="ko")
        return " ".join(seg.text for seg in segments).strip()


# ---------------------------------------------------------------------------
# tmux 헬퍼
# ---------------------------------------------------------------------------


def get_active_tmux_pane() -> str | None:
    """현재 활성 tmux pane ID를 반환. tmux 밖이면 None."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        pane_id = result.stdout.strip()
        return pane_id if pane_id else None
    except Exception:
        return None


def get_any_tmux_pane() -> str | None:
    """아무 tmux 세션의 첫 번째 pane을 반환."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True, text=True, timeout=2,
        )
        panes = result.stdout.strip().split("\n")
        return panes[0] if panes and panes[0] else None
    except Exception:
        return None


def send_to_tmux(pane_id: str, text: str) -> bool:
    """tmux pane에 텍스트 전송."""
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", pane_id, "--", text, "Enter"],
            timeout=2, check=True,
        )
        return True
    except Exception as e:
        logger.error(f"tmux send-keys 실패: {e}")
        return False


# ---------------------------------------------------------------------------
# 녹음
# ---------------------------------------------------------------------------

_recording = False
_audio_frames: list[np.ndarray] = []
_stream: sd.InputStream | None = None
_lock = threading.Lock()


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

    # STT
    text = transcribe(audio)
    if not text:
        logger.info("인식된 텍스트 없음")
        return

    logger.info(f"STT: {text}")

    # tmux에 주입
    pane = get_active_tmux_pane() or get_any_tmux_pane()
    if not pane:
        logger.warning("활성 tmux pane 없음")
        if TTS_CONFIRM:
            platform_utils.tts_speak("tmux 세션이 없습니다")
        return

    if send_to_tmux(pane, text):
        logger.info(f"→ tmux {pane}: {text}")
        if TTS_CONFIRM:
            platform_utils.tts_speak(text)


# ---------------------------------------------------------------------------
# 핫키 + 이어폰 공통 토글
# ---------------------------------------------------------------------------

def toggle_recording():
    """핫키와 이어폰 버튼 공통 진입점."""
    if _recording:
        threading.Thread(target=stop_recording_and_process, daemon=True).start()
    else:
        start_recording()


_pressed_keys: set = set()


def on_press(key):
    _pressed_keys.add(key)
    if HOTKEY.issubset(_pressed_keys):
        toggle_recording()


def on_release(key):
    _pressed_keys.discard(key)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    logger.info(f"Voice Daemon 시작 ({platform_utils.PLATFORM_NAME})")
    logger.info(f"핫키: Ctrl+Shift+V (토글)")
    if platform_utils.IS_MACOS:
        logger.info("macOS 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 필요")
    elif platform_utils.IS_WSL2 and not os.environ.get("DISPLAY"):
        logger.warning("WSL2에서 핫키 사용 시 WSLg 또는 X11 필요 ($DISPLAY 미설정)")
        logger.warning("브라우저 음성 입력은 서버 실행 후 웹에서 사용 가능")

    # STT 모델 미리 로드
    _init_whisper()

    # 이어폰 미디어 버튼 리스너 시작
    _start_media_key_listener(toggle_recording)

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()
