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
from session_store import SessionStore
from output_watcher import OutputWatcher
import voice_handler
import local_mic
import platform_utils

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# [M8] 절대 경로 사용
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = str(BASE_DIR / "frontend")

# 토큰 인증 — RALPH_TOKEN 환경변수가 설정되면 활성화
RALPH_TOKEN = os.environ.get("RALPH_TOKEN", "")


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not RALPH_TOKEN:
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
        if token != RALPH_TOKEN:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app = FastAPI()
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
    session_id = str(uuid.uuid4())[:8]
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

@app.get("/api/tmux/sessions")
async def list_tmux_sessions():
    """서버에서 실행 중인 tmux 세션 목록 (현재 명령, cwd 포함)."""
    import subprocess
    fmt = "#{session_name}\t#{session_windows}\t#{session_attached}\t#{pane_current_command}\t#{pane_current_path}"
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", fmt],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        sessions = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            name = parts[0]
            # 이미 웹에서 attach된 세션인지 표시
            web_session = session_store.find_by_tmux_name(name)
            sessions.append({
                "name": name,
                "windows": int(parts[1]) if len(parts) > 1 else 1,
                "attached": int(parts[2]) if len(parts) > 2 else 0,
                "command": parts[3] if len(parts) > 3 else "",
                "cwd": parts[4] if len(parts) > 4 else "",
                "web_session_id": web_session.session_id if web_session else None,
            })
        return sessions
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


@app.post("/api/tmux/create")
async def create_tmux_session(request: Request):
    """tmux 세션을 새로 생성하고 웹에서 attach."""
    import subprocess
    body = await request.json()
    tmux_name = body.get("name", f"web-{str(uuid.uuid4())[:4]}")
    cols = body.get("cols", 80)
    rows = body.get("rows", 24)

    # tmux 세션 생성
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", tmux_name, "-x", str(cols), "-y", str(rows)],
        timeout=5,
    )

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
    import subprocess
    # 웹 세션도 정리
    existing = session_store.find_by_tmux_name(tmux_name)
    if existing:
        pty_mgr.destroy_session(existing.session_id)
        session_store.remove(existing.session_id)
        output_watcher.remove_session(existing.session_id)

    subprocess.run(["tmux", "kill-session", "-t", tmux_name], timeout=5)
    return {"ok": True}


async def _attach_tmux(tmux_name: str, cols: int, rows: int) -> dict:
    """내부 헬퍼: tmux 세션에 PTY로 attach."""
    session_id = str(uuid.uuid4())[:8]
    pty_mgr.create_session(
        session_id,
        cmd=platform_utils.find_tmux(),
        cmd_args=["tmux", "attach-session", "-t", tmux_name],
        cols=cols,
        rows=rows,
    )
    info = session_store.add(session_id, name=f"tmux:{tmux_name}")
    info.tmux_name = tmux_name
    output_watcher.add_session(session_id)
    return {"id": session_id, "name": f"tmux:{tmux_name}", "tmux_session": tmux_name}


# ---------------------------------------------------------------------------
# 파일 업로드 / 다운로드
# ---------------------------------------------------------------------------

UPLOAD_DIR = Path("/tmp/ralphton_uploads")


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), session_id: str = Query("")):
    """모바일에서 파일을 업로드하여 /tmp/ralphton_uploads/에 저장."""
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
    """WebSocket 토큰 인증. RALPH_TOKEN 미설정이면 항상 통과."""
    if not RALPH_TOKEN:
        return True
    token = ws.query_params.get("token", "")
    return token == RALPH_TOKEN


@app.websocket("/ws/{session_id}")
async def ws_terminal(ws: WebSocket, session_id: str):
    if not _ws_auth(ws):
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()

    if session_id not in pty_mgr.sessions:
        await ws.close(code=4004, reason="Session not found")
        return

    loop = asyncio.get_running_loop()

    # [C1] broadcast 패턴 — subscribe/unsubscribe
    def on_data(data: bytes):
        output_watcher.feed_output(session_id, data)
        asyncio.run_coroutine_threadsafe(
            _safe_send(ws, data), loop
        )

    pty_mgr.subscribe(session_id, on_data)

    # 재접속 시 scrollback 전송
    for chunk in pty_mgr.get_scrollback(session_id):
        await _safe_send(ws, chunk)

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.receive":
                if "text" in msg:
                    data = json.loads(msg["text"])
                    if data.get("type") == "resize":
                        pty_mgr.resize(session_id, data["cols"], data["rows"])
                elif "bytes" in msg:
                    pty_mgr.write(session_id, msg["bytes"])
            elif msg["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        # [H5] 연결 종료 시 구독 해제 — dead WS에 send 방지
        pty_mgr.unsubscribe(session_id, on_data)


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

@app.post("/voice/input")
async def voice_input(request: Request):
    """음성 → STT → 지정된 세션 PTY 입력."""
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
        text = await voice_handler.transcribe(audio_bytes, input_format=fmt)
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
