"""FastAPI 서버 — PTY WebSocket + Voice + 출력 알림 엔드포인트."""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from pty_manager import PTYManager
from session_store import SessionStore, new_session_id
from output_watcher import OutputWatcher
import voice_handler
import local_mic
import platform_utils
import notify
import crypto_channel
import agent_detector
import agent_status
import safe_mode
import workspace
import tmux_runner
import network_access
import tunnel
import auto_responder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# [M8] 절대 경로 사용
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = str(BASE_DIR / "frontend")

# 토큰 인증 — VT_TOKEN 환경변수가 설정되면 활성화
VT_TOKEN = os.environ.get("VT_TOKEN", "")


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not VT_TOKEN:
            return await call_next(request)
        # 정적 파일, 헬스체크는 인증 불필요
        path = request.url.path
        if path in ("/", "/sw.js", "/manifest.json") or path.startswith("/static"):
            return await call_next(request)
        # 토큰 확인: 쿼리 파라미터 또는 Authorization 헤더
        token = request.query_params.get("token", "")
        if not token:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
        if token != VT_TOKEN:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


class NetworkAccessMiddleware(BaseHTTPMiddleware):
    """Phase 8 G1: CIDR 기반 IP 화이트리스트.

    `VT_NETWORK_MODE`(localhost/lan/all)에 따라 외부 요청 차단.
    Cloudflare Tunnel은 항상 127.0.0.1로 들어와서 `all` 모드면 자동 통과.
    """
    async def dispatch(self, request: Request, call_next):
        spec = network_access.get_current_spec()
        if spec.allow_all:
            return await call_next(request)
        # 정적 파일은 통과
        path = request.url.path
        if path in ("/", "/sw.js", "/manifest.json") or path.startswith("/static"):
            return await call_next(request)
        client_host = request.client.host if request.client else None
        # X-Forwarded-For 신뢰 옵션 (proxy 뒤 환경)
        if os.environ.get("VT_TRUST_PROXY", "").strip() == "1":
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                client_host = xff.split(",")[0].strip()
        if not spec.is_allowed(client_host):
            return JSONResponse(
                {"error": "forbidden", "reason": "ip_not_allowed", "ip": client_host},
                status_code=403,
            )
        return await call_next(request)


app = FastAPI()
app.add_middleware(NetworkAccessMiddleware)
app.add_middleware(TokenAuthMiddleware)

# [H3] CORS 미들웨어
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

pty_mgr = PTYManager()
session_store = SessionStore()
output_watcher = OutputWatcher()

# Phase 8 G5: trust prompt 자동 응답 (옵트인 — VT_AUTO_TRUST=1)
_auto_responder = auto_responder.get_global_responder(
    write_fn=lambda sid, data: pty_mgr.write(sid, data)
)

# 알림용 WebSocket 클라이언트 관리
_notify_clients: set[WebSocket] = set()

# frontend 정적 파일 서빙
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(str(Path(FRONTEND_DIR) / "index.html"))


@app.get("/sw.js")
async def service_worker():
    """[C1] SW를 루트 scope에서 서빙."""
    return FileResponse(
        str(Path(FRONTEND_DIR) / "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json")
async def manifest():
    return FileResponse(str(Path(FRONTEND_DIR) / "manifest.json"))


@app.get("/api/capabilities")
async def capabilities():
    """설치된 기능 반환 — 프론트엔드가 UI를 조건부로 표시."""
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


@app.get("/api/tunnel/status")
async def tunnel_status():
    """Cloudflare Tunnel 상태 (Phase 8 G1 옵션 B)."""
    return tunnel.get_tunnel_status()


@app.get("/api/sessions")
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


@app.post("/api/sessions")
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


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    info = session_store.get(session_id)
    tmux_name = info.tmux_name if info else None
    pty_mgr.destroy_session(session_id)
    session_store.remove(session_id)
    output_watcher.remove_session(session_id)
    # tmux 세션은 detach만 — 세션 자체는 유지됨
    return {"ok": True, "tmux_detached": tmux_name}


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    info = session_store.get(session_id)
    if not info:
        return {"error": "Session not found"}, 404
    if name:
        info.name = name
    return {"id": session_id, "name": info.name}


# ---------------------------------------------------------------------------
# tmux 연동 — 기존 tmux 세션에 attach
# ---------------------------------------------------------------------------

TMUX_SOCKET = tmux_runner.VT_TMUX_SOCKET  # D5: 단일 진실의 원천 — tmux_runner에서 참조


@app.get("/api/tmux/sessions")
async def list_tmux_sessions():
    """서버에서 실행 중인 tmux 세션 목록 (현재 명령, cwd 포함).

    Phase 8 G3: tmux_runner 사용 + 단일 list-panes -a 호출로 세션+pane 정보 일괄 수집.
    """
    fmt_sessions = "#{session_name}\t#{session_windows}\t#{session_attached}"
    fmt_panes = "#{session_name}\t#{pane_current_command}\t#{pane_current_path}"

    sessions_text = tmux_runner.run_text(["list-sessions", "-F", fmt_sessions], timeout=2.0)
    if not sessions_text:
        return []

    panes_text = tmux_runner.run_text(["list-panes", "-a", "-F", fmt_panes], timeout=2.0) or ""
    pane_by_session: dict[str, tuple[str, str]] = {}
    for line in panes_text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] not in pane_by_session:
            pane_by_session[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else "")

    sessions = []
    for line in sessions_text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        name = parts[0]
        cmd, cwd = pane_by_session.get(name, ("", ""))
        web_session = session_store.find_by_tmux_name(name)
        sessions.append({
            "name": name,
            "windows": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1,
            "attached": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            "command": cmd,
            "cwd": cwd,
            "web_session_id": web_session.session_id if web_session else None,
        })
    return sessions


