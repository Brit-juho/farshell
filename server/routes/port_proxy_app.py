"""노출 포트 프록시 앱 (2.2, plan-2.2/00-port-access.md ADR-31 · §2 · §3).

노출한 포트 하나당 **전용 loopback 리스너**에 올라가는 최소 ASGI 앱이다.
cloudflared는 `--url http://127.0.0.1:<proxyPort>`로 여기를 본다.

## 왜 별도 앱인가 (ADR-31)

FarShell의 라우터는 **이 앱에 mount되지 않는다.** 단일 7777 리스너에 Host 헤더
단락 미들웨어를 두는 방식도 같은 결과를 낼 수 있지만, 그 격리는 논증 셋에 얹힌다 —
미들웨어가 최외곽일 것, 어떤 예외 경로로도 fall-through 하지 않을 것,
`--http-host-header`가 위조 불가일 것. 여기서는 라우팅 키가 **TCP 포트**라
원격 클라이언트가 위조할 수단이 없고, 터미널 API는 이 소켓에 **애초에 없다.**
감사 질문이 "미들웨어 순서가 맞나"에서 "라우트가 셋뿐인가"로 바뀐다.

**이 파일의 라우트는 셋뿐이고, 늘리면 안 된다.** `_routes()`를 보라.

## PIN 시도 제한이 파일 공유와 다른 이유

`routes/share.py`는 PIN 5회 실패 시 **share를 삭제**한다. 포트에 같은 규칙을
쓰면 지나가는 스캐너 봇 하나가 데모를 죽인다. 그리고 여기서 잠금이 실제로
막는 것은 자격증명 추측이 아니다 — PIN이 40비트(base32 8자)라 온라인 추측은
애초에 불가능하다. 막는 것은 **scrypt CPU 소모**다(검증 1회 ~60ms). 그래서
잠금은 짧고(60초) scrypt를 돌리기 **전에** 단락시킨다.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute

import auth
import port_share_store as store
import tunnel
from auth.lockout import _KeyedLockout
from ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# 자격증명 추측이 아니라 scrypt CPU 소모를 막는 값이다(모듈 docstring 참고).
PIN_MAX_FAILS = 5
PIN_LOCK_SEC = 60

_pin_lockout = _KeyedLockout(PIN_MAX_FAILS, PIN_LOCK_SEC)

# share 조회는 flock + 파일 읽기다. 요청마다 하면 이 리스너들이 메인 서버와 같은
# 이벤트 루프에 있으므로 터미널 WS까지 멈춘다(preview.py:91-93의 교훈). 짧게 캐시하고,
# 모드·PIN을 바꾸는 쪽이 invalidate_share()로 즉시 무효화한다.
_shares = TTLCache(ttl=2.0)

COOKIE_BASE = "fsh_pshare"

# 프록시가 소비하고 업스트림에 넘기지 않을 쿠키 이름들.
_OUR_COOKIES = {COOKIE_BASE, f"__Host-{COOKIE_BASE}"}

# RFC 7230 hop-by-hop — 프록시 경계를 넘기면 안 되는 헤더.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}


def invalidate_share(port: int) -> None:
    """모드·PIN을 바꾼 쪽이 부른다 — 다음 요청부터 곧바로 반영된다."""
    _shares.invalidate(str(port))


# --- 쿠키 (§2-1) ---------------------------------------------------------------


def cookie_name(secure: bool) -> str:
    """HTTPS면 `__Host-` 접두사를 **반드시** 쓴다.

    모든 quick tunnel이 `*.trycloudflare.com` 아래 형제다. 이 도메인이 Public
    Suffix List에 없다면 공격자가 자기 터널을 띄워 `Domain=.trycloudflare.com`
    쿠키를 심을 수 있고, 쿠키는 출처 도메인을 안 싣기 때문에 서버가 구분할 수
    없다(세션 고정). `__Host-`는 브라우저가 Secure + Path=/ + Domain 없음을
    강제하므로 형제 도메인이 심는 것이 원천 차단된다 — 이걸 쓰면 PSL 등재
    여부를 조사할 필요 자체가 사라진다.
    """
    return f"__Host-{COOKIE_BASE}" if secure else COOKIE_BASE


def mint_cookie(share: dict, device_id: str = "-") -> tuple[str, int]:
    """(쿠키 값, 수명 초). 수명은 min(share 남은 수명, 12h)."""
    ttl = store.cookie_ttl(share)
    exp = int(time.time()) + ttl
    payload = f"v1.{exp}.{share['port']}.{share['shareId']}.{device_id or '-'}"
    return f"{payload}.{auth.sign_payload(payload)}", ttl


def verify_cookie(value: str, share: dict) -> bool:
    """서명·만료·포트·shareId·기기 폐기까지 전부 확인한다.

    - `shareId` 비교: 노출을 껐다 켜면 새 shareId가 발급되므로 **옛 쿠키가 죽는다**.
    - `deviceId` 비교: `fsh device revoke`로 폰을 폐기하면 그 기기의 포트 접근도
      함께 죽는다. `auth.make_session`이 세션 쿠키에 쓰는 기법과 같다.
    """
    if not value:
        return False
    parts = value.split(".")
    if len(parts) != 6 or parts[0] != "v1":
        return False
    _, exp_s, port_s, share_id, did, sig = parts
    payload = ".".join(parts[:5])
    if not hmac.compare_digest(auth.sign_payload(payload), sig):
        return False
    try:
        if int(exp_s) <= int(time.time()):
            return False
    except (TypeError, ValueError):
        return False
    if port_s != str(share["port"]):
        return False
    if share_id != share["shareId"]:
        return False
    if did != "-" and not auth._find_device(did):
        return False
    return True


def _set_cookie(resp: Response, share: dict, secure: bool, device_id: str = "-") -> None:
    value, ttl = mint_cookie(share, device_id)
    resp.set_cookie(
        cookie_name(secure), value,
        max_age=ttl, httponly=True, samesite="lax", secure=secure, path="/",
    )


# --- 요청 보조 -----------------------------------------------------------------


def _client_key(request) -> str:
    """잠금 카운터의 키.

    이 리스너는 127.0.0.1에만 바인드되므로 소켓 주소는 언제나 로컬(= cloudflared)이다.
    그걸 그대로 키로 쓰면 카운터가 전역이 되어 **봇 하나가 모두를 잠근다.**
    그래서 Cloudflare가 붙이는 `CF-Connecting-IP`를 우선한다. 이 값을 위조해봐야
    자기 버킷이 갈릴 뿐이고, 실제 보안은 PIN 엔트로피(40비트)가 진다.
    """
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    client = getattr(request, "client", None)
    return (client.host if client else "") or "unknown"


def safe_next(raw: str | None) -> str:
    """경로만 허용한다.

    `//evil.com`은 프로토콜 상대 URL이라 브라우저가 외부로 나간다 — 오픈
    리다이렉트의 고전적 형태다. 스킴이 붙은 것도 거부한다.
    """
    nxt = raw or "/"
    if not nxt.startswith("/") or nxt.startswith("//") or ":" in nxt.split("/")[0]:
        return "/"
    return nxt


async def _live_share(port: int) -> dict | None:
    cached = _shares.get(str(port))
    if cached is not None:
        return cached
    share = await asyncio.to_thread(store.get_share, port)
    if share is not None:
        _shares.set(str(port), share)
    return share


# --- PIN 페이지 (정적, 앱 크롬 없음) ------------------------------------------------


def _pin_page(label: str, error: str = "", status: int = 200) -> HTMLResponse:
    err = f'<p class="err">{error}</p>' if error else ""
    safe_label = (label or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FarShell — 접근 코드</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#111;color:#eee;
  display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
form{{background:#1c1c1c;padding:24px;border-radius:12px;width:min(340px,90vw)}}
h1{{font-size:15px;margin:0 0 4px}}
p.sub{{font-size:13px;color:#888;margin:0 0 16px}}
input{{width:100%;box-sizing:border-box;font-size:18px;padding:12px;border-radius:8px;
  border:1px solid #444;background:#000;color:#eee;letter-spacing:3px;text-align:center;
  text-transform:uppercase}}
button{{width:100%;margin-top:12px;padding:12px;border-radius:8px;border:none;
  background:#4a8;color:#000;font-weight:600;font-size:15px}}
.err{{color:#f66;font-size:13px;margin:0 0 8px}}
</style></head><body>
<form method="post" action="/__fsh/pin">
<h1>접근 코드를 입력하세요</h1>
<p class="sub">{safe_label}</p>
{err}
<input name="pin" autocomplete="off" autocapitalize="characters" autofocus maxlength="20"
       placeholder="XXXX-XXXX">
<button type="submit">열기</button>
</form></body></html>"""
    return HTMLResponse(html, status_code=status)


