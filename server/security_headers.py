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

이미 같은 이름의 헤더를 낸 응답(파일 미리보기의 `sandbox` CSP 등)은 덮어쓰지 않는다. 전체 CSP(`script-src`)는
`index.html`의 인라인 스크립트 때문에 여기서 하지 않는다(docs/ref/auth.md 「Security headers」).
"""
from __future__ import annotations

_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"referrer-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), geolocation=(), payment=(), usb=(), serial=(), bluetooth=(), hid=()"),
)


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
