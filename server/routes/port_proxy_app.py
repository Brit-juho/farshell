"""노출 포트 프록시 앱 (2.2, ADR-31).

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
잠금은 짧고(60초) scrypt를 돌리기 **전에** 단락시킨다. 여러 IP가 동시에 몰려도
서버 스레드 풀을 다 잡아먹지 않도록 scrypt 동시 실행을 `_PIN_VERIFY_SLOTS`개로
묶는다. share 자체는 잠그지 않고, 실패는 누적해 `fsh tunnel list`에 보여 준다.

## 업스트림이 믿을 수 있는 값만 넘긴다 (2026-10)

클라이언트가 직접 보낸 `X-Forwarded-*`·`Forwarded`·`X-Real-IP`·`CF-*`는 **지우고**
프록시가 확인한 값 하나만 다시 넣는다. 예전엔 받은 헤더를 그대로 넘긴 뒤 우리 값을
덧붙여서, 맨 앞 값을 읽는 앱이 `X-Forwarded-For: 127.0.0.1`을 보낸 외부인을 로컬
요청으로 봤다. Host는 Vite 호환 때문에 계속 `localhost:<port>`로 바꾼다 — 그래서
앱이 거는 "localhost에서만" 방어는 이 프록시 뒤에서 의미가 없고, 그게 위험 포트를
노출하기 전에 한 번 더 확인받는 이유다(port_share_store.danger_reason).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute

import auth
import i18n
from i18n import tr
import port_share_store as store
from auth.lockout import _KeyedLockout
from ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# 자격증명 추측이 아니라 scrypt CPU 소모를 막는 값이다(모듈 docstring 참고).
PIN_MAX_FAILS = 5
PIN_LOCK_SEC = 60
PIN_BODY_MAX = 4096  # PIN 하나를 담는 폼이다 — 그 이상은 읽지 않고 거절한다
_PIN_VERIFY_SLOTS = 2  # scrypt 동시 실행 상한(서버 전체, 모든 share 합산)

# WS는 핸드셰이크 뒤로는 게이트를 다시 안 탄다 — 이 주기로 다시 확인해서 PIN 재발급·
# 모드 변경·기기 폐기/만료·노출 만료 뒤에도 열린 연결이 살아 있지 않게 한다.
WS_RECHECK_SEC = 60.0

_pin_lockout = _KeyedLockout(PIN_MAX_FAILS, PIN_LOCK_SEC)
_pin_slots = asyncio.Semaphore(_PIN_VERIFY_SLOTS)

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

# 클라이언트가 보낸 값을 업스트림에 그대로 넘기면 안 되는 "프록시가 붙이는" 헤더들.
# 모듈 docstring 참고. 접두사 `cf-`(Cloudflare가 붙이는 값)도 같이 지운다.
_CLIENT_PROXY_HEADERS = {
    "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port",
    "x-forwarded-prefix", "x-forwarded-server", "forwarded", "x-real-ip",
    "true-client-ip", "x-client-ip", "x-cluster-client-ip",
}


def _is_client_proxy_header(lk: str) -> bool:
    return lk in _CLIENT_PROXY_HEADERS or lk.startswith("cf-")


# 프록시가 직접 그리는 페이지(PIN·안내)에 붙이는 헤더. 남의 사이트가 iframe에 넣어
# 입력을 가로채지 못하게 하고, 캐시·Referer로 주소가 새지 않게 한다.
_PAGE_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
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
    """(쿠키 값, 수명 초). 수명은 min(share 남은 수명, 12h, 기기 남은 수명)."""
    ttl = store.cookie_ttl(share)
    if device_id and device_id != "-":
        dev = auth.find_alive_device(device_id)
        ttl = min(ttl, auth.device_remaining(dev)) if dev else 0
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
    # 존재만이 아니라 **만료**까지 본다 — 임시 기기가 만료된 뒤에도 포트 쿠키가
    # 12시간 더 살던 틈(세션 쪽은 auth.session_device가 같은 판정으로 막는다).
    if did != "-" and not auth.find_alive_device(did):
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

    `X-Forwarded-For`는 **맨 오른쪽**(우리 바로 앞 프록시, 예: ngrok이 붙인 값)을
    쓴다. 맨 왼쪽은 클라이언트가 마음대로 넣는 값이라, 예전처럼 그걸 키로 쓰면
    요청마다 값을 바꿔 IP별 잠금을 통째로 우회할 수 있었다(main.py와 같은 규칙).
    """
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[-1].strip()
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


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _page(html: str, status: int) -> HTMLResponse:
    return HTMLResponse(html, status_code=status, headers=dict(_PAGE_HEADERS))