@app.post("/api/tmux/create")
async def create_tmux_session(request: Request):
    """tmux 세션을 새로 생성하고 웹에서 attach.

    body.auto_open_on_mac=True이면 macOS에서 iTerm 새 창을 열어
    같은 tmux 세션에 자동 attach (모바일↔맥북 동시 작업용).
    """
    import re
    body = await request.json()
    tmux_name = body.get("name", f"web-{str(uuid.uuid4())[:4]}")
    cols = body.get("cols", 80)
    rows = body.get("rows", 24)
    auto_open = bool(body.get("auto_open_on_mac", False))

    # D5: tmux_runner로 통일 — timeout + config 적용
    tmux_runner.run(
        ["new-session", "-d", "-s", tmux_name, "-x", str(cols), "-y", str(rows)],
        timeout=5.0,
    )

    # 맥북 터미널 자동 오픈 — AppleScript 인젝션 방지를 위해 세션명 화이트리스트 검증
    if auto_open and platform_utils.IS_MACOS and re.fullmatch(r"[A-Za-z0-9_\-]+", tmux_name):
        attach_cmd = f"tmux -L {TMUX_SOCKET} attach -t {tmux_name}"
        try:
            ok = platform_utils.spawn_mac_terminal(attach_cmd)
            if not ok:
                logger.info("맥 터미널 자동 오픈 — 지원 앱 없음")
        except Exception as e:
            logger.warning(f"맥 터미널 자동 오픈 실패: {e}")

    # 자동 attach
    return await _attach_tmux(tmux_name, cols, rows)


@app.post("/api/tmux/attach")
async def attach_tmux_session(request: Request):
    """tmux 세션에 attach하는 웹 터미널 세션 생성. 이미 attach면 기존 세션 반환."""
    body = await request.json()
    tmux_name = body.get("name", "")
    if not tmux_name:
        return {"error": "tmux session name required"}, 400

    cols = body.get("cols", 80)
    rows = body.get("rows", 24)

    # 중복 attach 방지
    existing = session_store.find_by_tmux_name(tmux_name)
    if existing and existing.session_id in pty_mgr.sessions:
        return {"id": existing.session_id, "name": existing.name, "tmux_session": tmux_name}

    return await _attach_tmux(tmux_name, cols, rows)


@app.delete("/api/tmux/kill/{tmux_name}")
async def kill_tmux_session(tmux_name: str):
    """tmux 세션을 완전히 종료."""
    # 웹 세션도 정리
    existing = session_store.find_by_tmux_name(tmux_name)
    if existing:
        pty_mgr.destroy_session(existing.session_id)
        session_store.remove(existing.session_id)
        output_watcher.remove_session(existing.session_id)

    # D5: tmux_runner로 통일
    tmux_runner.run(["kill-session", "-t", tmux_name], timeout=5.0)
    return {"ok": True}


