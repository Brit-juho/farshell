"""tmux 세션 CRUD + 라이브 프리뷰."""

from __future__ import annotations

import asyncio
import logging
import os
import re

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse

import platform_utils
import session_names
import tmux_runner
from deps import pty_mgr, session_store, output_watcher
from session_store import new_session_id
from i18n import tr
from resilience import pools as res_pools

logger = logging.getLogger(__name__)

router = APIRouter()

TMUX_SOCKET = tmux_runner.VT_TMUX_SOCKET


# 이름 고르기 → new-session 사이를 한 덩어리로 묶는다. 스니펫 「새 세션에서 실행」을
# 연달아 눌러도(같은 이름 요청 두 개) 서로 다른 번호가 나가야 한다.
_create_lock = asyncio.Lock()


# --------------------------------------------------------------------------
# 내부 헬퍼
# --------------------------------------------------------------------------

def _device_label_for_request(request: Request | None) -> str | None:
    """vt_session 쿠키 → 등록된 기기의 별명(설정에서 사용자가 바꿀 수 있음).

    「연결된 화면」이 tmux client_name(대개 tty 경로 그대로라 서로 구별이 안 됨)
    대신 보여줄 라벨이다. 비보호 서버(토큰 없음)거나 기기 등록이 없으면 None —
    이 경우 호출부는 기존 client_name/tty 폴백으로 조용히 떨어진다.
    """
    if request is None:
        return None
    try:
        import auth
        from auth import devices as device_store

        device_id = auth.session_device(request.cookies.get("vt_session", ""))
        if not device_id:
            return None
        for d in device_store.list_devices():
            if d.get("id") == device_id:
                return d.get("label") or None
    except Exception:
        pass
    return None


_SCREEN_RE = re.compile(r"[0-9a-f]{32}")


async def _attach_tmux(tmux_name: str, cols: int, rows: int, request: Request | None = None,
                       screen_id: str | None = None, catalog_binding: dict | None = None):
    # 존재하지 않는 tmux 세션에 attach하면 `tmux attach-session`이 즉시 실패하지만,
    # 그 전에 유령 web 세션(PTY)이 이미 생성·등록돼 /api/sessions에 남는다. 클라이언트는
    # 그 id로 WS를 열었다가 죽은 세션에 재연결을 반복하고, restoreWorkspace가 stale 탭마다
    # 이 유령 세션을 만들어 로드 즉시 세션·터미널·WS가 무더기로 쌓여 메모리가 폭증했다.
    # → PTY를 만들기 전에 세션 존재를 확인하고, 없으면 404로 돌려보내 복원 루틴이 건너뛰게 한다.
    if catalog_binding:
        from session_catalog.inventory import resolve_runtime
        from session_catalog.db import Refused
        try:
            runtime, physical = await asyncio.to_thread(resolve_runtime,catalog_binding['session_id'])
            if runtime['id'] != catalog_binding['runtime_id']:
                raise Refused('runtime_changed')
            catalog_binding = {**catalog_binding,'tmux_id':physical['tmux_id']}
        except Refused as error:
            return JSONResponse({'error':error.code},status_code=error.status)
    target = catalog_binding["tmux_id"] if catalog_binding else tmux_name
    state = await tmux_runner.session_state_async(target)
    if state == tmux_runner.SESSION_MISSING:
        return JSONResponse(
            {"error": "tmux session not found", "tmux_session": tmux_name},
            status_code=404,
        )
    if state == tmux_runner.SESSION_UNKNOWN:
        # 확인 못 한 것을 「없음」(404)으로 답하면 복원 중인 화면이 그 칸을 비워 저장한다.
        # 503은 클라이언트가 재시도 대상으로 다룬다(term/workspace.js restoreRequest).
        return JSONResponse(
            {"error": "tmux state unknown", "tmux_session": tmux_name},
            status_code=503,
        )
    session_id = new_session_id()
    pty_mgr.create_session(
        session_id,
        cmd=platform_utils.find_tmux(),
        cmd_args=["tmux", "-L", TMUX_SOCKET, "attach-session", "-t", target],
        cols=cols,
        rows=rows,
    )
    session_store.add(session_id, name=tmux_name, device_label=_device_label_for_request(request),
                      screen_id=screen_id)
    session_store.update_tmux_name(session_id, tmux_name)
    if catalog_binding:
        info = session_store.get(session_id)
        info.catalog_session_id = catalog_binding['session_id']
        info.runtime_id = catalog_binding['runtime_id']
        info.catalog_host = catalog_binding.get('host')
    output_watcher.add_session(session_id)
    return {"id": session_id, "name": tmux_name, "tmux_session": tmux_name,
            **({"catalog_session_id": catalog_binding['session_id'], "runtime_id": catalog_binding['runtime_id'], "catalog_host": catalog_binding.get('host')} if catalog_binding else {})}


