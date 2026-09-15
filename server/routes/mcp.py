"""MCP 서버 조회·토글 API (97번 1단계 3/n).

읽기와 쓰기를 라우터 두 개로 나눈다(R4 — 핸들러마다 승격 검사를 붙이면
하나라도 빠뜨린다). git 계정 API와 같은 구조다:

- `router`          : `GET /api/mcp` — 조회
- `elevated_router` : `POST /api/mcp/toggle` — **토글은 승격이 필요하다.**
  MCP 서버를 켜고 끄는 건 곧 에이전트가 어떤 도구를 부를 수 있는지를 바꾸는
  일이고, 이 UI는 터널 너머 공개 인터넷에 노출된다.

두 핸들러 모두 **스레드로 offload** 한다. `~/.claude.json`은 이 맥에서만
238KB고(프로젝트 69개) 워크트리 탐색은 git을 부른다 — 이벤트 루프에서
그대로 돌리면 전체 서버가 멈춘다(과거 E2E 스모크가 실제로 이렇게 멈췄다).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

import mcp_scan
import mcp_write
from auth import require_elevated

logger = logging.getLogger(__name__)

router = APIRouter()
elevated_router = APIRouter(dependencies=[Depends(require_elevated)])


@router.get("/api/mcp")
async def list_mcp(worktree: str | None = Query(default=None)):
    """워크트리 하나(+ 전역)를 기준으로 정의된 MCP 서버를 모은다.

    저장된 것을 돌려주는 게 아니라 **부를 때마다 실제 파일을 읽는다** — 그래서
    사용자가 터미널에서 직접 고친 것도 항상 최신으로 보인다(97번 §1-1).
    """
    try:
        result = await asyncio.to_thread(mcp_scan.scan, worktree)
    except Exception:
        logger.exception("mcp scan 실패")
        return JSONResponse({"error": "scan_failed"}, status_code=500)

    result["groups"] = mcp_scan.group_by_name(result["servers"])
    return result


@elevated_router.post("/api/mcp/toggle")
async def toggle_mcp(request: Request):
    """서버 하나를 스코프 하나에서 켜거나 끈다.

    **토글(뒤집기)이 아니라 목표 상태 지정이다** — `enabled`를 그대로 받는다.
    부분 실패 뒤 같은 요청을 다시 보내도 안전하다(멱등).

    응답은 세 가지다(97번 §3-3):
    - 200 `status:"ok"`      — 쓰고 다시 읽어 확인까지 됐다
    - 200 `status:"unknown"` — **썼는데 확인하지 못했다.** 성공이라고도 실패라고도
      말하지 않는다. 화면은 이 상태를 그대로 보여주고 재시도를 권해야 한다.
    - 409 `status:"failed"`  — 안전하게 쓸 수 없어 **파일을 건드리지 않았다**
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    tool = str(body.get("tool", "") or "")
    name = str(body.get("name", "") or "")
    scope = str(body.get("scope", "global") or "global")
    worktree_id = body.get("worktree") or None
    shared = bool(body.get("shared", False))
    enabled = body.get("enabled")

    if tool not in mcp_write.TOOLS:
        return JSONResponse(
            {"error": "invalid_tool", "reason": f"tool은 {sorted(mcp_write.TOOLS)} 중 하나"},
            status_code=400,
        )
    if not name:
        return JSONResponse({"error": "missing_name"}, status_code=400)
    if not isinstance(enabled, bool):
        # 문자열 "false"를 참으로 읽어 서버를 켜버리는 사고를 막는다.
        return JSONResponse(
            {"error": "invalid_enabled", "reason": "enabled는 true/false 여야 한다"},
            status_code=400,
        )
    if scope not in ("global", "local"):
        return JSONResponse({"error": "invalid_scope"}, status_code=400)

    result = await asyncio.to_thread(
        mcp_write.set_enabled,
        tool, name, enabled,
        scope=scope, worktree_id=worktree_id, shared=shared,
    )

    if result["status"] == "failed":
        return JSONResponse({"error": "write_refused", **result}, status_code=409)
    return result
