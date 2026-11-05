"""웹소켓 브로드캐스트 — 느린 클라이언트 하나가 나머지와 이벤트 루프 작업을 붙잡지 않게.

예전 패턴(`for ws in clients: await ws.send_json(msg)`)은 순서대로 보내서, 응답 없는 클라이언트 하나
(끊긴 채 정리되지 않은 폰 브라우저 등)의 송신 버퍼가 차면 그 뒤 클라이언트와 그 루프 본문이 전부
기다렸다. 여기서는 동시에 보내고, `SEND_TIMEOUT` 안에 못 보낸 클라이언트는 닫고 목록에서 뺀다.
클라이언트는 기존 재연결 경로로 다시 붙는다. (PTY 출력은 이미 연결별 큐 + 오래된 것 버리기로 안전하다.)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 2.0


async def _send_one(ws, msg, timeout: float) -> bool:
    try:
        send = ws.send_text(msg) if isinstance(msg, str) else ws.send_json(msg)
        await asyncio.wait_for(send, timeout)
        return True
    except asyncio.TimeoutError:
        logger.info("[ws] 송신이 %.0fs 넘게 막힌 클라이언트를 끊음", timeout)
        try:
            await asyncio.wait_for(ws.close(code=1011, reason="send timeout"), 1.0)
        except Exception:  # noqa: BLE001
            pass
        return False
    except Exception:  # noqa: BLE001 — 끊긴 소켓
        return False


async def broadcast(clients: set, msg, timeout: float = SEND_TIMEOUT) -> int:
    """보낸 수를 돌려준다. 실패·기한 초과 클라이언트는 `clients`에서 지운다."""
    targets: Iterable = list(clients)
    if not targets:
        return 0
    results = await asyncio.gather(*(_send_one(ws, msg, timeout) for ws in targets))
    sent = 0
    for ws, ok in zip(targets, results):
        if ok:
            sent += 1
        else:
            clients.discard(ws)
    return sent


PING_INTERVAL = 15.0


def start_pinger(ws, interval: float = PING_INTERVAL) -> "asyncio.Task":
    """`{"type":"ping"}`을 주기적으로 보낸다. 클라이언트 감시(45초 무소식이면 끊고 다시 붙기)의 근거다 —
    서버가 연결은 받아 두고 아무것도 안 보내는 상태(2026-11-05)를 브라우저가 알아챌 수 있게 한다.
    돌려준 태스크는 핸들러의 finally 에서 cancel 한다."""
    async def _loop():
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.wait_for(ws.send_text('{"type":"ping"}'), SEND_TIMEOUT)
            except Exception:  # noqa: BLE001 — 끊겼거나 막혔다. 수신 루프가 정리한다
                try:
                    await asyncio.wait_for(ws.close(code=1011, reason="send timeout"), 1.0)
                except Exception:  # noqa: BLE001
                    pass
                return
    return asyncio.create_task(_loop())