async def revive_tmux_attach(info) -> bool:
    """잠든 브라우저의 WS가 돌아오면 같은 웹 ID에 새 tmux 클라이언트를 붙인다.

    호출자는 tmux 이름별 attach 락을 잡아야 한다.
    """
    if not info.tmux_name or not info.screen_id:
        return False
    if info.session_id in pty_mgr.sessions:
        return True
    target = info.tmux_name
    if info.catalog_session_id:
        from session_catalog.inventory import resolve_runtime
        from session_catalog.db import Refused
        try:
            runtime, physical = await asyncio.to_thread(resolve_runtime, info.catalog_session_id)
            if runtime['id'] != info.runtime_id:
                return False
            target = physical['tmux_id']
        except Refused:
            return False
    if not await tmux_runner.has_session_async(target):
        return False
    pty_mgr.create_session(
        info.session_id, cmd=platform_utils.find_tmux(),
        cmd_args=["tmux", "-L", TMUX_SOCKET, "attach-session", "-t", target],
    )
    output_watcher.add_session(info.session_id)
    return True


async def attach_screen(tmux_name: str, cols: int, rows: int, request: Request,
                        screen_id: str, catalog_binding: dict | None = None):
    """같은 화면은 복구하고 다른 화면에는 새 PTY를 준다. attach 락 안에서 호출."""
    existing = session_store.find_by_tmux_and_screen(tmux_name, screen_id)
    if existing:
        if catalog_binding and existing.runtime_id != catalog_binding['runtime_id']:
            return JSONResponse({"error":"runtime_changed"},status_code=409)
        if existing.session_id in pty_mgr.sessions or await revive_tmux_attach(existing):
            return {"id": existing.session_id, "name": existing.name, "tmux_session": tmux_name,
                    **({"catalog_session_id": existing.catalog_session_id, "runtime_id": existing.runtime_id, "catalog_host": existing.catalog_host} if existing.catalog_session_id else {})}
        session_store.remove(existing.session_id)
        output_watcher.remove_session(existing.session_id)
    return await _attach_tmux(tmux_name, cols, rows, request, screen_id, catalog_binding)


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
    return await collect_tmux_sessions()


def _kick_sessions() -> None:
    """세션 목록이 바뀌었을 수 있다 — `/ws-workspace` 감시에 바로 확인하라고 알린다."""
    try:
        import routes.system as system_routes
        system_routes.kick_sessions()
    except Exception:  # noqa: BLE001 — 알림 실패는 다음 주기가 메운다
        pass


