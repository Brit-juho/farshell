"""Cloudflare Tunnel 상태 관리 (Phase 8 G1, 옵션 A+B+C).

옵션 A: 자동 감지 + 기존 URL 재사용
옵션 B: 상태 노출 (/api/tunnel/status)
옵션 C: 명명 터널 옵트인 (VT_TUNNEL_NAME, VT_TUNNEL_HOSTNAME)

purplemux의 Tailscale 자동 감지 패턴(getTailscaleIp)을 Cloudflare용으로 변형.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CLOUDFLARED_LOG = "/tmp/cloudflared.log"
URL_PATTERN = re.compile(r"https://[\w-]+\.trycloudflare\.com")


def is_installed() -> bool:
    return shutil.which("cloudflared") is not None


def find_active_pids() -> list[int]:
    """실행 중인 cloudflared 프로세스 PID. pgrep -f."""
    if not shutil.which("pgrep"):
        return []
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "cloudflared.*tunnel"],
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        ).decode()
        return [int(p) for p in out.split() if p.isdigit()]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []


def parse_url_from_log(log_path: str = CLOUDFLARED_LOG) -> Optional[str]:
    """log 파일에서 마지막 trycloudflare URL 추출."""
    p = Path(log_path)
    if not p.is_file():
        return None
    try:
        # 마지막 100KB만 읽음 (대용량 로그 안전)
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 100_000), 0)
            text = f.read().decode("utf-8", errors="ignore")
        urls = URL_PATTERN.findall(text)
        return urls[-1] if urls else None
    except Exception:
        return None


def get_named_url() -> Optional[str]:
    """명명 터널 사용 시 호스트명 기반 URL 반환."""
    hostname = os.environ.get("VT_TUNNEL_HOSTNAME", "").strip()
    if hostname:
        return f"https://{hostname}"
    return None


def get_tunnel_status() -> dict:
    """전체 터널 상태 (API 응답 구조)."""
    pids = find_active_pids()
    running = bool(pids)
    name = os.environ.get("VT_TUNNEL_NAME", "").strip()
    hostname = os.environ.get("VT_TUNNEL_HOSTNAME", "").strip()
    mode = "named" if (name and hostname) else "anonymous"
    url = get_named_url() if mode == "named" else parse_url_from_log()

    started_at = None
    if running and pids:
        try:
            # 가장 오래된 PID의 시작 시간
            ps_out = subprocess.check_output(
                ["ps", "-p", str(pids[0]), "-o", "lstart="],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode().strip()
            if ps_out:
                started_at = ps_out
        except Exception:
            pass

    return {
        "installed": is_installed(),
        "running": running,
        "pids": pids,
        "url": url,
        "mode": mode,
        "name": name or None,
        "hostname": hostname or None,
        "started_at": started_at,
        "log_path": CLOUDFLARED_LOG,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def is_named_tunnel_configured() -> bool:
    """명명 터널 환경변수가 모두 설정됐는지."""
    return bool(
        os.environ.get("VT_TUNNEL_NAME", "").strip()
        and os.environ.get("VT_TUNNEL_HOSTNAME", "").strip()
    )


def has_credentials_file(name: str) -> bool:
    """~/.cloudflared/<name>.json 또는 <UUID>.json 존재 여부."""
    cf_dir = Path.home() / ".cloudflared"
    if not cf_dir.is_dir():
        return False
    # name.json 직접 매치 또는 UUID 매치
    if (cf_dir / f"{name}.json").is_file():
        return True
    # cert.pem이 있어야 cloudflared tunnel 사용 가능
    if not (cf_dir / "cert.pem").is_file():
        return False
    # UUID JSON 파일 중 하나라도 있으면 OK (실제 매치는 cloudflared가 수행)
    return any(p.suffix == ".json" for p in cf_dir.iterdir())
