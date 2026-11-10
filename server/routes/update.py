"""업데이트 확인·자동 적용 설정 API (`update_check.py`). 표: [`API.md`](../../API.md) «Update».

- `GET /api/update/status`, `POST /api/update/check` — **사람 세션**(`require_human_session`)만. 에이전트·peer·기계 토큰·소켓은 403,
  호스트 중계 허용 목록(`relay_policy.py`)에도 없다(이 맥의 업데이트만 이 맥 화면에서 본다).
- `PUT /api/update/settings` — 네트워크에서 받은 코드를 실행하게 만드는 스위치이므로 **사람 세션 + 승격**(웹 비밀번호 재확인).
  감사 체인(`authz/audit.py`)에 「어느 키를 어느 값으로」만 남긴다(`started` 는 필수 — 못 남기면 503, 바꾸지 않는다).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import auth
import update_check
import updater
from authz import audit
from i18n import tr

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(auth.require_human_session)])
elevated_router = APIRouter(dependencies=[Depends(auth.require_human_session), Depends(auth.require_elevated)])

MANUAL_MIN_INTERVAL = 10.0     # 수동 확인 사이 최소 간격(초) — 원격 fetch 를 연달아 때리지 않게
_last_manual = -MANUAL_MIN_INTERVAL


def root() -> Path:
    return Path(os.environ.get("VT_DIR") or updater.ROOT)


async def notify(msg: dict) -> None:
    from routes import system
    await system.broadcast_workspace_event(msg)


@router.get("/api/update/status")
async def update_status():
    return JSONResponse(await asyncio.to_thread(update_check.public_status), headers={"Cache-Control": "no-store"})


@router.post("/api/update/check")
async def update_check_now():
    global _last_manual
    if not update_check.check_enabled():
        return JSONResponse({"error": "update_check_disabled",
                             "reason": tr("업데이트 확인이 꺼져 있어 네트워크 요청을 하지 않습니다")}, status_code=409)
    now = time.monotonic()
    ran = False
    if now - _last_manual >= MANUAL_MIN_INTERVAL:
        _last_manual = now
        try:
            _, ran = await update_check.manual_check(root(), notify)
        except Exception as e:  # noqa: BLE001 — 풀 기한/포화 포함. 서버는 멀쩡해야 한다
            logger.warning(f"[update] 수동 확인 실패: {type(e).__name__}: {e}")
            return JSONResponse({"error": "check_failed", "reason": tr("업데이트를 확인하지 못했습니다")}, status_code=503)
    body = await asyncio.to_thread(update_check.public_status)
    return JSONResponse({"ran": ran, "status": body}, headers={"Cache-Control": "no-store"})


def _audit(request: Request, patch: dict, result: str, *, must: bool = False) -> JSONResponse | None:
    owner = auth.session_device(request.cookies.get("vt_session", "")) or "-"
    rec = {"principal": f"human:{owner}", "action": "update.settings", "resource": "update:local",
           "scopes": ["update.settings"], "decision": "allow", "result": result,
           "detail": {"changes": {k: patch[k] for k in sorted(patch)}}}
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        if must:
            logger.warning(f"업데이트 설정 감사 기록 실패 — 거부: {type(e).__name__}: {e}")
            return JSONResponse({"error": "audit_unavailable", "reason": tr("감사 기록을 쓸 수 없어 요청을 거부했습니다")},
                                status_code=503)
        logger.warning(f"업데이트 설정 감사 기록 실패: {e}")
    return None


@elevated_router.put("/api/update/settings")
async def update_settings(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    try:
        patch = update_check.validate_patch(body)
    except ValueError as e:
        return JSONResponse({"error": "bad_request", "reason": str(e)}, status_code=400)
    refused = await asyncio.to_thread(_audit, request, patch, "started", must=True)
    if refused is not None:
        return refused
    try:
        await asyncio.to_thread(update_check.save_settings, patch)
    except Exception as e:  # noqa: BLE001
        await asyncio.to_thread(_audit, request, patch, "failed")
        logger.warning(f"[update] 설정 저장 실패: {type(e).__name__}: {e}")
        return JSONResponse({"error": "save_failed", "reason": tr("설정을 저장하지 못했습니다")}, status_code=500)
    await asyncio.to_thread(_audit, request, patch, "ok")
    await notify({"type": "update_status", "kind": "settings"})
    return await asyncio.to_thread(update_check.public_status)