async def collect_tmux_sessions() -> list[dict]:
    """GET /api/tmux/sessions와 `/ws-workspace`의 `tmux_sessions` push가 같은 값을
    내도록 한 곳에서 만든다."""
    # 2.1 D2: `@fsh_wt`도 같이 받는다 — 세션이 어느 워크트리 소속인지, cwd
    # 추측이 아니라 tmux에 적힌 사실로 답한다(subprocess 호출 수는 그대로다).
    # 마지막 두 칸(session_id·session_created)은 위임 장부의 세션 키다(delegation.py) — 계보 표시용.
    fmt_sessions = "#{session_name}\t#{session_windows}\t#{session_attached}\t#{@fsh_wt}\t#{@fsh_origin}\t#{@fsh_share_nick}\t#{@fsh_share_mode}\t#{session_id}\t#{session_created}\t#{@fsh_origin_host}\t#{@fsh_profile}"
    # A2: pane_id를 함께 받아 응답에 싣는다 — 프런트가 "이 카드가 그 pane인가"를
    # cwd 추측이 아니라 id로 판정할 수 있게(같은 cwd 세션 둘 문제의 해소).
    fmt_panes = "#{session_name}\t#{pane_current_command}\t#{pane_current_path}\t#{pane_id}\t#{window_active}\t#{pane_active}"

    sessions_text = await tmux_runner.run_text_async(["list-sessions", "-F", fmt_sessions], timeout=2.0)
    if not sessions_text:
        return []

    panes_text = await tmux_runner.run_text_async(["list-panes", "-a", "-F", fmt_panes], timeout=2.0) or ""
    pane_by_session: dict[str, tuple[str, str, str]] = {}
    pane_ranks: dict[str, int] = {}
    for line in panes_text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        # 첫 pane이 아니라 활성 window의 활성 pane을 대표로 선택한다.
        rank = (2 if len(parts) > 4 and parts[4] == "1" else 0) + (1 if len(parts) > 5 and parts[5] == "1" else 0)
        if len(parts) >= 2 and (parts[0] not in pane_by_session or rank > pane_ranks[parts[0]]):
            pane_ranks[parts[0]] = rank
            pane_by_session[parts[0]] = (
                parts[1],
                parts[2] if len(parts) > 2 else "",
                parts[3] if len(parts) > 3 else "",
            )

    from session_context import contexts_for
    import agent_status
    # 위치·브랜치는 터미널(셸)이 아니라 그 세션의 에이전트 CLI 기준이다. CLI가 알린 cwd가 있으면
    # 그걸로 Git을 읽고, 없거나 못 읽으면 pane cwd로 되돌아간다(에이전트 없는 세션은 그대로).
    agent_cwd = {name: agent_status.agent_cwd_for(name) for name in pane_by_session}
    contexts = await contexts_for(["", *(p[1] for p in pane_by_session.values()),
                                   *(c for c in agent_cwd.values() if c)])
    live_ptys = pty_mgr.sessions  # 복사본이라 한 번만 받는다
    keys: dict[str, str] = {}
    for line in sessions_text.strip().split("\n"):
        p = line.split("\t")
        if len(p) > 8 and p[8].isdigit():
            keys[f"{p[7]}:{int(p[8])}"] = p[0]
    lineage = await asyncio.to_thread(_lineage_by_name, keys)
    # 생성 때 고른 계정(`@fsh_profile`) — 태그가 있는 세션이 하나라도 있을 때만 레지스트리를 읽어 이름을 붙인다.
    tags = {line.split("\t")[10] for line in sessions_text.strip().split("\n")
            if len(line.split("\t")) > 10 and line.split("\t")[10]}
    profile_names = {}
    if tags:
        import session_profile
        for tag in tags:
            profile_names[tag] = await asyncio.to_thread(session_profile.describe, tag)
    sessions = []
    for line in sessions_text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        name = parts[0]
        cmd, cwd, pane_id = pane_by_session.get(name, ("", "", ""))
        web_sessions = [info for info in session_store.find_all_by_tmux_name(name)
                        if info.session_id in live_ptys]
        web_session = web_sessions[0] if web_sessions else None
        attached = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        context_cwd, context_source = cwd, "terminal"
        a_cwd = agent_cwd.get(name)
        if a_cwd and a_cwd != cwd and contexts[a_cwd].get("status") != "unavailable":
            context_cwd, context_source = a_cwd, "agent"
        sessions.append({
            "name": name,
            "physical_key": f"{parts[7]}:{parts[8]}" if len(parts) > 8 else None,
            "windows": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1,
            "attached": attached,
            # 웹 화면 = 이 서버가 PTY를 쥔 attach(브라우저 탭·폰 하나가 1개). 나머지는 웹이 아닌 attach
            # (맥 터미널 등). 추가 tmux 호출 없이 session_store와 같은 list-sessions 결과로 센다.
            "webScreens": len(web_sessions),
            "otherClients": max(0, attached - len(web_sessions)),
            "command": cmd,
            "cwd": cwd,
            "context": contexts[context_cwd],
            # context가 읽은 위치와 그 출처. "agent"면 `cwd`(터미널 pane)와 다르다 — 화면은
            # 에이전트 기준으로 그리고 터미널 위치는 툴팁에만 둔다.
            "contextCwd": context_cwd,
            "contextSource": context_source,
            "pane_id": pane_id,
            "web_session_id": web_session.session_id if web_session else None,
            "wt_id": parts[3] if len(parts) > 3 else "",
            # 만든 곳(local/peer/agent). 기록이 없으면 external — 같은 list-sessions 호출에서 읽는다.
            "origin": tmux_runner.normalize_origin(parts[4] if len(parts) > 4 else ""),
            # peer가 만든 세션이면 만든 맥의 표시 이름(`@fsh_origin_host`). 기록 없음·peer 아님이면 빈 문자열(키는 늘 있다).
            "originHost": tmux_runner.origin_host_of(
                tmux_runner.normalize_origin(parts[4] if len(parts) > 4 else ""), parts[9] if len(parts) > 9 else ""),
            # 에이전트 세션 공유 표시(`@fsh_share_*`) — 같은 list-sessions 호출에서 읽는다. 공유가 아니면 null.
            "share": tmux_runner.parse_share(parts[5] if len(parts) > 5 else "", parts[6] if len(parts) > 6 else ""),
            # 위임으로 만든 하위 세션이면 그 계보(장부 기준 — tmux 옵션은 에이전트가 바꿀 수 있어 안 쓴다).
            "lineage": lineage.get(name),
            # FarShell이 만들 때 넣은 계정(생성 시 기록 — 셸에서 바꾼 환경은 반영 안 됨). 없으면 null.
            # `profile`은 첫 번째(Claude 우선), `profiles`는 전부(기본 계정이 도구별로 둘일 수 있다).
            "profile": (profile_names.get(parts[10]) or [None])[0] if len(parts) > 10 and parts[10] else None,
            "profiles": profile_names.get(parts[10], []) if len(parts) > 10 and parts[10] else [],
        })
    return sessions


