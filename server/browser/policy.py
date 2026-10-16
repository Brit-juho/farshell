"""브라우저 broker 의 순수 정책 — URL 검사(R9.3)·값 가림(R9.5)·한도. 파일·네트워크·시계를 직접 읽지 않는다(DNS 확인만 주입 가능한 함수 하나).

## URL 정책 (R9.3, N-27)
기본(allowlist 가 `[]`)은 **`about:blank` 와 FarShell 이 노출한 localhost 포트뿐**이다. 그 밖은 grant 의 `url_allowlist` 에 적힌 것만 열린다.

  - 스킴: `http`·`https` 만. `file:`·`chrome:`·`chrome-extension:`·`devtools:`·`view-source:`·최상위 `data:`·`blob:`·`javascript:`·그 밖의 `about:`·`ws:`·`ftp:` 는 거부.
    서브프레임(`top_level=False`)에서는 `data:`·`blob:`·`about:blank` 가 열린다(페이지가 스스로 만드는 문서). `javascript:` 는 어디서나 거부(스크립트는 `eval` = `browser.js`).
  - 브라우저가 읽는 방식 그대로 정규화한 뒤 검사한다(WHATWG): 앞뒤 공백·제어 문자와 탭·개행 제거, `\\` → `/`, 호스트 퍼센트 디코드, NFKC·IDNA(punycode),
    끝의 점 제거, 십진·8진·16진 IPv4(`2130706433`, `0177.0.0.1`, `0x7f.1`), IPv6(`[::1]`, `::ffff:127.0.0.1`, 6to4·NAT64 안의 IPv4). 해석 못 하면 거부.
    `user:pass@` 는 거부(허용 목록 우회·자격 노출 통로).
  - 루프백(`localhost`·`127.0.0.1`·`::1`)은 **노출 포트 집합 안의 포트**일 때만 기본 허용. `0.0.0.0`·`*.localhost`·다른 루프백 주소는 허용 목록이 필요하다.
  - RFC1918·링크 로컬·메타데이터 IP(`169.254.169.254` …)·CGNAT·예약 대역은 허용 목록에 **그 IP·CIDR·host:port 로 명시**돼야 열린다. `*.도메인` 같은 이름 와일드카드는
    IP 주소·특수 이름을 덮지 못한다. 단독 `*` 는 무시한다.
  - 허용 목록에 이름으로 올라간 호스트는 DNS 로 풀어 보고, 결과가 공인 주소가 아니면 그 주소도 IP·CIDR 로 명시돼 있어야 한다(rebinding 한 겹 — 완전한 방어는 아니다, 문서에 적음).
  - redirect 는 hop 마다·서브프레임과 행동(클릭·입력·eval) 뒤 로드까지 같은 함수로 다시 본다(어댑터가 네트워크 계층에서 `guard`, broker 가 행동 뒤 `frame_urls` 로).
  - 하위 리소스(이미지·스크립트·XHR)는 `check_subresource` — 공인 호스트는 열어 두되 루프백·사설·링크 로컬·메타데이터는 위와 같은 규칙으로 막는다.

## 값 가림 (R9.5)
쿠키·storage 는 이름·도메인·플래그만. 값은 호출자가 `values=true` 를 청하고 grant 조건 `values` 가 있을 때만(엔진이 판정). network 는 `Cookie`·`Set-Cookie`·
`Authorization`·`Proxy-Authorization` 헤더와 64KB 초과 body 를 **언제나** 가린다.
"""

from __future__ import annotations

import ipaddress
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional
from urllib.parse import unquote, urlsplit

BLANK = "about:blank"
DEFAULT_PORTS = {"http": 80, "https": 443}
MAX_URL = 4096
MAX_BODY = 64 * 1024
MAX_EVAL_RESULT = 64 * 1024
MAX_TYPE_TEXT = 10_000
MAX_SCRIPT = 20_000
SENSITIVE_HEADERS = frozenset({"cookie", "set-cookie", "authorization", "proxy-authorization"})
REDACTED = "[redacted]"

