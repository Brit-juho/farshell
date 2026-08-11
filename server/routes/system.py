"""시스템 상태 — capabilities, tunnel, safe-mode, workspace."""

from __future__ import annotations

import hashlib
import json as _json
import logging
import os

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import fsguard
import network_access
import notify
import safe_mode
import tailscale
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
    # ⚠️ 모델을 로드하지 않고 설치 여부만 확인한다. 예전엔 _init_stt()를 불러서
    # 페이지 로드마다(capabilities는 grid.js가 시작 시 호출) faster-whisper 모델
    # ~400MB를 서버에 올렸다 → 터미널만 쓰는 사용자도 400MB를 물었다.
    stt_ok = voice_handler.stt_available()
    tts_ok = voice_handler.tts_available()
    spec = network_access.get_current_spec()
    payload = {
        "voice": stt_ok or tts_ok,
        # 로드돼 있으면 실제 엔진명, 아니면 설치만 됨(available)/미설치(none)
        "stt": (voice_handler._stt_engine if voice_handler.stt_loaded()
                else ("available" if stt_ok else "none")),
        "stt_loaded": voice_handler.stt_loaded(),
        "tts": "available" if tts_ok else "none",
        "network_mode": os.environ.get("VT_NETWORK_MODE", "all"),
        "bound_host": network_access.resolve_bind_host(spec),
        "lan_ip": network_access.get_lan_ip(),
        "tunnel": tunnel.get_tunnel_status(),
        "tailscale": tailscale.get_status_dict(),
        # P2: 열람 가능한 루트가 하나도 없으면 코드 뷰어 UI를 숨긴다.
        "fs": bool(fsguard.get_roots()),
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


@router.get("/api/tailscale/status")
async def tailscale_status(request: Request):
    return _etag_response(tailscale.get_status_dict(), request)


@router.post("/api/notify/client-event")
async def notify_client_event(request: Request):
    """D9: tmux client-attached/client-detached 훅이 호출하는 엔드포인트.

    SSH(+Tailscale)로 순수 텍스트 접속하는 클라이언트는 web/voice 경로와 달리
    서버가 자연히 알 방법이 없다. `bin/vt`의 `_maybe_register_client_hooks`가
    `VT_NOTIFY_CLIENT_EVENTS=1`일 때만 tmux 훅을 등록하고,
    `server/hooks/tmux_client_notify.sh`가 attach/detach 시 이 엔드포인트로
    POST해서 기존 ntfy/Telegram 브릿지(notify.py)로 "누가 언제 접속했는지"를 알린다.

    항상 127.0.0.1(tmux 서버가 도는 로컬 머신)에서만 호출되므로 원격 노출 없음.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    event = str(body.get("event", "attached"))[:16]
    session = str(body.get("session", "?"))[:64]
    remote_host = str(body.get("remote_host", "") or "").strip()[:128]

    if event == "detached":
        title = "tmux 세션 연결 해제"
        message = f"세션 '{session}'에서 클라이언트 연결이 끊어졌습니다"
        tags = "lock"
    else:
        title = "🔐 tmux 세션 접속"
        source = f"SSH ({remote_host})" if remote_host else "로컬 클라이언트"
        message = f"세션 '{session}'에 새 클라이언트 연결 — {source}"
        tags = "unlock,warning"

    sent = await notify.send(title, message, priority="default", tags=tags)
    return {"ok": True, "notified": sent, "configured": notify.is_configured()}


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