def _lineage_by_name(keys: dict[str, str]) -> dict[str, dict]:
    """{세션 키: 이름} → {자식 세션 이름: 계보}. 장부를 못 읽으면 빈 값(레일은 평평하게 그린다)."""
    try:
        import delegation
        from session_catalog.identity import visible_ledger
        items = visible_ledger(delegation.list_all())
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for x in sorted(items, key=lambda x: x.get("created_at", 0)):
        child = keys.get(x["child"]["key"])
        if not child or x.get("state") == "starting":
            continue
        summary = x.get("summary") or ""
        out[child] = {
            "delegation": x["id"],
            "parent": keys.get(x["parent"]["key"]),     # 부모 세션이 사라졌으면 None
            "state": x.get("state"),
            "outcome": x.get("outcome"),
            "createdAt": x.get("created_at"),
            "task": x.get("task_preview", "")[:200],
            "summary": summary[:600] + ("…" if len(summary) > 600 else ""),
        }
    return out




@router.post("/api/tmux/create")
async def create_tmux_session(request: Request):
    body = await request.json()
    screen_id = body.get("screen") or None
    if screen_id is not None and not _SCREEN_RE.fullmatch(str(screen_id)):
        return JSONResponse({"error": "invalid screen id"}, status_code=400)
    # Wave 1 W1-2: 사용자 지정 이름 없으면 cwd 기반 디폴트
    requested_name = body.get("name", "").strip()
    # `unique_name`: 이름이 이미 있으면 에러 대신 `이름-1`, `이름-2`로 비켜 간다(스니펫).
    unique_name = bool(body.get("unique_name"))
    cols = body.get("cols", 80)
    rows = body.get("rows", 24)
    auto_open = bool(body.get("auto_open_on_mac", False))
    # 이름을 그대로 쓰는 요청(unique_name 아님)은 계정 선택 여부와 상관없이 앱의 세션 이름 규칙을 지켜야 한다 —
    # tmux는 `-s` 값을 서식으로 확장해서 `#(...)`가 셸 명령으로 실행된다. 이 라우트는 기계 토큰도 부를 수 있다.
    if requested_name and not unique_name and not tmux_runner.valid_session_name(requested_name):
        return JSONResponse({"error": "invalid_name", "reason": tr("세션 이름은 영문자·숫자·_·-만 쓸 수 있습니다(64자까지, 첫 글자는 -가 아님)")}, status_code=400)

    # N6(60 §4) 스니펫 「새 섹션」 — 원 세션과 같은 cwd로 새 세션을 열 때 씀.
    # fsguard(코드 뷰어 열람 경계)는 여기 적용하지 않는다: tmux 세션은 그냥 셸이고,
    # 이미 인증된 사용자는 그 안에서 어디로든 cd할 수 있다 — cwd를 미리 지정할 수
    # 있게 해준다고 새로운 권한이 생기는 게 아니다. 존재하는 절대경로인지만 본다.
    requested_cwd = str(body.get("cwd") or "").strip()
    if requested_cwd:
        # isdir 도 그 경로에 닿는다 — 원격 마운트일 수 있으니 `fs` 풀에서 기한을 둔다.
        try:
            is_dir = os.path.isabs(requested_cwd) and await res_pools.run(
                "fs", os.path.isdir, requested_cwd, op="tmux.create.cwd", key=requested_cwd, timeout=5)
        except (res_pools.OpTimeout, res_pools.Busy):
            is_dir = False
        if not is_dir:
            return JSONResponse({"error": tr("cwd가 존재하는 절대경로가 아닙니다")}, status_code=400)
        start_dir = requested_cwd
    else:
        # 새 tmux 세션 시작 디렉토리 — 서버 cwd(프로젝트 폴더) 대신 홈/VT_START_DIR.
        start_dir = platform_utils.default_start_dir()
    # 계정 선택(핫스왑 H1): 필드가 없으면 지금 그대로(주입 없음). 있으면 사람 로그인 세션만 — 기계 토큰·peer·
    # 소켓·에이전트는 계정을 고를 수 없다. 고른 계정을 못 쓰면 기본 ~/.claude로 대체하지 않고 거절한다.
    profile_ref = body.get("profile")
    selection = None
    launch_info: dict = {}
    if profile_ref is not None:
        import auth
        auth.require_human_session(request)
        if not isinstance(profile_ref, str):
            return JSONResponse({"error": "invalid_profile", "reason": tr("프로필 값이 올바르지 않습니다")}, status_code=400)
        if profile_ref != "none":
            # 계정을 주입하는 생성은 tmux 설정 파일(`source-file`)을 거친다 — tmux는 `-s` 값을 서식으로 확장하므로
            # (`#{session_id}` 등) 앱의 세션 이름 규칙 밖 이름은 세션이 남기 전에 거절한다. 크기도 정수 범위만.
            for dimension in (cols, rows):
                if type(dimension) is not int or not 1 <= dimension <= 1000:
                    return JSONResponse({"error": "invalid_dimensions", "reason": tr("cols·rows는 1에서 1000 사이의 정수여야 합니다")}, status_code=400)
            import session_profile
            try:
                selection = await asyncio.to_thread(session_profile.select, profile_ref)
            except session_profile.SlotRefused as error:
                return JSONResponse({"error": error.code, "reason": error.message}, status_code=error.status)
            except Exception:  # noqa: BLE001 — 사유 문구에 내부 값이 섞이지 않게 고정 문구만 돌려준다
                logger.warning("profile selection failed", exc_info=False)
                return JSONResponse({"error": "profile_unavailable", "reason": tr("프로필을 확인하지 못했습니다")}, status_code=409)

    def _launch_with_profile(n, c, r, d):
        import session_profile
        try:
            result = session_profile.spawn(n, c, r, d, selection)
        except session_profile.SlotRefused as error:   # 토큰이 그 사이 사라짐 등 — 세션을 만들지 않는다
            launch_info["refused"] = error
            return 1, "", {}
        launch_info.update(result[2])
        return result

    async with _create_lock:
        # 이름 짓기는 session_names 한 곳이다(시작 폴더의 저장소 이름, 겹치면 -2·-3). 서버 cwd는 쓰지 않는다.
        auto_base = None
        if requested_name and not unique_name:
            tmux_name = requested_name
        else:
            # unique_name(스니펫): 규칙 밖 글자를 `-`로 바꾸고, 남는 게 없으면(한글만 등) 시작 폴더 기준.
            auto_base = session_names.slug(requested_name) if requested_name else ""
            if not auto_base:
                auto_base = await asyncio.to_thread(session_names.base_for, start_dir)
            tmux_name = await asyncio.to_thread(session_names.allocate, auto_base)
        catalog_binding = None
        if body.get('operation_id') is not None:
            from session_catalog.lifecycle import create as catalog_create
            from session_catalog.db import Refused
            try:
                spec={'name':requested_name,'unique_name':unique_name}
                if selection:
                    spec['profile']=profile_ref
                catalog_binding = await asyncio.to_thread(catalog_create,body['operation_id'],tmux_name,start_dir,cols,rows,request_spec=spec,launcher=_launch_with_profile if selection else None)
                tmux_name = catalog_binding['name']
                rc,err = 0,b''
            except Refused as error:
                if launch_info.get("refused"):
                    return JSONResponse({"error": launch_info["refused"].code, "reason": launch_info["refused"].message}, status_code=launch_info["refused"].status)
                return JSONResponse({'error':error.code},status_code=error.status)
        elif selection:
            rc, _reason, _info = await asyncio.to_thread(_launch_with_profile, tmux_name, cols, rows, start_dir)
            err = b""
        else:
            def _spawn(candidate):
                code, _, stderr = tmux_runner.run(
                    ["new-session", "-d", "-s", candidate, "-x", str(cols), "-y", str(rows), "-c", start_dir],
                    timeout=5.0,
                )
                return code, stderr
            if auto_base:
                # 이름을 고른 뒤 만들기 전에 다른 프로세스(`bin/fsh`, 직접 띄운 tmux)가 같은 이름을 가져가도 다음 번호로 간다.
                rc, tmux_name, err = await asyncio.to_thread(session_names.spawn_unique, auto_base, _spawn, start=tmux_name)
            else:
                rc, err = await asyncio.to_thread(_spawn, tmux_name)
    if rc != 0:
        if launch_info.get("refused"):
            return JSONResponse({"error": launch_info["refused"].code, "reason": launch_info["refused"].message}, status_code=launch_info["refused"].status)
        return JSONResponse(
            {"error": "tmux session create failed", "detail": err.decode("utf-8", errors="ignore")},
            status_code=500,
        )

    # 만든 곳 기록 — 이 화면의 새 세션. 값은 서버가 정한다(요청 본문의 origin은 무시).
    await tmux_runner.set_origin_async(tmux_name, "local")

    if selection and "cleared_env" in launch_info:
        # 어떤 계정으로 만들었는지 레지스트리에도 남긴다(태그 `@fsh_profile`은 spawn이 이미 달았다). 기록 실패가 세션을 막지 않는다.
        import session_profile
        try:
            await asyncio.to_thread(session_profile.record, tmux_name, selection)
        except Exception:  # noqa: BLE001
            logger.warning("profile session record failed", exc_info=False)

    # 2.1 D2 그루드워크 — 이 세션이 어느 워크트리에서 열렸는지 아는 호출자(스니펫
    # 「새 섹션」, 워크트리 화면 등)는 태그를 같이 보낼 수 있다. 지금은 어느
    # 프런트도 이 필드를 안 보내지만, 받아둔다고 기존 호출을 깨지 않는다 —
    # `worktree.py`의 wt_id와 같은 sha1[:12] 모양만 받는다.
    worktree_id = str(body.get("worktreeId") or "").strip()
    if worktree_id and re.fullmatch(r"[0-9a-f]{12}", worktree_id):
        await tmux_runner.set_option_async(tmux_name, "@fsh_wt", worktree_id)

    if auto_open and platform_utils.IS_MACOS and re.fullmatch(r"[A-Za-z0-9_\-]+", tmux_name):
        attach_cmd = f"tmux -L {TMUX_SOCKET} attach -t {tmux_name}"
        try:
            # osascript(최대 8초)라 루프 위에서 부르지 않는다.
            ok = await res_pools.run("proc", platform_utils.spawn_mac_terminal, attach_cmd,
                                     op="spawn_mac_terminal", timeout=15)
            if not ok:
                logger.info("맥 터미널 자동 오픈 — 지원 앱 없음")
        except Exception as e:
            logger.warning(f"맥 터미널 자동 오픈 실패: {e}")

    # session-created 훅(tmux_hooks.py)은 new-session 순간에 온다 — 위에서 붙인
    # 워크트리 표시(@fsh_wt)는 그 뒤라 옵션 변화엔 훅이 없어 여기서 한 번 더 알린다.
    _kick_sessions()
    async with _get_attach_lock(tmux_name):
        if screen_id:
            result = await attach_screen(tmux_name, cols, rows, request, screen_id, catalog_binding)
        elif catalog_binding:
            result = await _attach_tmux(tmux_name, cols, rows, request,catalog_binding=catalog_binding)
        else:
            result = await _attach_tmux(tmux_name, cols, rows, request)
    if selection and isinstance(result, dict):
        # 무엇이 주입됐는지 응답에도 싣는다(이름만 — 경로·토큰 없음). cleared_env는 지운 위험 변수의 이름.
        result = {**result, "profile": selection.public()[0], "profiles": selection.public(),
                  "cleared_env": launch_info.get("cleared_env", [])}
    return result


