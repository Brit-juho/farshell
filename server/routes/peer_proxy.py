"""원격 pane WS 프록시 (N7/N39 3단계) — 브라우저 ↔ 이 서버(A) ↔ 상대 서버(B).

## 왜 프록시인가
브라우저의 WebSocket API로는 커스텀 헤더를 못 보낸다. peer 인증은 서명 헤더
(`X-Peer-*`)에 얹혀 있으므로 **브라우저가 B에 직접 붙는 길은 없다**. 그래서 A의
서버가 중간에 서서, 브라우저에게는 평소의 로그인 세션으로, B에게는 peer 서명으로
말한다. ADR-24를 서버-서버로 바꾼 결정(SSH 대체)의 직접적인 귀결이다.

## 이 파일이 **하지 않는** 것 — 전부 의도된 것이다
- PTY를 만들지 않는다. 원격 pane의 PTY는 B에 있다(B의 `/api/peer/ws/{name}`).
- `output_watcher`/푸시 알림에 먹이지 않는다. **PTY를 소유한 호스트만 알린다** —
  A도 같이 알리면 같은 완료 알림이 두 번 온다.
- 스크롤백을 디스크에 쓰지 않는다. 영속화는 PTY 소유 호스트(B)의 설정만 따른다
  (A가 B의 출력을 A 디스크에 남기지 않는다).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import host_store
import peer_client
from routes.pty import _ws_auth_token

logger = logging.getLogger(__name__)

router = APIRouter()

CONNECT_TIMEOUT = 10.0


def _ws_url(base_url: str, path: str) -> str:
    u = base_url.rstrip("/")
    if u.startswith("https://"):
        return "wss://" + u[len("https://"):] + path
    if u.startswith("http://"):
        return "ws://" + u[len("http://"):] + path
    return u + path


@router.websocket("/ws/remote/{host_id}/{tmux_name}")
async def ws_remote(ws: WebSocket, host_id: str, tmux_name: str):
    # 브라우저 쪽 인증은 평소와 같다(로컬 터미널 WS와 동일한 다중 소스).
    if _ws_auth_token(ws) is None:
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()

    peer = await asyncio.to_thread(host_store.find_peer, host_id)
    if peer is None:
        await ws.close(code=4004, reason="host not found")
        return

    try:
        import websockets
    except ImportError:  # pragma: no cover — requirements-core에 있다
        await ws.close(code=4500, reason="websockets 미설치")
        return

    path = f"/api/peer/ws/{tmux_name}"
    headers = peer_client._signed_headers(peer, "GET", path)
    url = _ws_url(peer["url"], path)

    try:
        # max_size=None: 터미널 출력은 한 프레임이 커질 수 있고, 여기서 자르면
        # 화면이 깨진 채로 끊긴다. 어차피 상대는 우리가 등록한 호스트다.
        upstream = await asyncio.wait_for(
            websockets.connect(url, additional_headers=headers, max_size=None),
            timeout=CONNECT_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 — 연결 실패는 상태이지 서버 오류가 아니다
        logger.info(f"[peer proxy] {host_id} 연결 실패: {e}")
        await ws.close(code=4502, reason="원격 호스트에 연결할 수 없습니다")
        return

    async def _down():
        """B → 브라우저."""
        async for message in upstream:
            if isinstance(message, bytes):
                await ws.send_bytes(message)
            else:
                await ws.send_text(message)

    async def _up():
        """브라우저 → B. 내용은 보지 않는다 — 등급 판정은 **B가** 한다
        (A가 판정하면 A를 믿는 셈이 되고, 그건 등급의 의미가 없다)."""
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])
            elif msg.get("text") is not None:
                await upstream.send(msg["text"])

    down = asyncio.create_task(_down())
    up = asyncio.create_task(_up())
    try:
        done, pending = await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        down.cancel()
        up.cancel()
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
