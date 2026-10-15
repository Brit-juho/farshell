"""에이전트 간 메시지 — 이 맥에서 보내는 쪽 API (agent_msg.py 머리말 참고).

`fsh mcp`(에이전트가 쓰는 MCP 도구)와 `fsh msg`(CLI)가 부른다. 둘 다 이 서버의
평소 인증(기계 토큰 Bearer)을 지난다. 받는 쪽은 `routes/peer.py`의
`/api/peer/agent/message`다 — 다른 맥에서 오는 요청은 거기로만 들어온다.

보낸 세션은 호출자가 이름으로 주장하지 않고 **`$TMUX_PANE`으로 서버가 정한다**
(`pane_resolve`, 훅·`fsh pane report`와 같은 3단 해석). 답장 주소가 여기서 나오므로
에이전트가 아무 세션인 척 쓸 수 없어야 한다. tmux 밖에서 보내면 보낸 세션은 비어
있고, 받는 쪽은 답장할 곳이 없다는 것을 머리글로 안다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import agent_msg
import host_store
from authz import hooks as authz_hooks
import pane_resolve
import peer_client

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
    from_session, _how = await asyncio.to_thread(
        pane_resolve.resolve, body.get("pane"), body.get("tmux"), body.get("cwd"))
    from_session = from_session or ""

    me = await asyncio.to_thread(host_store.get_self)
    if host in ("local", me["id"]) or host.lower() == (me.get("label") or "").lower():
        authz_hooks.agent_msg_local(request, to_session)   # shadow(plan-2.6 1단계) — 지금은 인가 없음(D6)
        if not await _has_exact(to_session):
            return _err("session_not_found", f"이 맥에 '{to_session}' 세션이 없습니다", 404)
        if to_session == from_session:
            return _err("self", "자기 자신에게는 보낼 수 없습니다", 400)
        try:
            msg = await asyncio.to_thread(
                agent_msg.receive, from_host="local", from_label=me["label"],
                from_session=from_session, to_session=to_session, text=text,
                reply_to=reply_to)
        except agent_msg.MessageError as e:
            return _err(e.code, e.reason, e.status)
        immediate = agent_msg.maybe_drain(to_session)
        return {"ok": True, "id": msg["id"], "conv": msg["conv"], "hop": msg["hop"],
                "to": f"local/{to_session}", "status": msg["status"], "immediate": immediate}

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
    return {"ok": True, "id": msg_id, "conv": result.get("conv") or conv or msg_id,
            "hop": result.get("hop", hop), "to": f"{peer['id']}/{to_session}",
            "status": result.get("status", agent_msg.STATUS_QUEUED),
            "immediate": bool(result.get("immediate"))}


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