# 메타데이터 서비스로 알려진 이름·주소 — 허용 목록에 **정확히** 적지 않으면 열리지 않는다(와일드카드 불가).
METADATA_NAMES = frozenset({"metadata", "metadata.google.internal", "metadata.goog", "instance-data", "instance-data.ec2.internal"})
METADATA_IPS = frozenset(ipaddress.ip_address(x) for x in ("169.254.169.254", "169.254.170.2", "100.100.100.200", "192.0.0.192", "fd00:ec2::254"))
LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
_STRIP_CTRL = re.compile(r"^[\x00-\x20]+|[\x00-\x20]+$")
_TAB_NL = re.compile(r"[\t\n\r]")
_BAD_HOST_CHARS = re.compile(r"[\x00-\x20#%/:<>?@\[\\\]^|\x7f]")
_LABEL_RE = re.compile(r"^[a-z0-9_]([a-z0-9_\-]*[a-z0-9_])?$")
_DEC = re.compile(r"^[0-9]+$")
_HEX = re.compile(r"^[0-9a-fA-F]+$")
_OCT = re.compile(r"^[0-7]+$")

# 스킴별 거부 사유 이름 — 사유는 감사·거부 응답에 그대로 나간다.
_NAMED_SCHEMES = ("file", "chrome", "chrome-extension", "devtools", "view-source", "data", "blob", "javascript", "about", "ws", "wss", "ftp",
                  "chrome-untrusted", "chrome-search", "edge", "brave", "opera", "vivaldi", "moz-extension", "resource", "filesystem")


class UrlError(Exception):
    """URL 을 읽을 수 없거나 정책이 거부했다 — `reason` 은 짧은 영문 코드."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Verdict:
    allow: bool                       # 허용 목록 없이도 열리는가
    kind: str                         # blank | local_exposed | needs_allowlist | denied
    reason: str = ""
    url: str = ""                     # 정규화한 URL(허용·허용 목록 필요일 때)
    scheme: str = ""
    host: str = ""
    port: int = 0
    ip: Optional[str] = None
    flags: tuple = ()                 # loopback|private|link_local|metadata|reserved|name_special|ip|dns


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    verdict: Optional[Verdict] = None


# --- 정규화 -----------------------------------------------------------------------------------


def _ipv4_number(p: str) -> int:
    if p.lower().startswith("0x"):
        digits = p[2:] or "0"
        if not _HEX.match(digits):
            raise UrlError("bad_ip")
        return int(digits, 16)
    if len(p) > 1 and p.startswith("0"):
        if not _OCT.match(p[1:]):
            raise UrlError("bad_ip")
        return int(p[1:], 8)
    if not _DEC.match(p):
        raise UrlError("bad_ip")
    return int(p, 10)


def _ends_in_number(host: str) -> bool:
    parts = host.split(".")
    if parts and parts[-1] == "" and len(parts) > 1:
        parts.pop()
    last = parts[-1] if parts else ""
    if not last:
        return False
    if _DEC.match(last):
        return True
    return bool(re.match(r"^0[xX][0-9a-fA-F]*$", last))


def parse_ipv4(host: str) -> ipaddress.IPv4Address:
    """브라우저(WHATWG)가 읽는 IPv4 — 십진·8진·16진·축약형. 읽지 못하면 UrlError."""
    parts = host.split(".")
    if parts and parts[-1] == "":
        parts.pop()
    if not parts or len(parts) > 4 or any(p == "" for p in parts):
        raise UrlError("bad_ip")
    nums = [_ipv4_number(p) for p in parts]
    if any(n > 255 for n in nums[:-1]) or nums[-1] >= 256 ** (5 - len(nums)):
        raise UrlError("bad_ip")
    total = nums[-1]
    for i, n in enumerate(nums[:-1]):
        total += n * 256 ** (3 - i)
    return ipaddress.IPv4Address(total)


def _unwrap_v6(ip: ipaddress.IPv6Address):
    """IPv6 안에 IPv4 를 담은 형태(mapped·6to4·NAT64·호환)는 그 IPv4 로 본다 — 사설 주소를 IPv6 로 감싸 우회하는 길을 막는다."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    n = int(ip)
    if (n >> 32) == 0x0064FF9B000000000000000000000000 >> 32:        # 64:ff9b::/96
        return ipaddress.IPv4Address(n & 0xFFFFFFFF)
    if (n >> 32) == 0 and n > 1:                                      # ::a.b.c.d (호환 주소, ::1 제외)
        return ipaddress.IPv4Address(n & 0xFFFFFFFF)
    return ip


