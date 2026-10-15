"""에이전트 간 메시지 — 이 맥에서 보내는 쪽 API (agent_msg.py 머리말 참고).

`fsh mcp`(에이전트가 쓰는 MCP 도구)와 `fsh msg`(CLI)가 부른다. 둘 다 이 서버의
평소 인증(기계 토큰 Bearer)을 지난다. 받는 쪽은 `routes/peer.py`의
`/api/peer/agent/message`다 — 다른 맥에서 오는 요청은 거기로만 들어온다.

보낸 세션은 호출자가 이름으로 주장하지 않고 **`$TMUX_PANE`으로 서버가 정한다**
(`pane_resolve`, 훅·`fsh pane report`와 같은 3단 해석). 답장 주소가 여기서 나오므로
에이전트가 아무 세션인 척 쓸 수 없어야 한다. tmux 밖에서 보내면 보낸 세션은 비어
있고, 받는 쪽은 답장할 곳이 없다는 것을 머리글로 안다.

`VT_AUTHZ_ENFORCE=msg`(이면 보낸 쪽은 **인증된 호출자**다(unix socket 의 peer pid 체인 또는
`X-Fsh-*` 헤더 + 소켓 소유 프로세스 확인). 본문의 pane/tmux/cwd 는 무시하고, 확인되지 않으면
403 `identity_unverified`, 관계 없는 세션 앞으로는 사람이 승인하기 전까지 202 보류다
(`authz/msg_svc.py` 머리말). 꺼짐이면 위의 옛 동작 그대로다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import agent_msg
import host_store
from authz import hooks as authz_hooks
from authz import msg_svc
from authz import principals as P
import pane_resolve
import peer_client
import share_identity
from i18n import tr

logger = logging.getLogger(__name__)

router = APIRouter()


def _err(code: str, reason: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code, "reason": reason}, status_code=status)


def _find_peer(host: str) -> dict | None:
    """id로, 없으면 별명(대소문자 무시)으로 — 에이전트는 목록에서 본 이름을 그대로 쓴다."""
    peer = host_store.find_peer(host)
    if peer:
        return peer
    low = host.lower()
    for p in host_store.list_peers():
        if (p.get("label") or "").lower() == low:
            return host_store.find_peer(p["id"])
    return None


async def _has_exact(name: str) -> bool:
    import tmux_runner

    rc, _, _ = await tmux_runner.run_async(["has-session", "-t", f"={name}"], timeout=1.0)
    return rc == 0


@dataclass
class _Sender:
    key: str
    name: str
    pane: str


class _Unverified(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _rows_by_key(rows) -> dict:
    out: dict = {}
    for r in rows or []:
        out.setdefault(f"{r.sid}:{r.created}", r)
    return out


def _verify_sender_sync(request: Request, rows) -> _Sender:
    """보낸 쪽을 **인증된 호출자**로 확정한다. 소켓이면 서버가 커널에서 읽은 peer pid 가 닿는 pane 의 세션,
    아니면 `X-Fsh-*` 헤더를 `share_identity.verify_caller` 로(소켓 소유 프로세스 확인). 본문의 pane/tmux/cwd 는 보지 않는다."""
    if request.scope.get("fsh_sock") is True:
        p = getattr(request.state, "fsh_principal", None)
        key = (p.attrs.get("key") if p is not None and p.kind == "agent" and not P.is_hook(p) else "") or ""
        if not key:
            raise _Unverified("소켓 호출 프로세스가 FarShell 의 tmux pane 안에 있지 않습니다")
        row = _rows_by_key(rows).get(key)
        if row is None:
            raise _Unverified("FarShell 의 tmux 에 그런 세션이 없습니다")
        return _Sender(key, row.name, row.pane_id)
    h = request.headers
    pane, tmux_env, pid = (unquote(h.get(k, "")) for k in ("x-fsh-pane", "x-fsh-tmux", "x-fsh-pid"))
    server = request.scope.get("server")
    try:
        c = share_identity.verify_caller(
            pane, tmux_env, pid, client=tuple(request.client) if request.client else None,
            headers={k.lower(): v for k, v in h.items()}, server_port=server[1] if server else None,
            rows=rows, peer_pid=((request.scope.get("fsh_peer") or {}).get("pid")))
    except share_identity.IdentityError as e:
        raise _Unverified(e.reason) from None
    return _Sender(c.key, c.name, c.pane_id)


def _claim_mismatch(body: dict, sender: _Sender) -> Optional[dict]:
    """본문이 주장한 pane 이 확인된 보낸 쪽과 다르면 그 사실(내용 없음). 같거나 주장이 없으면 None."""
    claimed = body.get("pane")
    if isinstance(claimed, str) and claimed and claimed != sender.pane:
        return {"claimed_pane": claimed[:16], "actual_pane": sender.pane}
    return None


def _pending(req: dict) -> JSONResponse:
    return JSONResponse({
        "state": "pending", "requestId": req["id"].removeprefix("ap-"), "kind": "msg",
        "reason": "사용자 승인 대기 중(메시지 보내기) — 승인되면 같은 호출을 다시 하세요"}, status_code=202)


async def _announce_request(rec: dict, names: dict) -> None:
    view = msg_svc.request_view(rec, names)
    try:
        from routes import agent_share as share_routes
        await share_routes.announce_view(view, tr("에이전트 메시지 승인이 필요합니다"))
    except Exception as e:  # noqa: BLE001 — 알림 실패가 호출을 막으면 안 된다
        logger.debug(f"메시지 요청 알림 실패: {e}")


@router.post("/api/agent/message")
async def send_message(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        host, to_session = agent_msg.parse_to(str(body.get("to") or ""))
        text = agent_msg.check_text(str(body.get("text") or ""))
    except agent_msg.MessageError as e:
        return _err(e.code, e.reason, e.status)
    reply_to = str(body.get("replyTo") or "") or None
    enforced = msg_svc.enforced()
    sender: Optional[_Sender] = None
    rows = None
    me = await asyncio.to_thread(host_store.get_self)
    is_local = host in ("local", me["id"]) or host.lower() == (me.get("label") or "").lower()
    peer = None
    if is_local:
        authz_hooks.agent_msg_local(request, to_session)   # shadow — 응답은 바꾸지 않는다(켜짐에서도 차이를 센다)
    if enforced:
        # 존재하지 않는 대상은 보낸 쪽을 따지기 전에 404 — 호출자 확인은 그다음이다.
        if is_local:
            if not await _has_exact(to_session):
                return _err("session_not_found", f"이 맥에 '{to_session}' 세션이 없습니다", 404)
        else:
            peer = await asyncio.to_thread(_find_peer, host)
            if peer is None:
                return _err("host_not_found", f"등록되지 않은 호스트입니다: {host}", 404)
        # 보낸 쪽은 인증된 호출자. 본문의 pane/tmux/cwd 는 보지 않는다. 확인 못 하면 403.
        rows = await asyncio.to_thread(share_identity.list_pane_rows)
        try:
            sender = await asyncio.to_thread(_verify_sender_sync, request, rows)
        except _Unverified as e:
            await asyncio.to_thread(msg_svc.audit_op, "send", "unverified", reason="identity_unverified",
                                    resource=f"msg:{host}/{to_session}", **msg_svc.text_meta(text))
            return _err("identity_unverified", e.reason, 403)
        from_session = sender.name
        mismatch = _claim_mismatch(body, sender)
        if mismatch:
            await asyncio.to_thread(
                msg_svc.audit_op, "claim", "ignored", principal=f"agent:local/{sender.key}", decision="deny",
                reason="claim_mismatch", resource=f"msg:{host}/{to_session}", detail=mismatch)
    else:
        from_session, _how = await asyncio.to_thread(
            pane_resolve.resolve, body.get("pane"), body.get("tmux"), body.get("cwd"))
        from_session = from_session or ""

    if is_local:
        if not enforced:
            if not await _has_exact(to_session):
                return _err("session_not_found", f"이 맥에 '{to_session}' 세션이 없습니다", 404)
        if to_session == from_session or (sender is not None and _key_of(rows, to_session) == sender.key):
            return _err("self", "자기 자신에게는 보낼 수 없습니다", 400)
        ticket = None
        if enforced:
            denied = await _authorize_local(sender, to_session, rows, text)
            if isinstance(denied, JSONResponse):
                return denied
            ticket = denied
        try:
            msg = await asyncio.to_thread(
                agent_msg.receive, from_host="local", from_label=me["label"],
                from_session=from_session, to_session=to_session, text=text,
                reply_to=reply_to, authz=ticket)
        except agent_msg.MessageError as e:
            if enforced:
                await asyncio.to_thread(
                    msg_svc.audit_op, "send", "rate_limited" if e.code == "rate_limited" else "denied",
                    principal=f"agent:local/{sender.key}", resource=f"msg:local/{_key_of(rows, to_session)}",
                    reason=e.code, **msg_svc.text_meta(text))
            return _err(e.code, e.reason, e.status)
        if enforced:
            await asyncio.to_thread(
                msg_svc.audit_op, "send", "ok", principal=f"agent:local/{sender.key}",
                resource=f"msg:local/{_key_of(rows, to_session)}", reason=ticket.ctx.get("via"),
                detail={"id": msg["id"], "hop": msg["hop"]}, **msg_svc.text_meta(text))
        immediate = agent_msg.maybe_drain(to_session)
        return {"ok": True, "id": msg["id"], "conv": msg["conv"], "hop": msg["hop"],
                "to": f"local/{to_session}", "status": msg["status"], "immediate": immediate}

    if peer is None:
        peer = await asyncio.to_thread(_find_peer, host)
    if peer is None:
        return _err("host_not_found", f"등록되지 않은 호스트입니다: {host}", 404)
    try:
        conv, hop = await asyncio.to_thread(agent_msg.resolve_conv, reply_to)
    except agent_msg.MessageError as e:
        return _err(e.code, e.reason, e.status)
    msg_id = uuid.uuid4().hex[:12]
    payload = {"id": msg_id, "conv": conv, "hop": hop, "from": from_session,
               "to": to_session, "text": text, "replyTo": reply_to}
    try:
        result = await asyncio.to_thread(
            peer_client.send_bytes_sync, peer, "/api/peer/agent/message",
            json.dumps(payload).encode("utf-8"), {}, 15.0)
    except peer_client.PeerError as e:
        await asyncio.to_thread(
            agent_msg.record_outgoing, to_host=peer["id"], to_label=peer["label"],
            to_session=to_session, from_session=from_session, text=text,
            reply_to=reply_to, hop=hop, conv=conv, msg_id=msg_id,
            status=agent_msg.STATUS_FAILED, reason=e.reason)
        status = e.status if 400 <= e.status < 600 else 502
        if e.status == 404 and "세션" not in e.reason:
            return _err("peer_outdated",
                        f"{peer['label']}의 FarShell이 에이전트 메시지를 모릅니다 — 그 맥을 업데이트하세요", 501)
        return _err("peer_error", f"{peer['label']}: {e.reason}", status)
    await asyncio.to_thread(
        agent_msg.record_outgoing, to_host=peer["id"], to_label=peer["label"],
        to_session=to_session, from_session=from_session, text=text,
        reply_to=reply_to, hop=int(result.get("hop", hop)),
        conv=str(result.get("conv") or conv), msg_id=msg_id)
    if enforced:
        # 보내는 호스트의 허용은 받는 호스트의 사람이 정한다 — 여기서는 나간 사실만 남긴다.
        await asyncio.to_thread(
            msg_svc.audit_op, "send", "forwarded", principal=f"agent:local/{sender.key}",
            resource=f"msg:{peer['id']}/{to_session}", decision="allow", **msg_svc.text_meta(text))
    return {"ok": True, "id": msg_id, "conv": result.get("conv") or conv or msg_id,
            "hop": result.get("hop", hop), "to": f"{peer['id']}/{to_session}",
            "status": result.get("status", agent_msg.STATUS_QUEUED),
            "immediate": bool(result.get("immediate"))}


def _key_of(rows, session_name: str) -> str:
    row = next((r for r in rows or [] if r.name == session_name), None)
    return f"{row.sid}:{row.created}" if row else ""


async def _authorize_local(sender: _Sender, to_session: str, rows, text: str):
    """엔진 판정. 허용이면 `Ticket`, 아니면 보낼 응답(202 보류 / 403 / 503)."""
    recv_key = _key_of(rows, to_session)
    meta = msg_svc.text_meta(text)
    principal = f"agent:local/{sender.key}"
    if rows is None or not recv_key:
        await asyncio.to_thread(msg_svc.audit_op, "send", "denied", principal=principal, reason="receiver_unresolved",
                                resource=f"msg:local/{to_session}", **meta)
        return _err("session_not_found", f"이 맥에 '{to_session}' 세션이 없습니다", 404)
    alive = {f"{r.sid}:{r.created}" for r in rows}
    try:
        v = await asyncio.to_thread(msg_svc.authorize_local, sender.key, sender.name, recv_key, alive)
    except agent_msg.MessageError as e:
        await asyncio.to_thread(msg_svc.audit_op, "send", "denied", principal=principal, reason=e.code,
                                resource=f"msg:local/{recv_key}", **meta)
        return _err(e.code, e.reason, e.status)
    if v.allow:
        v.ticket.ctx["via"] = v.relation or "grant"
        return v.ticket
    resource = f"msg:local/{recv_key}"
    if v.reason not in ("not_related", "no_grant"):
        await asyncio.to_thread(msg_svc.audit_op, "send", "denied", principal=principal, reason=v.reason,
                                resource=resource, **meta)
        return _err("forbidden", "메시지를 보낼 권한이 없습니다", 403)
    from authz.errors import ShareError
    names = {f"{r.sid}:{r.created}": r.name for r in rows}
    try:
        rec, created = await asyncio.to_thread(msg_svc.request_access, sender.key, sender.name, recv_key, to_session, alive)
    except ShareError as e:
        await asyncio.to_thread(msg_svc.audit_op, "send", "denied", principal=principal, reason=e.code,
                                resource=resource, **meta)
        return JSONResponse(e.body(), status_code=e.status)
    await asyncio.to_thread(msg_svc.audit_op, "request", "pending" if created else "pending_dup", principal=principal,
                            resource=resource, decision="pending", approval_id=rec["id"], **meta)
    if created:
        await _announce_request(rec, names)
    return _pending(rec)


@router.get("/api/agent/messages")
async def list_messages(conv: str = Query(""), limit: int = Query(50)):
    items = await asyncio.to_thread(agent_msg.list_messages, conv, limit)
    return {"messages": items}


@router.get("/api/agent/directory")
async def directory(fresh: bool = Query(False)):
    """보낼 수 있는 곳 — 이 맥과 페어링된 맥의 tmux 세션. `/api/hosts`와 같은 데이터에
    "무엇이 돌고 있나"(command)와 상태만 남긴다."""
    from routes.hosts import list_hosts

    data = await list_hosts(fresh=fresh)
    out = []
    for h in data.get("hosts", []):
        out.append({
            "host": h["id"], "label": h.get("label") or h["id"], "online": h.get("online", False),
            "reason": h.get("reason"),
            "sessions": [{"name": s.get("name"), "command": s.get("command") or "",
                          "status": s.get("status") or "idle"} for s in h.get("sessions") or []],
        })
    return {"hosts": out}
