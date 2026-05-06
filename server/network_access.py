"""네트워크 액세스 정책 (Phase 8 G1).

`localhost`/`lan`/`all` 모드 분리 + CIDR IP 화이트리스트.
purplemux/src/lib/network-access.ts에서 Tailscale 부분 제외하고 차용.
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
    return "all"


def get_current_spec() -> AccessSpec:
    """현재 환경변수 기준 정책. VT_NETWORK_MODE > VT_ACCESS_SPEC > all."""
    raw = os.environ.get("VT_ACCESS_SPEC", "").strip()
    if raw:
        return parse_access_spec(raw)
    mode = os.environ.get("VT_NETWORK_MODE", "all").strip().lower()
    return parse_access_spec(network_mode_to_spec(mode))


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
