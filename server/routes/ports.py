"""포트 대시보드 — 리스닝 포트 조회 / 종료 / 터널 노출 (P3).

blocking 호출(lsof, ps, kill 대기, vt tunnel)은 전부 asyncio.to_thread로 offload한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import portscan

logger = logging.getLogger(__name__)

router = APIRouter()

# vt tunnel expose 는 cloudflared 기동 + URL 확보까지 최대 30초를 기다린다(bin/vt:340).
VT_TIMEOUT = 45.0


@router.get("/api/ports")
async def list_ports(fresh: bool = Query(False)):
    return await asyncio.to_thread(portscan.scan, not fresh)


@router.delete("/api/ports/{port}")
async def kill_port(port: int, pid: int = Query(None)):
    if port < 1 or port > 65535:
        return JSONResponse({"ok": False, "error": "bad_port"}, status_code=400)
    result = await asyncio.to_thread(portscan.kill_port, port, pid)
    if result.get("ok"):
        return result
    status = {
        "protected": 403,
        "permission": 403,
        "not_found": 404,
        "pid_changed": 409,
    }.get(result.get("error"), 500)
    return JSONResponse(result, status_code=status)


# --- 터널 노출 --------------------------------------------------------------
# 이 엔드포인트는 로컬 개발 서버를 '공개 인터넷'에 연다. P1에서 접근 범위를 tailnet으로
# 좁혀놓고 여기서 다시 뚫으면 의미가 없으므로, 두 겹으로 막는다:
#   - confirm=true 를 본문에 명시해야 실행된다(오탭 방지)
#   - VT_NETWORK_MODE 가 all 이 아니면 아예 거부하고 직접 접근을 안내한다


def _vt_bin() -> Path | None:
    vt_dir = os.environ.get("VT_DIR")
    if not vt_dir:
        return None
    p = Path(vt_dir) / "bin" / "vt"
    return p if p.is_file() else None


def _run_vt(*args: str) -> tuple[int, str]:
    vt = _vt_bin()
    if vt is None:
        return 127, "vt CLI를 찾을 수 없습니다 (VT_DIR 미설정)"
    try:
        p = subprocess.run(["bash", str(vt), *args], capture_output=True, timeout=VT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, "시간 초과"
    except (OSError, ValueError) as e:
        return 127, str(e)
    return p.returncode, p.stdout.decode("utf-8", errors="replace")


@router.post("/api/ports/{port}/expose")
async def expose_port(port: int, request: Request):
    if port < 1 or port > 65535:
        return JSONResponse({"ok": False, "error": "bad_port"}, status_code=400)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if not body.get("confirm"):
        return JSONResponse(
            {"ok": False, "error": "confirm_required",
             "reason": "이 포트를 공개 인터넷에 노출합니다. confirm=true 로 다시 요청하세요."},
            status_code=428,
        )

    mode = os.environ.get("VT_NETWORK_MODE", "all")
    if mode != "all":
        return JSONResponse(
            {"ok": False, "error": "network_mode",
             "reason": f"접근 범위가 '{mode}'로 제한된 상태입니다. "
                       f"공개 터널을 여는 대신 같은 망에서 직접 접속하세요."},
            status_code=409,
        )

    label = str(body.get("label") or f"localhost:{port}")[:64]
    rc, out = await asyncio.to_thread(_run_vt, "tunnel", "expose", str(port), label)
    if rc != 0:
        return JSONResponse({"ok": False, "error": "expose_failed", "reason": out.strip()},
                            status_code=500)
    url = ""
    for tok in out.split():
        if tok.startswith("https://"):
            url = tok
            break
    return {"ok": True, "port": port, "url": url, "output": out.strip()}


@router.delete("/api/ports/{port}/expose")
async def unexpose_port(port: int):
    rc, out = await asyncio.to_thread(_run_vt, "tunnel", "unexpose", str(port))
    if rc != 0:
        return JSONResponse({"ok": False, "error": "unexpose_failed", "reason": out.strip()},
                            status_code=500)
    return {"ok": True, "port": port, "output": out.strip()}
