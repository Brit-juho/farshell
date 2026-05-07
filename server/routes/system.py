"""시스템 상태 — capabilities, tunnel, safe-mode, workspace."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import network_access
import safe_mode
import tunnel
import voice_handler
import workspace

logger = logging.getLogger(__name__)

router = APIRouter()

# WS 클라이언트 — 워크스페이스 변경 브로드캐스트
_workspace_clients: set[WebSocket] = set()


@router.get("/api/capabilities")
async def capabilities():
    stt = voice_handler._init_stt()
    tts = voice_handler._init_tts()
    spec = network_access.get_current_spec()
    return {
        "voice": stt != "none" or tts != "none",
        "stt": stt,
        "tts": tts,
        "network_mode": os.environ.get("VT_NETWORK_MODE", "all"),
        "bound_host": network_access.resolve_bind_host(spec),
        "lan_ip": network_access.get_lan_ip(),
        "tunnel": tunnel.get_tunnel_status(),
    }


@router.get("/api/tunnel/status")
async def tunnel_status():
    return tunnel.get_tunnel_status()


@router.get("/api/safe-mode")
async def safe_mode_status():
    return {"enabled": safe_mode.is_enabled()}


@router.get("/api/workspace")
async def workspace_get():
    return workspace.load()


@router.put("/api/workspace")
async def workspace_put(request):
    from fastapi import Request
    data = await request.json()
    merged = workspace.update(data)

    msg = {"type": "workspace_updated", "data": merged}
    dead = set()
    for ws in list(_workspace_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _workspace_clients.difference_update(dead)
    return {"ok": True, "data": merged}


@router.websocket("/ws-workspace")
async def ws_workspace(websocket: WebSocket):
    await websocket.accept()
    _workspace_clients.add(websocket)
    try:
        await websocket.send_json({"type": "workspace_snapshot", "data": workspace.load()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _workspace_clients.discard(websocket)
