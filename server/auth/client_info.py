"""기기 등록 기록용 클라이언트 정보 — UA 요약, 접속 IP, 접속 경로.

**표시용이다. 인증·차단 판단에 쓰지 않는다.** UA는 클라이언트가 마음대로 보내는
값이고, 터널 경유 IP는 `CF-Connecting-IP` 헤더를 믿어서 얻는다. 그 헤더는
같은 맥 안의 프로세스가 127.0.0.1로 접속하면서 꾸며 보낼 수 있다 — 그래서
IP 필터(`_effective_client_ip`, `VT_TRUST_PROXY`)와는 일부러 갈라 둔다.
기록이 틀릴 수는 있어도, 이 값으로 열리는 문은 없다.

UA 파싱은 표준 라이브러리 정규식만 쓴다. 목록에서 "어느 기기인지" 알아볼 만큼만
뽑으면 되고(OS + 브라우저 메이저 버전), 그 이상은 UA 원문으로 남긴다.
"""

from __future__ import annotations

import ipaddress
import re

UA_MAX_LEN = 300

# 순서가 판정이다 — Edge/Opera/삼성 UA에는 "Chrome/"도, Chrome UA에는 "Safari/"도
# 들어 있어서 더 구체적인 것을 먼저 본다.
_BROWSERS = (
    ("Edge", re.compile(r"\bEdgA?/(\d+)")),
    ("Edge", re.compile(r"\bEdgiOS/(\d+)")),
    ("Samsung Internet", re.compile(r"\bSamsungBrowser/(\d+)")),
    ("Opera", re.compile(r"\bOPR/(\d+)")),
    ("Firefox", re.compile(r"\b(?:Firefox|FxiOS)/(\d+)")),
    ("Chrome", re.compile(r"\b(?:CriOS|Chrome)/(\d+)")),
    ("Safari", re.compile(r"\bVersion/(\d+)[.\d]*.*\bSafari/")),
)

_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")


def clean_ua(ua: str) -> str:
    """제어문자를 지우고 길이를 자른 UA 원문. 비어 있으면 빈 문자열."""
    s = re.sub(r"[\x00-\x1f\x7f]", "", ua or "")
    return s[:UA_MAX_LEN]


def _os(ua: str) -> str:
    m = re.search(r"\b(?:iPhone|CPU) OS (\d+)[_.](\d+)", ua)
    if m:
        name = "iPadOS" if "iPad" in ua else "iOS"
        return f"{name} {m.group(1)}.{m.group(2)}"
    if "iPad" in ua:
        return "iPadOS"
    m = re.search(r"\bAndroid (\d+(?:\.\d+)?)", ua)
    if m:
        return f"Android {m.group(1)}"
    if "Android" in ua:
        return "Android"
    # macOS·Windows는 브라우저들이 버전을 고정해 보내서(10_15_7, NT 10.0) 숫자가
    # 실제 버전을 말해 주지 않는다 — 틀린 숫자를 보여 주느니 이름만 쓴다.
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "macOS"
    if "Windows" in ua:
        return "Windows"
    if "CrOS" in ua:
        return "ChromeOS"
    if "Linux" in ua:
        return "Linux"
    return ""


def _browser(ua: str) -> str:
    for name, rx in _BROWSERS:
        m = rx.search(ua)
        if m:
            return f"{name} {m.group(1)}"
    return ""


def parse_user_agent(ua: str) -> dict:
    """UA → `{"os": "Android 15", "browser": "Chrome 140"}`. 모르는 칸은 빈 문자열."""
    s = clean_ua(ua)
    return {"os": _os(s), "browser": _browser(s)}


def _valid_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return ""


def classify_client(raw_host: str | None, headers) -> tuple[str, str]:
    """(기록할 IP, 접속 경로). 경로는 tunnel / tailscale / lan / local / direct.

    루프백에서 왔는데 `CF-Connecting-IP`가 붙어 있으면 cloudflared가 전달한 터널
    접속으로 보고 그 헤더 값을 기록한다. 헤더 값이 IP 형식이 아니면 버린다.
    `headers`는 get을 지원하는 dict-like(Request.headers 등).
    """
    raw = _valid_ip(raw_host or "")
    if not raw:
        return "", ""
    addr = ipaddress.ip_address(raw)
    if addr.is_loopback:
        cf = _valid_ip(headers.get("cf-connecting-ip") or "") if headers is not None else ""
        if cf:
            return cf, "tunnel"
        return raw, "local"
    if addr.version == 4 and addr in _TAILSCALE_NET:
        return raw, "tailscale"
    if addr.is_private or addr.is_link_local:
        return raw, "lan"
    return raw, "direct"


def request_meta(request) -> dict:
    """요청 → 기기 레코드에 저장할 메타(ua, client, ip, via)."""
    ua = request.headers.get("user-agent", "") if request is not None else ""
    host = request.client.host if (request is not None and request.client) else None
    ip, via = classify_client(host, request.headers if request is not None else None)
    return {"ua": clean_ua(ua), "client": parse_user_agent(ua), "ip": ip, "via": via}
