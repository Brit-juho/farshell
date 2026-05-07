"""FastAPI 서버 진입점.

미들웨어 + 라우터 등록 + 정적 파일 + lifecycle.
비즈니스 로직은 routes/ 하위 모듈에 위치.
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

import network_access
from deps import pty_mgr, output_watcher
from routes.pty import router as pty_router, on_task_complete
from routes.tmux import router as tmux_router
from routes.voice import router as voice_router
from routes.agents import router as agents_router
from routes.system import router as system_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = str(BASE_DIR / "frontend")
VT_TOKEN = os.environ.get("VT_TOKEN", "")


# ---------------------------------------------------------------------------
# 미들웨어
# ---------------------------------------------------------------------------

class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not VT_TOKEN:
            return await call_next(request)
        path = request.url.path
        if path in ("/", "/sw.js", "/manifest.json") or path.startswith("/static"):
            return await call_next(request)
        token = request.query_params.get("token", "")
        if not token:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
        if token != VT_TOKEN:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


class NetworkAccessMiddleware(BaseHTTPMiddleware):
    """Phase 8 G1 + Codex: CIDR 기반 IP 화이트리스트 (HTTP + WebSocket)."""

    async def dispatch(self, request: Request, call_next):
        spec = network_access.get_current_spec()
        if spec.allow_all:
            return await call_next(request)
        path = request.url.path
        if path in ("/", "/sw.js", "/manifest.json") or path.startswith("/static"):
            return await call_next(request)
        client_host = request.client.host if request.client else None
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

    async def __call__(self, scope, receive, send):
        """Codex: WebSocket scope도 IP 필터 적용."""
        if scope["type"] == "websocket":
            spec = network_access.get_current_spec()
            if not spec.allow_all:
                client = scope.get("client")
                host = client[0] if client else None
                if not spec.is_allowed(host):
                    response = JSONResponse(
                        {"error": "forbidden", "reason": "ip_not_allowed", "ip": host},
                        status_code=403,
                    )
                    await response(scope, receive, send)
                    return
        await super().__call__(scope, receive, send)


# ---------------------------------------------------------------------------
# 앱 구성
# ---------------------------------------------------------------------------

app = FastAPI()
app.add_middleware(NetworkAccessMiddleware)
app.add_middleware(TokenAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 정적 파일
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# 라우터 등록
app.include_router(pty_router)
app.include_router(tmux_router)
app.include_router(voice_router)
app.include_router(agents_router)
app.include_router(system_router)


# ---------------------------------------------------------------------------
# 정적 파일 직접 서빙 (SW, manifest는 루트 경로 필요)
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(str(Path(FRONTEND_DIR) / "index.html"))


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        str(Path(FRONTEND_DIR) / "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json")
async def manifest():
    return FileResponse(str(Path(FRONTEND_DIR) / "manifest.json"))


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    output_watcher.on_notify(on_task_complete)
    output_watcher.start()


@app.on_event("shutdown")
def shutdown():
    output_watcher.stop()
    pty_mgr.destroy_all()