def parse_ip_host(host: str):
    """호스트 문자열(대괄호 없는 IPv6 포함)이 IP 면 정규화된 주소 객체, 아니면 None. 해석 못 하는 숫자 모양은 UrlError."""
    if ":" in host:
        if "%" in host:
            raise UrlError("bad_ip")
        try:
            return _unwrap_v6(ipaddress.IPv6Address(host))
        except ValueError as e:
            raise UrlError("bad_ip") from e
    if _ends_in_number(host):
        return parse_ipv4(host)
    return None


def _ip_flags(ip) -> tuple:
    out = []
    if ip in METADATA_IPS:
        out.append("metadata")
    if ip.is_loopback:
        out.append("loopback")
    if ip.is_link_local:
        out.append("link_local")
    if ip.is_private and not ip.is_loopback and not ip.is_link_local:
        out.append("private")
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved or (not ip.is_global and not out):
        out.append("reserved")
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10"):
        out.append("private")
    return tuple(dict.fromkeys(out))


def _norm_host(raw_host: str) -> str:
    h = unquote(raw_host)
    h = unicodedata.normalize("NFKC", h).lower()
    if h.startswith("[") or h.endswith("]"):
        raise UrlError("bad_host")
    h = h.rstrip(".")
    if not h:
        raise UrlError("empty_host")
    if any(ord(c) > 127 for c in h):
        try:
            h = h.encode("idna").decode("ascii")
        except UnicodeError as e:
            raise UrlError("bad_idn") from e
        h = h.lower().rstrip(".")
    if len(h) > 253 or _BAD_HOST_CHARS.search(h):
        raise UrlError("bad_host")
    return h


@dataclass(frozen=True)
class Parsed:
    scheme: str
    host: str
    port: int
    ip: Optional[object]
    path: str
    url: str


def _sanitize(raw) -> str:
    if not isinstance(raw, str):
        raise UrlError("bad_url")
    s = _TAB_NL.sub("", _STRIP_CTRL.sub("", raw))
    if not s or len(s) > MAX_URL:
        raise UrlError("bad_url" if s else "empty_url")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in s):
        raise UrlError("bad_url")
    return s


def parse_url(raw) -> Parsed:
    """http(s) URL 을 정규화한다. http(s) 가 아니면 UrlError(스킴 사유). 자격 정보·잘못된 포트·호스트는 거부."""
    s = _sanitize(raw)
    m = _SCHEME_RE.match(s)
    if not m:
        raise UrlError("no_scheme")
    scheme = m.group(1).lower()
    if scheme not in DEFAULT_PORTS:
        raise UrlError(f"scheme_{scheme}" if scheme in _NAMED_SCHEMES else "scheme_not_allowed")
    rest = s[m.end():]
    rest = rest.replace("\\", "/")
    if not rest.startswith("//"):
        raise UrlError("bad_url")
    try:
        u = urlsplit(scheme + ":" + rest)
        netloc = u.netloc
    except ValueError as e:
        raise UrlError("bad_url") from e
    if "@" in netloc:
        raise UrlError("userinfo")
    if netloc.startswith("["):
        end = netloc.find("]")
        if end < 0:
            raise UrlError("bad_host")
        host_raw, tail = netloc[1:end], netloc[end + 1:]
        if tail and not tail.startswith(":"):
            raise UrlError("bad_host")
        port_raw = tail[1:] if tail else ""
        ip = parse_ip_host(host_raw.lower())
        if ip is None:
            raise UrlError("bad_ip")
        host = str(ip)
    else:
        if netloc.count(":") > 1:
            raise UrlError("bad_host")
        host_raw, _, port_raw = netloc.partition(":")
        host = _norm_host(host_raw)
        ip = parse_ip_host(host)
        if ip is not None:
            host = str(ip)
        else:
            for lab in host.split("."):
                if not _LABEL_RE.match(lab):
                    raise UrlError("bad_host")
    if port_raw:
        if not _DEC.match(port_raw) or int(port_raw) < 1 or int(port_raw) > 65535:
            raise UrlError("bad_port")
        port = int(port_raw)
    else:
        port = DEFAULT_PORTS[scheme]
    disp = f"[{host}]" if ":" in host else host
    path = u.path or "/"
    url = f"{scheme}://{disp}" + ("" if port == DEFAULT_PORTS[scheme] else f":{port}") + path + (f"?{u.query}" if u.query else "") \
        + (f"#{u.fragment}" if u.fragment else "")
    return Parsed(scheme, host, port, ip, path, url)


# --- 분류 -------------------------------------------------------------------------------------