# --- 게이트 --------------------------------------------------------------------


async def _gate(request, share: dict) -> Response | None:
    """통과면 None, 막으면 돌려보낼 응답.

    **모르는 모드는 거부한다 — `public`으로 폴백하지 않는다**(fail-closed).
    """
    mode = share.get("mode")
    if mode == "public":
        return None
    if mode not in ("session", "pin"):
        logger.warning(f"[port-proxy] 알 수 없는 모드 '{mode}' — 거부 (port={share['port']})")
        return JSONResponse({"error": "bad_mode"}, status_code=403)

    secure = auth.is_https(request)
    if verify_cookie(request.cookies.get(cookie_name(secure), ""), share):
        return None

    if mode == "pin":
        return _pin_page(share.get("label", ""))

    # session — 메인 origin으로 보내 로그인/기기 확인을 받고 티켓을 받아온다(§3).
    status = await asyncio.to_thread(tunnel.get_tunnel_status)
    main_url = (status or {}).get("url") or ""
    if not main_url:
        return JSONResponse(
            {"error": "main_tunnel_down",
             "reason": "로그인 확인을 받을 메인 주소를 찾을 수 없습니다."},
            status_code=503,
        )
    nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    # safe="" — 기본값은 `/`를 남기는데, 원 경로에 `&`나 `#`가 섞이면 쿼리가
    # 쪼개져 next가 잘리거나 다른 파라미터가 주입된다.
    target = (
        f"{main_url.rstrip('/')}/p/authorize"
        f"?share={quote(share['shareId'], safe='')}&next={quote(safe_next(nxt), safe='')}"
    )
    return RedirectResponse(target, status_code=302)


