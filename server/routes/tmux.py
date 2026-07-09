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

async def _attach_tmux(tmux_name: str, cols: int, rows: int):
    # 존재하지 않는 tmux 세션에 attach하면 `tmux attach-session`이 즉시 실패하지만,
    # 그 전에 유령 web 세션(PTY)이 이미 생성·등록돼 /api/sessions에 남는다. 클라이언트는
    # 그 id로 WS를 열었다가 죽은 세션에 재연결을 반복하고, restoreWorkspace가 stale 탭마다
    # 이 유령 세션을 만들어 로드 즉시 세션·터미널·WS가 무더기로 쌓여 메모리가 폭증했다.
    # → PTY를 만들기 전에 세션 존재를 확인하고, 없으면 404로 돌려보내 복원 루틴이 건너뛰게 한다.
    if not tmux_runner.has_session(tmux_name):
        return JSONResponse(
            {"error": "tmux session not found", "tmux_session": tmux_name},
            status_code=404,
        )
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

    # 새 tmux 세션 시작 디렉토리 — 서버 cwd(프로젝트 폴더) 대신 홈/VT_START_DIR.
    start_dir = platform_utils.default_start_dir()
    rc, _, err = tmux_runner.run(
        ["new-session", "-d", "-s", tmux_name, "-x", str(cols), "-y", str(rows), "-c", start_dir],
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


@router.post("/api/tmux/open-on-mac")
async def open_tmux_on_mac(request: Request):
    """이미 존재하는 tmux 세션을 맥 터미널(iTerm2 우선)에 새 창으로 attach.

    웹에서 보고 있는 세션을 나중에 맥에서도 열고 싶을 때 사용. 같은 tmux 소켓(-L vt)에
    attach하므로 웹/음성/맥이 동일 세션을 공유한다.
    """
    body = await request.json()
    tmux_name = (body.get("name") or "").strip()
    if not tmux_name or not re.fullmatch(r"[A-Za-z0-9_\-]+", tmux_name):
        return JSONResponse({"ok": False, "error": "유효하지 않은 세션 이름"}, status_code=400)
    if not platform_utils.IS_MACOS:
        return JSONResponse({"ok": False, "error": "서버가 macOS가 아님"}, status_code=400)
    rc, _, _ = tmux_runner.run(["has-session", "-t", tmux_name], timeout=5.0)
    if rc != 0:
        return JSONResponse({"ok": False, "error": "tmux 세션을 찾을 수 없음"}, status_code=404)
    attach_cmd = f"tmux -L {TMUX_SOCKET} attach -t {tmux_name}"
    try:
        ok = platform_utils.spawn_mac_terminal(attach_cmd)
        return {"ok": bool(ok), "error": None if ok else "지원하는 터미널 앱이 없음"}
    except Exception as e:
        logger.warning(f"open-on-mac 실패: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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
        from deps import _auto_responder
        pty_mgr.destroy_session(existing.session_id)
        session_store.remove(existing.session_id)
        output_watcher.remove_session(existing.session_id)
        _auto_responder.remove(existing.session_id)  # 세션별 상태 정리 (누수 방지)
    # 실제 tmux 세션 종료 — 이 줄이 빠져 있어 rc가 정의되지 않은 채 참조돼 500이 났고,
    # 세션이 전혀 kill되지 않았다.
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
