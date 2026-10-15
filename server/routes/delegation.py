"""에이전트 위임 API (docs/plan-2.4/50-agent-delegation.md).

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
            server_port=server[1] if server else None,
            peer_pid=((request.scope.get("fsh_peer") or {}).get("pid")))
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


def _actor(request: Request):
    """사람의 주체(켜짐에서 엔진·감사에 쓴다). 꺼져 있거나 만들지 못하면 None."""
    if not delegation.enforced():
        return None
    try:
        from authz import principals as P
        return P.from_request(request)
    except Exception:  # noqa: BLE001
        return None


@human_router.post("/api/delegations/{did}/close-by-user")
async def close_by_user(did: str, request: Request):
    try:
        r = await asyncio.to_thread(delegation.close, did, reason="closed_by_user", actor=_actor(request))
    except DelegationError as e:
        return _err(e)
    import routes.system as _system_routes
    _system_routes.kick_sessions()
    return r


@router.post("/api/delegations/{did}/close-by-cli")
async def close_by_cli(did: str, request: Request):
    """`fsh delegate close` 의 서버 경로(H3) — **unix socket 로만** 닿는다(TCP·기계 토큰은 403).

    주체는 소켓 peer 가 정한다: pane 밖 프로세스 = `cli` — 위임을 **닫기만** 할 수 있다(보내기·승인·권한 변경 불가, D1=b).
    pane 안 프로세스 = `agent` — 그 위임의 부모일 때만(`/close` 와 같은 소유 검사). 그 밖(svc·hook)은 403."""
    sock = request.scope.get("fsh_sock") is True
    p = getattr(request.state, "fsh_principal", None) if sock else None
    if p is None:
        return JSONResponse({"error": "socket_required", "reason": "이 경로는 unix socket 으로만 열려 있습니다"}, status_code=403)
    try:
        if p.kind == "cli":
            r = await asyncio.to_thread(delegation.close, did, reason="closed_by_user", actor=p)
        elif p.kind == "agent" and p.attrs.get("key"):
            r = await asyncio.to_thread(delegation.close, did, caller_key=p.attrs["key"])
        else:
            return JSONResponse({"error": "forbidden", "reason": "이 주체는 위임을 닫을 수 없습니다"}, status_code=403)
    except DelegationError as e:
        return _err(e)
    import routes.system as _system_routes
    _system_routes.kick_sessions()
    return r
