"""AI agent 감지 + Pre/PostToolUse 훅 상태 + WS 브로드캐스트."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

import agent_detector
import agent_status

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

    state = agent_status.on_event(event, payload)

    # P4: 작업이 끝났다는 가장 정확한 신호가 stop 훅이다. 여기서 큐를 한 건 흘린다.
    # 유예 시간(VT_QUEUE_GRACE_SEC)은 queue_runner가 둔다 — 사용자가 곧바로
    # 직접 타이핑을 시작했을 수 있으므로 즉시 밀어 넣지 않는다.
    queued = False
    if event == "stop":
        try:
            import queue_runner
            import tmux_target
            # state는 on_event(stop)이 pop 직전에 건져준 {"cwd": ...} — 그리드 뷰와
            # 같은 방식으로 cwd를 세션 이름으로 특정해, 그 세션 몫 항목만 흘려보낸다.
            cwd = (state or {}).get("cwd")
            session = tmux_target.session_for_cwd(cwd) if cwd else None
            queued = queue_runner.schedule_drain(session=session, session_scoped=True)
        except Exception as e:                       # 큐 문제로 훅 응답이 깨지면 안 된다
            logger.warning(f"큐 드레인 예약 실패: {e}")

    msg = {"type": "agent_event", "event": event, "state": state}
    dead = set()
    for ws in list(_agent_event_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _agent_event_clients.difference_update(dead)

    return {"ok": True, "state": state, "queue_scheduled": queued}


@router.get("/api/agent/status")
async def agent_status_get():
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
            except Exception:
                return

    hb_task = asyncio.create_task(_hb())
    try:
        # snapshot: active state + 현재 detect 결과 (frontend가 폴링 안 해도 즉시 반영)
        await websocket.send_json({
            "type": "agent_snapshot",
            "active": agent_status.all_active(),
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
