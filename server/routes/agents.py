"""AI agent 감지 + Pre/PostToolUse 훅 상태 + WS 브로드캐스트."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import agent_detector
import agent_status

# Phase 9 #5: heartbeat (pty.py와 동일 정책)
_HB_INTERVAL = float(os.environ.get("VT_WS_HEARTBEAT_INTERVAL", "15.0"))
_HB_TIMEOUT = float(os.environ.get("VT_WS_HEARTBEAT_TIMEOUT", "45.0"))

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
    # codex review fix: VT_TOKEN 보호
    from routes.pty import _ws_auth
    if not _ws_auth(websocket):
        await websocket.close(code=4001)
        return
    await websocket.accept()
    _agent_event_clients.add(websocket)
    loop = asyncio.get_running_loop()
    last_pong = loop.time()

    async def _hb():
        nonlocal last_pong
        while True:
            await asyncio.sleep(_HB_INTERVAL)
            if loop.time() - last_pong > _HB_TIMEOUT:
                try:
                    await websocket.close(code=1001, reason="heartbeat timeout")
                except Exception:
                    pass
                return
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
            except Exception:
                return

    hb_task = asyncio.create_task(_hb())
    try:
        # snapshot: active state + 현재 detect 결과 (frontend가 폴링 안 해도 즉시 반영)
        await websocket.send_json({
            "type": "agent_snapshot",
            "active": agent_status.all_active(),
            "agents": agent_detector.detect_all(),
        })
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if msg.get("type") == "pong":
                last_pong = loop.time()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hb_task.cancel()
        _agent_event_clients.discard(websocket)
