"""워크트리 API (N8/N44, 30-worktree.md §2)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import fsguard
import worktree

logger = logging.getLogger(__name__)

router = APIRouter()


async def _broadcast_worktrees_changed() -> None:
    """§2: 변경을 `/ws-workspace`로 push. 연결이 없거나 브로드캐스트 헬퍼가
    아직 없는 환경(테스트 등)에서도 조용히 넘어간다 — 레일은 폴링으로도 동작한다."""
    try:
        import routes.system as system_routes
        await system_routes.broadcast_workspace_event({"type": "worktrees_changed"})
    except Exception:
        pass


@router.get("/api/worktrees")
async def list_worktrees():
    return {"worktrees": worktree.list_worktrees()}


@router.get("/api/worktrees/precheck")
async def precheck_worktree(repo: str, base: str):
    try:
        return worktree.precheck(repo, base)
    except fsguard.FsDenied as e:
        return JSONResponse({"error": e.reason}, status_code=403)


@router.post("/api/worktrees")
async def create_worktree(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "잘못된 요청 본문"}, status_code=400)
    try:
        result = worktree.create_worktree(body)
    except worktree.WorktreeError as e:
        return JSONResponse(e.payload, status_code=e.status)
    await _broadcast_worktrees_changed()
    return result


@router.delete("/api/worktrees/{wt_id}")
async def delete_worktree(wt_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    force = bool(body.get("force"))
    kill_sessions = bool(body.get("killSessions"))
    try:
        result = worktree.delete_worktree(wt_id, force=force, kill_sessions=kill_sessions)
    except worktree.WorktreeError as e:
        return JSONResponse(e.payload, status_code=e.status)
    await _broadcast_worktrees_changed()
    return result


@router.post("/api/worktrees/{wt_id}/open")
async def open_worktree(wt_id: str):
    try:
        result = worktree.open_worktree(wt_id)
    except worktree.WorktreeError as e:
        return JSONResponse(e.payload, status_code=e.status)
    await _broadcast_worktrees_changed()
    return result
