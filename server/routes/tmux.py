"""tmux 세션 CRUD + 라이브 프리뷰."""

from __future__ import annotations

import asyncio
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


# attach 확인(find_by_tmux_name)과 생성(_attach_tmux)이 원자적이지 않아, 같은 tmux_name에
# 대한 두 요청이 겹치면(예: 새로고침 도중 이전 페이지 요청이 아직 처리 중일 때 새 페이지가
# 또 attach를 요청) 둘 다 "없음"으로 보고 각자 새 PTY를 만들어버려 탭이 순간적으로
# 중복 표시됐다(다음 새로고침에선 마지막 등록만 살아남아 저절로 복구되는 것처럼 보임).
# → tmux_name 단위 락으로 두 번째 요청이 첫 번째의 등록 완료를 기다리게 해서 막는다.
_attach_locks: dict[str, asyncio.Lock] = {}


def _get_attach_lock(tmux_name: str) -> asyncio.Lock:
    lock = _attach_locks.get(tmux_name)
    if lock is None:
        lock = asyncio.Lock()
        _attach_locks[tmux_name] = lock
    return lock


# --------------------------------------------------------------------------
# 엔드포인트
# --------------------------------------------------------------------------

@router.get("/api/tmux/sessions")
async def list_tmux_sessions():
    fmt_sessions = "#{session_name}\t#{session_windows}\t#{session_attached}"
    # A2: pane_id를 함께 받아 응답에 싣는다 — 프런트가 "이 카드가 그 pane인가"를
    # cwd 추측이 아니라 id로 판정할 수 있게(같은 cwd 세션 둘 문제의 해소).
    fmt_panes = "#{session_name}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_id}"

    sessions_text = tmux_runner.run_text(["list-sessions", "-F", fmt_sessions], timeout=2.0)
    if not sessions_text:
        return []

    panes_text = tmux_runner.run_text(["list-panes", "-a", "-F", fmt_panes], timeout=2.0) or ""
    pane_by_session: dict[str, tuple[str, str, str]] = {}
    for line in panes_text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] not in pane_by_session:
            pane_by_session[parts[0]] = (
                parts[1],
                parts[2] if len(parts) > 2 else "",
                parts[3] if len(parts) > 3 else "",
            )

    sessions = []
    for line in sessions_text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        name = parts[0]
        cmd, cwd, pane_id = pane_by_session.get(name, ("", "", ""))
        web_session = session_store.find_by_tmux_name(name)
        sessions.append({
            "name": name,
            "windows": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1,
            "attached": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            "command": cmd,
            "cwd": cwd,
            "pane_id": pane_id,
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

    async with _get_attach_lock(tmux_name):
        return await _attach_tmux(tmux_name, cols, rows)


# open-on-mac 동시 클릭(더블클릭 등) 시 레이스로 중복 스폰되는 걸 막는 세션명 단위 락.
_mac_open_locks: set[str] = set()


@router.post("/api/tmux/open-on-mac")
async def open_tmux_on_mac(request: Request):
    """이미 존재하는 tmux 세션을 맥 터미널(iTerm2 우선)에 새 창으로 attach.

    웹에서 보고 있는 세션을 나중에 맥에서도 열고 싶을 때 사용. 같은 tmux 소켓(-L fsh)에
    attach하므로 웹/음성/맥이 동일 세션을 공유한다.

    맥에 이미 attach된 창이 있으면(세션 생성 시 auto_open_on_mac로 열어뒀거나, 이전 클릭으로
    이미 열어둔 채 웹에는 안 붙어 있는 "잠든" 세션 등) 또 열지 않고 스킵한다 — session_attached는
    웹 PTY attach도 함께 세므로, web_session_id로 웹쪽 attach 1개를 빼고 남는 게 있는지로 판단.
    """
    body = await request.json()
    tmux_name = (body.get("name") or "").strip()
    if not tmux_name or not re.fullmatch(r"[A-Za-z0-9_\-]+", tmux_name):
        return JSONResponse({"ok": False, "error": "유효하지 않은 세션 이름"}, status_code=400)
    if not platform_utils.IS_MACOS:
        return JSONResponse({"ok": False, "error": "서버가 macOS가 아님"}, status_code=400)

    if tmux_name in _mac_open_locks:
        return JSONResponse({"ok": False, "error": "이미 여는 중"}, status_code=409)
    _mac_open_locks.add(tmux_name)
    try:
        rc, _, _ = tmux_runner.run(["has-session", "-t", tmux_name], timeout=5.0)
        if rc != 0:
            return JSONResponse({"ok": False, "error": "tmux 세션을 찾을 수 없음"}, status_code=404)

        # display-message -t는 대상 세션이 없어도 attach된 클라이언트가 하나도 없으면
        # rc=0/빈 출력을 내는 tmux 특이 동작이 있어(has-session과 결과가 어긋남),
        # attached 수는 list-sessions 출력에서 이름으로 직접 파싱한다.
        sessions_text = tmux_runner.run_text(
            ["list-sessions", "-F", "#{session_name}\t#{session_attached}"], timeout=5.0
        ) or ""
        total_attached = 0
        for line in sessions_text.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0] == tmux_name:
                total_attached = int(parts[1]) if parts[1].isdigit() else 0
                break

        web_session = session_store.find_by_tmux_name(tmux_name)
        web_attached = 1 if web_session and web_session.session_id in pty_mgr.sessions else 0
        desktop_attached = total_attached - web_attached

        if desktop_attached > 0:
            return {"ok": True, "skipped": True, "error": None, "reason": "이미 맥에서 열려 있음"}

        attach_cmd = f"tmux -L {TMUX_SOCKET} attach -t {tmux_name}"
        try:
            ok = platform_utils.spawn_mac_terminal(attach_cmd)
            return {"ok": bool(ok), "skipped": False, "error": None if ok else "지원하는 터미널 앱이 없음"}
        except Exception as e:
            logger.warning(f"open-on-mac 실패: {e}")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        _mac_open_locks.discard(tmux_name)


@router.post("/api/tmux/attach")
async def attach_tmux_session(request: Request):
    body = await request.json()
    tmux_name = body.get("name", "")
    if not tmux_name:
        return {"error": "tmux session name required"}, 400

    cols = body.get("cols", 80)
    rows = body.get("rows", 24)

    async with _get_attach_lock(tmux_name):
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


# ── C1·C2: 멀티 클라이언트 화면 관리 ─────────────────────────────────────
# 웹 attach 1개 = `tmux attach-session` PTY 1개다. 여러 브라우저 + 맥북 iTerm2가
# 같은 세션에 붙으면 tmux가 **가장 작은 클라이언트**에 맞춰 계속 리레이아웃한다.
# `L2`(window-size latest)로 대부분 해소됐고, 여기는 "그래도 남은 화면을 정리하고
# 싶을 때"의 수단이다.
#
# 🔴 **클라이언트가 tty를 직접 보내게 하지 않는다.** 남의 tty를 보내 끊어버릴 수
# 있기 때문이다. 요청자는 자기 **web session id**만 보내고, 서버가 그것으로
# tty를 역산한다(pty_manager가 기록해둔 슬레이브 tty).

# tmux 세션 이름은 이미 이 파일의 다른 곳에서 같은 문자셋으로 검증한다
# (`re.fullmatch(r"[A-Za-z0-9_\-]+", ...)`). 인자로 셸을 타지는 않지만(subprocess
# 리스트 호출), 이름을 그대로 tmux에 넘기므로 옵션처럼 보이는 값(-t 등)을 막는다.
_CLIENT_SESSION_RE = re.compile(r"[A-Za-z0-9_\-.]{1,64}")


def _client_rows(session: str) -> list[dict]:
    fmt = "#{client_tty}\t#{client_name}\t#{client_width}\t#{client_height}\t#{client_activity}"
    text = tmux_runner.run_text(["list-clients", "-t", session, "-F", fmt], timeout=2.0) or ""
    rows = []
    for line in text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        rows.append({
            "tty": parts[0],
            "name": parts[1],
            "width": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
            "height": int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0,
            "activity": parts[4] if len(parts) > 4 else "",
        })
    return rows


def _tty_of_web_session(session_id: str | None) -> str | None:
    """web session id → 그 attach PTY의 슬레이브 tty."""
    if not session_id:
        return None
    sess = pty_mgr.sessions.get(session_id)
    tty = getattr(sess, "tty", "") if sess else ""
    return tty or None


def _label(row: dict, my_tty: str | None) -> str:
    """사람이 알아볼 라벨. tmux는 클라이언트 종류를 알려주지 않으므로 추정이다 —
    추정임이 드러나게 '웹(추정)' 같은 표기는 쓰지 않고, 확실한 것(나)만 단정한다."""
    if my_tty and row["tty"] == my_tty:
        return "이 화면"
    return row.get("name") or row["tty"]


@router.get("/api/tmux/clients")
async def tmux_clients(session: str, me: str | None = None):
    """세션에 붙어 있는 클라이언트 목록. `me`는 요청자의 web session id(선택)."""
    if not _CLIENT_SESSION_RE.fullmatch(session or ""):
        return JSONResponse({"error": "invalid session name"}, status_code=400)
    my_tty = _tty_of_web_session(me)
    rows = _client_rows(session)
    for r in rows:
        r["is_me"] = bool(my_tty and r["tty"] == my_tty)
        r["label"] = _label(r, my_tty)
    return {"session": session, "clients": rows, "me_tty": my_tty}


@router.post("/api/tmux/detach-client")
async def tmux_detach_client(request: Request):
    body = await request.json()
    tty = str(body.get("tty") or "")
    me = body.get("me")
    if not tty:
        return JSONResponse({"error": "tty required"}, status_code=400)
    # 지금 보고 있는 화면을 스스로 끊으면 복구 경로가 없다 — 400으로 막는다.
    if tty == _tty_of_web_session(me):
        return JSONResponse({"error": "cannot detach self", "reason": "지금 보고 있는 화면은 끊을 수 없습니다"}, status_code=400)
    tmux_runner.run(["detach-client", "-t", tty], timeout=2.0)
    return {"ok": True, "detached": tty}


@router.post("/api/tmux/clients/solo")
async def tmux_clients_solo(request: Request):
    """"이 화면만 남기기" — 요청자를 뺀 나머지 클라이언트를 전부 끊는다.

    맥북 iTerm2(로컬 tty)도 대상이다 — 그게 "맥북 창 닫기"의 실체다.
    """
    body = await request.json()
    session = str(body.get("session") or "")
    if not _CLIENT_SESSION_RE.fullmatch(session):
        return JSONResponse({"error": "invalid session name"}, status_code=400)
    keep = _tty_of_web_session(body.get("me"))
    if not keep:
        # 남길 화면을 특정하지 못하면 **아무것도 끊지 않는다**. 전부 끊고 나면
        # 되돌릴 방법이 없다.
        return JSONResponse({"error": "unknown client", "reason": "이 화면의 tty를 확인할 수 없습니다"}, status_code=400)

    detached = []
    for row in _client_rows(session):
        if row["tty"] == keep:
            continue
        # 이미 죽은 tty면 tmux가 에러를 내지만 무시한다(graceful).
        tmux_runner.run(["detach-client", "-t", row["tty"]], timeout=2.0)
        detached.append(row["tty"])
    # 남은 클라이언트 크기로 즉시 재동기화 — 안 하면 방금 사라진 작은 화면
    # 크기 그대로 한동안 남는다.
    tmux_runner.run(["refresh-client", "-t", keep], timeout=2.0)
    return {"ok": True, "kept": keep, "detached": detached}


# Phase 9 #1: ws push로 grid 1초 폴링 대체 — 변화 시에만 푸시.
@router.websocket("/ws-preview/{tmux_name}")
async def ws_preview(websocket: WebSocket, tmux_name: str):
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