# PIN 칸 입력 상태 — 로그인 화면(frontend/js/gate.js)과 같은 판정이다. 한글 조합이
# 시작됐는지와 Caps Lock만 본다(PIN은 대소문자를 안 가리지만, 한글은 그대로
# 들어가 틀린 코드가 된다). 둘 다 키를 한 번 누른 뒤에야 알 수 있다.
_PIN_STATE_JS_TEMPLATE = """<script>
(function(){
  var i=document.querySelector('input[name=pin]'), st=document.getElementById('st');
  if(!i||!st) return;
  var ko=false, HAN=/[\u3131-\u318e\uac00-\ud7a3]/;
  function show(){
    st.textContent = ko ? __KO_MSG__ : '';
    st.hidden = !ko;
  }
  i.addEventListener('keydown',function(e){
    if(e.key==='HangulMode'){ ko=true; }
    else if(!e.isComposing && e.key && e.key.length===1 && /[A-Za-z0-9]/.test(e.key)){ ko=false; }
    show();
  });
  i.addEventListener('compositionupdate',function(e){ if(HAN.test(e.data||'')){ ko=true; show(); } });
  i.addEventListener('input',function(){ if(HAN.test(i.value)){ ko=true; } show(); });
})();
</script>"""


def _pin_state_js() -> str:
    # 문구는 호출 시점의 언어로 — 모듈 상수에 굳히지 않는다.
    msg = tr("한글 입력 중입니다. 영문으로 바꿔 입력하세요.")
    return _PIN_STATE_JS_TEMPLATE.replace("__KO_MSG__", json.dumps(msg).replace("</", "<\\/"))


