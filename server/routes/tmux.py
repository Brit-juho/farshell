"""tmux 세션 CRUD + 라이브 프리뷰."""

from __future__ import annotations

import logging
import os
import re
import uuid

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse

import platform_utils
import tmux_runner
from deps import pty_mgr, session_store, output_watcher
from session_store import new_session_id

logger = logging.getLogger(__name__)

router = APIRouter()

TMUX_SOCKET = tmux_runner.VT_TMUX_SOCKET


# Wave 1 W1-2: 의미있는 디폴트 세션 이름 (cwd basename + 충돌 시 순번)
def _generate_default_session_name() -> str:
    """cwd basename 기반 + 충돌 시 -1, -2 순번. 안전한 영숫자만 사용."""
    try:
        cwd = os.getcwd()
        base = os.path.basename(cwd) or "session"
    except Exception:
        base = "session"
    # 안전한 슬러그: 영숫자/dash/underscore만, 소문자, 최대 20자
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-").lower()[:20]
    if not slug:
        slug = "session"
    candidate = slug
    n = 1
    while tmux_runner.has_session(candidate):
        n += 1
        candidate = f"{slug}-{n}"
        if n > 99:
            # 안전 폴백: uuid
            candidate = f"web-{str(uuid.uuid4())[:4]}"
            break
    return candidate


# --------------------------------------------------------------------------
# 내부 헬퍼
# --------------------------------------------------------------------------

async def _attach_tmux(tmux_name: str, cols: int, rows: int) -> dict:
    session_id = new_session_id()
    pty_mgr.create_session(
        session_id,
        cmd=platform_utils.find_tmux(),
        cmd_args=["tmux", "-L", TMUX_SOCKET, "attach-session", "-t", tmux_name],
        cols=cols,
        rows=rows,
    )
    session_store.add(session_id, name=f"tmux:{tmux_name}")
    session_store.update_tmux_name(session_id, tmux_name)
    output_watcher.add_session(session_id)
    return {"id": session_id, "name": f"tmux:{tmux_name}", "tmux_session": tmux_name}


# --------------------------------------------------------------------------
# 엔드포인트
# --------------------------------------------------------------------------

@router.get("/api/tmux/sessions")
async def list_tmux_sessions():
    fmt_sessions = "#{session_name}\t#{session_windows}\t#{session_attached}"
    fmt_panes = "#{session_name}\t#{pane_current_command}\t#{pane_current_path}"

    sessions_text = tmux_runner.run_text(["list-sessions", "-F", fmt_sessions], timeout=2.0)
    if not sessions_text:
        return []

    panes_text = tmux_runner.run_text(["list-panes", "-a", "-F", fmt_panes], timeout=2.0) or ""
    pane_by_session: dict[str, tuple[str, str]] = {}
    for line in panes_text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] not in pane_by_session:
            pane_by_session[parts[0]] = (parts[1], parts[2] if len(parts) > 2 else "")

    sessions = []
    for line in sessions_text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        name = parts[0]
        cmd, cwd = pane_by_session.get(name, ("", ""))
        web_session = session_store.find_by_tmux_name(name)
        sessions.append({
            "name": name,
            "windows": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1,
            "attached": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            "command": cmd,
            "cwd": cwd,
            "web_session_id": web_session.session_id if web_session else None,
        })
    return sessions


@router.post("/api/tmux/create")
async def create_tmux_session(request: Request):
    body = await request.json()
    # Wave 1 W1-2: 사용자 지정 이름 없으면 cwd 기반 디폴트
    requested_name = body.get("name", "").strip()
    tmux_name = requested_name if requested_name else _generate_default_session_name()
    cols = body.get("cols", 80)
    rows = body.get("rows", 24)
    auto_open = bool(body.get("auto_open_on_mac", False))

    rc, _, err = tmux_runner.run(
        ["new-session", "-d", "-s", tmux_name, "-x", str(cols), "-y", str(rows)],
        timeout=5.0,
    )
    if rc != 0:
        return JSONResponse(
            {"error": "tmux session create failed", "detail": err.decode("utf-8", errors="ignore")},
            status_code=500,
        )

    if auto_open and platform_utils.IS_MACOS and re.fullmatch(r"[A-Za-z0-9_\-]+", tmux_name):
        attach_cmd = f"tmux -L {TMUX_SOCKET} attach -t {tmux_name}"
        try:
            ok = platform_utils.spawn_mac_terminal(attach_cmd)
            if not ok:
                logger.info("맥 터미널 자동 오픈 — 지원 앱 없음")
        except Exception as e:
            logger.warning(f"맥 터미널 자동 오픈 실패: {e}")

    return await _attach_tmux(tmux_name, cols, rows)


@router.post("/api/tmux/attach")
async def attach_tmux_session(request: Request):
    body = await request.json()
    tmux_name = body.get("name", "")
    if not tmux_name:
        return {"error": "tmux session name required"}, 400

    cols = body.get("cols", 80)
    rows = body.get("rows", 24)

    existing = session_store.find_by_tmux_name(tmux_name)
    if existing and existing.session_id in pty_mgr.sessions:
        return {"id": existing.session_id, "name": existing.name, "tmux_session": tmux_name}

    return await _attach_tmux(tmux_name, cols, rows)


@router.delete("/api/tmux/kill/{tmux_name}")
async def kill_tmux_session(tmux_name: str):
    existing = session_store.find_by_tmux_name(tmux_name)
    if existing:
        pty_mgr.destroy_session(existing.session_id)
        session_store.remove(existing.session_id)
        output_watcher.remove_session(existing.session_id)

    rc, _, _ = tmux_runner.run(["kill-session", "-t", tmux_name], timeout=5.0)
    if rc != 0:
        return JSONResponse({"error": "tmux kill failed", "name": tmux_name}, status_code=500)
    return {"ok": True}


@router.get("/api/tmux/preview/{tmux_name}")
async def get_tmux_preview(tmux_name: str, lines: int = 20, ansi: int = 1):
    import preview
    content = preview.capture_pane(tmux_name, lines=lines, ansi=bool(ansi))
    if content is None:
        return {"name": tmux_name, "content": "", "available": False}
    return {"name": tmux_name, "content": content, "available": True, "lines": lines}


# Phase 9 #1: ws push로 grid 1초 폴링 대체 — 변화 시에만 푸시.
@router.websocket("/ws-preview/{tmux_name}")
async def ws_preview(websocket: WebSocket, tmux_name: str):
    import asyncio as _asyncio
    import json as _json
    import preview as _preview
    from fastapi import WebSocketDisconnect
    # codex review fix: VT_TOKEN 환경에서 인증 없는 미리보기 접근 차단
    from routes.pty import _ws_auth
    if not _ws_auth(websocket):
        await websocket.close(code=4001)
        return
    await websocket.accept()

    async def _send(text: str):
        try:
            await websocket.send_text(_json.dumps({"type": "preview", "name": tmux_name, "content": text}))
        except Exception:
            pass

    await _preview.subscribe(tmux_name, _send)
    try:
        while True:
            # 클라이언트로부터 메시지(ping 등) 대기 — 끊김 감지용
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _preview.unsubscribe(tmux_name, _send)
