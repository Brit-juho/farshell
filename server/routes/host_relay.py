"""호스트 전환 중계의 보내는 쪽(A) — 브라우저가 부르는 `/api/hosts/{id}/relay/*`,
`/ws/hosts/{id}/relay/*`.

2026-09-28(사용자 요청) — 레일 스위처로 다른 맥을 고르면 화면 **전체**(세션·탭·
포트·파일·첨부·설정)가 그 맥 것이 된다. 브라우저는 로그인한 이 서버에만 붙어
있고(쿠키·OTP·기기 등록이 전부 여기 있다), 이 서버가 등록된 peer secret으로
서명해 상대의 `/api/peer/relay/*`(routes/peer_relay.py)로 넘긴다. 브라우저는
peer 서명 헤더를 못 만드므로(WebSocket은 헤더 자체를 못 단다) 서버-서버가 유일한
길이다 — `routes/peer_proxy.py`와 같은 이유.

## 이 파일이 지키는 것
- 브라우저 쪽 인증은 평소 그대로(HTTP는 TokenAuthMiddleware, WS는 `_ws_auth_token`).
- 쿼리의 `token`은 **떼고** 보낸다 — 이 서버의 기계 토큰이다.
- 쿠키·Authorization을 상대에게 넘기지 않고, 상대의 Set-Cookie도 받아오지 않는다.
- 허용 목록(`relay_policy`)으로 미리 거른다. 판정의 정본은 상대(B)다.
- 상대가 이 기능을 모르는 구버전이면 501 `peer_outdated`로 알린다(B는 중계 응답에
  `x-peer-relay` 헤더를 단다 — 그게 없으면 라우트가 없는 것).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import httpx
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

import host_store
from i18n import tr
import peer_client
import relay_policy
from routes.peer_proxy import CONNECT_TIMEOUT, _ws_url
from routes.peer_relay import (
    _PASS_REQUEST_HEADERS,
    _PASS_RESPONSE_HEADERS,
    RELAY_HEADER,
)
from routes.pty import _ws_auth_token

logger = logging.getLogger(__name__)

router = APIRouter()

# 연결은 빨리 포기하고, 읽기는 길게 둔다 — 큰 파일 다운로드가 터널을 넘는다.
_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=300.0, pool=10.0)
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # 리다이렉트는 따라가지 않는다 — 상대가 돌려준 Location으로 서명 헤더를
        # 들고 제3의 주소에 가는 일이 없게.
        _client = httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
    return _client


def _outdated(peer: dict) -> JSONResponse:
    return JSONResponse(
        {"error": "peer_outdated",
         "reason": tr("{label}의 FarShell이 호스트 전환을 모릅니다 — 그 맥을 업데이트하세요", label=peer['label'])},
        status_code=501,
    )


@router.api_route("/api/hosts/{host_id}/relay/{rest:path}",
                  methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def host_relay(host_id: str, rest: str, request: Request):
    peer = await asyncio.to_thread(host_store.find_peer, host_id)
    if peer is None:
        return JSONResponse({"error": "not_found", "reason": tr("등록되지 않은 호스트입니다")},
                            status_code=404)
    target = "/" + rest
    method = request.method.upper()
    if not relay_policy.http_allowed(method, target):
        return JSONResponse(
            {"error": "relay_forbidden", "reason": tr("다른 맥에서 쓸 수 없는 기능입니다: {target}", target=target)},
            status_code=403,
        )

    data = await request.body()
    digest = hashlib.sha256(data).hexdigest()
    query = relay_policy.strip_token(request.url.query)
    path = "/api/peer/relay" + target
    signed = path + ("?" + query if query else "")
    headers = {
        k: v for k, v in request.headers.items() if k.lower() in _PASS_REQUEST_HEADERS
    }
    headers.update(peer_client._signed_headers(peer, method, signed, digest))

    client = _http()
    req = client.build_request(method, peer["url"].rstrip("/") + signed,
                               content=data, headers=headers)
    try:
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as e:
        logger.info(f"[relay] {host_id} {method} {target} 실패: {e}")
        return JSONResponse(
            {"error": "peer_unreachable", "reason": tr("{label}에 연결할 수 없습니다", label=peer['label'])},
            status_code=502,
        )

    if resp.headers.get(RELAY_HEADER) != "1":
        await resp.aclose()
        if resp.status_code in (404, 405):
            return _outdated(peer)
        return JSONResponse(
            {"error": "peer_error", "reason": tr("{label} 응답 HTTP {status}", label=peer['label'], status=resp.status_code)},
            status_code=502,
        )

    out_headers = {
        k: v for k, v in resp.headers.items() if k.lower() in _PASS_RESPONSE_HEADERS
    }
    # httpx가 압축을 풀어 주므로 원래 content-length는 맞지 않을 수 있다.
    if resp.headers.get("content-encoding"):
        out_headers.pop("content-length", None)
    return StreamingResponse(
        resp.aiter_bytes(), status_code=resp.status_code, headers=out_headers,
        background=BackgroundTask(resp.aclose),
    )


@router.websocket("/ws/hosts/{host_id}/relay/{rest:path}")
async def host_relay_ws(ws: WebSocket, host_id: str, rest: str):
    if _ws_auth_token(ws) is None:
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()
    peer = await asyncio.to_thread(host_store.find_peer, host_id)
    if peer is None:
        await ws.close(code=4004, reason="host not found")
        return
    target = "/" + rest
    if not relay_policy.ws_allowed(target):
        await ws.close(code=4003, reason="relay forbidden")
        return

    import websockets

    query = relay_policy.strip_token(ws.url.query)
    path = "/api/peer/relay-ws" + target
    signed = path + ("?" + query if query else "")
    headers = peer_client._signed_headers(peer, "GET", signed)
    try:
        upstream = await asyncio.wait_for(
            websockets.connect(_ws_url(peer["url"], signed), additional_headers=headers,
                               max_size=None),
            timeout=CONNECT_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 — 연결 실패는 상태이지 서버 오류가 아니다
        logger.info(f"[relay ws] {host_id} {target} 연결 실패: {e}")
        await ws.close(code=4502, reason=tr("원격 호스트에 연결할 수 없습니다"))
        return

    async def _down():
        async for message in upstream:
            if isinstance(message, bytes):
                await ws.send_bytes(message)
            else:
                await ws.send_text(message)

    async def _up():
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
        await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        down.cancel()
        up.cancel()
        try:
            await upstream.close()
        except Exception:  # noqa: BLE001
            pass
        code = upstream.close_code or 1000
        # 안쪽 라우트의 종료 코드(4001/4004 등 "다시 붙어도 소용없다")를 그대로
        # 넘긴다 — 안쪽이 **진짜** 터미널 라우트라 뜻이 같다. B 인증 거절(4401)만
        # 브라우저가 아는 4001로 바꾼다.
        if code == 4401:
            code = 4001
        if not (code == 1000 or 4000 <= code < 5000):
            code = 1000
        try:
            await ws.close(code=code, reason=(upstream.close_reason or "")[:120])
        except Exception:  # noqa: BLE001
            pass
