"""에이전트 세션 공유 — HTTP 면 (설계·규칙은 `agent_share.py` 머리말).

라우터가 둘이다. 이 구분이 이 파일의 보안 경계다:

  - `router`        **에이전트용**. 기계 토큰(Bearer, 서버의 평소 인증) + **호출자 식별**
                    (`share_identity.verify_caller` — `X-Fsh-Pane`/`X-Fsh-Tmux`/`X-Fsh-Pid`).
                    읽기·쓰기·목록뿐이다. 승인할 수 있는 것은 하나도 없다.
  - `human_router`  **사람용**. `Depends(auth.require_human_session)` — 브라우저 로그인 세션 쿠키만.
                    기계 토큰·peer 서명·무인증은 403. 공유 지정·해제, 승인 요청 조회·결정, 권한 조회·철회,
                    감사 기록 조회. 에이전트가 자기 접근을 스스로 승인하는 길이 없어야 해서 의존성을
                    라우터 전체에 건다(핸들러마다 붙이면 하나는 빠진다).

두 라우터 모두 relay 허용 목록(`relay_policy`)에 없다 — 다른 맥에서 이 기능을 쓸 수 없다.
사용자에게 알리는 길: `/ws-workspace` 의 `agent_share_request` 이벤트 + 웹 푸시(브라우저가 열려 있지
않을 때만; 본문에 세션 이름을 싣지 않는다).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

import agent_share
import auth
import share_identity
from i18n import tr
from agent_share import ShareError

logger = logging.getLogger(__name__)

router = APIRouter()
human_router = APIRouter(dependencies=[Depends(auth.require_human_session)])

_NAME_RE = re.compile(r"[A-Za-z0-9_\-.]{1,64}")


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class ShareInfo(_Loose):
    nick: str
    mode: str


class ShareSetResponse(_Loose):
    ok: bool
    name: str
    share: ShareInfo


class UnshareResponse(_Loose):
    ok: bool
    name: str
    revokedGrants: int
    clearedRequests: int


class SharedListResponse(_Loose):
    me: str
    sessions: list[dict]


class ReadResponse(_Loose):
    state: str
    nick: str
    text: str
    lines: int
    bytes: int
    truncated: bool


class SendResponse(_Loose):
    state: str
    nick: str
    chars: int
    enter: bool


class RequestsResponse(_Loose):
    requests: list[dict]


class DecisionResponse(_Loose):
    ok: bool
    request: dict
    grant: Optional[dict] = None


class GrantsResponse(_Loose):
    grants: list[dict]


class RevokeResponse(_Loose):
    ok: bool
    grant: dict


class AuditResponse(_Loose):
    entries: list[dict]


def _actor(request: Request):
    """결정한 사람의 주체(감사·승인권 확인용). 꺼져 있으면 None — 옛 경로는 주체를 쓰지 않는다."""
    if not agent_share.enforced():
        return None
    try:
        from authz import principals as P
        return P.from_request(request)
    except Exception:  # noqa: BLE001 — 주체를 못 만들면 권한 없는 사람으로(승인 못 함)
        return None


def _err(e: ShareError) -> JSONResponse:
    return JSONResponse(e.body(), status_code=e.status)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return b if isinstance(b, dict) else {}


# --- 사용자에게 알림 -----------------------------------------------------------------------


async def _broadcast(msg: dict) -> None:
    try:
        import routes.system as system_routes
        await system_routes.broadcast_workspace_event(msg)
    except Exception as e:  # noqa: BLE001 — 알림 실패가 호출을 막으면 안 된다
        logger.debug(f"공유 이벤트 알림 실패: {e}")


def _kick() -> None:
    try:
        import routes.system as system_routes
        system_routes.kick_sessions()
    except Exception:  # noqa: BLE001
        pass


def request_view(r: dict, snap: Optional[dict]) -> dict:
    """저장된 요청 → 화면용. 세션 이름·닉네임은 지금 값이다(이름이 바뀌어도 따라간다)."""
    snap = snap or {}
    a = snap.get(f"{r['agent']['sid']}:{r['agent']['created']}")
    t = snap.get(f"{r['target']['sid']}:{r['target']['created']}")
    return {"id": r["id"], "kind": r["kind"], "state": r["state"], "created": r["created"],
            "expires": r.get("expires"), "decided": r.get("decided"),
            "agent": {"name": a.name if a else r["agent"].get("name", ""), "id": r["agent"]["sid"]},
            "target": {"nick": ((t.share or {}).get("nick") if t else None), "mode": ((t.share or {}).get("mode") if t else None),
                       "name": t.name if t else None, "id": r["target"]["sid"]}}


async def _announce(req: dict, snap: dict) -> None:
    """새 보류 요청을 사용자에게. 열린 브라우저가 있으면 이벤트, 없으면 웹 푸시."""
    view = request_view(req, snap)
    await _broadcast({"type": "agent_share_request", "request": view})
    try:
        import push
        import routes.system as system_routes
        if not getattr(system_routes, "_workspace_clients", None) and push.available():
            # 잠금화면에 뜨는 글 — 세션 이름·닉네임·내용은 싣지 않는다.
            await asyncio.to_thread(push.send, tr("에이전트 접근 요청"), tr("공유 세션 사용 승인이 필요합니다"), "/")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"공유 요청 푸시 실패: {e}")


# --- 에이전트용 -------------------------------------------------------------------------------


async def _identify(request: Request, action: str, nick: Optional[str] = None):
    """호출자 식별. 실패하면 감사 기록을 남기고 ShareError(403 identity_unverified)."""
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
        await asyncio.to_thread(agent_share.audit, action, "identity_unverified", nick=nick, by="agent", reason=e.reason)
        raise ShareError("identity_unverified", e.reason, 403) from None


async def _snap() -> dict:
    snap = await asyncio.to_thread(agent_share.snapshot)
    if snap is None:
        raise ShareError("tmux_unavailable", "tmux 상태를 읽지 못했습니다", 503)
    return snap


def _pending_response(req: dict, kind: str) -> JSONResponse:
    what = "읽기" if kind == agent_share.KIND_READ else "쓰기"
    return JSONResponse({
        "state": "pending", "requestId": req["id"], "kind": kind,
        "reason": f"사용자 승인 대기 중({what}) — 승인되면 같은 호출을 다시 하세요"}, status_code=202)


@router.get("/api/agent/shared", response_model=SharedListResponse)
async def agent_list_shared(request: Request):
    """공유된 세션 — 닉네임·모드·상태와 **내** 권한 상태. 세션 이름·cwd 는 싣지 않는다."""
    try:
        me = await _identify(request, "list")
        snap = await _snap()
        state = await asyncio.to_thread(agent_share.list_state, snap)
    except ShareError as e:
        return _err(e)
    out = []
    for s in snap.values():
        if not s.share or s.key == me.key:
            continue
        g = next((g for g in state["grants"] if agent_share._same(g["agent"], me) and agent_share._same(g["target"], s)), None)
        eff = agent_share.effective(g, s.share)
        reqs = [r for r in state["requests"] if agent_share._same(r["agent"], me) and agent_share._same(r["target"], s)]

        def perm(kind: str, granted: bool) -> str:
            if granted:
                return "granted"
            if kind == "write" and s.share["mode"] != "readwrite":
                return "unavailable"   # 읽기 전용 공유 — 요청해도 즉시 거부된다
            mine = next((r for r in reqs if r["kind"] == kind), None)
            return mine["state"] if mine else "none"   # pending | denied | none

        out.append({"nick": s.share["nick"], "mode": s.share["mode"], "status": agent_share.agent_state_of(s.name),
                    "command": s.command,
                    "permission": {"read": perm("read", eff["read"]), "write": perm("write", eff["write"])}})
    out.sort(key=lambda x: x["nick"].lower())
    await asyncio.to_thread(agent_share.audit, "list", "ok", agent=me)
    return {"me": me.name, "sessions": out}


async def _target_for(request: Request, action: str, body: dict):
    nick = body.get("nickname")
    if not isinstance(nick, str) or not nick.strip():
        raise ShareError("bad_nick", "nickname 이 필요합니다", 400)
    me = await _identify(request, action, nick=nick[:32])
    snap = await _snap()
    target = agent_share.find_by_nick(snap, nick)
    if target.key == me.key:
        raise ShareError("self", "자기 자신의 세션은 대상이 될 수 없습니다", 400)
    return me, snap, target


@router.post("/api/agent/shared/read", response_model=ReadResponse)
async def agent_read(request: Request):
    body = await _body(request)
    nick = str(body.get("nickname") or "")[:32]
    try:
        lines = agent_share.check_lines(body.get("lines"))
        me, snap, target = await _target_for(request, "read", body)
    except ShareError as e:
        return _err(e)
    try:
        acc = await asyncio.to_thread(agent_share.authorize, "read", snap, me, target)
        if not acc.allowed:
            req, created = await asyncio.to_thread(agent_share.request_access, snap, me, target, agent_share.KIND_READ)
            await asyncio.to_thread(agent_share.audit, "request", "pending" if created else "pending_dup",
                                    agent=me, nick=target.share["nick"], kind="read", request=req["id"])
            if created:
                await _announce(req, snap)
            return _pending_response(req, "read")
        res = await asyncio.to_thread(agent_share.read_pane, target, lines)
    except ShareError as e:
        await asyncio.to_thread(agent_share.audit, "read", e.code, agent=me, nick=target.share["nick"])
        return _err(e)
    await asyncio.to_thread(agent_share.audit, "read", "ok", agent=me, nick=target.share["nick"],
                            lines=res["lines"], truncated=res["truncated"] or None, grant=acc.grant_id)
    out = {"state": "ok", "nick": target.share["nick"], **res}
    if acc.grant_id:
        out["grantId"] = acc.grant_id
    return out


@router.post("/api/agent/shared/send", response_model=SendResponse)
async def agent_send(request: Request):
    body = await _body(request)
    try:
        text = agent_share.check_send_text(body.get("text"))
        enter = body.get("enter", False)
        if not isinstance(enter, bool):
            raise ShareError("bad_enter", "enter 는 true/false 입니다", 400)
        me, snap, target = await _target_for(request, "send", body)
    except ShareError as e:
        return _err(e)
    nick = target.share["nick"]
    meta = {"chars": len(text), "sha8": agent_share.sha8(text), "enter": enter}
    try:
        if target.share["mode"] != agent_share.LEVEL_READWRITE:
            # 읽기 전용 공유 — 승인 요청을 만들지 않고 즉시 거부한다(허용해 줄 수 없는 것을 묻지 않는다).
            raise ShareError("read_only_share", "이 공유는 읽기 전용입니다", 403)
        acc = await asyncio.to_thread(agent_share.authorize, "write", snap, me, target)
        if not acc.allowed:
            req, created = await asyncio.to_thread(agent_share.request_access, snap, me, target, agent_share.KIND_WRITE)
            await asyncio.to_thread(agent_share.audit, "request", "pending" if created else "pending_dup",
                                    agent=me, nick=nick, kind="write", request=req["id"])
            if created:
                await _announce(req, snap)
            return _pending_response(req, "write")
        await asyncio.to_thread(agent_share.check_send_allowed, target)
        await asyncio.to_thread(agent_share.send_keys, target, text, enter)
    except ShareError as e:
        await asyncio.to_thread(agent_share.audit, "send", e.code, agent=me, nick=nick, **meta)
        return _err(e)
    await asyncio.to_thread(agent_share.audit, "send", "ok", agent=me, nick=nick, grant=acc.grant_id, **meta)
    out = {"state": "ok", "nick": nick, "chars": len(text), "enter": enter}
    if acc.grant_id:
        out["grantId"] = acc.grant_id
    return out


# --- 사람용 (브라우저 로그인 세션만) ------------------------------------------------------------


@human_router.post("/api/tmux/{name}/share", response_model=ShareSetResponse)
async def share_session(name: str, request: Request):
    """세션을 에이전트에게 공유(또는 닉네임·모드 변경). 모드를 낮추면 즉시 효력이 있다."""
    body = await _body(request)
    if not _NAME_RE.fullmatch(name):
        return _err(ShareError("not_found", tr("'{name}' 세션이 없습니다", name=name), 404))
    try:
        share = await asyncio.to_thread(agent_share.set_share, name, body.get("nick"), body.get("mode"), _actor(request))
    except ShareError as e:
        return _err(e)
    await asyncio.to_thread(agent_share.audit, "share", "ok", nick=share["nick"], mode=share["mode"])
    _kick()
    return {"ok": True, "name": name, "share": share}


@human_router.delete("/api/agent/share/sessions/{name}", response_model=UnshareResponse)
async def unshare_session(name: str, request: Request):
    """공유 해제 — 그 세션의 모든 권한과 대기·거부 요청도 지운다."""
    if not _NAME_RE.fullmatch(name):
        return _err(ShareError("not_found", tr("'{name}' 세션이 없습니다", name=name), 404))
    try:
        res = await asyncio.to_thread(agent_share.unset_share, name, _actor(request))
    except ShareError as e:
        return _err(e)
    was = res["was"] or {}
    await asyncio.to_thread(agent_share.audit, "unshare", "ok", nick=was.get("nick"))
    for rid in res["clearedRequests"]:
        await _broadcast({"type": "agent_share_request_resolved", "id": rid, "state": "cleared"})
    _kick()
    return {"ok": True, "name": name, "revokedGrants": res["revokedGrants"], "clearedRequests": len(res["clearedRequests"])}


@human_router.get("/api/agent/share/requests", response_model=RequestsResponse)
async def list_requests():
    """승인 대기 중인 요청(24시간 뒤 만료). 거부 쿨다운 기록은 싣지 않는다."""
    snap = await asyncio.to_thread(agent_share.snapshot)
    state = await asyncio.to_thread(agent_share.list_state, snap)
    return {"requests": [request_view(r, snap) for r in state["requests"] if r.get("state") == "pending"]}


@human_router.post("/api/agent/share/requests/{request_id}/decision", response_model=DecisionResponse)
async def decide_request(request_id: str, request: Request):
    body = await _body(request)
    snap = await asyncio.to_thread(agent_share.snapshot)
    level = body.get("level")
    if level is not None and not isinstance(level, str):
        return _err(ShareError("bad_level", tr("level 은 read 또는 readwrite 입니다"), 400))
    try:
        res = await asyncio.to_thread(agent_share.decide, request_id, body.get("decision"), level, snap, _actor(request))
    except ShareError as e:
        return _err(e)
    req, grant = res["request"], res["grant"]
    view = request_view(req, snap)
    allowed = grant is not None
    await asyncio.to_thread(
        agent_share.audit, "decide", "allowed" if allowed else "denied", nick=view["target"]["nick"],
        kind=req["kind"], level=("readwrite" if grant and grant.get("write") else "read") if allowed else None,
        request=req["id"], grant=grant["id"] if grant else None,
        reason=f"agent={view['agent']['name']}")
    await _broadcast({"type": "agent_share_request_resolved", "id": req["id"], "state": "allowed" if allowed else "denied"})
    out = {"ok": True, "request": view, "grant": grant_view(grant, snap) if grant else None}
    if res.get("grantId"):
        out["grantId"] = res["grantId"]
    return out


def grant_view(g: dict, snap: Optional[dict]) -> dict:
    snap = snap or {}
    a = snap.get(f"{g['agent']['sid']}:{g['agent']['created']}")
    t = snap.get(f"{g['target']['sid']}:{g['target']['created']}")
    share = (t.share if t else None) or {}
    eff = agent_share.effective(g, share or None)
    return {"id": g["id"], "read": bool(g.get("read")), "write": bool(g.get("write")),
            "effective": eff, "granted": g.get("granted"), "updated": g.get("updated"),
            "agent": {"name": a.name if a else g["agent"].get("name", ""), "id": g["agent"]["sid"]},
            "target": {"nick": share.get("nick"), "mode": share.get("mode"), "name": t.name if t else None,
                       "id": g["target"]["sid"]}}


@human_router.get("/api/agent/share/grants", response_model=GrantsResponse)
async def list_grants():
    snap = await asyncio.to_thread(agent_share.snapshot)
    state = await asyncio.to_thread(agent_share.list_state, snap)
    return {"grants": [grant_view(g, snap) for g in state["grants"]]}


@human_router.delete("/api/agent/share/grants/{grant_id}", response_model=RevokeResponse)
async def revoke_grant(grant_id: str, request: Request, scope: str = Query("all")):
    """권한 철회. `scope=write` 는 쓰기만 거두고 읽기는 남긴다."""
    snap = await asyncio.to_thread(agent_share.snapshot)
    try:
        g = await asyncio.to_thread(agent_share.revoke, grant_id, scope, snap, _actor(request))
    except ShareError as e:
        return _err(e)
    view = grant_view(g, snap)
    await asyncio.to_thread(agent_share.audit, "revoke", "ok", nick=view["target"]["nick"], grant=g["id"],
                            level="write" if scope == "write" else "all", reason=f"agent={view['agent']['name']}")
    out = {"ok": True, "grant": view}
    if agent_share.enforced():
        out["grantId"] = f"ag-{g['id']}"
    return out


@human_router.get("/api/agent/share/audit", response_model=AuditResponse)
async def audit_log(limit: int = Query(100)):
    """에이전트 접근 감사 기록(최근 순). 원문은 없다 — 글자 수와 sha 앞 8자리뿐."""
    return {"entries": await asyncio.to_thread(agent_share.read_audit, limit)}