# --- 라우트 1: 티켓 콜백 (§3 6단계) ------------------------------------------------


def _cb_handler(port: int):
    async def handler(request):
        share = await _live_share(port)
        if share is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if share.get("mode") != "session":
            return JSONResponse({"error": "not_session_mode"}, status_code=400)

        ticket = request.query_params.get("t", "")
        rec = await asyncio.to_thread(auth.consume_ticket, ticket)  # 1회용 — 즉시 소멸
        if rec is None:
            return JSONResponse({"error": "bad_ticket"}, status_code=401)
        # 티켓은 이 share에 묶여 발급된다. 다른 share의 티켓으로는 못 들어온다.
        if rec.get("share") != share["shareId"]:
            logger.warning(f"[port-proxy] 다른 share의 티켓 사용 시도 (port={port})")
            return JSONResponse({"error": "bad_ticket"}, status_code=401)

        resp = RedirectResponse(safe_next(request.query_params.get("next")), status_code=303)
        _set_cookie(resp, share, auth.is_https(request), rec.get("device") or "-")
        await asyncio.to_thread(store.note_view, port)
        return resp

    return handler


# --- 라우트 2: PIN 제출 ----------------------------------------------------------


def _pin_handler(port: int):
    async def handler(request):
        share = await _live_share(port)
        if share is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if share.get("mode") != "pin":
            return JSONResponse({"error": "not_pin_mode"}, status_code=400)

        key = _client_key(request)
        remaining = _pin_lockout.lock_remaining(key)
        if remaining > 0:
            # scrypt를 돌리기 **전에** 단락시킨다 — 잠금의 목적이 CPU 보호다.
            return _pin_page(share.get("label", ""),
                             f"시도가 많습니다. {remaining}초 후 다시 시도하세요.", status=429)

        form = await request.form()
        pin = str(form.get("pin", ""))
        if not await asyncio.to_thread(store.verify_pin, share, pin):
            _pin_lockout.note_failure(key)
            return _pin_page(share.get("label", ""), "코드가 올바르지 않습니다.", status=401)

        _pin_lockout.reset_failures(key)
        resp = RedirectResponse(safe_next(request.query_params.get("next")), status_code=303)
        _set_cookie(resp, share, auth.is_https(request))
        await asyncio.to_thread(store.note_view, port)
        return resp

    return handler


