"""프롬프트 큐 API (P4)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import queue_runner
import queue_store
import tmux_target

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/queue")
async def get_queue():
    items = queue_store.list_items()
    # U9: "자동" 타깃 드롭다운/문구가 실제로 어느 세션을 가리키는지 눈에 보이게.
    # 큐/음성이 공유하는 같은 판정(tmux_target.resolve_voice_target_pane)을 그대로 재사용.
    pane, mode = tmux_target.resolve_voice_target_pane()
    auto_target_label = tmux_target.pane_label(pane) if pane else None
    return {
        "items": items,
        "pending": sum(1 for x in items if x.get("status") == queue_store.STATUS_PENDING),
        "autodrain": queue_runner.autodrain_enabled(),
        "max": queue_store.MAX_ITEMS,
        "auto_target": auto_target_label,
        "auto_target_mode": mode,
    }


@router.post("/api/queue")
async def add_item(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    r = queue_store.add(body.get("text", ""), body.get("target"))
    if not r.get("ok"):
        status = 409 if r.get("error") == "full" else 400
        return JSONResponse(r, status_code=status)
    return r


@router.delete("/api/queue/{item_id}")
async def delete_item(item_id: str):
    if item_id == "all":
        return queue_store.clear()
    r = queue_store.remove(item_id)
    if not r.get("ok"):
        return JSONResponse(r, status_code=404)
    return r


@router.post("/api/queue/{item_id}/unblock")
async def unblock_item(item_id: str):
    r = queue_store.unblock(item_id)
    if not r.get("ok"):
        return JSONResponse(r, status_code=404)
    return r


@router.post("/api/queue/run")
async def run_queue():
    """수동 드레인 — 한 건 투입. codex/aider 처럼 stop 훅이 없는 에이전트의 유일한 경로."""
    r = await queue_runner.drain_now()
    if not r.get("ok"):
        status = 403 if r.get("error") == "blocked" else 409
        return JSONResponse(r, status_code=status)
    return r
