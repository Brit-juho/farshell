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
    """Phase 9 #8: 토큰을 query/Bearer/cookie 모두에서 받는다.

    `/api/auth`로 query token을 보내면 HttpOnly cookie로 전환되며, 이후 frontend는
    URL에서 token을 history.replaceState로 제거해 로그/공유 노출을 차단한다.
    """

    async def dispatch(self, request: Request, call_next):
        if not VT_TOKEN:
            return await call_next(request)
        path = request.url.path
        if path in ("/", "/sw.js", "/manifest.json", "/favicon.ico", "/api/auth") or path.startswith("/static"):
            return await call_next(request)
        token = (
            request.cookies.get("vt_session", "")
            or request.query_params.get("token", "")
        )
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
        if path in ("/", "/sw.js", "/manifest.json", "/favicon.ico") or path.startswith("/static"):
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


@app.post("/api/auth")
async def auth_login(request: Request):
    """Phase 9 #8: query/Bearer 토큰을 HttpOnly cookie로 변환."""
    if not VT_TOKEN:
        return JSONResponse({"ok": True, "no_token": True})
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    token = body.get("token", "") or request.query_params.get("token", "")
    if token != VT_TOKEN:
        return JSONResponse({"error": "invalid"}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "vt_session",
        VT_TOKEN,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=86400,
        path="/",
    )
    return resp


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("vt_session", path="/")
    return resp


@app.get("/favicon.ico")
async def favicon():
    """브라우저 자동 favicon 요청 — 별도 ico 없이 PNG 아이콘 재사용 (TEST_REPORT.md Bug #6)."""
    return FileResponse(
        str(Path(FRONTEND_DIR) / "icon-192.png"),
        media_type="image/png",
    )


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
