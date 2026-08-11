"""FastAPI 서버 진입점.

미들웨어 + 라우터 등록 + 정적 파일 + lifecycle.
비즈니스 로직은 routes/ 하위 모듈에 위치.
"""

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

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
from routes.files import router as files_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = str(BASE_DIR / "frontend")


# ---------------------------------------------------------------------------
# 로그 마스킹
# ---------------------------------------------------------------------------

_SECRET_QS_RE = re.compile(r"((?:token|ticket|otp)=)[^&\s\"']+", re.IGNORECASE)


class _RedactSecretsFilter(logging.Filter):
    """access log의 쿼리스트링에서 자격증명을 지운다.

    uvicorn access log는 요청 라인을 그대로 남기므로 `?token=...`이 평문으로 디스크에
    박힌다(로그 파일은 세션보다 오래 살아남고, 백업·공유 경로로도 퍼진다).
    포맷 인자 단위로 치환해 두면 서버를 어떻게 기동하든 항상 적용된다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _SECRET_QS_RE.sub(r"\1***", a) if isinstance(a, str) else a
                for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = _SECRET_QS_RE.sub(r"\1***", record.msg)
        return True


for _name in ("uvicorn.access", "uvicorn.error"):
    logging.getLogger(_name).addFilter(_RedactSecretsFilter())


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
        if path in (
            "/", "/sw.js", "/manifest.json", "/favicon.ico",
            "/api/auth", "/api/auth/status", "/api/auth/logout",
        ) or path.startswith("/static"):
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


def _allowed_origins() -> set:
    """추가로 허용할 출처 — 기본은 비어 있음(동일 출처만)."""
    raw = os.environ.get("VT_ALLOWED_ORIGINS", "")
    return {o.strip().rstrip("/").lower() for o in raw.split(",") if o.strip()}


def _origin_ok(origin: str, host_header: str) -> bool:
    """요청 Origin이 이 서버 자신인지 판정."""
    if not origin or origin == "null":
        return True  # curl/데몬/네이티브 앱 — 브라우저가 아님
    origin = origin.rstrip("/").lower()
    if origin in _allowed_origins():
        return True
    try:
        origin_host = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(origin_host) and origin_host.lower() == (host_header or "").lower()


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """크로스 사이트 접근 차단 (HTTP + WebSocket).

    OTP·비밀번호로는 막을 수 없는 유일한 경로다. 사용자가 아무 웹사이트나 방문하면
    그 페이지의 JS가 localhost:7777(주소가 뻔하다)로 붙어 명령을 실행할 수 있고,
    브라우저에 이미 세션 쿠키가 있으면 인증은 그대로 통과한다.
    - HTTP: Origin 헤더가 자기 자신이 아니면 403.
    - WebSocket: CORS가 적용되지 않는 경로라 여기서 직접 막아야 한다.
      브라우저는 WS 핸드셰이크에 Origin을 항상 붙이므로 판정이 가능하다.
    비브라우저 클라이언트(curl·clipboard_daemon·훅)는 Origin이 없어 그대로 통과한다.
    """

    async def dispatch(self, request: Request, call_next):
        if not _origin_ok(request.headers.get("origin", ""), request.headers.get("host", "")):
            return JSONResponse(
                {"error": "forbidden", "reason": "cross_origin"}, status_code=403
            )
        return await call_next(request)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            headers = _scope_headers(scope)
            if not _origin_ok(headers.get("origin", ""), headers.get("host", "")):
                response = JSONResponse(
                    {"error": "forbidden", "reason": "cross_origin"}, status_code=403
                )
                await response(scope, receive, send)
                return
        await super().__call__(scope, receive, send)


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
# 프론트엔드는 이 서버가 직접 서빙하므로(동일 출처) CORS가 필요 없다. 예전엔 `*`로
# 열려 있어서 임의의 웹사이트가 응답 본문까지 읽을 수 있었다 — 인증이 꺼진 기본
# 구성에서는 그대로 원격 코드 실행이었다. 외부 클라이언트가 필요한 경우에만 옵트인.
_cors = _allowed_origins()
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(_cors),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
# Origin 검사는 가장 바깥 — 인증 여부와 무관하게 크로스 사이트 접근을 먼저 끊는다.
app.add_middleware(OriginGuardMiddleware)

# 정적 파일
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# 라우터 등록
app.include_router(pty_router)
app.include_router(tmux_router)
app.include_router(voice_router)
app.include_router(agents_router)
app.include_router(system_router)
app.include_router(clipboard_router)
app.include_router(files_router)


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


def _is_https(request: Request) -> bool:
    """터널 뒤에서도 정확한 https 판정.

    cloudflared가 TLS를 종단하고 서버에는 평문 HTTP로 전달하므로 request.url.scheme은
    항상 http다 → 예전엔 원격 접속에서 세션 쿠키에 Secure가 **한 번도** 붙지 않았다.
    X-Forwarded-Proto를 믿어도 안전하다: 이 헤더로 할 수 있는 건 쿠키를 더 엄격하게
    만드는 것뿐이고, 약화시키는 방향은 불가능하다.
    """
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https"


def _device_label(request: Request) -> str:
    """UA에서 사람이 알아볼 만한 짧은 라벨만 뽑는다 (vt device list 표시용)."""
    ua = request.headers.get("user-agent", "")
    for needle, label in (
        ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
        ("Macintosh", "Mac"), ("Windows", "Windows"), ("Linux", "Linux"),
    ):
        if needle in ua:
            return label
    return "기기"


@app.post("/api/auth")
async def auth_login(request: Request):
    """로그인 — 비밀번호(항상) + 처음 보는 기기면 OTP(연동된 경우에만).

    흐름:
      1. 1회용 등록 티켓(QR)이면 → 그것만으로 기기 등록 (맥 물리 접근이 이미 증명됨)
      2. 비밀번호/기계 토큰 검증
      3. vt_device 쿠키가 등록된 기기면 → 바로 세션 발급
      4. 처음 보는 기기면 → OTP 연동 시에만 6자리 요구, 통과하면 기기 등록

    OTP 미연동(`vt otp setup` 전) 상태에서는 3~4가 조용히 기기 등록만 하고 넘어가므로
    동작이 기존과 완전히 같다. 그래서 나중에 OTP를 켜도 쓰던 기기는 잠기지 않는다.
    쿠키에는 비밀번호 원문이 아니라 HMAC 서명된 세션표가 실린다(auth.make_session).
    """
    if not auth.is_protected():
        return JSONResponse({"ok": True, "no_token": True})
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    qp = request.query_params
    cred = body.get("token", "") or qp.get("token", "")
    ticket = body.get("ticket", "") or qp.get("ticket", "")
    otp = str(body.get("otp", "") or "")

    device_secret = ""
    device_id = ""

    if ticket:
        if not auth.consume_ticket(ticket):
            return JSONResponse({"error": "ticket_invalid"}, status_code=401)
        device_secret, device_id = auth.register_device(_device_label(request))
        logger.info(f"[auth] 티켓으로 기기 등록: {device_id} ({_device_label(request)})")
    else:
        kind = auth.credential_kind(cred)
        if kind is None:
            return JSONResponse({"error": "invalid"}, status_code=401)

        if kind == "password":
            known = auth.verify_device(request.cookies.get("vt_device", ""))
            if known:
                device_id = known["id"]
            else:
                if auth.totp_enabled():
                    locked = auth.otp_lock_remaining()
                    if locked:
                        return JSONResponse(
                            {"error": "otp_locked", "retry_after": locked}, status_code=429
                        )
                    if not otp:
                        return JSONResponse({"error": "otp_required"}, status_code=401)
                    if not auth.verify_totp(otp):
                        auth.otp_note_failure()
                        return JSONResponse(
                            {"error": "otp_invalid",
                             "remaining": max(0, auth.OTP_MAX_FAILS - len(auth._otp_failures))},
                            status_code=401,
                        )
                    auth.otp_reset_failures()
                device_secret, device_id = auth.register_device(_device_label(request))
                logger.info(f"[auth] 새 기기 등록: {device_id} ({_device_label(request)})")
        # kind == "token"(데몬)은 기기를 만들지 않는다 — device_id 없이 세션만 발급.

    secure = _is_https(request)
    resp = JSONResponse({"ok": True, "device_id": device_id or None})
    resp.set_cookie(
        "vt_session",
        auth.make_session(device_id),
        httponly=True,
        samesite="strict",
        secure=secure,
        max_age=auth.SESSION_TTL,
        path="/",
    )
    if device_secret:
        resp.set_cookie(
            "vt_device",
            device_secret,
            httponly=True,
            samesite="strict",
            secure=secure,
            max_age=auth.DEVICE_TTL,
            path="/",
        )
    return resp


@app.get("/api/auth/status")
async def auth_status(request: Request):
    """로그인 화면이 OTP 단계를 미리 준비할 수 있도록 하는 최소 정보.

    미인증 상태에서도 접근 가능해야 하므로 비밀 정보는 담지 않는다.
    """
    return {
        "protected": auth.is_protected(),
        "otp_enabled": auth.totp_enabled(),
        "device_known": bool(auth.verify_device(request.cookies.get("vt_device", ""))),
    }


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """로그아웃 — 세션만 끊는다.

    기기 등록(vt_device)은 유지한다. 재로그인 시 OTP를 다시 치지 않기 위해서다.
    기기 자체를 끊으려면 `vt device revoke <id>`(폰 분실 시).
    """
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
            "[보안] 인증(비밀번호/VT_AUTH_TOKEN) 없음 + IP 필터 없음(VT_NETWORK_MODE=all). "
            "이 서버에 도달할 수 있는 누구나 터미널을 실행할 수 있습니다. "
            "원격 노출 시 'vt password' 또는 VT_AUTH_TOKEN 설정, VT_NETWORK_MODE=localhost/lan/tailscale 권장."
        )
    # A4: cloudflare 터널 뒤에서는 cloudflared가 localhost에서 접속하므로 client IP가
    # 항상 127.0.0.1 → IP 화이트리스트가 원격 요청을 걸러내지 못한다. 실질 방어는 VT_TOKEN.
    if not spec.allow_all and tunnel.find_active_pids():
        logger.warning(
            "[보안] cloudflare 터널 활성 + IP 필터 모드. 터널 경유 요청은 모두 127.0.0.1로 "
            "보여 IP 필터가 무력화됩니다. 원격 인증은 'vt password'/VT_AUTH_TOKEN으로 하세요 "
            "(VT_TRUST_PROXY=1 + CF-Connecting-IP 신뢰 시에만 IP 필터가 의미 있음)."
        )
    # 인증이 켜져 있는데 OTP가 아직 연동 전이면, 지금 접속하는 기기들이 자동으로
    # 신뢰 목록에 쌓이고 있다는 사실을 알린다(나중에 OTP를 켜도 잠기지 않는 이유).
    if auth.is_protected():
        n_dev = len(auth.list_devices())
        if auth.totp_enabled():
            logger.info(f"[보안] OTP 활성 — 새 기기 등록 시 6자리 요구. 등록 기기 {n_dev}개")
        else:
            logger.info(
                f"[보안] OTP 미연동 — 비밀번호만으로 동작(기존과 동일). 등록 기기 {n_dev}개. "
                "'vt otp setup'으로 연동하면 그 시점부터 '새' 기기에만 OTP를 요구합니다."
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