async def _attach_tmux(tmux_name: str, cols: int, rows: int) -> dict:
    """내부 헬퍼: tmux 세션에 PTY로 attach."""
    session_id = new_session_id()
    pty_mgr.create_session(
        session_id,
        cmd=platform_utils.find_tmux(),
        cmd_args=["tmux", "-L", TMUX_SOCKET, "attach-session", "-t", tmux_name],
        cols=cols,
        rows=rows,
    )
    info = session_store.add(session_id, name=f"tmux:{tmux_name}")
    session_store.update_tmux_name(session_id, tmux_name)
    output_watcher.add_session(session_id)
    return {"id": session_id, "name": f"tmux:{tmux_name}", "tmux_session": tmux_name}


@app.get("/api/agents")
async def list_agents():
    """모든 tmux 세션의 AI CLI 감지 결과 (탭 배지용)."""
    return agent_detector.detect_all()


@app.get("/api/agents/{tmux_name}")
async def get_agent(tmux_name: str):
    """특정 tmux 세션의 AI CLI 감지 결과."""
    info = agent_detector.detect(tmux_name)
    return info or {"agent": None}


@app.get("/api/tmux/preview/{tmux_name}")
async def get_tmux_preview(tmux_name: str, lines: int = 20, ansi: int = 1):
    """tmux pane 라이브 프리뷰 (Phase 7 #7-3).

    그리드 뷰에서 다른 세션의 마지막 N줄을 ANSI escape 보존한 채 폴링.
    """
    import preview
    content = preview.capture_pane(tmux_name, lines=lines, ansi=bool(ansi))
    if content is None:
        return {"name": tmux_name, "content": "", "available": False}
    return {"name": tmux_name, "content": content, "available": True, "lines": lines}


# Agent 훅 이벤트 ----------------------------------------------------------------
_agent_event_clients: set[WebSocket] = set()


@app.post("/api/agent/event")
async def agent_event(request: Request):
    """Claude Code Pre/Post/Stop 훅이 호출하는 엔드포인트.
    상태 갱신 + WebSocket 브로드캐스트.
    """
    body = await request.json()
    event = body.get("event", "stop")
    payload = body.get("payload", {})

    state = agent_status.on_event(event, payload)

    # 브로드캐스트
    msg = {"type": "agent_event", "event": event, "state": state}
    dead = set()
    for ws in list(_agent_event_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _agent_event_clients.difference_update(dead)

    return {"ok": True, "state": state}


@app.get("/api/agent/status")
async def agent_status_get():
    """현재 모든 활성 세션의 도구 사용 상태."""
    return {"active": agent_status.all_active(), "all": agent_status.get_state()}


# 워크스페이스 동기화 ----------------------------------------------------------
_workspace_clients: set[WebSocket] = set()


@app.get("/api/workspace")
async def workspace_get():
    return workspace.load()


@app.put("/api/workspace")
async def workspace_put(request: Request):
    data = await request.json()
    merged = workspace.update(data)

    # 다른 디바이스로 브로드캐스트
    msg = {"type": "workspace_updated", "data": merged}
    dead = set()
    for ws in list(_workspace_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _workspace_clients.difference_update(dead)
    return {"ok": True, "data": merged}


@app.websocket("/ws-workspace")
async def ws_workspace(websocket: WebSocket):
    """워크스페이스 변경 브로드캐스트 구독."""
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


@app.get("/api/safe-mode")
async def safe_mode_status():
    """현재 안전 모드 상태 조회."""
    return {"enabled": safe_mode.is_enabled()}


@app.websocket("/ws-agent")
async def ws_agent(websocket: WebSocket):
    """agent 이벤트 브로드캐스트 구독."""
    await websocket.accept()
    _agent_event_clients.add(websocket)
    try:
        # 연결 시 현재 상태 전송
        await websocket.send_json({
            "type": "agent_snapshot",
            "active": agent_status.all_active(),
        })
        while True:
            # 클라이언트 ping 또는 close 대기
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _agent_event_clients.discard(websocket)


# ---------------------------------------------------------------------------
# 파일 업로드 / 다운로드
# ---------------------------------------------------------------------------

UPLOAD_DIR = Path("/tmp/vt-uploads")


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), session_id: str = Query("")):
    """모바일에서 파일을 업로드하여 /tmp/vt-uploads/에 저장."""
    UPLOAD_DIR.mkdir(exist_ok=True)
    # 파일명 sanitize — 경로 구분자 제거, basename만 사용
    safe_name = Path(file.filename).name.replace("..", "").strip()
    if not safe_name:
        safe_name = f"upload-{uuid.uuid4().hex[:8]}"
    dest = UPLOAD_DIR / safe_name
    content = await file.read()
    dest.write_bytes(content)
    return {"ok": True, "path": str(dest), "size": len(content)}


