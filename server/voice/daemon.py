"""Voice Daemon 메인 — 핫키 리스너 + 미디어 키 + 메인 루프."""
import logging
import os

from pynput import keyboard

import platform_utils

from .config import (
    VOICE_HOTKEY_DISABLED,
    VOICE_HOTKEY_SPEC,
    hotkey_match,
    logger,
    vt_getenv,
)
from .media_keys import start_media_key_listener
from .recorder import toggle_recording
from .stt import init_whisper
from .tmux_target import read_voice_target_lock

# 로깅 — voice_daemon.py 진입 시 한 번만 설정
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="[voice-daemon] %(message)s",
    )

_pressed_keys: set = set()


def _on_press(key):
    _pressed_keys.add(key)
    if hotkey_match(_pressed_keys):
        toggle_recording()


def _on_release(key):
    _pressed_keys.discard(key)


def main():
    logger.info(f"Voice Daemon 시작 ({platform_utils.PLATFORM_NAME})")

    if VOICE_HOTKEY_DISABLED:
        logger.warning("핫키 비활성화됨 (VT_HOTKEY_VOICE_DISABLED=true)")
    else:
        logger.info(f"핫키: {VOICE_HOTKEY_SPEC} (토글)")

    if platform_utils.IS_MACOS:
        logger.info("macOS 시스템 설정 → 개인정보 → 접근성에서 터미널 앱 허용 필요")
    elif platform_utils.IS_WSL2 and not os.environ.get("DISPLAY"):
        logger.warning("WSL2에서 핫키 사용 시 WSLg 또는 X11 필요 ($DISPLAY 미설정)")
        logger.warning("브라우저 음성 입력은 서버 실행 후 웹에서 사용 가능")
    elif platform_utils.IS_LINUX:
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session_type == "wayland":
            logger.warning("Wayland 환경 — 글로벌 핫키가 캡처되지 않을 수 있음")
            logger.warning("X11 세션으로 전환하거나 모바일 🎤 / vt manage 사용 권장")

    locked = read_voice_target_lock()
    if locked:
        logger.info(f"음성 타깃 LOCK: {locked}")
    else:
        logger.info("음성 타깃 AUTO (most-recent)")

    init_whisper()

    media_keys_off = vt_getenv("VT_VOICE_MEDIA_KEYS", "on").lower() == "off"
    if media_keys_off:
        logger.info("이어폰 미디어 키 트리거 OFF (VT_VOICE_MEDIA_KEYS=off)")
    else:
        start_media_key_listener(toggle_recording)

    if VOICE_HOTKEY_DISABLED:
        import threading as _t
        try:
            _t.Event().wait()
        except KeyboardInterrupt:
            pass
        return

    with keyboard.Listener(on_press=_on_press, on_release=_on_release) as listener:
        listener.join()


if __name__ == "__main__":
    main()
