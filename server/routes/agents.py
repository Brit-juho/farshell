"""AI agent 감지 + Pre/PostToolUse 훅 상태 + WS 브로드캐스트."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

import agent_detector
import agent_status
import pane_resolve

# Phase 9 #5: heartbeat (pty.py와 동일 정책)
_HB_INTERVAL = float(os.environ.get("VT_WS_HEARTBEAT_INTERVAL", "15.0"))
_HB_TIMEOUT = float(os.environ.get("VT_WS_HEARTBEAT_TIMEOUT", "45.0"))

logger = logging.getLogger(__name__)

router = APIRouter()

# WebSocket 클라이언트 집합 (모듈 수준 — 라우터 전체가 공유)
_agent_event_clients: set[WebSocket] = set()


@router.get("/api/agents")
async def list_agents():
    return agent_detector.detect_all()


# N9/N45(80-multihost-agents.md §2) — CLI마다 waiting을 실제로 얼마나 정확히
# 잡을 수 있는지 사용자가 스스로 판단할 근거. 코드가 codex/gemini/aider의
# 실제 프롬프트 문구를 추측해서 채우는 건 금지(문서가 명시) — 이 엔드포인트는
# "지금 detect/*.toml에 뭐가 있는지"만 정직하게 보고한다.
#
# ⚠ 라우트 순서: 아래 `/api/agents/{tmux_name}`보다 **먼저** 등록해야 한다.
# Starlette는 등록 순서대로 매치를 시도하므로, {tmux_name}이 먼저면
# "/api/agents/coverage" 요청의 tmux_name이 문자열 "coverage"로 해석되어
# 이 라우트에 절대 도달하지 못한다.
KNOWN_CLIS = ("claude", "codex", "aider", "gemini")  # bin/fsh `agent <name>`과 동일 목록


def _coverage_trust(path_kind: str, states: list[str]) -> str:
    """등급 임의 기준 — 문서(80 §2)는 값만 요구하고 구체적 임계값은 위임했다.

    high — 훅(가장 정확한 신호: Claude Code pre/stop 이벤트)이 걸려 있고 PTY
           패턴 감지(waiting)까지 겹친다 — 두 경로가 서로 보강한다.
    mid  — PTY 패턴 감지만 있다(출력 grep 하나뿐이라 오탐·누락 가능성이 있다).
    low  — 아무 감지도 없다(toml이 비어 있거나 파일 자체가 없다) — 레일이
           이 CLI의 승인 대기를 절대 띄우지 못한다.

    toml 포맷이 지금은 enter/exit/options뿐이라 states는 최대 1개
    (["waiting"])다. 포맷이 확장돼 states가 여러 개(working/done 등)를 표현하게
    되면, 그때는 개수 기반으로 세분화한다(예: 3개 이상+hook=high).
    """
    if path_kind == "hook" and states:
        return "high"
    if states:
        return "mid"
    return "low"


@router.get("/api/agents/coverage")
async def agent_coverage():
    """CLI별 승인 대기 감지 커버리지 — 설정 → 에이전트 탭이 그린다.

    `path`는 Claude Code에 한해서만 "hook"일 수 있다(다른 CLI는 훅 개념
    자체가 없다 — codex/aider/gemini는 `fsh pane report`로 직접 보고할 뿐).
    """
    import agent_prompt_detect as detect
    import claude_hooks

    hook_ok = False
    try:
        settings = claude_hooks.load_settings(claude_hooks.settings_path())
        plan = claude_hooks.plan(settings)
        hook_ok = bool(plan) and all(state == "ok" for state, _ in plan.values())
    except ValueError:
        hook_ok = False  # settings.json이 깨져 있어도 커버리지 표는 떠야 한다

    # force=True — 방금 고친 toml이 캐시 때문에 안 보이면 "줄 수를 반영한다"는
    # 수용 기준을 못 지킨다. 이 표는 자주 열리는 화면이 아니라 매번 다시
    # 읽어도 비용이 작다(파일 몇 개, KB 단위).
    parsed = detect.load_patterns(force=True)

    detect_dir = detect.DETECT_DIR
    files = {p.stem: p for p in detect_dir.glob("*.toml")} if detect_dir.is_dir() else {}
    clis = sorted(set(KNOWN_CLIS) | set(files))

    rows = []
    for cli in clis:
        path_obj = files.get(cli)
        if path_obj is not None:
            try:
                pattern_lines = len(path_obj.read_text().splitlines())
            except OSError:
                pattern_lines = 0
        else:
            pattern_lines = 0  # toml 파일 자체가 없다

        spec = parsed.get(cli)
        has_patterns = bool(spec and (spec.get("enter") or spec.get("exit")))
        # 상태 키가 toml 포맷에 아직 명시적으로 없다 — enter/exit 쌍이 있으면
        # 감지 가능한 상태는 "waiting" 하나뿐. 포맷이 확장되면 여기도 확장한다.
        states = ["waiting"] if has_patterns else []

        if cli == "claude" and hook_ok:
            path_kind = "hook"
        elif has_patterns:
            path_kind = "pty"
        else:
            path_kind = "none"

        rows.append({
            "cli": cli,
            "path": path_kind,
            "patternLines": pattern_lines,
            "states": states,
            "trust": _coverage_trust(path_kind, states),
        })
    return rows


@router.get("/api/agents/{tmux_name}")
async def get_agent(tmux_name: str):
    info = agent_detector.detect(tmux_name)
    return info or {"agent": None}


@router.post("/api/agent/event")
async def agent_event(request: Request):
    # `request` 에 타입 annotation이 없으면 FastAPI가 이걸 '필수 쿼리 파라미터'로 읽어
    # 모든 요청이 422로 떨어진다. 함수 안의 `from fastapi import Request` 는 시그니처에
    # 아무 영향이 없어서, agent_hook.sh 가 보내는 이벤트가 줄곧 조용히 실패하고 있었다.
    try:
        body = await request.json()
    except Exception:
        body = {}
    event = body.get("event", "stop")
    payload = body.get("payload", {})

    # A2: 훅이 자기보고한 pane id를 1차 근거로, cwd를 폴백으로 세션을 특정한다.
    # 둘 다 실패하면 session=None — "모호하면 아무것도 강조하지 않는다"는
    # 기존 규칙 그대로다(엉뚱한 카드를 켜는 것보다 안전하다).
    session, how = pane_resolve.resolve(
        body.get("pane"), body.get("tmux"), payload.get("cwd")
    )
    if how == "foreign-tmux":
        logger.debug("훅이 우리 소켓이 아닌 tmux에서 왔다 — pane id 폐기")

    state = agent_status.on_event(event, payload, session=session)

    # P4: 작업이 끝났다는 가장 정확한 신호가 stop 훅이다. 여기서 큐를 한 건 흘린다.
    # 유예 시간(VT_QUEUE_GRACE_SEC)은 queue_runner가 둔다 — 사용자가 곧바로
    # 직접 타이핑을 시작했을 수 있으므로 즉시 밀어 넣지 않는다.
    # A1: "stop 이벤트"라는 문자열이 아니라 **done 전이**를 트리거로 삼는다.
    # 상태 판정이 서버로 옮겨온 이상, 큐도 같은 판정을 봐야 한다(나중에 done에
    # 이르는 경로가 하나 더 생겨도 큐 쪽을 또 고칠 필요가 없다).
    queued = False
    if (state or {}).get("status") == agent_status.DONE:
        try:
            import queue_runner
            import tmux_target
            # state는 on_event(stop)이 pop 직전에 건져준 {"cwd": ...} — 그리드 뷰와
            # 같은 방식으로 cwd를 세션 이름으로 특정해, 그 세션 몫 항목만 흘려보낸다.
            # A2 이후로는 3단 해석 결과를 그대로 쓴다 — cwd 추측(같은 cwd
            # 세션 둘이면 항상 None)보다 정확하다. 이번 이벤트가 특정하지
            # 못했으면 엔트리에 남아 있던 값(pre 때 pane id로 정한 것)을 쓴다.
            target = session or (state or {}).get("tmux_session")
            if not target:
                cwd = (state or {}).get("cwd")
                target = tmux_target.session_for_cwd(cwd) if cwd else None
            queued = queue_runner.schedule_drain(session=target, session_scoped=True)
        except Exception as e:                       # 큐 문제로 훅 응답이 깨지면 안 된다
            logger.warning(f"큐 드레인 예약 실패: {e}")

    msg = {"type": "agent_event", "event": event, "state": state, "resolved_by": how}
    dead = set()
    for ws in list(_agent_event_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _agent_event_clients.difference_update(dead)

    return {"ok": True, "state": state, "queue_scheduled": queued}


@router.post("/api/agent/report")
async def agent_report(request: Request):
    """A2 — pane 자기보고 (`fsh pane report`).

    Claude Code는 훅이 있지만 codex/aider/gemini는 없다. 그 pane들이 스스로
    "나 지금 working이야"를 알릴 수 있는 유일한 경로다. pane id는 호출자가
    `$TMUX_PANE`으로 실어 보내고, 서버는 훅과 **같은 3단 해석**을 태운다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    state = str(body.get("state", "working"))
    session, how = pane_resolve.resolve(
        body.get("pane"), body.get("tmux"), body.get("cwd")
    )
    # 키는 세션 이름이 가장 안정적이다 — 같은 pane에서 여러 번 보고해도 한
    # 엔트리로 모이고, 훅 기반 엔트리(session_id 키)와도 섞이지 않는다.
    sid = body.get("session_id") or (f"pane:{session}" if session else f"pane:{body.get('pane') or 'unknown'}")
    try:
        ent = agent_status.report(
            sid, state, session=session, cwd=body.get("cwd"), agent=body.get("agent")
        )
    except ValueError as e:
        return {"ok": False, "error": "bad_state", "reason": str(e)}

    msg = {"type": "agent_event", "event": "report", "state": ent, "resolved_by": how}
    dead = set()
    for ws in list(_agent_event_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _agent_event_clients.difference_update(dead)
    return {"ok": True, "session": session, "resolved_by": how, "state": ent}


@router.get("/api/agent/status")
async def agent_status_get():
    # 기존 필드(active/all)는 그대로 둔다 — 하위호환. A1에서 각 엔트리에
    # status가 추가됐고, 소비자는 그걸 그대로 쓰면 된다(파생 금지).
    return {"active": agent_status.all_active(), "all": agent_status.get_state()}


@router.websocket("/ws-agent")
async def ws_agent(websocket: WebSocket):
    # codex review fix: VT_TOKEN 보호
    from routes.pty import _ws_auth
    if not _ws_auth(websocket):
        await websocket.close(code=4001)
        return
    await websocket.accept()
    _agent_event_clients.add(websocket)
    loop = asyncio.get_running_loop()
    last_pong = loop.time()

    async def _hb():
        nonlocal last_pong
        while True:
            await asyncio.sleep(_HB_INTERVAL)
            if loop.time() - last_pong > _HB_TIMEOUT:
                try:
                    await websocket.close(code=1001, reason="heartbeat timeout")
                except Exception:
                    pass
                return
            try:
                await websocket.send_text(json.dumps({"type": "ping"}))
                # A6 검증에서 발견한 갭: TTL 만료(working 15분→idle, waiting
                # 2분→working 등)는 sweeper가 **서버 안에서 조용히** 일으키는
                # 전이라 어떤 이벤트도 발생하지 않는다. 그래서 클라이언트는
                # 다음 훅 이벤트가 올 때까지 만료된 상태를 계속 그리고 있었다
                # (실제로 화면은 waiting, 서버는 working인 상태를 재현했다).
                # 하트비트마다 상태 스냅샷을 함께 실어 최대 지연을 한 주기로
                # 묶는다 — get_state()가 sweep()을 태우므로 조회 없는 서버에서도
                # 만료가 제때 도는 부수 효과가 있다. detect_all()(프로세스 스캔)은
                # 여기 넣지 않는다(비싸다 — 그건 agents_change 때만).
                await websocket.send_json({
                    "type": "agent_status_sync",
                    "all": agent_status.get_state(),
                })
            except Exception:
                return

    hb_task = asyncio.create_task(_hb())
    try:
        # snapshot: active state + 현재 detect 결과 (frontend가 폴링 안 해도 즉시 반영)
        await websocket.send_json({
            "type": "agent_snapshot",
            "active": agent_status.all_active(),
            # A1: 재접속·새로고침 시 done/waiting까지 복원되려면 active(=도구
            # 실행 중)만으론 부족하다 — 상태를 가진 엔트리 전체를 함께 보낸다.
            "all": agent_status.get_state(),
            "agents": agent_detector.detect_all(),
        })
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if msg.get("type") == "pong":
                last_pong = loop.time()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hb_task.cancel()
        _agent_event_clients.discard(websocket)