# open-on-mac 동시 클릭(더블클릭 등) 시 레이스로 중복 스폰되는 걸 막는 세션명 단위 락.
_mac_open_locks: set[str] = set()


@router.post("/api/tmux/open-on-mac")
async def open_tmux_on_mac(request: Request):
    """이미 존재하는 tmux 세션을 맥 터미널(VT_TERMINAL로 고른 앱, 기본 자동)에 새 창으로 attach.

    웹에서 보고 있는 세션을 나중에 맥에서도 열고 싶을 때 사용. 같은 tmux 소켓(-L fsh)에
    attach하므로 웹/음성/맥이 동일 세션을 공유한다.

    맥에 이미 attach된 창이 있으면(세션 생성 시 auto_open_on_mac로 열어뒀거나, 이전 클릭으로
    이미 열어둔 채 웹에는 안 붙어 있는 "잠든" 세션 등) 또 열지 않고 스킵한다 — session_attached는
    웹 PTY attach도 함께 세므로, web_session_id로 웹쪽 attach 1개를 빼고 남는 게 있는지로 판단.
    """
    body = await request.json()
    tmux_name = (body.get("name") or "").strip()
    if not tmux_name or not re.fullmatch(r"[A-Za-z0-9_\-]+", tmux_name):
        return JSONResponse({"ok": False, "error": tr("유효하지 않은 세션 이름")}, status_code=400)
    if not platform_utils.IS_MACOS:
        return JSONResponse({"ok": False, "error": tr("서버가 macOS가 아님")}, status_code=400)

    if tmux_name in _mac_open_locks:
        return JSONResponse({"ok": False, "error": tr("이미 여는 중")}, status_code=409)
    _mac_open_locks.add(tmux_name)
    try:
        rc, _, _ = await tmux_runner.run_async(["has-session", "-t", tmux_runner.exact_target(tmux_name)], timeout=5.0)
        if rc != 0:
            return JSONResponse({"ok": False, "error": tr("tmux 세션을 찾을 수 없음")}, status_code=404)

        # display-message -t는 대상 세션이 없어도 attach된 클라이언트가 하나도 없으면
        # rc=0/빈 출력을 내는 tmux 특이 동작이 있어(has-session과 결과가 어긋남),
        # attached 수는 list-sessions 출력에서 이름으로 직접 파싱한다.
        sessions_text = await tmux_runner.run_text_async(
            ["list-sessions", "-F", "#{session_name}\t#{session_attached}"], timeout=5.0
        ) or ""
        total_attached = 0
        for line in sessions_text.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0] == tmux_name:
                total_attached = int(parts[1]) if parts[1].isdigit() else 0
                break

        web_attached = sum(info.session_id in pty_mgr.sessions
                           for info in session_store.find_all_by_tmux_name(tmux_name))
        desktop_attached = total_attached - web_attached

        if desktop_attached > 0:
            return {"ok": True, "skipped": True, "error": None, "reason": tr("이미 맥에서 열려 있음")}

        attach_cmd = f"tmux -L {TMUX_SOCKET} attach -t {tmux_name}"
        try:
            ok = await res_pools.run("proc", platform_utils.spawn_mac_terminal, attach_cmd,
                                     op="spawn_mac_terminal", timeout=15)
            return {"ok": bool(ok), "skipped": False, "error": None if ok else tr("지원하는 터미널 앱이 없음")}
        except Exception as e:
            logger.warning(f"open-on-mac 실패: {e}")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        _mac_open_locks.discard(tmux_name)


