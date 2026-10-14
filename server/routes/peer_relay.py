"""호스트 전환 중계의 받는 쪽(B) — `/api/peer/relay/*`, `/api/peer/relay-ws/*`.

A(로그인한 서버)가 브라우저 대신 B의 일반 API를 부른다. 허용 범위는
`relay_policy.py` 한 곳이고, 이 파일은 **검증 → 판정 → 이 서버 안에서 그대로
실행**만 한다. 로컬 구현을 다시 쓰지 않고 라우터에 그대로 넘기므로 응답은
브라우저가 B에 직접 붙었을 때와 같다.

## 인증 (peer.py와 같은 서명, 두 가지를 더 묶는다)
- 서명 대상 path에 **쿼리까지** 넣는다. 일반 API는 쿼리가 대상을 고른다
  (`/api/fs/raw?path=…`) — 쿼리를 서명 밖에 두면 서명 하나로 아무 파일이나 읽는다.
- 본문 해시(`X-Peer-Body`)를 **필수**로 한다(빈 본문도 해시가 있다).
- 등급은 **control만**. 이 중계는 파일 쓰기·세션 종료·설정 변경을 포함한다 —
  control은 이미 셸에 무엇이든 칠 수 있으니 새로 주는 권한이 아니다.

## 안에서 실행하는 방법
미들웨어를 건너뛰고 `app.router`에 새 scope로 넘긴다 — 인증은 위에서 끝났고,
세션 쿠키 검사(TokenAuthMiddleware)는 이 요청에 맞지 않는다. scope에
`vt_relay`를 심는데, **이 키는 서버 코드만 만들 수 있다**(바깥 요청이 ASGI scope에
키를 넣을 방법은 없다). WS 라우트의 `_ws_auth_token`이 이 키를 보고 통과시킨다.
승격(require_elevated) 라우트는 쿠키가 없어 여기서도 거절된다 — 이중 잠금.

## 감사 로그
GET은 남기지 않는다 — 레일이 몇 초마다 폴링해서 로그가 그걸로 덮인다.
바꾸는 요청(POST/PUT/PATCH/DELETE)과 WS 연결, 거절은 전부 남긴다.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

import host_store
from i18n import tr
import relay_policy
from routes.peer_protocol import PeerDenied, _authenticate_raw

logger = logging.getLogger(__name__)

router = APIRouter()

# A가 "상대가 구버전이라 이 경로가 없다"를 "상대 API가 404를 냈다"와 구별하는 표시.
RELAY_HEADER = "x-peer-relay"

# 중계 응답에서 브라우저까지 넘길 헤더. 쿠키는 **절대** 넘기지 않는다 — B가
# 발급한 쿠키가 A의 출처에 박히면 A의 세션과 섞인다.
_PASS_RESPONSE_HEADERS = {
    "content-type", "content-disposition", "content-length", "cache-control",
    "etag", "last-modified", "content-range", "accept-ranges",
}
# 안쪽 요청에 넘길 헤더. 인증·쿠키·peer 헤더는 뺀다.
_PASS_REQUEST_HEADERS = {
    "content-type", "accept", "if-none-match", "if-modified-since", "range",
    "x-file-name", "x-requested-with",
}


def _signed_path(path: str, query: str) -> str:
    return path + ("?" + query if query else "")


def _denied(reason: str, status: int, code: str = "peer_denied") -> JSONResponse:
    return JSONResponse({"error": code, "reason": reason}, status_code=status,
                        headers={RELAY_HEADER: "1"})


def _inner_scope(outer: dict, target: str, query: str, peer_id: str) -> dict:
    headers = [
        (k, v) for k, v in outer.get("headers", [])
        if k.decode("latin-1").lower() in _PASS_REQUEST_HEADERS
    ]
    # host/origin은 안쪽 판정(동일 출처 등)이 쓸 수 있어 원래 것을 둔다.
    for k, v in outer.get("headers", []):
        if k.decode("latin-1").lower() == "host":
            headers.append((k, v))
    scope = {
        k: v for k, v in outer.items()
        if k not in ("route", "endpoint", "path_params", "router", "app_root_path")
    }
    scope.update(
        path=target,
        raw_path=target.encode("utf-8"),
        query_string=query.encode("latin-1"),
        root_path="",
        headers=headers,
        vt_relay={"peer": peer_id},
    )
    return scope


@router.api_route("/api/peer/relay/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def peer_relay(request: Request, rest: str):
    data = await request.body()
    digest = hashlib.sha256(data).hexdigest()
    claimed = request.headers.get("x-peer-body", "")
    if not claimed or not hmac.compare_digest(claimed, digest):
        return _denied(tr("본문 해시가 없거나 맞지 않습니다"), 401)
    query = request.url.query
    try:
        grant = _authenticate_raw(request.method, _signed_path(request.url.path, query),
                                  request.headers, digest)
    except PeerDenied as e:
        return _denied(e.reason, e.status)

    target = "/" + rest
    method = request.method.upper()
    if grant.get("level") != host_store.LEVEL_CONTROL:
        host_store.audit(grant["id"], "relay", False, f"control 등급 필요: {method} {target}")
        return _denied(
            tr("이 맥은 상대에게 읽기 전용(view)만 허용했습니다 — 이 맥에서 'fsh host allow-control <id>'를 실행해야 전환할 수 있습니다"), 403, "level_required")
    if not relay_policy.http_allowed(method, target):
        host_store.audit(grant["id"], "relay", False, f"허용 목록 밖: {method} {target}")
        return _denied(tr("다른 맥에서 부를 수 없는 기능입니다: {target}", target=target), 403, "relay_forbidden")
    if method != "GET":
        host_store.audit(grant["id"], "relay", True, f"{method} {target}")

    return await _dispatch(request, target, relay_policy.strip_token(query), grant["id"], data)


async def _dispatch(request: Request, target: str, query: str, peer_id: str,
                    data: bytes):
    """라우터를 이 프로세스 안에서 돌리고 응답을 흘려보낸다(버퍼링하지 않는다 —
    파일 다운로드가 수백 MB일 수 있다)."""
    scope = _inner_scope(request.scope, target, query, peer_id)
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": data, "more_body": False}
        # 본문을 다 준 뒤엔 연결이 끊길 때까지 기다린다(스트리밍 응답이 이걸로
        # 끊김을 감지한다).
        return await request.receive()

    q: asyncio.Queue = asyncio.Queue(maxsize=64)

    async def send(message):
        await q.put(message)

    async def run():
        try:
            await request.app.router(scope, receive, send)
        except Exception as e:  # noqa: BLE001 — 안쪽 오류는 500으로 보이게
            logger.warning(f"[relay] {target} 처리 중 오류: {e}")
            await q.put({"type": "http.response.start", "status": 500,
                         "headers": [(b"content-type", b"application/json")]})
            await q.put({"type": "http.response.body",
                         "body": b'{"error":"relay_internal"}', "more_body": False})
        finally:
            await q.put(None)

    task = asyncio.create_task(run())
    start = await q.get()
    if start is None or start.get("type") != "http.response.start":
        task.cancel()
        return _denied(tr("응답이 없습니다"), 502, "relay_empty")

    headers = {RELAY_HEADER: "1"}
    for k, v in start.get("headers", []):
        name = k.decode("latin-1").lower()
        if name in _PASS_RESPONSE_HEADERS:
            headers[name] = v.decode("latin-1")

    async def body():
        try:
            while True:
                msg = await q.get()
                if msg is None:
                    return
                if msg.get("type") == "http.response.body":
                    chunk = msg.get("body", b"")
                    if chunk:
                        yield chunk
                    if not msg.get("more_body", False):
                        return
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(body(), status_code=start.get("status", 200),
                             headers=headers)


@router.websocket("/api/peer/relay-ws/{rest:path}")
async def peer_relay_ws(ws: WebSocket, rest: str):
    query = ws.url.query
    try:
        grant = _authenticate_raw("GET", _signed_path(ws.url.path, query), ws.headers)
    except PeerDenied as e:
        await ws.accept()
        await ws.close(code=4401, reason=e.reason[:120])
        return
    target = "/" + rest
    if grant.get("level") != host_store.LEVEL_CONTROL:
        host_store.audit(grant["id"], "relay-ws", False, f"control 등급 필요: {target}")
        await ws.accept()
        await ws.close(code=4403, reason="control level required")
        return
    if not relay_policy.ws_allowed(target):
        host_store.audit(grant["id"], "relay-ws", False, f"허용 목록 밖: {target}")
        await ws.accept()
        await ws.close(code=4403, reason="relay forbidden")
        return
    host_store.audit(grant["id"], "relay-ws", True, target)
    scope = _inner_scope(ws.scope, target, relay_policy.strip_token(query), grant["id"])
    # 아직 accept하지 않은 소켓을 안쪽 라우트에 그대로 넘긴다 — accept·송수신·
    # 종료 코드 전부 안쪽 라우트가 정한다(4004 "세션 없음" 같은 뜻이 그대로 간다).
    # 바깥 WebSocket 객체는 이후 쓰지 않으므로 그 상태 추적과 어긋나도 괜찮다.
    watchdog = asyncio.create_task(_revocation_watchdog(ws, grant["id"]))
    try:
        await ws.app.router(scope, ws._receive, ws._send)  # noqa: SLF001
    finally:
        watchdog.cancel()


async def _revocation_watchdog(ws: WebSocket, peer_id: str, interval: float = 30.0):
    """연결 중에 `fsh host rm`/`deny-control`이 되면 열린 소켓도 끊는다. 세션
    쿠키 만료를 연결 내내 재검사하는 `auth.spawn_session_watchdog`의 peer판 —
    없으면 취소한 상대가 이미 열어둔 터미널로 계속 입력할 수 있다."""
    while True:
        await asyncio.sleep(interval)
        g = await asyncio.to_thread(host_store.find_grant, peer_id)
        if g is None or g.get("level") != host_store.LEVEL_CONTROL:
            host_store.audit(peer_id, "relay-ws", False, "연결 중 권한 회수 — 끊음")
            try:
                await ws._send({"type": "websocket.close", "code": 4401,  # noqa: SLF001
                                "reason": "peer revoked"})
            except Exception:  # noqa: BLE001 — 이미 닫혔으면 할 일이 없다
                pass
            return
