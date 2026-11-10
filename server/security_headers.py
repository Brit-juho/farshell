"""메인 앱 응답에 붙이는 브라우저 보안 헤더(순수 ASGI, HTTP 응답만).

- `X-Frame-Options: DENY` + `Content-Security-Policy: frame-ancestors 'none'` — FarShell은 다른 곳에 끼워 넣는
  앱이 아니다. 끼워 넣을 수 있으면 로그인된 브라우저에서 클릭재킹이 되고(터미널이 곧 셸이다), 인증이 없는
  구성에서는 쿠키 없이도 된다. 앱이 *다른* 주소를 iframe 으로 띄우는 포트 미리보기는 영향 없다(이 헤더는 우리 응답에만).
- `X-Content-Type-Options: nosniff` — 내려주는 파일을 브라우저가 내용으로 다시 추측하지 않는다.
- `Referrer-Policy: same-origin` — 다른 출처로는 리퍼러를 보내지 않는다. **`no-referrer`를 쓰지 않는 이유**:
  Fetch 규격상 `no-referrer` 문서에서 폼(탐색 요청)을 POST 하면 `Origin: null`이 나가고, `OriginGuardMiddleware`가
  "null"을 거부하므로 공유 PIN 폼(`/s/{token}/pin`)이 403이 된다. `same-origin`은 같은 출처 폼의 Origin을 유지한다.
- `Permissions-Policy` — 쓰지 않는 강력한 기능(카메라·위치·결제·USB·시리얼·블루투스·HID)을 끈다. 마이크(음성)와
  클립보드는 기본값 그대로 둔다.

이미 같은 이름의 헤더를 낸 응답(파일 미리보기의 `sandbox` CSP 등)은 덮어쓰지 않는다.

앱 문서(`GET /`)는 라우트가 `APP_CSP`(전체 CSP, `script-src 'self'`)를 직접 붙인다 — 위 규칙 덕분에 이 미들웨어의
`frame-ancestors`만 있는 기본값이 그것을 덮지 않는다. 정책 문자열은 **이 파일 한 곳**에만 둔다. 공유 PIN 페이지·
포트 프록시 응답·원격 relay 응답(`relay_policy.RELAY_CSP`)은 각자 규칙을 그대로 쓴다.
"""
from __future__ import annotations

_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), hid=()"),
)


# 앱 문서(index.html)의 CSP. 인라인/외부 임의 스크립트 실행을 막는 것이 목적이다.
# - script-src 'self': 인라인 <script>·on*= 핸들러·eval·외부 출처 스크립트·blob:/data: 스크립트 전부 차단.
#   (index.html은 인라인 스크립트가 없다 — 테마 부팅은 js/theme-boot.js, AudioWorklet은 정적 파일이다.
#    `unsafe-inline`·`unsafe-eval`·`wasm-unsafe-eval`·nonce는 쓰지 않는다: wasm 을 쓰지 않고, HTML 이 정적 캐시된다.)
# - style-src 'unsafe-inline': 로그인 게이트 <style>(첫 페인트용)과 style="" 속성·솔리드 템플릿이 쓴다.
#   스타일은 스크립트 실행 경로가 아니므로 남긴다.
# - connect-src 'self': fetch·WebSocket·EventSource 는 전부 같은 출처(호스트 전환도 /api/hosts/{id}/relay 경유).
#   터널(Cloudflare/ngrok) 뒤에서는 페이지 출처 = 터널 도메인이라 'self'가 그대로 맞는다.
# - frame-src http: https: — 포트 미리보기(ports.js)가 터널 URL을 iframe 으로 연다(sandbox 속성이 붙는다).
# - object-src 'none' / base-uri 'self' / form-action 'self' / frame-ancestors 'none'(기존 클릭재킹 방어 유지).
APP_CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "media-src 'self' blob: data:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "frame-src 'self' http: https:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))


class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                present = {k.lower() for k, _ in message.get("headers", [])}
                message = {**message, "headers": [*message.get("headers", []),
                                                  *((k, v) for k, v in _HEADERS if k not in present)]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
