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

import auth
import network_access
import tunnel
from deps import pty_mgr, output_watcher
from routes.pty import router as pty_router, on_task_complete
from routes.tmux import router as tmux_router
from routes.voice import router as voice_router
from routes.agents import router as agents_router
from routes.system import router as system_router
from routes.clipboard import router as clipboard_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = str(BASE_DIR / "frontend")


# ---------------------------------------------------------------------------
# 미들웨어
# ---------------------------------------------------------------------------

class TokenAuthMiddleware(BaseHTTPMiddleware):
    """인증: 서명 세션 쿠키(사람) 또는 기계 토큰(데몬/QR)을 받는다.

    - 사람: `/api/auth`에 비밀번호 제출 → 검증 성공 시 만료 서명 세션 쿠키 발급.
      이후 요청은 `vt_session` 쿠키로 통과(원문 비밀번호 아님).
    - 기계: clipboard_daemon·tui·hook은 Bearer/query로 VT_TOKEN 직접 전달.
    자세한 판정 로직은 auth 모듈(비밀번호 해시 + HMAC 서명) 참조.
    """

    async def dispatch(self, request: Request, call_next):
        if not auth.is_protected():
            return await call_next(request)
        path = request.url.path
        if path in ("/", "/sw.js", "/manifest.json", "/favicon.ico", "/api/auth") or path.startswith("/static"):
            return await call_next(request)
        token = (
            request.cookies.get("vt_session", "")
            or request.query_params.get("token", "")
        )
        if not token:
            authz = request.headers.get("authorization", "")
            if authz.startswith("Bearer "):
                token = authz[7:]
        if not auth.check_request(token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def _effective_client_ip(raw_host, headers) -> "str | None":
    """VT_TRUST_PROXY=1일 때만 프록시 헤더로 실 클라이언트 IP를 도출.

    A3: 예전엔 X-Forwarded-For 최좌측 값을 신뢰했는데, 그건 클라이언트가 임의로
    넣는 값이라 `X-Forwarded-For: 127.0.0.1` 한 줄로 IP 화이트리스트를 우회했다.
    - 신뢰 프록시가 붙인 값만 믿는다: Cloudflare는 `CF-Connecting-IP`가 신뢰 가능.
    - 일반 프록시는 XFF의 **최우측**(우리 바로 앞 신뢰 홉이 추가한 값)을 쓴다.
    `headers`는 dict-like(get 지원) — Request.headers / WebSocket scope headers 모두 수용.
    """
    if os.environ.get("VT_TRUST_PROXY", "").strip() != "1":
        return raw_host
    cf = (headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    xff = (headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[-1].strip()
    return raw_host


def _scope_headers(scope) -> dict:
    """ASGI scope headers(list[tuple[bytes,bytes]])를 소문자 dict로."""
    out = {}
    for k, v in scope.get("headers", []):
        try:
            out[k.decode("latin-1").lower()] = v.decode("latin-1")
        except Exception:
            continue
    return out


class NetworkAccessMiddleware(BaseHTTPMiddleware):
    """Phase 8 G1 + Codex: CIDR 기반 IP 화이트리스트 (HTTP + WebSocket)."""

    async def dispatch(self, request: Request, call_next):
        spec = network_access.get_current_spec()
        if spec.allow_all:
            return await call_next(request)
        path = request.url.path
        if path in ("/", "/sw.js", "/manifest.json", "/favicon.ico") or path.startswith("/static"):
            return await call_next(request)
        raw = request.client.host if request.client else None
        client_host = _effective_client_ip(raw, request.headers)
        if not spec.is_allowed(client_host):
            return JSONResponse(
                {"error": "forbidden", "reason": "ip_not_allowed", "ip": client_host},
                status_code=403,
            )
        return await call_next(request)

    async def __call__(self, scope, receive, send):
        """Codex: WebSocket scope도 IP 필터 적용. A3: HTTP와 동일한 프록시 헤더 처리."""
        if scope["type"] == "websocket":
            spec = network_access.get_current_spec()
            if not spec.allow_all:
                client = scope.get("client")
                raw = client[0] if client else None
                host = _effective_client_ip(raw, _scope_headers(scope))
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
app.include_router(clipboard_router)


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
    """비밀번호(또는 기계 토큰) 제출 → 검증 성공 시 만료 서명 세션 쿠키 발급.

    쿠키에는 비밀번호 원문이 아니라 HMAC 서명된 세션표가 실린다(auth.make_session).
    """
    if not auth.is_protected():
        return JSONResponse({"ok": True, "no_token": True})
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    cred = body.get("token", "") or request.query_params.get("token", "")
    if not auth.check_credential(cred):
        return JSONResponse({"error": "invalid"}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        "vt_session",
        auth.make_session(),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=auth.SESSION_TTL,
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
    # A5: 기본 자세가 open이면(인증 없음 + IP 필터 없음) 명시적으로 경고. LAN 노출 시
    # 인증 없는 터미널 = 원격 코드 실행이므로 사용자가 인지하도록 한다.
    spec = network_access.get_current_spec()
    if spec.allow_all and not auth.is_protected():
        logger.warning(
            "[보안] 인증(비밀번호/VT_TOKEN) 없음 + IP 필터 없음(VT_NETWORK_MODE=all). "
            "이 서버에 도달할 수 있는 누구나 터미널을 실행할 수 있습니다. "
            "원격 노출 시 'vt password' 또는 VT_TOKEN 설정, VT_NETWORK_MODE=localhost/lan/tailscale 권장."
        )
    # A4: cloudflare 터널 뒤에서는 cloudflared가 localhost에서 접속하므로 client IP가
    # 항상 127.0.0.1 → IP 화이트리스트가 원격 요청을 걸러내지 못한다. 실질 방어는 VT_TOKEN.
    if not spec.allow_all and tunnel.find_active_pids():
        logger.warning(
            "[보안] cloudflare 터널 활성 + IP 필터 모드. 터널 경유 요청은 모두 127.0.0.1로 "
            "보여 IP 필터가 무력화됩니다. 원격 인증은 VT_TOKEN으로 하세요 "
            "(VT_TRUST_PROXY=1 + CF-Connecting-IP 신뢰 시에만 IP 필터가 의미 있음)."
        )
    output_watcher.on_notify(on_task_complete)
    output_watcher.start()
    # STT 모델 idle 언로드 모니터 — 음성 미사용 시 ~150MB 회수 (VT_STT_IDLE_SEC)
    import voice_handler
    import asyncio as _asyncio
    _asyncio.create_task(voice_handler.stt_idle_monitor())


@app.on_event("shutdown")
def shutdown():
    output_watcher.stop()
    pty_mgr.destroy_all()
