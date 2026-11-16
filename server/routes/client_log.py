"""화면 오류·경고 기록 API — **사람 세션만**(기계 토큰·소켓 주체·다른 맥은 403), 다른 맥 중계 밖.

- `POST /api/client-log {events:[…]}` — 브라우저가 모아 둔 오류·경고를 쓴다(최대 50건·64KB). 형식·가리기·상한은 `client_log.py`.
- `GET /api/client-log?limit=&before=&level=&q=` — 최신순. 같은 id 는 가장 새 줄만.
- `DELETE /api/client-log` — 서버에 쌓인 기록을 모두 지운다.

에이전트·다른 맥이 이 기록을 쓰거나 읽거나 지우지 못하게 `relay_policy.py`·소켓 허용표에는 넣지 않는다.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

import auth
import client_log
from i18n import tr

router = APIRouter(dependencies=[Depends(auth.require_human_session)])


async def _read_body(request: Request) -> bytes | None:
    """본문을 상한까지만 읽는다. 넘으면 None — 청크 전송으로 Content-Length 를 속여도 메모리에 다 올리지 않는다."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > client_log.MAX_BODY_BYTES:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > client_log.MAX_BODY_BYTES:
            return None
    return bytes(buf)


@router.post("/api/client-log")
async def post_client_log(request: Request):
    raw = await _read_body(request)
    if raw is None:
        return JSONResponse({"error": "too_large", "reason": tr("기록 묶음이 너무 큽니다")}, status_code=413)
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return JSONResponse({"error": "bad_json", "reason": tr("JSON을 읽을 수 없습니다")}, status_code=400)
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        return JSONResponse({"error": "bad_request", "reason": tr("events 배열이 필요합니다")}, status_code=400)
    if len(events) > client_log.MAX_BATCH:
        return JSONResponse({"error": "too_large", "reason": tr("기록 묶음이 너무 큽니다")}, status_code=413)
    try:
        r = await asyncio.to_thread(client_log.append, events)
    except client_log.RateLimited as e:
        return JSONResponse({"error": "rate_limited", "reason": tr("기록이 너무 빠르게 들어옵니다")}, status_code=429,
                            headers={"Retry-After": str(e.retry_after)})
    return {"ok": True, **r}


@router.get("/api/client-log")
async def get_client_log(limit: int = Query(200, ge=1, le=500), before: float | None = Query(None),
                         level: str | None = Query(None, pattern="^(error|warn)$"), q: str = Query("", max_length=100)):
    items = await asyncio.to_thread(client_log.read, limit, before=before, level=level, q=q)
    return {"items": items}


@router.delete("/api/client-log")
async def delete_client_log():
    return {"ok": True, "removed": await asyncio.to_thread(client_log.clear)}
