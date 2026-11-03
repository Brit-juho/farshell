"""Host 헤더 허용 목록 — DNS rebinding 방어(HTTP + WebSocket).

공격: 사용자가 `rebind.evil:7777` 페이지를 연다. 페이지가 뜬 뒤 그 이름의 DNS가 127.0.0.1(또는 이 맥의
LAN·tailnet 주소)로 바뀌면, 브라우저에게 그 페이지와 FarShell은 **같은 출처**다. `Origin`과 `Host`가 둘 다
`rebind.evil:7777`이라 OriginGuard(같은 출처인지 비교)는 통과하고, 인증이 없는 구성에서는 그대로 셸이다.
인증이 있어도 피해자 브라우저로 로그인 추측을 돌릴 수 있다. MCP Inspector(CVE-2025-49596)·Vite
(CVE-2025-24010)가 같은 계열이었고, Jupyter `check_host`·Vite `server.allowedHosts`가 같은 처방이다.

그래서 "공격자가 DNS를 쥘 수 없는 이름"만 받는다:
- IP 리터럴(IPv4, `[IPv6]`) — 브라우저가 IP로 직접 열었으면 DNS가 끼지 않는다. LAN·Tailscale IP 포함.
- `localhost`, `*.localhost` — 브라우저가 이름 해석 없이 loopback으로 고정한다.
- 점 없는 한 단어 이름(이 맥의 호스트명, tailnet MagicDNS 짧은 이름)과 `*.local`(mDNS) — 공개 DNS로
  해석되지 않는다. 이 이름을 조작할 수 있는 사람은 이미 같은 망에 있어 서버에 직접 붙을 수 있다.
- `*.ts.net`(Tailscale), `*.trycloudflare.com`, ngrok 도메인 — DNS를 그 회사가 쥔다. 공격자가 만든 터널
  이름은 공격자 쪽 엣지로 가지, 이 맥의 loopback으로 가지 않는다. 퀵 터널 주소가 재시작마다 바뀌어도
  따라갈 필요가 없다.
- 설정한 이름: `VT_TUNNEL_HOSTNAME`(명명 터널), `VT_NGROK_DOMAIN`, `VT_ALLOWED_ORIGINS`의 호스트,
  `VT_ALLOWED_HOSTS`(쉼표 목록, `*.example.com` 접두 와일드카드 허용).

Host 헤더가 없는 요청(HTTP/1.0 비브라우저 클라이언트)은 통과한다 — 브라우저는 늘 보낸다.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

# 공개 DNS를 공격자가 쥘 수 없는 접미사(회사가 운영하는 영역).
_PROVIDER_SUFFIXES = (
    ".ts.net",
    ".trycloudflare.com",
    ".ngrok-free.app", ".ngrok-free.dev", ".ngrok.app", ".ngrok.dev", ".ngrok.io",
)
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
_LOG_EVERY = 3600.0
_logged: dict[str, float] = {}


def _strip_port(host: str) -> str | None:
    """`name:port` / `[v6]:port` → 이름(소문자, 끝의 점 제거). 형식이 이상하면 None."""
    host = host.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end < 0:
            return None
        rest = host[end + 1:]
        if rest and not re.fullmatch(r":\d{1,5}", rest):
            return None
        return host[: end + 1]
    if host.count(":") == 1:
        host, port = host.split(":")
        if not port.isdigit() or len(port) > 5:
            return None
    elif ":" in host:
        return None
    return host.rstrip(".")


def _configured() -> tuple[set[str], tuple[str, ...]]:
    """(정확히 일치할 이름, 접미사). 환경값은 호출 시점에 읽는다(테스트·재설정이 바로 반영)."""
    exact: set[str] = set()
    suffixes: list[str] = []
    for key in ("VT_TUNNEL_HOSTNAME", "VT_NGROK_DOMAIN"):
        v = (os.environ.get(key) or "").strip().lower().rstrip(".")
        if v:
            exact.add(v)
    for raw in (os.environ.get("VT_ALLOWED_HOSTS") or "").split(","):
        v = raw.strip().lower().rstrip(".")
        if not v:
            continue
        if v.startswith("*."):
            suffixes.append(v[1:])
        else:
            exact.add(_strip_port(v) or v)
    for raw in (os.environ.get("VT_ALLOWED_ORIGINS") or "").split(","):
        v = raw.strip().lower()
        if "://" in v:
            name = _strip_port(v.split("://", 1)[1].split("/", 1)[0])
            if name:
                exact.add(name)
    return exact, tuple(suffixes)


def host_allowed(host_header: str | None) -> bool:
    if host_header is None or host_header == "":
        return True  # 비브라우저(HTTP/1.0) — 브라우저는 Host를 늘 보낸다
    name = _strip_port(host_header)
    if not name:
        return False
    literal = name[1:-1] if name.startswith("[") else name
    try:
        ipaddress.ip_address(literal)
        return True
    except ValueError:
        if name.startswith("["):
            return False
    if not _NAME_RE.match(name):
        return False
    if name == "localhost" or name.endswith(".localhost"):
        return True
    if "." not in name or name.endswith(".local"):
        return True
    if name.endswith(_PROVIDER_SUFFIXES):
        return True
    exact, suffixes = _configured()
    return name in exact or (bool(suffixes) and name.endswith(suffixes))


def _note_refused(name: str) -> None:
    now = time.monotonic()
    if now - _logged.get(name, -_LOG_EVERY) >= _LOG_EVERY:
        if len(_logged) > 256:
            _logged.clear()
        _logged[name] = now
        logger.warning("[보안] 허용하지 않은 Host 헤더를 거절했다: %r — 정상 접속이면 VT_ALLOWED_HOSTS에 추가", name[:200])


class HostGuardMiddleware:
    """`host_allowed`가 거짓이면 403 `host_not_allowed`(HTTP·WS 둘 다, 인증보다 먼저)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        host = None
        for k, v in scope.get("headers", []):
            if k == b"host":
                host = v.decode("latin-1")
                break
        if not host_allowed(host):
            _note_refused(host or "")
            from starlette.responses import JSONResponse
            await JSONResponse({"error": "forbidden", "reason": "host_not_allowed"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)