def _pin_page(label: str, error: str = "", status: int = 200, next_path: str = "/") -> HTMLResponse:
    err = f'<p class="err">{_esc(error)}</p>' if error else ""
    lang = i18n.current()
    title = _esc(tr("FarShell — 접근 코드"))
    heading = _esc(tr("접근 코드를 입력하세요"))
    submit = _esc(tr("열기"))
    state_js = _pin_state_js()
    safe_label = _esc(label)
    safe_nxt = _esc(safe_next(next_path))
    html = f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
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
.st{{color:#f0a860;font-size:12.5px;margin:8px 0 0}}
.st[hidden]{{display:none}}
</style></head><body>
<form method="post" action="/__fsh/pin">
<h1>{heading}</h1>
<p class="sub">{safe_label}</p>
{err}
<input type="hidden" name="next" value="{safe_nxt}">
<input name="pin" autocomplete="off" autocapitalize="characters" autofocus maxlength="20"
       placeholder="XXXX-XXXX" lang="en" aria-describedby="st">
<p class="st" id="st" role="status" aria-live="polite" hidden></p>
<button type="submit">{submit}</button>
</form>
{state_js}
</body></html>"""
    return _page(html, status)


def _session_only_page(label: str) -> HTMLResponse:
    """session 모드에 쿠키 없이 온 방문자용 안내.

    **메인 FarShell 주소를 싣지 않는다.** 예전엔 여기서 메인 주소의 `/p/authorize`로
    리다이렉트해서, 앱 링크만 받은 사람도 터미널 로그인 주소를 알게 됐다. 이제
    처음 여는 경로는 FarShell 쪽(포트 패널·`fsh tunnel expose` 출력)에서 시작한다 —
    한 번 통과한 기기는 쿠키가 있어 이 화면을 보지 않는다.
    """
    lang = i18n.current()
    title = _esc(tr("FarShell — 로그인한 기기 전용"))
    heading = _esc(tr("로그인한 기기에서만 열 수 있습니다"))
    body = _esc(tr("이 앱은 FarShell에 로그인한 기기 전용입니다. FarShell의 포트 패널에서 열어 주세요."))
    html = f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#111;color:#eee;
  display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
div{{background:#1c1c1c;padding:24px;border-radius:12px;width:min(360px,90vw)}}
h1{{font-size:15px;margin:0 0 8px}}
p{{font-size:13px;color:#aaa;margin:0;line-height:1.6}}
</style></head><body><div>
<h1>{heading}</h1>
<p>{_esc(label)}</p>
<p style="margin-top:10px">{body}</p>
</div></body></html>"""
    return _page(html, 403)


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

    nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if mode == "pin":
        return _pin_page(share.get("label", ""), next_path=nxt)

    # session — 쿠키가 없으면 안내만 한다. 로그인 확인은 FarShell 쪽에서 시작하는
    # `/p/authorize`가 맡는다(_session_only_page docstring).
    return _session_only_page(share.get("label", ""))


# --- 라우트 1: 티켓 콜백 (§3 6단계) ------------------------------------------------


def _cb_handler(port: int):
    async def handler(request):
        share = await _live_share(port)
        if share is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if share.get("mode") != "session":
            return JSONResponse({"error": "not_session_mode"}, status_code=400)

        ticket = request.query_params.get("t", "")
        rec = await asyncio.to_thread(auth.consume_ticket, ticket, auth.PURPOSE_PORT_SHARE)  # 1회용 — 즉시 소멸
        if rec is None:
            return JSONResponse({"error": "bad_ticket"}, status_code=401)
        # 티켓은 이 share에 묶여 발급된다. 다른 share의 티켓으로는 못 들어온다.
        if rec.get("share") != share["shareId"]:
            logger.warning(f"[port-proxy] 다른 share의 티켓 사용 시도 (port={port})")
            return JSONResponse({"error": "bad_ticket"}, status_code=401)
        # 티켓을 받은 뒤 60초 사이에 기기가 만료·폐기됐을 수 있다 — 수명 0인 쿠키를
        # 굽느니 여기서 거절한다.
        did = rec.get("device") or "-"
        if did != "-" and not auth.find_alive_device(did):
            return JSONResponse({"error": "device_expired"}, status_code=401)

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

        label = share.get("label", "")
        key = _client_key(request)
        remaining = _pin_lockout.lock_remaining(key)
        if remaining > 0:
            # scrypt를 돌리기 **전에** 단락시킨다 — 잠금의 목적이 CPU 보호다.
            return _pin_page(label, tr("시도가 많습니다. {n}초 후 다시 시도하세요.", n=remaining), status=429)

        form = await _read_small_form(request)
        if form is None:
            return JSONResponse({"error": "too_large"}, status_code=413)
        nxt = form.get("next") or request.query_params.get("next")
        pin = form.get("pin", "")

        # 슬롯이 다 찼으면 기다리지 않고 돌려보낸다 — 대기열이 쌓이는 것 자체가 부하다.
        if _pin_slots.locked():
            return _pin_page(label, tr("잠시 후 다시 시도하세요."), status=429, next_path=nxt or "/")
        async with _pin_slots:
            ok = await asyncio.to_thread(store.verify_pin, share, pin)
        if not ok:
            _pin_lockout.note_failure(key)
            total = await asyncio.to_thread(store.note_pin_failure, port)
            locked = _pin_lockout.lock_remaining(key) > 0
            logger.warning(
                f"[port-proxy] PIN 실패 port={port} ip={key} 누적={total}"
                + (f" — 이 IP {PIN_LOCK_SEC}초 차단" if locked else "")
            )
            return _pin_page(label, tr("코드가 올바르지 않습니다."), status=401, next_path=nxt or "/")

        _pin_lockout.reset_failures(key)
        resp = RedirectResponse(safe_next(nxt), status_code=303)
        _set_cookie(resp, share, auth.is_https(request))
        await asyncio.to_thread(store.note_view, port)
        return resp

    return handler


async def _read_small_form(request) -> dict | None:
    """urlencoded 폼을 PIN_BODY_MAX까지만 읽어 {이름: 첫 값}으로. 넘으면 None.

    `request.form()`은 본문 크기 제한 없이 다 읽는다 — PIN 하나 받는 공개 엔드포인트에서
    그럴 이유가 없다.
    """
    from urllib.parse import parse_qs

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > PIN_BODY_MAX:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > PIN_BODY_MAX:
            return None
    parsed = parse_qs(buf.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items() if v}


# --- 라우트 3: 프록시 ------------------------------------------------------------


def _upstream_headers(request, port: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP or lk == "host" or _is_client_proxy_header(lk):
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
    # 각각 **한 번만** 넣는다 — 클라이언트가 보낸 같은 이름은 위에서 지웠다.
    out.append(("x-forwarded-for", _client_key(request)))
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
                 "reason": tr("localhost:{port} 에 응답하는 서비스가 없습니다.", port=port)},
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
        secure = (websocket.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
                  == "https") or websocket.url.scheme == "wss"
        our_cookie = websocket.cookies.get(cookie_name(secure), "")
        if share.get("mode") != "public":
            if share.get("mode") not in ("session", "pin"):
                await websocket.close(code=4403)
                return
            if not verify_cookie(our_cookie, share):
                await websocket.close(code=4001)
                return
        admitted = (share.get("mode"), share.get("shareId"))

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

        async def _recheck():
            # 통과시킨 조건이 아직 그대로인지 주기적으로 본다. 끝나면(=더는 자격 없음)
            # 이 태스크가 완료되고 아래 wait가 연결을 닫는다.
            while True:
                await asyncio.sleep(WS_RECHECK_SEC)
                if not await _ws_still_admitted(port, admitted, our_cookie):
                    logger.info(f"[port-proxy] WS 자격 만료 — 연결 종료 (port={port})")
                    try:
                        await websocket.close(code=4001)
                    except Exception:
                        pass
                    return

        down = asyncio.create_task(_down())
        up = asyncio.create_task(_up())
        watch = asyncio.create_task(_recheck())
        try:
            _done, pending = await asyncio.wait({down, up, watch}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
        except Exception:  # noqa: BLE001
            pass
        finally:
            down.cancel()
            up.cancel()
            watch.cancel()
            try:
                await upstream.close()
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass

    return handler


async def _ws_still_admitted(port: int, admitted: tuple, cookie: str) -> bool:
    """연결을 받아 줄 때의 (mode, shareId)가 그대로이고, 인증 모드면 쿠키도 아직 유효한가.

    shareId는 PIN 재발급·모드 변경 때 바뀌므로(port_share_store) 그 둘을 이 비교 하나로
    잡는다. 노출이 꺼지거나 만료되면 share가 없어져 False. 캐시(2초)를 거친다.
    """
    share = await _live_share(port)
    if share is None:
        return False
    if (share.get("mode"), share.get("shareId")) != admitted:
        return False
    if share.get("mode") == "public":
        return True
    return verify_cookie(cookie, share)


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