@app.get("/api/download")
async def download_file(path: str = Query(...)):
    """지정 경로의 파일 다운로드. 업로드 디렉토리 내 파일만 허용."""
    fp = Path(path).resolve()
    # path traversal 방지 — 업로드 디렉토리 내 파일만 허용
    if not str(fp).startswith(str(UPLOAD_DIR.resolve())):
        return Response(content="Access denied", status_code=403)
    if not fp.is_file():
        return Response(content="File not found", status_code=404)
    return FileResponse(str(fp), filename=fp.name)


def _ws_auth(ws: WebSocket) -> bool:
    """WebSocket 토큰 인증. VT_TOKEN 미설정이면 항상 통과."""
    if not VT_TOKEN:
        return True
    token = ws.query_params.get("token", "")
    return token == VT_TOKEN


# Phase 8 G2: 연결 한도 + 백프레셔 + 하트비트
WS_MAX_PER_SESSION = int(os.environ.get("VT_WS_MAX_PER_SESSION", "8"))
WS_MAX_TOTAL = int(os.environ.get("VT_WS_MAX_TOTAL", "32"))
WS_HEARTBEAT_INTERVAL = float(os.environ.get("VT_WS_HEARTBEAT_INTERVAL", "30.0"))
WS_HEARTBEAT_TIMEOUT = float(os.environ.get("VT_WS_HEARTBEAT_TIMEOUT", "90.0"))
WS_QUEUE_HIGH = 200  # qsize 임계: PTY pause
WS_QUEUE_LOW = 50    # qsize 임계: PTY resume

_ws_count_per_session: dict[str, int] = {}
_ws_total_count = 0


@app.websocket("/ws/{session_id}")
async def ws_terminal(ws: WebSocket, session_id: str):
    global _ws_total_count

    if not _ws_auth(ws):
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()

    if session_id not in pty_mgr.sessions:
        await ws.close(code=4004, reason="Session not found")
        return

    # 연결 한도 검사
    if _ws_total_count >= WS_MAX_TOTAL:
        await ws.close(code=1013, reason="Max total connections")
        return
    per_session = _ws_count_per_session.get(session_id, 0)
    if per_session >= WS_MAX_PER_SESSION:
        await ws.close(code=1013, reason="Max per-session connections")
        return
    _ws_count_per_session[session_id] = per_session + 1
    _ws_total_count += 1

    loop = asyncio.get_running_loop()
    last_pong = loop.time()
    send_queue: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_HIGH * 2)
    pty_paused = False

    # [E2E] E2E 모드 협상 — 쿼리 파라미터 ?e2e=1 또는 환경변수로 opt-in
    # 요청 시 서버가 ephemeral X25519 공개키를 텍스트 JSON으로 전송,
    # 클라이언트가 자기 공개키로 응답하면 이후 바이트 메시지 전부 암호화.
    e2e_requested = (
        ws.query_params.get("e2e", "") in ("1", "true", "yes")
        or crypto_channel.is_enabled()
    )
    channel: "crypto_channel.Channel | None" = None
    if e2e_requested and crypto_channel.is_available():
        server_kp = crypto_channel.new_server_keypair()
        if server_kp is None:
            await ws.close(code=4500, reason="E2E unavailable")
            return
        await ws.send_text(json.dumps({
            "type": "e2e-hello",
            "pub": server_kp.public_b64,
        }))
        # 클라이언트 공개키 수신 대기 (첫 텍스트 메시지)
        try:
            first = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
            handshake = json.loads(first)
            if handshake.get("type") == "e2e-ack" and handshake.get("pub"):
                channel = crypto_channel.Channel.derive(
                    server_kp.private, handshake["pub"]
                )
                logger.info(f"[E2E] 핸드셰이크 성공 sid={session_id}")
            else:
                await ws.close(code=4400, reason="E2E handshake invalid")
                return
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[E2E] 핸드셰이크 실패: {e}")
            await ws.close(code=4400, reason="E2E handshake failed")
            return

    # [C1] broadcast 패턴 — subscribe/unsubscribe
    # Phase 8 G2: 자체 send 큐 + 백프레셔
    def on_data(data: bytes):
        nonlocal pty_paused
        output_watcher.feed_output(session_id, data)
        # Phase 8 G5: trust prompt 자동 응답 (VT_AUTO_TRUST=1일 때만)
        _auto_responder.feed(session_id, data)
        out = channel.encrypt_simple(data) if channel else data
        try:
            send_queue.put_nowait(out)
        except asyncio.QueueFull:
            # 큐 풀 — 가장 오래된 것 버리고 새 것 넣음 (lossy)
            try:
                send_queue.get_nowait()
                send_queue.put_nowait(out)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
        # 큐 임계 검사 — D3: pty_manager에 실제 pause/resume 전달
        qs = send_queue.qsize()
        if qs > WS_QUEUE_HIGH and not pty_paused:
            pty_paused = True
            pty_mgr.pause_read(session_id)
            logger.debug(f"[ws] qsize={qs} → PTY pause sid={session_id}")
        elif qs < WS_QUEUE_LOW and pty_paused:
            pty_paused = False
            pty_mgr.resume_read(session_id)
            logger.debug(f"[ws] qsize={qs} → PTY resume sid={session_id}")

    async def _send_worker():
        """별도 task로 큐 → ws 전송 (백프레셔 분리)."""
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
        """30초마다 ping, 90초 무응답 시 close."""
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

    send_task = asyncio.create_task(_send_worker())
    hb_task = asyncio.create_task(_heartbeat_loop())

    pty_mgr.subscribe(session_id, on_data)

    # 재접속 시 scrollback 전송 (E2E면 암호화) — 큐 우회 직접 전송
    for chunk in pty_mgr.get_scrollback(session_id):
        out = channel.encrypt_simple(chunk) if channel else chunk
        await _safe_send(ws, out)

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
        # [H5] 연결 종료 시 구독 해제 — dead WS에 send 방지
        pty_mgr.unsubscribe(session_id, on_data)
        send_task.cancel()
        hb_task.cancel()
        # 카운터 감소
        _ws_count_per_session[session_id] = max(0, _ws_count_per_session.get(session_id, 1) - 1)
        if _ws_count_per_session[session_id] == 0:
            _ws_count_per_session.pop(session_id, None)
        _ws_total_count = max(0, _ws_total_count - 1)