def _name_flags(host: str) -> tuple:
    out = []
    if host in METADATA_NAMES:
        out.append("metadata")
    if host in LOOPBACK_NAMES or host.endswith(".localhost"):
        out.append("loopback")
    if "." not in host:
        out.append("name_special")           # 점 없는 이름(사내 짧은 이름·`metadata`)은 이름 와일드카드가 덮지 못한다
    return tuple(out)


def _local_default(p: Parsed) -> bool:
    return p.host in ("localhost", "127.0.0.1", "::1")


def _non_http_scheme_decision(raw, top_level: bool) -> Optional[Verdict]:
    """http(s) 가 아닌 URL 의 판정 — about:blank 는 허용, 서브프레임의 data:·blob: 는 허용, 나머지는 사유와 함께 거부. http(s) 면 None."""
    try:
        s = _sanitize(raw)
    except UrlError as e:
        return Verdict(False, "denied", e.reason)
    m = _SCHEME_RE.match(s)
    if not m:
        return Verdict(False, "denied", "no_scheme")
    scheme = m.group(1).lower()
    if scheme in DEFAULT_PORTS:
        return None
    if scheme == "about":
        if s.lower() == BLANK:
            return Verdict(True, "blank", "", BLANK, "about")
        return Verdict(False, "denied", "scheme_about")
    if scheme in ("data", "blob") and not top_level:
        return Verdict(True, "blank", "", s[:64], scheme)
    return Verdict(False, "denied", f"scheme_{scheme}" if scheme in _NAMED_SCHEMES else "scheme_not_allowed")


def classify(raw, *, exposed_ports: Iterable[int] = (), top_level: bool = True) -> Verdict:
    """허용 목록을 보지 않은 1차 분류. `allow=True` 면 허용 목록 없이 열리는 것(about:blank · 노출 포트의 localhost)."""
    v = _non_http_scheme_decision(raw, top_level)
    if v is not None:
        return v
    try:
        p = parse_url(raw)
    except UrlError as e:
        return Verdict(False, "denied", e.reason)
    fl = list(_ip_flags(p.ip)) if p.ip is not None else list(_name_flags(p.host))
    if p.ip is not None:
        fl.append("ip")
    base = dict(url=p.url, scheme=p.scheme, host=p.host, port=p.port, ip=str(p.ip) if p.ip is not None else None, flags=tuple(fl))
    if _local_default(p) and "loopback" in fl:
        if p.port in set(exposed_ports):
            return Verdict(True, "local_exposed", "", **base)
        return Verdict(False, "needs_allowlist", "port_not_exposed", **base)
    return Verdict(False, "needs_allowlist", "not_allowlisted", **base)


# --- 허용 목록 ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    scheme: Optional[str]
    host: str                          # 정규화한 호스트·IP, 또는 `*.접미사`
    port: Optional[int]                # None = 스킴의 기본 포트
    net: Optional[object] = None       # CIDR


