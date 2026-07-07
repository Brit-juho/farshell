"""PTY 세션 CRUD + WebSocket 터미널 + 파일 업로드/다운로드."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

import crypto_channel
from deps import pty_mgr, session_store, output_watcher, _auto_responder
from deps import ws_count_per_session, ws_total_count
from session_store import new_session_id

logger = logging.getLogger(__name__)

router = APIRouter()

UPLOAD_DIR = Path("/tmp/vt-uploads")

# Phase 8 G2: 연결 한도 + 백프레셔 + 하트비트
WS_MAX_PER_SESSION = int(os.environ.get("VT_WS_MAX_PER_SESSION", "8"))
WS_MAX_TOTAL = int(os.environ.get("VT_WS_MAX_TOTAL", "32"))
WS_HEARTBEAT_INTERVAL = float(os.environ.get("VT_WS_HEARTBEAT_INTERVAL", "15.0"))
WS_HEARTBEAT_TIMEOUT = float(os.environ.get("VT_WS_HEARTBEAT_TIMEOUT", "45.0"))
WS_QUEUE_HIGH = 200
WS_QUEUE_LOW = 50

import deps as _deps  # 전역 카운터 직접 수정용

VT_TOKEN = os.environ.get("VT_TOKEN", "")


def _ws_auth(ws: WebSocket) -> bool:
    """Phase 9 #8: WS 인증은 query/Authorization/cookie 모두에서 토큰 수용.
    HTTP 미들웨어와 동일한 다중 소스를 받아야 cookie-only 클라이언트(/api/auth 후)도 통과.
    """
    if not VT_TOKEN:
        return True
    # 1) query string
    token = ws.query_params.get("token", "")
    if token == VT_TOKEN:
        return True
    # 2) Authorization: Bearer
    auth_hdr = ws.headers.get("authorization", "")
    if auth_hdr.startswith("Bearer ") and auth_hdr[7:] == VT_TOKEN:
        return True
    # 3) HttpOnly cookie (vt_session)
    cookie_token = ws.cookies.get("vt_session", "")
    if cookie_token == VT_TOKEN:
        return True
    return False


# --------------------------------------------------------------------------
# PTY 세션 CRUD
# --------------------------------------------------------------------------

@router.api_route("/api/sessions", methods=["GET", "HEAD"])
async def list_sessions():
    return [
        {
            "id": s.session_id,
            "name": (info.name if (info := session_store.get(s.session_id)) else s.session_id),
            "cols": s.cols,
            "rows": s.rows,
        }
        for s in pty_mgr.sessions.values()
    ]


@router.post("/api/sessions")
async def create_session(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    cols = body.get("cols", 80)
    rows = body.get("rows", 24)
    name = body.get("name", "")
    session_id = new_session_id()
    pty_mgr.create_session(session_id, cols=cols, rows=rows)
    session_store.add(session_id, name=name)
    output_watcher.add_session(session_id)
    return {"id": session_id, "name": name or session_id}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    info = session_store.get(session_id)
    tmux_name = info.tmux_name if info else None
    pty_mgr.destroy_session(session_id)
    session_store.remove(session_id)
    output_watcher.remove_session(session_id)
    return {"ok": True, "tmux_detached": tmux_name}


@router.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    info = session_store.get(session_id)
    if not info:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if not name:
        return {"id": session_id, "name": info.name, "tmux_name": info.tmux_name}

    # Wave 1 W1-1: tmux 세션 이름도 같이 변경 (이전엔 메타데이터만 변경)
    tmux_renamed = False
    warning = None
    if info.tmux_name and info.tmux_name != name:
        import re
        import tmux_runner
        # tmux 세션명 안전 문자 검증 (영숫자, dash, underscore만 허용)
        if re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            # 충돌 검사
            if tmux_runner.has_session(name):
                return JSONResponse(
                    {"error": "tmux session name already exists", "name": name},
                    status_code=409,
                )
            rc, _, err = tmux_runner.run(
                ["rename-session", "-t", info.tmux_name, name],
                timeout=2.0,
            )
            if rc != 0:
                return JSONResponse(
                    {"error": "tmux rename-session failed", "detail": err.decode("utf-8", errors="ignore")},
                    status_code=500,
                )
            session_store.update_tmux_name(session_id, name)
            tmux_renamed = True
        else:
            # 안전하지 않은 문자 포함 — tmux는 안 건드리고 메타데이터만 변경 + 경고 명시
            warning = (
                "tmux 세션 이름은 변경되지 않음 — 영숫자/dash/underscore만 허용. "
                "웹 라벨만 변경됨."
            )
            logger.warning(f"rename {session_id}: unsafe chars in '{name}' — tmux unchanged")
    info.name = name
    resp = {"id": session_id, "name": info.name, "tmux_name": info.tmux_name, "tmux_renamed": tmux_renamed}
    if warning:
        resp["warning"] = warning
    return resp


# --------------------------------------------------------------------------
# 파일 업로드 / 다운로드
# --------------------------------------------------------------------------

@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...), session_id: str = Query("")):
    UPLOAD_DIR.mkdir(exist_ok=True)
    safe_name = Path(file.filename).name.replace("..", "").strip()
    if not safe_name:
        safe_name = f"upload-{uuid.uuid4().hex[:8]}"
    dest = UPLOAD_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)
    return {"ok": True, "path": str(dest), "size": len(content)}


@router.get("/api/download")
async def download_file(path: str = Query(...)):
    fp = Path(path).resolve()
    if not str(fp).startswith(str(UPLOAD_DIR.resolve())):
        return Response(content="Access denied", status_code=403)
    if not fp.is_file():
        return Response(content="File not found", status_code=404)
    return FileResponse(str(fp), filename=fp.name)


# --------------------------------------------------------------------------
# WebSocket 터미널
# --------------------------------------------------------------------------

async def _safe_send(ws: WebSocket, data: bytes) -> None:
    try:
        await ws.send_bytes(data)
    except Exception:
        pass


@router.websocket("/ws/{session_id}")
async def ws_terminal(ws: WebSocket, session_id: str):
    if not _ws_auth(ws):
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()

    if session_id not in pty_mgr.sessions:
        await ws.close(code=4004, reason="Session not found")
        return

    # 연결 한도 검사
    if _deps.ws_total_count >= WS_MAX_TOTAL:
        await ws.close(code=1013, reason="Max total connections")
        return
    per_session = _deps.ws_count_per_session.get(session_id, 0)
    if per_session >= WS_MAX_PER_SESSION:
        await ws.close(code=1013, reason="Max per-session connections")
        return
    # A1: 연결 카운터 증가는 E2E 협상 성공 이후로 미룬다. 예전엔 여기서 증가시켰는데,
    # 아래 E2E 핸드셰이크 실패 return(4500/4400)은 try/finally 이전이라 감소를 못 타서
    # 카운터가 샜다 → ?e2e=1로 실패 핸드셰이크를 반복하면 WS_MAX_TOTAL이 소진돼
    # 정상 접속이 거부되는 DoS. 증가/감소를 finally와 확실히 페어링한다.

    loop = asyncio.get_running_loop()
    last_pong = loop.time()
    send_queue: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_HIGH * 2)
    pty_paused = False
    ws_id = id(ws)

    # E2E 협상
    e2e_requested = (
        ws.query_params.get("e2e", "") in ("1", "true", "yes")
        or crypto_channel.is_enabled()
    )
    channel = None
    if e2e_requested and crypto_channel.is_available():
        server_kp = crypto_channel.new_server_keypair()
        if server_kp is None:
            await ws.close(code=4500, reason="E2E unavailable")
            return
        await ws.send_text(json.dumps({"type": "e2e-hello", "pub": server_kp.public_b64}))
        try:
            first = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
            handshake = json.loads(first)
            if handshake.get("type") == "e2e-ack" and handshake.get("pub"):
                channel = crypto_channel.Channel.derive(server_kp.private, handshake["pub"])
                logger.info(f"[E2E] 핸드셰이크 성공 sid={session_id}")
            else:
                await ws.close(code=4400, reason="E2E handshake invalid")
                return
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[E2E] 핸드셰이크 실패: {e}")
            await ws.close(code=4400, reason="E2E handshake failed")
            return

    def on_data(data: bytes):
        nonlocal pty_paused
        output_watcher.feed_output(session_id, data)
        _auto_responder.feed(session_id, data)
        out = channel.encrypt_simple(data) if channel else data
        try:
            send_queue.put_nowait(out)
        except asyncio.QueueFull:
            try:
                send_queue.get_nowait()
                send_queue.put_nowait(out)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
        qs = send_queue.qsize()
        if qs > WS_QUEUE_HIGH and not pty_paused:
            pty_paused = True
            pty_mgr.pause_read(session_id, ws_id)
        elif qs < WS_QUEUE_LOW and pty_paused:
            pty_paused = False
            pty_mgr.resume_read(session_id, ws_id)

    async def _send_worker():
        while True:
            try:
                data = await send_queue.get()
                await ws.send_bytes(data)
            except (WebSocketDisconnect, RuntimeError):
                break
            except Exception as e:
                logger.debug(f"[ws] send error: {e}")
                break

    async def _heartbeat_loop():
        while True:
            try:
                await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
                if loop.time() - last_pong > WS_HEARTBEAT_TIMEOUT:
                    logger.info(f"[ws] heartbeat timeout sid={session_id}")
                    try:
                        await ws.close(code=1001, reason="heartbeat timeout")
                    except Exception:
                        pass
                    return
                try:
                    await ws.send_text(json.dumps({"type": "ping"}))
                except Exception:
                    return
            except asyncio.CancelledError:
                return

    # A1: E2E 협상까지 성공한 지금 카운터를 올린다 — 아래 try/finally가 감소를 보장.
    # 여기~try 사이에는 raise 가능한 await가 없어 증가/감소 페어링이 유지된다.
    _deps.ws_count_per_session[session_id] = _deps.ws_count_per_session.get(session_id, 0) + 1
    _deps.ws_total_count += 1

    # C3: scrollback을 live 데이터와 같은 send_queue로 흘려보낸다. 예전엔 subscribe로
    # live 데이터가 큐에 쌓이는 동안 scrollback을 _safe_send로 직접 보내 두 전송 경로가
    # 경쟁했다(재접속 tail 중복/역전). scrollback을 먼저 큐에 넣고 subscribe하면 단일
    # FIFO 경로로 순서가 보장된다.
    for chunk in pty_mgr.get_scrollback(session_id):
        out = channel.encrypt_simple(chunk) if channel else chunk
        try:
            send_queue.put_nowait(out)
        except asyncio.QueueFull:
            break
    pty_mgr.subscribe(session_id, on_data)

    send_task = asyncio.create_task(_send_worker())
    hb_task = asyncio.create_task(_heartbeat_loop())

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.receive":
                if "text" in msg:
                    data = json.loads(msg["text"])
                    msg_type = data.get("type")
                    if msg_type == "resize":
                        pty_mgr.resize(session_id, data["cols"], data["rows"])
                    elif msg_type == "pong":
                        last_pong = loop.time()
                elif "bytes" in msg:
                    payload = msg["bytes"]
                    if channel:
                        try:
                            payload = channel.decrypt(payload)
                        except Exception as e:
                            logger.warning(f"[E2E] 복호화 실패: {e}")
                            continue
                    pty_mgr.write(session_id, payload)
            elif msg["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        pty_mgr.unsubscribe(session_id, on_data)
        send_task.cancel()
        hb_task.cancel()
        if pty_paused:
            pty_mgr.resume_read(session_id, ws_id)
        _deps.ws_count_per_session[session_id] = max(0, _deps.ws_count_per_session.get(session_id, 1) - 1)
        if _deps.ws_count_per_session[session_id] == 0:
            _deps.ws_count_per_session.pop(session_id, None)
        _deps.ws_total_count = max(0, _deps.ws_total_count - 1)


# --------------------------------------------------------------------------
# 알림 WebSocket + on_task_complete 콜백
# --------------------------------------------------------------------------

import platform_utils as _platform_utils


async def on_task_complete(session_id: str, summary: str, audio: bytes):
    from deps import notify_clients
    meta = json.dumps({
        "type": "task_complete",
        "session_id": session_id,
        "summary": summary,
        "has_audio": len(audio) > 0,
    })
    dead = set()
    for ws in list(notify_clients):
        try:
            await ws.send_text(meta)
            if audio:
                await ws.send_bytes(audio)
        except Exception:
            dead.add(ws)
    notify_clients -= dead
    if not notify_clients:
        _platform_utils.tts_speak(summary)


@router.websocket("/ws-notify")
async def ws_notify(ws: WebSocket):
    from deps import notify_clients
    if not _ws_auth(ws):
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()
    notify_clients.add(ws)
    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            if data.get("type") == "set_watch":
                sid = data.get("session_id")
                output_watcher.set_enabled(sid, data.get("enabled", True))
            elif data.get("type") == "set_timeout":
                sid = data.get("session_id")
                output_watcher.set_idle_timeout(sid, data.get("timeout", 3.0))
    except WebSocketDisconnect:
        pass
    finally:
        notify_clients.discard(ws)
