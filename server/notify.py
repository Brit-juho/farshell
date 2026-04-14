"""Push Notification — ntfy.sh / Telegram Bot 브릿지.

환경변수:
  RALPH_NOTIFY_URL       — ntfy 토픽 URL (예: https://ntfy.sh/your-topic)
  RALPH_TELEGRAM_TOKEN   — Telegram Bot 토큰 (선택)
  RALPH_TELEGRAM_CHAT_ID — Telegram 채팅 ID (선택)

사용:
  await notify.send("Claude 완료", "리팩토링 끝났어요", priority="default")
"""

from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
import urllib.request
from typing import Literal

logger = logging.getLogger(__name__)

Priority = Literal["min", "low", "default", "high", "urgent"]


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def is_configured() -> bool:
    return bool(_env("RALPH_NOTIFY_URL") or
                (_env("RALPH_TELEGRAM_TOKEN") and _env("RALPH_TELEGRAM_CHAT_ID")))


async def send(title: str, message: str, priority: Priority = "default",
               tags: str = "") -> bool:
    """비동기 알림 전송. 설정된 채널 모두에 병렬로 보냄.

    Returns: 하나라도 성공하면 True, 모두 실패하면 False.
    """
    tasks = []
    if _env("RALPH_NOTIFY_URL"):
        tasks.append(_send_ntfy(title, message, priority, tags))
    if _env("RALPH_TELEGRAM_TOKEN") and _env("RALPH_TELEGRAM_CHAT_ID"):
        tasks.append(_send_telegram(title, message))
    if not tasks:
        return False
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return any(r is True for r in results)


async def _send_ntfy(title: str, message: str, priority: Priority, tags: str) -> bool:
    url = _env("RALPH_NOTIFY_URL")

    def _post():
        headers = {
            "Title": title.encode("utf-8"),
            "Priority": priority,
            "Content-Type": "text/plain; charset=utf-8",
        }
        if tags:
            headers["Tags"] = tags
        req = urllib.request.Request(url, data=message.encode("utf-8"),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300

    try:
        return await asyncio.to_thread(_post)
    except Exception as e:
        logger.warning(f"ntfy 전송 실패: {e}")
        return False


async def _send_telegram(title: str, message: str) -> bool:
    token = _env("RALPH_TELEGRAM_TOKEN")
    chat_id = _env("RALPH_TELEGRAM_CHAT_ID")
    text = f"*{title}*\n{message}" if title else message
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()

    def _post():
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300

    try:
        return await asyncio.to_thread(_post)
    except Exception as e:
        logger.warning(f"Telegram 전송 실패: {e}")
        return False


def send_sync(title: str, message: str, priority: Priority = "default",
              tags: str = "") -> bool:
    """동기 버전 — 쉘 스크립트/훅에서 사용."""
    try:
        return asyncio.run(send(title, message, priority, tags))
    except RuntimeError:
        # 이미 event loop 안에 있으면 fire-and-forget
        asyncio.create_task(send(title, message, priority, tags))
        return True
