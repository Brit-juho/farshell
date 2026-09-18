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
import threading
import time
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


# `tailscale status --json` 서브프로세스는 실측 **53.6ms**가 걸린다(2026-09-16).
# `/api/capabilities`가 이 값을 싣는데 그 화면은 30초마다 두 곳에서 폴링되므로,
# 캐시가 없으면 분당 수백 ms를 서브프로세스에 태운다. 테일스케일이 붙었다
# 떨어지는 일은 분 단위로도 드물어 30초 캐시로 잃는 정확도가 없다.
# 테스트는 `is_installed`·`subprocess.check_output`를 monkeypatch하므로
# 반드시 `invalidate_cache()`로 초기화해야 한다(conftest의 autouse 픽스처).
CACHE_TTL_SEC = 30.0
_cache: dict = {"at": 0.0, "data": None}
_cache_lock = threading.Lock()


def invalidate_cache() -> None:
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["data"] = None


def get_status(force: bool = False) -> TailscaleStatus:
    """현재 Tailscale 상태(30초 캐시). daemon 미실행/미설치 시에도 안전하게 기본값 반환."""
    if not force:
        with _cache_lock:
            data, at = _cache["data"], _cache["at"]
        if data is not None and time.time() - at < CACHE_TTL_SEC:
            return data
    status = _get_status_uncached()
    with _cache_lock:
        _cache["at"] = time.time()
        _cache["data"] = status
    return status


def _get_status_uncached() -> TailscaleStatus:
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
