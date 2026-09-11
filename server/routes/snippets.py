"""프롬프트 스니펫 API (L3)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import snippet_store

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/snippets")
async def get_snippets():
    return {"items": snippet_store.list_items(), "max": snippet_store.MAX_ITEMS}


# N6(60 §4) — 「프로젝트」탭이 지금 보고 있는 스니펫이 어느 project 키에
# 속하는지 판정하려면, 활성 페인의 cwd를 저장소 top으로 바꿔야 한다. 그
# 판정 로직(fsguard 검증 + `git rev-parse --show-toplevel`)은 저장(add)과
# 이 조회가 정확히 같은 값을 내야 탭에 항목이 안 걸리는 사고가 안 난다 —
# 그래서 snippet_store.resolve_project_key 하나를 공유한다.
@router.get("/api/snippets/project")
async def get_project_key(cwd: str = Query(...)):
    return {"project": snippet_store.resolve_project_key(cwd)}


@router.post("/api/snippets")
async def add_snippet(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    r = snippet_store.add(
        body.get("text", ""),
        body.get("label"),
        scope=body.get("scope") or snippet_store.SCOPE_GLOBAL,
        cwd=body.get("cwd"),
        mode=body.get("mode"),
    )
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


@router.patch("/api/snippets/{item_id}")
async def update_snippet_mode(item_id: str, request: Request):
    """지금은 mode만 바꾼다 — 텍스트·라벨 수정은 스코프 밖(지우고 다시 저장)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = body.get("mode")
    if not mode:
        return JSONResponse({"error": "missing_mode"}, status_code=400)
    r = snippet_store.set_mode(item_id, mode)
    if not r.get("ok"):
        status = 404 if r.get("error") == "not_found" else 400
        return JSONResponse(r, status_code=status)
    return r
