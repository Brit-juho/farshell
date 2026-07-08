"""Tailscale 상태 감지 (회사망 등 원격 데스크톱이 막힌 환경에서 SSH 경유 접속용).

tunnel.py(Cloudflare)와 동일한 패턴. `tailscale status --json`을 파싱해
설치/실행/자신의 tailnet IP·MagicDNS 호스트명을 노출한다.

이 모듈은 서버 바인딩 정책(network_access.py)과 CLI(`vt ssh`, `vt status`,
`vt doctor`)가 함께 참조하는 단일 진실의 원천이다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional


def is_installed() -> bool:
    return shutil.which("tailscale") is not None


@dataclass
class TailscaleStatus:
    installed: bool
    running: bool = False
    backend_state: Optional[str] = None
    ips: list[str] = field(default_factory=list)
    hostname: Optional[str] = None  # MagicDNS 이름 (trailing dot 제거)

    @property
    def ipv4(self) -> Optional[str]:
        for ip in self.ips:
            if ":" not in ip:
                return ip
        return None


def _run_status_json(timeout: float = 2.0) -> Optional[dict]:
    if not is_installed():
        return None
    try:
        out = subprocess.check_output(
            ["tailscale", "status", "--json"],
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        ).decode()
        return json.loads(out)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return None


def get_status() -> TailscaleStatus:
    """현재 Tailscale 상태. daemon 미실행/미설치 시에도 안전하게 기본값 반환."""
    installed = is_installed()
    data = _run_status_json() if installed else None
    if not data:
        return TailscaleStatus(installed=installed)

    backend_state = data.get("BackendState")
    self_node = data.get("Self") or {}
    ips = list(self_node.get("TailscaleIPs") or [])
    dns_name = (self_node.get("DNSName") or "").rstrip(".")

    return TailscaleStatus(
        installed=True,
        running=backend_state == "Running",
        backend_state=backend_state,
        ips=ips,
        hostname=dns_name or None,
    )


def get_ip() -> Optional[str]:
    """자신의 Tailscale IPv4 주소. 미실행/미설치 시 None."""
    status = get_status()
    return status.ipv4 if status.running else None


def get_hostname() -> Optional[str]:
    """MagicDNS 호스트명 (예: my-mac.tailxxxx.ts.net). 미실행/MagicDNS 비활성 시 None."""
    status = get_status()
    return status.hostname if status.running else None


def get_status_dict() -> dict:
    """API 응답용 dict."""
    s = get_status()
    return {
        "installed": s.installed,
        "running": s.running,
        "backend_state": s.backend_state,
        "ip": s.ipv4,
        "hostname": s.hostname,
    }
