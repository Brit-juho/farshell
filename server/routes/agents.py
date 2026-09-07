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