# --- 라우트 3: 프록시 ------------------------------------------------------------


def _upstream_headers(request, port: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP or lk == "host":
            continue
        # content-length는 **남긴다** — httpx는 Content-Length가 이미 있으면
        # Transfer-Encoding: chunked를 덧붙이지 않는다. 떼면 모든 업로드가
        # chunked가 되어 그걸 싫어하는 업스트림에서 깨진다.
        if lk == "cookie":
            v = _strip_our_cookies(v)
            if not v:
                continue
        out.append((k, v))
    # 업스트림에는 localhost를 보낸다 — Vite 등의 DNS rebinding 방어
    # (server.allowedHosts)에 걸리지 않는 쪽이 호환성이 넓다.
    out.append(("host", f"localhost:{port}"))
    client = getattr(request, "client", None)
    out.append(("x-forwarded-for", request.headers.get("cf-connecting-ip")
                or (client.host if client else "") or "127.0.0.1"))
    out.append(("x-forwarded-proto", "https" if auth.is_https(request) else "http"))
    fwd_host = request.headers.get("host", "")
    if fwd_host:
        out.append(("x-forwarded-host", fwd_host))
    return out


def _strip_our_cookies(cookie_header: str) -> str:
    """우리 인증 쿠키는 앱에 넘기지 않는다 — 앱이 읽을 이유가 없다."""
    kept = []
    for part in cookie_header.split(";"):
        name = part.split("=", 1)[0].strip()
        if name not in _OUR_COOKIES:
            kept.append(part.strip())
    return "; ".join(kept)


def _rewrite_location(value: str, port: int, public_host: str, secure: bool) -> str:
    """업스트림이 절대 URL로 리다이렉트하면 localhost가 새어나간다."""
    if not public_host:
        return value
    for prefix in (f"http://localhost:{port}", f"http://127.0.0.1:{port}"):
        if value.startswith(prefix):
            scheme = "https" if secure else "http"
            return f"{scheme}://{public_host}{value[len(prefix):]}"
    return value


def _strip_cookie_domain(set_cookie: str) -> str:
    """업스트림이 상위 도메인에 쿠키를 심지 못하게 Domain 속성을 뗀다."""
    return "; ".join(
        p for p in set_cookie.split(";") if not p.strip().lower().startswith("domain=")
    )


def _proxy_handler(port: int, client_holder: dict):
    async def handler(request):
        share = await _live_share(port)
        if share is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        blocked = await _gate(request, share)
        if blocked is not None:
            return blocked

        client: httpx.AsyncClient = client_holder["client"]
        url = f"http://127.0.0.1:{port}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"

        # 본문이 없는 메서드에 스트림을 물리면 길이를 모르는 chunked GET이 된다.
        has_body = request.method not in ("GET", "HEAD", "OPTIONS")
        upstream_req = client.build_request(
            request.method, url,
            headers=_upstream_headers(request, port),
            content=request.stream() if has_body else None,  # 업로드도 스트리밍
        )
        try:
            upstream = await client.send(upstream_req, stream=True)
        except httpx.ConnectError:
            return JSONResponse(
                {"error": "upstream_down",
                 "reason": f"localhost:{port} 에 응답하는 서비스가 없습니다."},
                status_code=502,
            )
        except httpx.HTTPError as e:
            logger.info(f"[port-proxy] upstream 오류 port={port}: {e}")
            return JSONResponse({"error": "upstream_error"}, status_code=502)

        secure = auth.is_https(request)
        public_host = request.headers.get("host", "")
        headers = {}
        cookies = []
        for k, v in upstream.headers.multi_items():
            lk = k.lower()
            if lk in _HOP_BY_HOP:
                continue
            if lk == "set-cookie":
                cookies.append(_strip_cookie_domain(v))
                continue
            if lk == "location":
                v = _rewrite_location(v, port, public_host, secure)
            headers[k] = v

        resp = StreamingResponse(
            upstream.aiter_raw(),  # raw — content-encoding을 그대로 통과시킨다
            status_code=upstream.status_code,
            headers=headers,
            background=BackgroundTask(upstream.aclose),
        )
        for c in cookies:
            resp.raw_headers.append((b"set-cookie", c.encode("latin-1", "ignore")))
        return resp

    return handler


def _ws_handler(port: int):
    async def handler(websocket):
        share = await _live_share(port)
        if share is None:
            await websocket.close(code=4004)
            return
        # WS는 리다이렉트도 PIN 페이지도 못 보낸다 — 쿠키만 본다. 브라우저는
        # 앞선 HTTP 요청에서 이미 게이트를 통과해 쿠키를 갖고 있다.
        if share.get("mode") != "public":
            if share.get("mode") not in ("session", "pin"):
                await websocket.close(code=4403)
                return
            secure = (websocket.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
                      == "https") or websocket.url.scheme == "wss"
            if not verify_cookie(websocket.cookies.get(cookie_name(secure), ""), share):
                await websocket.close(code=4001)
                return

        try:
            import websockets
        except ImportError:  # pragma: no cover — requirements-core에 있다
            await websocket.close(code=4500)
            return

        path = websocket.url.path
        query = websocket.url.query
        url = f"ws://127.0.0.1:{port}{path}" + (f"?{query}" if query else "")
        subprotocols = [
            p.strip() for p in
            (websocket.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()
        ]
        cookie = _strip_our_cookies(websocket.headers.get("cookie", ""))
        extra = {"Host": f"localhost:{port}"}
        if cookie:
            extra["Cookie"] = cookie
        if websocket.headers.get("origin"):
            extra["Origin"] = websocket.headers["origin"]

        try:
            upstream = await asyncio.wait_for(
                websockets.connect(
                    url, additional_headers=extra, max_size=None,
                    subprotocols=subprotocols or None,
                ),
                timeout=10.0,
            )
        except Exception as e:  # noqa: BLE001 — 연결 실패는 상태이지 서버 오류가 아니다
            logger.info(f"[port-proxy] WS upstream 연결 실패 port={port}: {e}")
            await websocket.close(code=4502)
            return

        await websocket.accept(subprotocol=upstream.subprotocol)

        async def _down():
            async for message in upstream:
                if isinstance(message, bytes):
                    await websocket.send_bytes(message)
                else:
                    await websocket.send_text(message)

        async def _up():
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
                elif msg.get("text") is not None:
                    await upstream.send(msg["text"])

        down = asyncio.create_task(_down())
        up = asyncio.create_task(_up())
        try:
            _done, pending = await asyncio.wait({down, up}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
        except Exception:  # noqa: BLE001
            pass
        finally:
            down.cancel()
            up.cancel()
            try:
                await upstream.close()
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass

    return handler


# --- 앱 조립 -------------------------------------------------------------------

_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def build_proxy_app(port: int) -> Starlette:
    """포트 하나를 front하는 앱. **라우트는 셋뿐이다 — 늘리지 말 것**(ADR-31).

    `port`로 묶고 share는 매 요청 조회한다 — 모드를 바꿔도, 껐다 켜도 리스너를
    다시 띄울 필요가 없다.
    """
    client_holder: dict = {}

    @asynccontextmanager
    async def _lifespan(_app):
        client_holder["client"] = httpx.AsyncClient(
            timeout=httpx.Timeout(None, connect=5.0),  # SSE·긴 폴링을 끊지 않는다
            follow_redirects=False,  # 리다이렉트는 브라우저가 따르게 둔다
        )
        try:
            yield
        finally:
            c = client_holder.pop("client", None)
            if c is not None:
                await c.aclose()

    routes = [
        Route("/__fsh/cb", _cb_handler(port), methods=["GET"]),
        Route("/__fsh/pin", _pin_handler(port), methods=["POST"]),
        WebSocketRoute("/{path:path}", _ws_handler(port)),
        Route("/{path:path}", _proxy_handler(port, client_holder), methods=_ALL_METHODS),
    ]
    return Starlette(routes=routes, lifespan=_lifespan)
