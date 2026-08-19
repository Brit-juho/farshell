"""Output Watcher — 터미널 출력 모니터링 + 작업 완료 TTS 알림.

PTY 출력이 일정 시간(idle_timeout) 동안 없으면 "작업 완료"로 판단하고,
마지막 출력을 요약해서 TTS로 알림한다.

알림 대상:
- 데스크톱: macOS say로 직접 재생
- 모바일: WebSocket으로 오디오 데이터 push
"""

import asyncio
import logging
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

import voice_handler
import notify

logger = logging.getLogger(__name__)

# 출력 버퍼 최대 라인 수
MAX_BUFFER_LINES = 50
# 프롬프트 패턴 (작업 완료 감지용)
PROMPT_PATTERNS = [
    b"$ ",        # bash
    b"% ",        # zsh
    b">>> ",      # python
    b"# ",        # root
]


@dataclass
class SessionWatcher:
    session_id: str
    enabled: bool = False  # 기본 비활성. Claude Code Stop hook으로만 TTS 알림
    idle_timeout: float = 3.0  # 초 — 이 시간 동안 출력 없으면 완료로 판단
    min_output_lines: int = 3  # 최소 이 줄 수 이상의 출력이 있어야 알림
    output_buffer: list[bytes] = field(default_factory=list)
    last_output_time: float = 0.0
    _notified: bool = False  # 이미 알림을 보냈는지
    _active: bool = False  # 출력 진행 중인지


class OutputWatcher:
    def __init__(self):
        self._watchers: dict[str, SessionWatcher] = {}
        self._notify_callbacks: list[Callable] = []  # async callbacks
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

    def add_session(self, session_id: str, **kwargs) -> SessionWatcher:
        watcher = SessionWatcher(session_id=session_id, **kwargs)
        self._watchers[session_id] = watcher
        return watcher

    def remove_session(self, session_id: str) -> None:
        self._watchers.pop(session_id, None)

    def on_notify(self, callback: Callable) -> None:
        """알림 콜백 등록. callback(session_id, summary_text, audio_bytes)"""
        self._notify_callbacks.append(callback)

    def feed_output(self, session_id: str, data: bytes) -> None:
        """PTY 출력 데이터를 버퍼에 추가."""
        watcher = self._watchers.get(session_id)
        if not watcher or not watcher.enabled:
            logger.debug(f"[watcher] feed_output ignored: sid={session_id}, watcher={'found' if watcher else 'NOT FOUND'}")
            return

        watcher.last_output_time = time.monotonic()
        watcher._active = True
        watcher._notified = False

        # 라인 단위로 버퍼에 추가
        lines = data.split(b"\n")
        watcher.output_buffer.extend(lines)
        # 최대 라인 수 유지
        if len(watcher.output_buffer) > MAX_BUFFER_LINES:
            watcher.output_buffer = watcher.output_buffer[-MAX_BUFFER_LINES:]

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    def stop(self) -> None:
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()

    async def _monitor_loop(self) -> None:
        """주기적으로 idle 상태를 체크하고 알림을 트리거."""
        while self._running:
            await asyncio.sleep(1.0)
            now = time.monotonic()

            for watcher in list(self._watchers.values()):
                logger.debug(f"[watcher] check {watcher.session_id}: active={watcher._active}, notified={watcher._notified}, lines={len(watcher.output_buffer)}, elapsed={now - watcher.last_output_time:.1f}s")
                if not watcher.enabled or not watcher._active or watcher._notified:
                    continue

                elapsed = now - watcher.last_output_time
                if elapsed < watcher.idle_timeout:
                    continue

                # idle 상태 감지 — 출력이 충분한지 확인
                output_lines = len(watcher.output_buffer)
                if output_lines < watcher.min_output_lines:
                    watcher._active = False
                    continue

                # 알림 트리거
                watcher._notified = True
                watcher._active = False

                summary = self._summarize(watcher.output_buffer)
                if summary:
                    asyncio.create_task(self._send_notification(watcher.session_id, summary))

                # 버퍼 초기화
                watcher.output_buffer.clear()

    # [M5] ANSI/OSC escape 정규식 — BEL(\x07) 또는 ST(\x1b\\) 종료 모두 처리
    _ansi_escape = __import__("re").compile(
        rb'\x1b\[[0-9;?]*[a-zA-Z]'    # CSI sequences
        rb'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'  # OSC (BEL or ST 종료)
        rb'|\x1b[()][0-9A-Z]'         # charset
        rb'|\x1b[>=]'                 # keypad mode
        rb'|\r'                       # carriage return
    )

    def _summarize(self, buffer: list[bytes]) -> str:
        """출력 버퍼를 간단히 요약. ANSI escape 제거 후 마지막 줄들 추출."""
        import re

        lines = []
        for raw in buffer:
            clean = self._ansi_escape.sub(b"", raw).strip()
            # 제어 문자 제거
            clean = re.sub(rb'[\x00-\x1f\x7f]', b'', clean)
            if clean and len(clean) > 1:
                try:
                    text = clean.decode("utf-8", errors="ignore").strip()
                    # zsh 프롬프트 잔여물 제거 (%, ❯ 등 단독 문자)
                    if text and text not in ('%', '❯', '>', '$', '#'):
                        lines.append(text)
                except Exception:
                    pass

        if not lines:
            return ""

        # 마지막 8줄로 요약
        recent = lines[-8:]
        # TTS용으로 줄바꿈을 쉼표로 변환
        text = ", ".join(recent)

        if len(text) > 200:
            text = text[:200] + "..."

        return f"작업이 완료되었습니다. {text}"

    async def _send_notification(self, session_id: str, summary: str) -> None:
        """TTS 생성 + 콜백 호출."""
        logger.info(f"[watcher] Session {session_id} idle — notifying: {summary[:80]}...")

        try:
            audio = await voice_handler.synthesize(summary)
        except Exception as e:
            logger.warning(f"TTS failed for notification: {e}")
            # TTS fallback (직접 재생)
            try:
                import platform_utils
                platform_utils.tts_speak(summary)
            except Exception:
                pass
            audio = b""

        for cb in self._notify_callbacks:
            try:
                await cb(session_id, summary, audio)
            except Exception as e:
                logger.warning(f"Notification callback error: {e}")

        # 모바일 푸시 알림 (ntfy/Telegram) — 설정돼 있으면 병렬로 전송
        if notify.is_configured():
            try:
                push_msg = summary[:300] if summary else "세션이 idle 상태입니다"
                await notify.send(
                    f"farshell: {session_id[:8]}",
                    push_msg,
                    priority="default",
                    tags="white_check_mark",
                )
            except Exception as e:
                logger.warning(f"Push 알림 실패: {e}")

    def set_enabled(self, session_id: str, enabled: bool) -> None:
        watcher = self._watchers.get(session_id)
        if watcher:
            watcher.enabled = enabled

    def set_idle_timeout(self, session_id: str, timeout: float) -> None:
        watcher = self._watchers.get(session_id)
        if watcher:
            watcher.idle_timeout = timeout
