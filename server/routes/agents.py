"""AI agent 감지 + Pre/PostToolUse 훅 상태 + WS 브로드캐스트."""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import agent_detector
import agent_status

logger = logging.getLogger(__name__)

router = APIRouter()

# WebSocket 클라이언트 집합 (모듈 수준 — 라우터 전체가 공유)
_agent_event_clients: set[WebSocket] = set()


@router.get("/api/agents")
async def list_agents():
    return agent_detector.detect_all()


@router.get("/api/agents/{tmux_name}")
async def get_agent(tmux_name: str):
    info = agent_detector.detect(tmux_name)
    return info or {"agent": None}


@router.post("/api/agent/event")
async def agent_event(request):
    from fastapi import Request
    body = await request.json()
    event = body.get("event", "stop")
    payload = body.get("payload", {})

    state = agent_status.on_event(event, payload)

    msg = {"type": "agent_event", "event": event, "state": state}
    dead = set()
    for ws in list(_agent_event_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _agent_event_clients.difference_update(dead)

    return {"ok": True, "state": state}


@router.get("/api/agent/status")
async def agent_status_get():
    return {"active": agent_status.all_active(), "all": agent_status.get_state()}


@router.websocket("/ws-agent")
async def ws_agent(websocket: WebSocket):
    await websocket.accept()
    _agent_event_clients.add(websocket)
    try:
        await websocket.send_json({
            "type": "agent_snapshot",
            "active": agent_status.all_active(),
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _agent_event_clients.discard(websocket)