def _catalog_host_id():
    from session_catalog.db import database, host_id
    with database() as db:
        return host_id(db)


@router.post("/api/tmux/attach")
async def attach_tmux_session(request: Request):
    body = await request.json()
    tmux_name = body.get("name", "")
    if not tmux_name:
        return {"error": "tmux session name required"}, 400

    cols = body.get("cols", 80)
    rows = body.get("rows", 24)
    screen_id = body.get("screen") or None
    if screen_id is not None and not _SCREEN_RE.fullmatch(str(screen_id)):
        return JSONResponse({"error": "invalid screen id"}, status_code=400)

    catalog_binding = None
    if body.get('catalog_session_id') is not None:
        from session_catalog import inventory as catalog_inventory
        from session_catalog.db import identifier, host_id, database, Refused
        try:
            catalog_id = identifier(body.get('catalog_session_id'))
            expected_runtime = identifier(body.get('runtime_id'))
            # sqlite + flock(합쳐 최대 약 4초) — 루프 밖에서.
            catalog_host = await asyncio.to_thread(_catalog_host_id)
            if body.get('catalog_host') is not None and identifier(body['catalog_host']) != catalog_host:
                raise Refused('host_changed')
            runtime, physical = await asyncio.to_thread(catalog_inventory.resolve_runtime, catalog_id)
            if runtime['id'] != expected_runtime:
                raise Refused('runtime_changed')
            tmux_name = physical['name']
            catalog_binding = {'session_id':catalog_id,'runtime_id':runtime['id'],'tmux_id':physical['tmux_id'],'host':catalog_host}
        except Refused as error:
            return JSONResponse({'error':error.code},status_code=error.status)
    async with _get_attach_lock(tmux_name):
        if screen_id:
            return await attach_screen(tmux_name, cols, rows, request, screen_id, catalog_binding)
        existing = session_store.find_by_tmux_name(tmux_name)
        if existing:
            if existing.session_id in pty_mgr.sessions or await revive_tmux_attach(existing):
                return {"id": existing.session_id, "name": existing.name, "tmux_session": tmux_name,
                    **({"catalog_session_id": existing.catalog_session_id, "runtime_id": existing.runtime_id, "catalog_host": existing.catalog_host} if existing.catalog_session_id else {})}
            session_store.remove(existing.session_id)
            output_watcher.remove_session(existing.session_id)
        result = await _attach_tmux(tmux_name, cols, rows, request, catalog_binding=catalog_binding)
    # client-attached 훅(tmux_hooks.py)도 오지만, 붙는 도중의 await 사이에 웹 세션
    # 등록이 훅보다 늦을 수 있어(web_session_id가 빈 채 push) 여기서도 알린다.
    _kick_sessions()   # attached 수·web_session_id가 바뀐다
    return result


