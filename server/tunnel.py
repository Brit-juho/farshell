"""Cloudflare Tunnel 상태 관리 (Phase 8 G1, 옵션 A+B+C).

옵션 A: 자동 감지 + 기존 URL 재사용
옵션 B: 상태 노출 (/api/tunnel/status)
옵션 C: 명명 터널 옵트인 (VT_TUNNEL_NAME, VT_TUNNEL_HOSTNAME)

2026-09-17: 공개 입구 제공자가 하나가 아니게 됐다(`VT_TUNNEL_PROVIDER` =
cloudflare | ngrok | none, bin/fsh와 같은 키). 파일 이름은 그대로 두되
`get_tunnel_status()`만 제공자를 본다 — 이 함수 하나가 `/api/tunnel/status`를
거쳐 HUD까지 먹이므로, 여기를 안 고치면 ngrok으로 도는 내내 화면이
"터널 끊김"이라고 **거짓말**한다. 파일을 쪼개지 않는 이유는 그 소비자들을
같이 흔들기 때문이다.

purplemux의 Tailscale 자동 감지 패턴(getTailscaleIp)을 Cloudflare용으로 변형.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
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


# 이 함수 한 번이 실측 **32.9ms**다(2026-09-16) — pgrep + which + `ps -p` +
# 로그 파싱이 전부 서브프로세스/파일 I/O다. `/api/capabilities`와
# `/api/tunnel/status` 양쪽이 이걸 싣고 둘 다 30초마다 폴링된다.
# TTL을 10초로 짧게 잡은 이유: 터널 URL은 사용자가 지켜보는 값이라
# 바뀌면 빨리 드러나야 한다(재시작·좀비 복구 시). 10초면 충분히 짧고,
# 폴링 한 주기 안에 여러 호출자가 겹칠 때의 중복은 걷어낸다.
CACHE_TTL_SEC = 10.0
_status_cache: dict = {"at": 0.0, "data": None}
_status_lock = threading.Lock()


def invalidate_status_cache() -> None:
    with _status_lock:
        _status_cache["at"] = 0.0
        _status_cache["data"] = None


def get_provider() -> str:
    """공개 입구 제공자 — "cloudflare"(기본) | "ngrok" | "none".

    bin/fsh의 `_tunnel_provider`와 같은 규칙이다(모르는 값은 cloudflare로 본다).
    """
    p = (os.environ.get("VT_TUNNEL_PROVIDER") or "").strip().lower()
    return p if p in ("cloudflare", "ngrok", "none") else "cloudflare"


def _ngrok_status() -> dict:
    """ngrok 에이전트 로컬 API에서 VT 포트를 내보내는 터널을 찾는다.

    에이전트를 여러 개 띄우면(메인 + 추가 포트) 4040이 점유돼 4041…로 밀리므로
    몇 개를 훑는다. VT_NGROK_API를 주면 그 주소만 본다.
    """
    import json
    import urllib.request

    port = (os.environ.get("VT_PORT") or "7777").strip()
    apis = [a for a in (os.environ.get("VT_NGROK_API") or "").split(",") if a.strip()]
    if not apis:
        apis = [f"http://127.0.0.1:{p}" for p in (4040, 4041, 4042, 4043)]

    url = None
    for api in apis:
        try:
            with urllib.request.urlopen(f"{api.rstrip('/')}/api/tunnels", timeout=1.5) as r:
                tunnels = (json.loads(r.read().decode()) or {}).get("tunnels") or []
        except Exception:
            continue
        for t in tunnels:
            addr = str((t.get("config") or {}).get("addr") or "")
            pub = str(t.get("public_url") or "")
            # 우리 포트로 가는 터널만 — 다른 앱을 내보내는 ngrok을 FarShell
            # 입구라고 보고하면 화면이 거짓말을 하게 된다.
            if addr.rsplit(":", 1)[-1] == port and pub.startswith("https"):
                url = pub
                break
        if url:
            break

    pids = []
    if shutil.which("pgrep"):
        try:
            out = subprocess.check_output(
                ["pgrep", "-f", "ngrok .*http"], stderr=subprocess.DEVNULL, timeout=2.0
            ).decode()
            pids = [int(x) for x in out.split() if x.isdigit()]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            pids = []

    domain = (os.environ.get("VT_NGROK_DOMAIN") or "").strip()
    domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
    return {
        "provider": "ngrok",
        "installed": shutil.which("ngrok") is not None,
        # URL을 못 읽었어도 프로세스가 있으면 "실행 중"이다 — 에이전트 API만
        # 막힌 경우까지 "꺼짐"이라고 말하지 않는다.
        "running": bool(url) or bool(pids),
        "pids": pids,
        "url": url,
        "mode": "reserved" if domain else "ephemeral",
        "name": None,
        "hostname": domain or None,
        "started_at": None,
        "log_path": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def get_tunnel_status(force: bool = False) -> dict:
    """전체 터널 상태 (API 응답 구조). 10초 캐시."""
    if not force:
        with _status_lock:
            data, at = _status_cache["data"], _status_cache["at"]
        if data is not None and time.time() - at < CACHE_TTL_SEC:
            # checked_at은 "언제 실제로 확인했는가"라 캐시된 값을 그대로 둔다 —
            # 지금 시각으로 덮으면 확인하지 않은 것을 확인한 것처럼 말하게 된다.
            return data
    status = _get_tunnel_status_uncached()
    with _status_lock:
        _status_cache["at"] = time.time()
        _status_cache["data"] = status
    return status


def _get_tunnel_status_uncached() -> dict:
    provider = get_provider()
    if provider == "ngrok":
        return _ngrok_status()
    if provider == "none":
        return {
            "provider": "none",
            "installed": is_installed(),
            "running": False,
            "pids": [],
            "url": None,
            "mode": "disabled",
            "name": None,
            "hostname": None,
            "started_at": None,
            "log_path": None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
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
        "provider": "cloudflare",
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
