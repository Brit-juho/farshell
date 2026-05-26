"""macOS NSSystemDefined 이어폰 미디어 키 (Play/Pause) 리스너."""
import threading

import platform_utils

from .config import logger

_media_listener_thread: threading.Thread | None = None


def start_media_key_listener(toggle_fn):
    """macOS NSSystemDefined 이벤트로 이어폰 Play/Pause 버튼 감지.

    pyobjc-framework-Cocoa 필요:
        pip install pyobjc-framework-Cocoa
    """
    if not platform_utils.IS_MACOS:
        return

    try:
        import AppKit  # noqa: F401
    except ImportError:
        logger.warning("pyobjc-framework-Cocoa 미설치 → 이어폰 버튼 비활성")
        logger.warning("  pip install pyobjc-framework-Cocoa")
        return

    _MEDIA_PLAY_PAUSE = 16

    def _monitor():
        try:
            import AppKit
            mask = AppKit.NSSystemDefinedMask
            AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask,
                lambda event: _handle(event),
            )
            AppKit.NSRunLoop.mainRunLoop().run()
        except Exception as e:
            logger.error(f"미디어 키 모니터 오류: {e}")

    def _handle(event):
        try:
            if event.type() != 14:  # NSSystemDefined
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