@router.delete("/api/tmux/kill/{tmux_name}")
async def kill_tmux_session(tmux_name: str):
    for existing in session_store.find_all_by_tmux_name(tmux_name):
        from deps import _auto_responder
        pty_mgr.destroy_session(existing.session_id)
        session_store.remove(existing.session_id)
        output_watcher.remove_session(existing.session_id)
        _auto_responder.remove(existing.session_id)  # 세션별 상태 정리 (누수 방지)
    # 실제 tmux 세션 종료 — 이 줄이 빠져 있어 rc가 정의되지 않은 채 참조돼 500이 났고,
    # 세션이 전혀 kill되지 않았다.
    rc, _, _ = await tmux_runner.run_async(["kill-session", "-t", tmux_name], timeout=5.0)
    if rc != 0:
        return JSONResponse({"error": "tmux kill failed", "name": tmux_name}, status_code=500)
    return {"ok": True}


@router.get("/api/tmux/preview/{tmux_name}")
async def get_tmux_preview(tmux_name: str, lines: int = 20, ansi: int = 1):
    import preview
    try:
        content = await res_pools.run("proc", preview.capture_pane, tmux_name, lines=lines, ansi=bool(ansi),
                                      op="tmux.preview", key=tmux_name, timeout=5)
    except (res_pools.OpTimeout, res_pools.Busy):
        content = None
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


