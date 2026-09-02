"""프롬프트 스니펫 API (L3)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import snippet_store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/snippets")
async def get_snippets():
    return {"items": snippet_store.list_items(), "max": snippet_store.MAX_ITEMS}


@router.post("/api/snippets")
async def add_snippet(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    r = snippet_store.add(body.get("text", ""), body.get("label"))
    if not r.get("ok"):
        status = 409 if r.get("error") == "full" else 400
        return JSONResponse(r, status_code=status)
    return r


@router.delete("/api/snippets/{item_id}")
async def delete_snippet(item_id: str):
    r = snippet_store.remove(item_id)
    if not r.get("ok"):
        return JSONResponse(r, status_code=404)
    return r
