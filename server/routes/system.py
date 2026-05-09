"""시스템 상태 — capabilities, tunnel, safe-mode, workspace."""

from __future__ import annotations

import hashlib
import json as _json
import logging
import os

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import network_access
import safe_mode
import tunnel
import voice_handler
import workspace

logger = logging.getLogger(__name__)

router = APIRouter()


def _etag_response(payload, request: Request, stable_for_etag=None) -> Response:
    """Phase 9 #9: ETag/304 — 변화 적은 GET에 대해 If-None-Match 처리.

    payload를 정렬-직렬화한 후 sha1 16자리를 ETag로 사용.
    `stable_for_etag`가 주어지면 그것으로 hash를 계산 — payload 안에 timestamp 같은
    매번 변하는 필드가 있어도 ETag는 안정적으로 유지된다.
    """
    hash_src = stable_for_etag if stable_for_etag is not None else payload
    body = _json.dumps(hash_src, sort_keys=True, separators=(",", ":")).encode()
    etag = hashlib.sha1(body).hexdigest()[:16]
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})

# WS 클라이언트 — 워크스페이스 변경 브로드캐스트
_workspace_clients: set[WebSocket] = set()


@router.get("/api/capabilities")
async def capabilities(request: Request):
    stt = voice_handler._init_stt()
    tts = voice_handler._init_tts()
    spec = network_access.get_current_spec()
    payload = {
        "voice": stt != "none" or tts != "none",
        "stt": stt,
        "tts": tts,
        "network_mode": os.environ.get("VT_NETWORK_MODE", "all"),
        "bound_host": network_access.resolve_bind_host(spec),
        "lan_ip": network_access.get_lan_ip(),
        "tunnel": tunnel.get_tunnel_status(),
    }
    # ETag는 결정적 부분(tunnel.checked_at 같은 timestamp 제외)으로만 계산.
    stable = {k: v for k, v in payload.items() if k != "tunnel"}
    tun = dict(payload.get("tunnel") or {})
    tun.pop("checked_at", None)
    stable["tunnel"] = tun
    return _etag_response(payload, request, stable_for_etag=stable)


@router.get("/api/tunnel/status")
async def tunnel_status(request: Request):
    return _etag_response(tunnel.get_tunnel_status(), request)


@router.get("/api/safe-mode")
async def safe_mode_status(request: Request):
    return _etag_response({"enabled": safe_mode.is_enabled()}, request)


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
    # codex review fix: VT_TOKEN 보호
    from routes.pty import _ws_auth
    if not _ws_auth(websocket):
        await websocket.close(code=4001)
        return
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
