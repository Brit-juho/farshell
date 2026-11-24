"""응답 압축(gzip) — 텍스트 응답만 줄이고, 비밀이 든 응답·이진·이어받기는 건드리지 않는다.

서버는 예전에 아무것도 압축하지 않았다(실측: `app.css` 485KB가 그대로, 첫 화면 핵심 파일 약 1.2MB). 집 안 네트워크나 터널
어디서든 같은 글자를 4~7배 적게 보낸다(`app.css` 485→66KB, 카탈로그 목록 48→8KB).

Starlette `GZipMiddleware`를 쓰고, 그것이 이미 건너뛰는 것(이미 압축된 응답, `206` 이어받기, 이미지·오디오·비디오·글꼴,
`text/event-stream`)에 더해 다음을 건너뛴다.
- **자격·페어링 응답**(`SKIP_PREFIXES`): 압축된 길이로 본문의 비밀을 추측하는 공격(BREACH)을 아예 열지 않는다. 이 응답들은
  작아서 압축 이득도 없다.
- **이진 다운로드**(`application/octet-stream`·`pdf`·`tar` 등): 이미 압축됐거나 압축 이득이 없는 데다 파일 크기가 커서 CPU만 쓴다.

1KB 미만은 압축하지 않는다(머리글이 더 크다). 128KB 이상은 Starlette가 작업 스레드에서 압축해 이벤트 루프를 막지 않는다.
압축 수준은 5 — 9보다 몇 배 빠르고 크기는 몇 % 차이다."""
from __future__ import annotations

from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

MIN_SIZE = 1024
LEVEL = 5

# 비밀(토큰·티켓·OTP·페어링 코드)이 응답 본문에 실릴 수 있는 경로 — 압축하지 않는다.
SKIP_PREFIXES = ("/api/auth", "/api/pairs", "/api/peer/pair", "/p/")

EXCLUDED_CONTENT_TYPES = DEFAULT_EXCLUDED_CONTENT_TYPES + (
    "application/octet-stream",
    "application/pdf",
    "application/x-tar",
    "application/x-7z-compressed",
    "application/x-bzip2",
    "application/x-xz",
    "application/zstd",
)


class CompressMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._gzip = GZipMiddleware(app, minimum_size=MIN_SIZE, compresslevel=LEVEL,
                                    exclude_content_types=EXCLUDED_CONTENT_TYPES)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        await self._gzip(scope, receive, send)
