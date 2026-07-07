"""네트워크 액세스 정책 (Phase 8 G1 + D9 Tailscale).

`localhost`/`lan`/`tailscale`/`all` 모드 분리 + CIDR IP 화이트리스트.
purplemux/src/lib/network-access.ts에서 최초엔 Tailscale 부분을 제외하고 차용했으나,
회사망 등 원격 데스크톱이 막힌 환경에서 Tailscale+SSH로 접속하는 시나리오를 지원하기
위해 D9에서 "tailscale" 모드로 재도입했다. Tailscale 상태 조회 자체는
`server/tailscale.py` (tunnel.py와 동일한 패턴)가 단일 진실의 원천.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import Iterable, Optional

LOCALHOST_RANGES = ["127.0.0.0/8", "::1/128"]
LAN_RANGES = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "fc00::/7",
    "fe80::/10",
]
# Tailscale의 CGNAT 대역 (RFC 6598, 100.64.0.0/10). 모든 tailnet 기기의 IPv4가
# 여기서 할당됨 — tailnet 안에서만 오는 요청을 LAN과 구분해 허용하고 싶을 때 사용
# (예: 회사망처럼 LAN 자체는 못 믿는 환경). 실제 상태 조회는 server/tailscale.py 참조.
TAILSCALE_RANGES = ["100.64.0.0/10"]


@dataclass
class AccessSpec:
    networks: list[ipaddress._BaseNetwork]
    allow_all: bool = False

    def is_allowed(self, remote_ip: Optional[str]) -> bool:
        if self.allow_all:
            return True
        if not remote_ip:
            return False
        try:
            # IPv4-mapped IPv6 (::ffff:127.0.0.1) 처리
            if remote_ip.startswith("::ffff:"):
                remote_ip = remote_ip[7:]
            ip = ipaddress.ip_address(remote_ip)
        except ValueError:
            return False
        for net in self.networks:
            try:
                if ip in net:
                    return True
            except (TypeError, ValueError):
                continue
        return False


def _expand_keyword(keyword: str) -> Optional[Iterable[str]]:
    k = keyword.strip().lower()
    if not k:
        return []
    if k in ("all", "*", "0.0.0.0"):
        return None  # 특수: allow_all
    if k == "localhost":
        return LOCALHOST_RANGES
    if k == "lan":
        return LAN_RANGES
    if k == "tailscale":
        return TAILSCALE_RANGES
    return None  # CIDR로 시도


def parse_access_spec(spec: str) -> AccessSpec:
    """`"localhost,lan"` 같은 콤마 구분 정책 → AccessSpec."""
    networks: list[ipaddress._BaseNetwork] = []
    allow_all = False
    for token in spec.split(","):
        t = token.strip()
        if not t:
            continue
        if t.lower() in ("all", "*", "0.0.0.0"):
            allow_all = True
            continue
        expanded = _expand_keyword(t)
        if expanded is None:
            # CIDR로 직접 파싱 시도
            try:
                networks.append(ipaddress.ip_network(t, strict=False))
            except ValueError:
                continue
        else:
            for cidr in expanded:
                try:
                    networks.append(ipaddress.ip_network(cidr, strict=False))
                except ValueError:
                    continue
    return AccessSpec(networks=networks, allow_all=allow_all)


def network_mode_to_spec(mode: str) -> str:
    """vt 표준 모드 이름 → access spec 문자열."""
    m = mode.strip().lower() if mode else "all"
    if m == "localhost":
        return "localhost"
    if m == "lan":
        return "localhost,lan"
    if m == "tailscale":
        return "localhost,tailscale"
    return "all"


# P2: 미들웨어가 매 요청/WS마다 get_current_spec()을 호출한다. env는 프로세스 시작 시
# 1회만 설정되므로(bin/vt에서 export) 매번 재파싱 + CIDR 객체 재생성은 낭비다.
# (env_key -> AccessSpec) 1-엔트리 캐시. env가 바뀌면 키 불일치로 자동 재빌드.
_spec_cache: "tuple[tuple[str, str], AccessSpec] | None" = None


def get_current_spec() -> AccessSpec:
    """현재 환경변수 기준 정책. VT_ACCESS_SPEC > VT_NETWORK_MODE > all (캐시됨)."""
    global _spec_cache
    raw = os.environ.get("VT_ACCESS_SPEC", "").strip()
    mode = os.environ.get("VT_NETWORK_MODE", "all").strip().lower()
    key = (raw, mode)
    if _spec_cache is not None and _spec_cache[0] == key:
        return _spec_cache[1]
    spec = parse_access_spec(raw) if raw else parse_access_spec(network_mode_to_spec(mode))
    _spec_cache = (key, spec)
    return spec


def resolve_bind_host(spec: AccessSpec) -> str:
    """정책 기반 listener bind host 결정. localhost-only면 127.0.0.1, 그 외 0.0.0.0."""
    if spec.allow_all:
        return "0.0.0.0"
    if not spec.networks:
        return "127.0.0.1"
    # 모두 loopback 대역인지 검사
    loopback = [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
    ]
    for net in spec.networks:
        is_loopback = any(
            net.subnet_of(lb) if isinstance(net, type(lb)) else False
            for lb in loopback
        )
        if not is_loopback:
            return "0.0.0.0"
    return "127.0.0.1"


def is_request_allowed(remote_ip: Optional[str]) -> bool:
    """현재 정책 기준 요청 IP 허용 여부."""
    return get_current_spec().is_allowed(remote_ip)


def get_lan_ip() -> Optional[str]:
    """LAN IP 자동 추출 (외부 연결 안 하고 socket trick)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None