async def _safe_send(ws: WebSocket, data: bytes) -> None:
    """[H5] WS가 이미 닫혔으면 무시."""
    try:
        await ws.send_bytes(data)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 알림 WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws-notify")
async def ws_notify(ws: WebSocket):
    if not _ws_auth(ws):
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()
    _notify_clients.add(ws)
    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            if data.get("type") == "set_watch":
                sid = data.get("session_id")
                enabled = data.get("enabled", True)
                output_watcher.set_enabled(sid, enabled)
            elif data.get("type") == "set_timeout":
                sid = data.get("session_id")
                timeout = data.get("timeout", 3.0)
                output_watcher.set_idle_timeout(sid, timeout)
    except WebSocketDisconnect:
        pass
    finally:
        _notify_clients.discard(ws)


async def _on_task_complete(session_id: str, summary: str, audio: bytes):
    global _notify_clients  # -= 연산 때문에 global 필요
    meta = json.dumps({
        "type": "task_complete",
        "session_id": session_id,
        "summary": summary,
        "has_audio": len(audio) > 0,
    })

    dead = set()
    for ws in list(_notify_clients):
        try:
            await ws.send_text(meta)
            if audio:
                await ws.send_bytes(audio)
        except Exception:
            dead.add(ws)
    _notify_clients -= dead

    # 모바일 클라이언트가 없으면 데스크톱에서 직접 재생
    if not _notify_clients:
        platform_utils.tts_speak(summary)


# ---------------------------------------------------------------------------
# Voice endpoints — [H2] session_id를 요청에서 직접 받기
# ---------------------------------------------------------------------------

def _cancel_tts_playback() -> int:
    """재생 중인 TTS 프로세스 종료 (barge-in).

    macOS: afplay + say 프로세스 종료
    Linux: aplay / paplay 종료
    Returns: 종료된 프로세스 수 (디버깅용).
    """
    import subprocess
    killed = 0
    targets = ["afplay", "say"] if platform_utils.IS_MACOS else ["aplay", "paplay", "ffplay"]
    for tgt in targets:
        try:
            r = subprocess.run(["pkill", "-x", tgt], capture_output=True, timeout=2)
            if r.returncode == 0:
                killed += 1
        except Exception:
            pass
    return killed


