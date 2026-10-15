"""에이전트 위임 API.

호출자는 `share_identity.verify_caller`로 확정한다(`X-Fsh-Pane`/`X-Fsh-Tmux`/`X-Fsh-Pid` + 루프백 소켓
소유 프로세스의 부모 체인) — 기계 토큰은 모든 에이전트가 같은 값이라 "어느 세션인가"를 가리지 못한다.
본문으로 부모·자식을 주장할 통로는 없다. relay 허용 목록에 없으므로 다른 맥에서는 쓸 수 없다.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import auth

import delegation
import share_identity
from delegation import DelegationError

logger = logging.getLogger(__name__)

router = APIRouter()
# 사람용 — 브라우저 로그인 세션 쿠키만(기계 토큰은 403). 레일 행 메뉴의 「위임 닫기」가 쓴다.
human_router = APIRouter(dependencies=[Depends(auth.require_human_session)])


def _err(e: DelegationError) -> JSONResponse:
    return JSONResponse(e.body(), status_code=e.status)


async def _caller(request: Request):
    h = request.headers
    pane, tmux_env, pid = (unquote(h.get(k, "")) for k in ("x-fsh-pane", "x-fsh-tmux", "x-fsh-pid"))
    server = request.scope.get("server")
    try:
        return await asyncio.to_thread(
            share_identity.verify_caller, pane, tmux_env, pid,
            client=tuple(request.client) if request.client else None,
            headers={k.lower(): v for k, v in h.items()},
            server_port=server[1] if server else None)
    except share_identity.IdentityError as e:
        await asyncio.to_thread(delegation._audit, "identify", "identity_unverified", reason=e.reason)
        raise DelegationError("identity_unverified", e.reason, 403) from None


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        return {}
    return b if isinstance(b, dict) else {}


def _parent_agent(name: str) -> str:
    try:
        import agent_detector
        info = agent_detector.detect(name) or {}
        return str(info.get("agent") or "")
    except Exception:  # noqa: BLE001
        return ""


@router.get("/api/delegations/role")
async def role(request: Request):
    """MCP가 tools/list 때 묻는다 — 깊이 한도에 닿은 세션에는 spawn_session을 보여주지 않는다."""
    try:
        caller = await _caller(request)
        return await asyncio.to_thread(delegation.role_of, caller.key)
    except DelegationError as e:
        return _err(e)


@router.get("/api/delegations")
async def list_mine(request: Request):
    try:
        caller = await _caller(request)
        items = await asyncio.to_thread(delegation.list_all)
    except DelegationError as e:
        return _err(e)
    mine = [delegation.public(x, caller.key) for x in items
            if caller.key in (x["parent"]["key"], x["child"]["key"])]
    return {"items": mine}


@router.post("/api/delegations")
async def spawn(request: Request):
    body = await _body(request)
    wt = body.get("worktree")
    try:
        caller = await _caller(request)
        parent_agent = await asyncio.to_thread(_parent_agent, caller.name)
        r = await asyncio.to_thread(
            delegation.spawn, caller,
            task=str(body.get("task") or ""), title=str(body.get("title") or ""),
            agent=str(body.get("agent") or ""), cwd=str(body.get("cwd") or ""),
            worktree_branch=str((wt or {}).get("branch") or "") if isinstance(wt, dict) else "",
            parent_agent=parent_agent)
    except DelegationError as e:
        return _err(e)
    import routes.system as _system_routes
    _system_routes.kick_sessions()
    return {"ok": True, **r}


@router.post("/api/delegations/report")
async def report(request: Request):
    body = await _body(request)
    try:
        caller = await _caller(request)
        r = await asyncio.to_thread(delegation.report, caller.key,
                                    str(body.get("outcome") or ""), str(body.get("summary") or ""))
    except DelegationError as e:
        return _err(e)
    import routes.system as _system_routes
    _system_routes.kick_sessions()
    return r


@router.post("/api/delegations/{did}/send")
async def send(did: str, request: Request):
    body = await _body(request)
    try:
        caller = await _caller(request)
        return await asyncio.to_thread(delegation.send_to_child, caller.key, did, str(body.get("text") or ""))
    except DelegationError as e:
        return _err(e)


@router.post("/api/delegations/{did}/close")
async def close(did: str, request: Request):
    try:
        caller = await _caller(request)
        r = await asyncio.to_thread(delegation.close, did, caller_key=caller.key)
    except DelegationError as e:
        return _err(e)
    import routes.system as _system_routes
    _system_routes.kick_sessions()
    return r


@human_router.post("/api/delegations/{did}/close-by-user")
async def close_by_user(did: str):
    try:
        r = await asyncio.to_thread(delegation.close, did, reason="closed_by_user")
    except DelegationError as e:
        return _err(e)
    import routes.system as _system_routes
    _system_routes.kick_sessions()
    return r
