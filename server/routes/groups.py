"""세션 그룹 API — ADR-29(세션 중심 재설계) A단계.

지금은 어느 화면도 이 라우트를 안 부른다 — B/C/D단계가 레일 행·탭·드래그를
세션 단위로 바꿀 때 이 위에 얹인다. 화면보다 먼저 세우는 이유는 1단계
(`@fsh_wt`)와 같다: 값을 심는 자리부터 만들고, 화면은 그 값을 읽기만 하게
한다.

그룹 자체의 CRUD(이름·순서)는 `group_store.py`가 맡고, 세션 하나를 그룹에
"넣고 빼는" 것은 여기서 tmux에 직접 쓴다(`@fsh_grp`) — 그 값의 주인은 파일이
아니라 tmux 세션 자신이기 때문이다(group_store.py 머리말 참고).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import group_store
import tmux_runner

router = APIRouter()

_GROUP_ID_RE = re.compile(r"[0-9a-f]{12}")
_SESSION_NAME_RE = re.compile(r"[A-Za-z0-9_\-]+")


@router.get("/api/groups")
async def list_groups():
    return {"groups": group_store.list_groups()}


@router.patch("/api/groups/{group_id}")
async def rename_group(group_id: str, request: Request):
    if not _GROUP_ID_RE.fullmatch(group_id):
        return JSONResponse({"error": "유효하지 않은 그룹 id"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "잘못된 요청 본문"}, status_code=400)
    label = body.get("label")
    if not isinstance(label, str) or not label.strip():
        return JSONResponse({"error": "label이 필요합니다"}, status_code=400)
    result = group_store.set_label(group_id, label)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/api/groups/reorder")
async def reorder_groups(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    order = body.get("order") if isinstance(body, dict) else None
    if not isinstance(order, list):
        return JSONResponse({"error": "order가 필요합니다"}, status_code=400)
    result = group_store.set_order(order)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/api/tmux/{name}/group")
async def set_session_group(name: str, request: Request):
    """세션 하나를 그룹에 넣거나(`groupId`) 뺀다(`groupId: null` → 「묶지 않음」).

    tmux 세션 자신에게 적으므로(`@fsh_wt`와 같은 메커니즘) 세션이 자고 있어도
    (웹 세션이 없어도) 소속이 유지된다 — 그게 그룹째 재웠다 통째로 깨우는
    동작(D단계)의 전제다.
    """
    if not _SESSION_NAME_RE.fullmatch(name):
        return JSONResponse({"error": "유효하지 않은 세션 이름"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "잘못된 요청 본문"}, status_code=400)
    group_id = body.get("groupId")
    if group_id is not None and (not isinstance(group_id, str) or not _GROUP_ID_RE.fullmatch(group_id)):
        return JSONResponse({"error": "groupId가 유효하지 않습니다"}, status_code=400)
    if not await tmux_runner.has_session_async(name):
        return JSONResponse({"error": "세션을 찾을 수 없습니다"}, status_code=404)
    ok = await tmux_runner.set_option_async(name, "@fsh_grp", group_id or "")
    if not ok:
        return JSONResponse({"error": "그룹 설정 실패"}, status_code=500)
    return {"ok": True, "name": name, "groupId": group_id}