@app.post("/voice/cancel")
async def voice_cancel():
    """재생 중인 TTS 중단 (barge-in). 프론트에서 녹음 시작 시 호출."""
    n = _cancel_tts_playback()
    return {"cancelled": n}


@app.post("/voice/input")
async def voice_input(request: Request):
    """음성 → STT → 지정된 세션 PTY 입력.

    [D8 barge-in] 음성 입력이 시작되면 재생 중인 TTS를 먼저 중단.
    사용자가 Claude 응답을 듣다가 말하기 시작하면 자연스럽게 말이 끊김.
    """
    # barge-in: 재생 중인 afplay/say 프로세스 종료
    _cancel_tts_playback()

    content_type = request.headers.get("content-type", "")
    audio_bytes = await request.body()

    if "webm" in content_type:
        fmt = "webm"
    elif "wav" in content_type:
        fmt = "wav"
    elif "ogg" in content_type:
        fmt = "ogg"
    else:
        fmt = "webm"

    try:
        # 언어는 body?lang / 쿼리?lang / 자동감지 순
        lang = request.query_params.get("lang", "") or None
        text = await voice_handler.transcribe(audio_bytes, input_format=fmt, language=lang)
    except Exception as e:
        logger.error(f"STT failed: {e}")
        return {"text": "", "error": str(e), "injected": False}

    # session_id를 쿼리 파라미터 또는 헤더에서 가져옴
    session_id = request.query_params.get(
        "session_id",
        request.headers.get("x-session-id", ""),
    )

    injected = False
    if text and session_id and session_id in pty_mgr.sessions:
        pty_mgr.write(session_id, (text + "\n").encode())
        injected = True

    return {"text": text, "injected": injected, "session_id": session_id}


@app.post("/voice/output")
async def voice_output(request: Request):
    body = await request.json()
    text = body.get("text", "")
    voice = body.get("voice", "ko-KR-SunHiNeural")

    try:
        audio = await voice_handler.synthesize(text, voice=voice)
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        return Response(content=b"", status_code=500)

    ct = "audio/aiff" if voice_handler._tts_engine == "macos-say" else "audio/mpeg"
    return Response(content=audio, media_type=ct)


# ---------------------------------------------------------------------------
# 출력 감시 설정 API
# ---------------------------------------------------------------------------

@app.post("/api/watch/{session_id}")
async def toggle_watch(session_id: str, request: Request):
    body = await request.json()
    enabled = body.get("enabled", True)
    timeout = body.get("timeout", 3.0)
    output_watcher.set_enabled(session_id, enabled)
    output_watcher.set_idle_timeout(session_id, timeout)
    return {"session_id": session_id, "enabled": enabled, "timeout": timeout}


# ---------------------------------------------------------------------------
# Push notifications (ntfy / Telegram)
# ---------------------------------------------------------------------------

@app.get("/api/notify/status")
async def notify_status():
    """현재 알림 채널 설정 상태."""
    return {
        "configured": notify.is_configured(),
        "ntfy": bool(os.environ.get("VT_NOTIFY_URL", "").strip()),
        "telegram": bool(
            os.environ.get("VT_TELEGRAM_TOKEN", "").strip()
            and os.environ.get("VT_TELEGRAM_CHAT_ID", "").strip()
        ),
    }


@app.post("/api/notify/test")
async def notify_test(request: Request):
    """테스트 푸시 전송. body: {title?, message?, priority?}"""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    title = body.get("title", "voice-terminal 테스트")
    message = body.get("message", "푸시 알림이 정상 작동합니다 🎉")
    priority = body.get("priority", "default")
    ok = await notify.send(title, message, priority=priority, tags="mega")
    return {"ok": ok, "configured": notify.is_configured()}


# ---------------------------------------------------------------------------
# Local mic endpoints
# ---------------------------------------------------------------------------

@app.post("/voice/local/start")
async def local_mic_start():
    return local_mic.start_recording()


@app.post("/voice/local/stop")
async def local_mic_stop(request: Request):
    result = await local_mic.stop_recording()
    text = result.get("text", "")

    session_id = request.query_params.get("session_id", "")
    injected = False
    if text and session_id and session_id in pty_mgr.sessions:
        pty_mgr.write(session_id, (text + "\n").encode())
        injected = True

    result["injected"] = injected
    result["session_id"] = session_id
    return result


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    output_watcher.on_notify(_on_task_complete)
    output_watcher.start()


@app.on_event("shutdown")
def shutdown():
    output_watcher.stop()
    pty_mgr.destroy_all()