def _known_web_device_labels() -> dict[str, str]:
    """tty → 등록 기기 라벨. 나뿐 아니라 지금 붙어 있는 모든 웹 세션에 대해 구한다.

    이게 없으면 나 아닌 다른 웹 탭은 tmux client_name(대개 tty 경로 그대로라 서로
    구별이 안 됨)으로만 보인다. 여기서 확실히 아는 것(그 tty가 어느 등록 기기의
    web attach인지)만 채운다 — 매칭 안 되는 tty(맥북 iTerm2 등 웹을 안 거친 진짜
    외부 클라이언트)는 손대지 않고 호출부가 기존 client_name/tty로 폴백한다.
    """
    labels: dict[str, str] = {}
    for info in session_store.list_all():
        if not info.device_label:
            continue
        tty = _tty_of_web_session(info.session_id)
        if tty:
            labels[tty] = info.device_label
    return labels


def _label(row: dict, my_tty: str | None, device_labels: dict[str, str] | None = None) -> str:
    """사람이 알아볼 라벨. tmux는 클라이언트 종류를 알려주지 않으므로 추정이다 —
    추정임이 드러나게 '웹(추정)' 같은 표기는 쓰지 않고, 확실한 것(나·등록 기기)만 단정한다."""
    if my_tty and row["tty"] == my_tty:
        return tr("이 화면")
    if device_labels:
        label = device_labels.get(row["tty"])
        if label:
            return label
    return row.get("name") or row["tty"]


@router.get("/api/tmux/clients")
async def tmux_clients(session: str, me: str | None = None):
    """세션에 붙어 있는 클라이언트 목록. `me`는 요청자의 web session id(선택)."""
    if not _CLIENT_SESSION_RE.fullmatch(session or ""):
        return JSONResponse({"error": "invalid session name"}, status_code=400)
    my_tty = _tty_of_web_session(me)
    device_labels = _known_web_device_labels()
    # 블로킹은 _client_rows 안의 tmux 호출이다 — 헬퍼째로 스레드에 넘긴다.
    rows = await asyncio.to_thread(_client_rows, session)
    for r in rows:
        r["is_me"] = bool(my_tty and r["tty"] == my_tty)
        r["label"] = _label(r, my_tty, device_labels)
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
        return JSONResponse({"error": "cannot detach self", "reason": tr("지금 보고 있는 화면은 끊을 수 없습니다")}, status_code=400)
    await tmux_runner.run_async(["detach-client", "-t", tty], timeout=2.0)
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
        return JSONResponse({"error": "unknown client", "reason": tr("이 화면의 tty를 확인할 수 없습니다")}, status_code=400)

    # tmux 호출이 여러 번이라 한 덩어리로 스레드에 넘긴다(routes/peer.py의 _solo와 같은 관용구).
    def _solo() -> list[str]:
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
        return detached

    detached = await asyncio.to_thread(_solo)
    return {"ok": True, "kept": keep, "detached": detached}


# Phase 9 #1: ws push로 grid 1초 폴링 대체 — 변화 시에만 푸시.
@router.websocket("/ws-preview/{tmux_name}")
async def ws_preview(websocket: WebSocket, tmux_name: str):
    import json as _json
    import preview as _preview
    from fastapi import WebSocketDisconnect
    # codex review fix: VT_TOKEN 환경에서 인증 없는 미리보기 접근 차단
    from routes.pty import _ws_auth_token
    import auth
    token = _ws_auth_token(websocket)
    if token is None:
        await websocket.close(code=4001)
        return
    await websocket.accept()
    session_watchdog = auth.spawn_session_watchdog(websocket, token)

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
        session_watchdog.cancel()
        _preview.unsubscribe(tmux_name, _send)