def parse_allowlist_entry(raw: str) -> Optional[_Entry]:
    """허용 목록 한 줄 → 항목. 읽지 못하는 줄·단독 `*` 는 None(무시 = 좁게)."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s == "*" or len(s) > 300:
        return None
    try:
        if "/" in s and "://" not in s:
            host, _, bits = s.partition("/")
            if _DEC.match(bits):
                return _Entry(None, host, None, ipaddress.ip_network(s if ":" in host else f"{parse_ipv4(host)}/{bits}", strict=False))
            return None
        scheme = None
        if "://" in s:
            scheme, _, s = s.partition("://")
            scheme = scheme.lower()
            if scheme not in DEFAULT_PORTS:
                return None
            s = s.split("/", 1)[0]
        if s.startswith("["):
            end = s.find("]")
            if end < 0:
                return None
            host = str(parse_ip_host(s[1:end].lower()))
            port_raw = s[end + 1:].lstrip(":")
        else:
            if s.count(":") > 1:
                return None
            host_raw, _, port_raw = s.partition(":")
            if host_raw.startswith("*."):
                suffix = _norm_host(host_raw[2:])
                if parse_ip_host(suffix) is not None or "." not in suffix:
                    return None
                host = "*." + suffix
            else:
                host = _norm_host(host_raw)
                ip = parse_ip_host(host)
                host = str(ip) if ip is not None else host
        port = None
        if port_raw:
            if not _DEC.match(port_raw) or not 1 <= int(port_raw) <= 65535:
                return None
            port = int(port_raw)
        return _Entry(scheme, host, port)
    except (UrlError, ValueError):
        return None


def _entry_matches(e: _Entry, v: Verdict) -> bool:
    if e.net is not None:
        if v.ip is None:
            return False
        try:
            ip = ipaddress.ip_address(v.ip)
        except ValueError:
            return False
        return ip.version == e.net.version and ip in e.net
    if e.scheme and e.scheme != v.scheme:
        return False
    if (e.port if e.port is not None else DEFAULT_PORTS[v.scheme]) != v.port:
        return False
    if e.host.startswith("*."):
        # 이름 와일드카드는 IP·특수 이름을 덮지 못하고, 접미사 자체(apex)도 덮지 않는다.
        return v.ip is None and not (set(v.flags) & {"metadata", "loopback", "name_special"}) and v.host.endswith(e.host[1:]) and v.host != e.host[2:]
    return e.host == v.host


def allowlist_match(v: Verdict, allowlist: Iterable[str]) -> bool:
    entries = [e for e in (parse_allowlist_entry(x) for x in (allowlist or ())) if e is not None]
    return any(_entry_matches(e, v) for e in entries)


# --- DNS ---------------------------------------------------------------------------------------

_dns_cache: dict[str, tuple[float, Optional[list]]] = {}
_dns_lock = threading.Lock()
DNS_TTL = 30.0
DNS_TIMEOUT = 2.0


def _real_resolve(host: str) -> Optional[list]:
    import socket
    result: list = []

    def work():
        try:
            result.append([ai[4][0] for ai in socket.getaddrinfo(host, None)])
        except OSError:
            result.append(None)

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(DNS_TIMEOUT)
    return result[0] if result else None


def resolve_host(host: str) -> Optional[list]:
    """호스트 → 주소 문자열 목록. 못 풀면 None. 시험은 이 모듈 속성을 바꿔 끼운다. 30초 캐시."""
    now = time.monotonic()
    with _dns_lock:
        hit = _dns_cache.get(host)
        if hit and now - hit[0] < DNS_TTL:
            return hit[1]
    out = _real_resolve(host)
    with _dns_lock:
        if len(_dns_cache) > 256:
            _dns_cache.clear()
        _dns_cache[host] = (now, out)
    return out


def _dns_ok(v: Verdict, allowlist: Iterable[str]) -> tuple[bool, str]:
    addrs = resolve_host(v.host)
    if not addrs:
        return False, "dns_unresolved"
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a.split("%", 1)[0])
        except ValueError:
            return False, "dns_bad_address"
        if isinstance(ip, ipaddress.IPv6Address):
            ip = _unwrap_v6(ip)
        if ip.is_global and ip not in METADATA_IPS:
            continue
        sub = Verdict(False, "needs_allowlist", "", "", v.scheme, str(ip), v.port, str(ip), (*_ip_flags(ip), "ip"))
        if not allowlist_match(sub, allowlist):
            return False, "dns_private_address"
    return True, ""


# --- 판정 --------------------------------------------------------------------------------------


def check_url(raw, *, allowlist: Iterable[str] = (), exposed_ports: Iterable[int] = (), top_level: bool = True,
              dns: bool = True) -> Decision:
    """문서 URL(이동·redirect hop·서브프레임) 검사. 허용이면 `Decision(True, "")`."""
    v = classify(raw, exposed_ports=exposed_ports, top_level=top_level)
    if v.kind == "denied":
        return Decision(False, v.reason, v)
    if v.allow:
        return Decision(True, "", v)
    if not allowlist_match(v, allowlist):
        return Decision(False, v.reason or "not_allowlisted", v)
    if dns and v.ip is None and not (set(v.flags) & {"metadata", "loopback", "name_special"} - {"name_special"}):
        ok, why = _dns_ok(v, allowlist)
        if not ok:
            return Decision(False, why, v)
    return Decision(True, "", v)


def check_subresource(raw, *, allowlist: Iterable[str] = (), exposed_ports: Iterable[int] = ()) -> Decision:
    """하위 리소스 요청 — 공인 호스트는 열고, 루프백(노출 포트 밖)·사설·링크 로컬·메타데이터·특수 이름은 허용 목록이 있어야 한다."""
    v = _non_http_scheme_decision(raw, top_level=False)
    if v is not None:
        return Decision(v.allow, v.reason, v)
    try:
        p = parse_url(raw)
    except UrlError as e:
        return Decision(False, e.reason)
    c = classify(raw, exposed_ports=exposed_ports, top_level=False)
    risky = bool(set(c.flags) & {"loopback", "private", "link_local", "metadata", "reserved", "name_special"})
    if not risky or c.allow or allowlist_match(c, allowlist):
        return Decision(True, "", c)
    return Decision(False, "subresource_" + (c.flags[0] if c.flags else "blocked"), c)


def audit_url(raw) -> str:
    """감사에 남길 URL 모양 — query·fragment 는 버린다(origin + path 만). 읽지 못하면 빈 문자열."""
    try:
        s = _sanitize(raw)
        u = urlsplit(s)
        return f"{u.scheme}://{u.netloc}{u.path}" if u.netloc else s.split("?", 1)[0].split("#", 1)[0][:200]
    except (UrlError, ValueError):
        return ""


# --- 값 가림 (R9.5) -----------------------------------------------------------------------------


def redact_cookies(items, values: bool) -> list:
    """쿠키 목록. 기본은 이름·도메인·경로·플래그만, `values` 일 때만 값."""
    out = []
    for c in items or []:
        if not isinstance(c, dict):
            continue
        row = {k: c.get(k) for k in ("name", "domain", "path", "secure", "httpOnly", "sameSite", "expires", "session") if k in c}
        if values:
            row["value"] = c.get("value")
        else:
            row["valueRedacted"] = True
        out.append(row)
    return out


def redact_storage(data, values: bool) -> dict:
    """`{local: {k: v}, session: {k: v}}` → 기본은 키 이름만."""
    out: dict = {}
    for area in ("local", "session"):
        d = (data or {}).get(area) if isinstance(data, dict) else None
        d = d if isinstance(d, dict) else {}
        out[area] = {str(k): (v if values else REDACTED) for k, v in d.items()} if values else {str(k): REDACTED for k in d}
    if not values:
        out["valuesRedacted"] = True
    return out


def _redact_headers(h) -> dict:
    out = {}
    for k, v in (h or {}).items() if isinstance(h, dict) else []:
        out[str(k)] = REDACTED if str(k).lower() in SENSITIVE_HEADERS else v
    return out


def redact_network(entries) -> list:
    """network 항목 — 민감 헤더 값과 64KB 초과 body 는 언제나 가린다(`values` 와 무관)."""
    out = []
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        row = dict(e)
        for k in ("headers", "requestHeaders", "responseHeaders"):
            if k in row:
                row[k] = _redact_headers(row[k])
        for k in ("body", "requestBody", "responseBody", "postData"):
            b = row.get(k)
            if isinstance(b, (str, bytes)) and len(b) > MAX_BODY:
                row[k] = REDACTED
                row[k + "Redacted"] = "too_large"
        out.append(row)
    return out


def cap_result(v):
    """`eval` 결과 상한 — 직렬화했을 때 64KB 를 넘으면 잘라 표시한다."""
    import json
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = json.dumps(str(v))
    if len(s) <= MAX_EVAL_RESULT:
        return v, False
    return s[:MAX_EVAL_RESULT], True


# --- 한도 (R12) ---------------------------------------------------------------------------------


@dataclass
class TabLimits:
    """탭당 한도 — 전체 60/분, `browser.js` 10/분, 이동 30/분. 슬라이딩 창, 메모리 보관."""
    general: int = 60
    js: int = 10
    navigate: int = 30
    period: float = 60.0
    _hits: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def hit(self, tab_id: str, kind: str, now: Optional[float] = None) -> Optional[str]:
        """허용이면 None, 넘었으면 넘은 한도 이름. 거부된 호출은 세지 않는다. 한 호출이 여러 창에 든다(js·navigate 도 general)."""
        t = time.monotonic() if now is None else now
        names = ["general"] + ([kind] if kind in ("js", "navigate") else [])
        limit = {"general": self.general, "js": self.js, "navigate": self.navigate}
        with self._lock:
            qs = {n: self._hits.setdefault((tab_id, n), []) for n in names}
            for n, q in qs.items():
                q[:] = [x for x in q if t - x < self.period]
                if len(q) >= limit[n]:
                    return n
            for q in qs.values():
                q.append(t)
        return None

    def forget(self, tab_id: str) -> None:
        with self._lock:
            for k in [k for k in self._hits if k[0] == tab_id]:
                del self._hits[k]
