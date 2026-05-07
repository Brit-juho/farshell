"""tmux 세션 CRUD + 라이브 프리뷰."""

from __future__ import annotations

import logging
import re
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import platform_utils
import tmux_runner
from deps import pty_mgr, session_store, output_watcher
from session_store import new_session_id

logger = logging.getLogger(__name__)

router = APIRouter()

TMUX_SOCKET = tmux_runner.VT_TMUX_SOCKET


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
    tmux_name = body.get("name", f"web-{str(uuid.uuid4())[:4]}")
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
