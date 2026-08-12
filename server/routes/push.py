"""Web Push 구독 관리 (P5)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import push

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/push/status")
async def push_status():
    return await asyncio.to_thread(push.status)


@router.get("/api/push/key")
async def push_key():
    if not push.available():
        return JSONResponse(
            {"error": "unavailable", "reason": "pywebpush 미설치"}, status_code=501)
    key = await asyncio.to_thread(push.public_key)
    if not key:
        return JSONResponse({"error": "no_keys"}, status_code=500)
    return {"key": key}


@router.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    if not push.available():
        return JSONResponse({"error": "unavailable"}, status_code=501)
    try:
        body = await request.json()
    except Exception:
        body = {}
    sub = body.get("subscription")
    if not isinstance(sub, dict) or not sub.get("endpoint"):
        return JSONResponse({"ok": False, "error": "bad_subscription"}, status_code=400)
    # origin 은 클라이언트가 보낸 값보다 실제 요청 헤더를 신뢰한다.
    origin = (request.headers.get("origin") or body.get("origin") or "").rstrip("/")
    r = await asyncio.to_thread(push.add_sub, sub, origin, body.get("label", ""))
    if not r.get("ok"):
        return JSONResponse(r, status_code=400)
    return r


@router.delete("/api/push/subscribe")
async def push_unsubscribe(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    ep = body.get("endpoint", "")
    if not ep:
        return JSONResponse({"ok": False, "error": "endpoint_required"}, status_code=400)
    return await asyncio.to_thread(push.remove_sub, ep)


@router.post("/api/push/test")
async def push_test():
    r = await asyncio.to_thread(push.send, "VT 테스트 알림", "푸시가 정상 동작합니다", "/")
    if not r.get("ok"):
        return JSONResponse(r, status_code=501)
    return r
