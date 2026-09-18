"""워크트리 API (N8/N44, 30-worktree.md §2)."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import fsguard
import repo_store
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
async def list_worktrees(include_hidden: int = 0):
    # `worktree.list_worktrees()`는 저장소마다 git 서브프로세스를 돌리는 동기
    # 작업이라 실측 1~2초가 걸린다. 이벤트 루프에서 직접 부르면 그동안 서버
    # 전체가 멈춘다 — HTTP도 WebSocket도 PTY 출력 브로드캐스트도. 같은 이유로
    # routes/git.py의 git_status도 to_thread를 쓴다.
    #
    # 숨김 필터는 **여기(라우트)에서** 건다. worktree.list_worktrees()는 캐시의
    # 원본이고 mcp_scan 같은 다른 소비자도 쓰는데, 거기서 걸러버리면 "레일에서
    # 숨겼다"가 "MCP 탐색에서도 사라졌다"가 된다 — 숨김은 표시 설정이지
    # 경계 설정이 아니다(경계는 VT_BROWSE_ROOTS다).
    items, hidden = await asyncio.gather(
        asyncio.to_thread(worktree.list_worktrees),
        asyncio.to_thread(repo_store.hidden_set),
    )
    if include_hidden:
        out = [
            {**w, "hidden": repo_store.is_hidden(w, hidden)}
            for w in items
        ]
    else:
        out = [w for w in items if not repo_store.is_hidden(w, hidden)]
    return {
        "worktrees": out,
        # §1 — 탐색이 MAX_REPOS에서 잘렸다. 레일이 "목록이 전부가 아니다"를
        # 사용자에게 말할 수 있어야 한다(조용히 자르면 §1과 같은 버그로 읽힌다).
        "truncated": worktree.last_scan_truncated(),
        "hiddenCount": sum(1 for w in items if repo_store.is_hidden(w, hidden)),
        # §3 시트 하단의 "왜 이것들이 목록에 있나" — 읽기 전용 표시.
        "roots": [str(r) for r in fsguard.get_roots()],
    }


@router.post("/api/worktrees/hidden")
async def set_worktree_hidden(request: Request):
    """레일에서 저장소 하나를 숨기거나 되돌린다 (§3).

    경로 검증은 `fsguard.resolve_under_roots`에 맡긴다 — 경계 밖 경로를 설정에
    적을 이유가 없고, 검사 위치 단일 원칙(90-verification.md §4-3)이다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "잘못된 요청 본문"}, status_code=400)
    raw_path = body.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return JSONResponse({"error": "path가 필요합니다"}, status_code=400)
    try:
        path = fsguard.resolve_under_roots(raw_path)
    except fsguard.FsDenied as e:
        return JSONResponse({"error": e.reason}, status_code=403)
    hidden = bool(body.get("hidden", True))
    result = await asyncio.to_thread(repo_store.set_hidden, str(path), hidden)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    await _broadcast_worktrees_changed()
    return result


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
    # ADR-29 A0 — `git worktree add` + node_modules 복사(`shutil.copytree`,
    # 수십 초까지 가능)까지 포함한 동기 작업이다. to_thread 없이 직접 부르면
    # 그동안 서버 전체(HTTP·WS·PTY 출력)가 멎는다 — /api/worktrees(GET)이
    # 이미 같은 이유로 쓰고 있는 것과 같은 처방이다.
    try:
        result = await asyncio.to_thread(worktree.create_worktree, body)
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
        result = await asyncio.to_thread(worktree.delete_worktree, wt_id, force=force, kill_sessions=kill_sessions)
    except worktree.WorktreeError as e:
        return JSONResponse(e.payload, status_code=e.status)
    await _broadcast_worktrees_changed()
    return result


@router.post("/api/worktrees/{wt_id}/open")
async def open_worktree(wt_id: str):
    # ADR-29 A0 — `find_by_id(force=True)`가 캐시를 무시한 전체 재탐색을 돈다
    # (저장소 실측 1.0~1.7초). to_thread 없이 부르면 세션 없는 워크트리 행을
    # 한 번 클릭할 때마다 서버가 그만큼 멎는다.
    try:
        result = await asyncio.to_thread(worktree.open_worktree, wt_id)
    except worktree.WorktreeError as e:
        return JSONResponse(e.payload, status_code=e.status)
    await _broadcast_worktrees_changed()
    return result
