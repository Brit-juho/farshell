"""원격 호스트에서 들어오는 요청 (N7/N39 1단계) — `/api/peer/*`.

## 왜 별도 네임스페이스인가

기존 API(`/api/tmux`, `/api/files`, `/api/git/*` …)를 peer 토큰으로도 통과시키는
방식은 쓰지 않는다. 그러면 토큰 하나가 새는 순간 **서버 API 전체**가 열린다.
대신 peer가 닿을 수 있는 라우트를 처음부터 여기에만 만든다 — 이 파일에 없는 것은
peer가 아예 호출할 수 없다. `routes/files.py`의 "검사는 한 곳에서만"과 같은 원칙을
경로 설계 수준에서 적용한 것.

## 인증

`main.py`의 TokenAuthMiddleware는 `/api/peer/`를 우회한다(브라우저 세션 쿠키가
있을 리 없으므로). 그 대신 **이 파일이 서명 검증을 직접 한다**:

    X-Peer-Id / X-Peer-Ts / X-Peer-Nonce / X-Peer-Sig
    sig = HMAC(secret, "METHOD\\npath\\nts\\nnonce")

secret은 절대 전송되지 않는다. ts는 60초 창, nonce는 그 창 안에서 1회용(재생 차단).
서명 대상에 method/path가 들어가므로 view용 GET 서명을 control용 POST에 돌려쓸 수 없다.

## 전이(transitive) 금지

peer 응답에는 **내 로컬 것만** 담는다. 내가 등록한 다른 peer의 목록·세션은 절대
중계하지 않는다 — A↔B 상호 페어링에서 A→B→A 무한 재귀가 생기기 때문(hop 0 고정).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import host_store

logger = logging.getLogger(__name__)

router = APIRouter()

_nonces = host_store.NonceCache()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VERSION: str | None = None


def _version() -> str:
    global _VERSION
    if _VERSION is None:
        try:
            _VERSION = (_REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            _VERSION = ""
    return _VERSION


class PeerDenied(Exception):
    def __init__(self, reason: str, status: int = 401):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _authenticate_raw(method: str, path: str, headers) -> dict:
    """서명 검증 → grant. 실패는 PeerDenied. 실패도 전부 감사 로그에 남긴다.

    Request가 아니라 (method, path, headers)를 받는 이유: 3단계의 WebSocket
    엔드포인트도 **같은 서명 규칙**을 써야 하는데 WebSocket에는 Request가 없다.
    검증 로직이 둘로 갈리면 한쪽만 고쳐지는 날이 온다.
    """
    peer_id = headers.get("x-peer-id", "")
    ts_raw = headers.get("x-peer-ts", "")
    nonce = headers.get("x-peer-nonce", "")
    sig = headers.get("x-peer-sig", "")
    if not (peer_id and ts_raw and nonce and sig):
        raise PeerDenied("서명 헤더가 없습니다")
    try:
        ts = int(ts_raw)
    except ValueError:
        raise PeerDenied("잘못된 타임스탬프")

    grant = host_store.find_grant(peer_id)
    if grant is None:
        # 취소된 상대가 옛 secret으로 계속 두드리는 경우가 여기다 — 기록해둔다.
        host_store.audit(peer_id, "auth", False, "등록되지 않은 호스트")
        raise PeerDenied("등록되지 않은 호스트입니다")

    if not host_store.verify_signature(grant["secret"], method, path, ts, nonce, sig):
        host_store.audit(peer_id, "auth", False, "서명 불일치 또는 시간창 밖")
        raise PeerDenied("서명이 유효하지 않습니다 — 시계 차이가 크면 'fsh host ping'으로 확인하세요")

    # 재생 차단은 서명 검증을 통과한 뒤에 본다 — 그 전에 하면 아무나 nonce를
    # 채워 넣어 정상 요청을 막을 수 있다(캐시 오염).
    if not _nonces.check_and_add(f"{peer_id}:{nonce}"):
        host_store.audit(peer_id, "auth", False, "nonce 재사용(재생 시도)")
        raise PeerDenied("이미 사용된 요청입니다")

    host_store.touch_grant(peer_id)
    return grant


def _authenticate(request: Request) -> dict:
    return _authenticate_raw(request.method, request.url.path, request.headers)


def _require(request: Request, level: str = host_store.LEVEL_VIEW) -> tuple[dict | None, JSONResponse | None]:
    """(grant, 오류응답) — 오류응답이 None이 아니면 그대로 반환하면 된다."""
    try:
        grant = _authenticate(request)
    except PeerDenied as e:
        return None, JSONResponse({"error": "peer_denied", "reason": e.reason}, status_code=e.status)
    if level == host_store.LEVEL_CONTROL and grant.get("level") != host_store.LEVEL_CONTROL:
        host_store.audit(grant["id"], request.url.path, False, "control 등급 필요")
        return None, JSONResponse(
            {"error": "level_required", "reason":
             "이 호스트는 읽기 전용(view)으로 허용돼 있습니다 — 상대 맥에서 "
             "'fsh host allow-control <id>'로 켜야 합니다"},
            status_code=403,
        )
    return grant, None


# --- 페어링 (서명 없음 — 티켓 자체가 인증) --------------------------------------


@router.post("/api/peer/pair")
async def peer_pair(request: Request):
    """상대가 티켓을 제출하면 그 연결 전용 secret을 발급한다.

    이 엔드포인트만 서명 없이 열린다 — 아직 공유 secret이 없는 시점이기 때문.
    대신 1회용·5분 만료 티켓이 그 역할을 한다(QR 기기 등록과 같은 구조).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    ticket = str(body.get("ticket", ""))
    raw_id = str(body.get("id", ""))
    peer_id = host_store.normalize_id(raw_id)
    if peer_id is None:
        # `local` 등 예약어로 등록하려는 시도를 여기서 막는다 — 통과시키면
        # 로컬 호스트를 가로챈다.
        host_store.audit(raw_id or "?", "pair", False, "잘못되거나 예약된 호스트 id")
        return JSONResponse(
            {"error": "bad_id", "reason": f"쓸 수 없는 호스트 id입니다: {raw_id!r}"},
            status_code=400,
        )
    if host_store.consume_pair_ticket(ticket) is None:
        host_store.audit(peer_id, "pair", False, "티켓 무효/만료")
        return JSONResponse(
            {"error": "ticket_invalid",
             "reason": "티켓이 유효하지 않거나 만료됐습니다 — 'fsh host pair'를 다시 실행하세요"},
            status_code=401,
        )

    result = host_store.add_grant(
        peer_id, str(body.get("label", "")), version=str(body.get("version", "")),
    )
    if result is None:
        return JSONResponse({"error": "bad_id"}, status_code=400)
    grant, secret = result
    me = host_store.get_self()
    host_store.audit(peer_id, "pair", True, f"등급 {grant['level']}")
    logger.info(f"[peer] 페어링 완료: {peer_id} ({grant['label']}) 등급={grant['level']}")
    return {
        "ok": True,
        "secret": secret,
        "id": me["id"],
        "label": me["label"],
        "version": _version(),
        "serverTime": time.time(),
        "level": grant["level"],
    }


# --- ping (서명 필요) -------------------------------------------------------------


@router.get("/api/peer/sessions")
async def peer_sessions(request: Request):
    """이 호스트의 tmux 세션 + 에이전트 상태 (2단계, `view` 등급).

    **전이 금지(hop 0)**: 내가 등록한 다른 peer의 세션은 절대 섞지 않는다.
    A↔B 상호 페어링에서 A→B→A 무한 재귀가 생기기 때문 — 여기서는 언제나
    `agent_status.LOCAL_HOST` 엔트리만 본다.

    `cwd`를 함께 싣는 이유: 상대 화면이 "무슨 작업 중인 세션인가"를 보여주려면
    필요하다. 다만 이건 **경로 문자열 노출**이라, view 등급이 이미 세션 이름과
    출력까지 볼 수 있는 관계라는 전제 위에서만 정당하다(그보다 더 주지는 않는다).
    """
    grant, err = _require(request)
    if err:
        return err

    import agent_status
    import tmux_runner

    def _collect() -> list[dict]:
        fmt = "#{session_name}\t#{session_windows}\t#{session_attached}"
        text = tmux_runner.run_text(["list-sessions", "-F", fmt], timeout=2.0)
        if not text:
            return []
        panes = {}
        for p in tmux_runner.get_all_panes():
            panes.setdefault(p.session, p)
        out = []
        for line in text.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            name = parts[0]
            pane = panes.get(name)
            out.append({
                "name": name,
                "windows": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1,
                "attached": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                "command": pane.command if pane else "",
                "cwd": pane.path if pane else "",
                # 로컬 엔트리만 본다 — 이 값이 상대에게 건너가 그쪽에서 다시
                # host=<나>로 기록된다(A1의 host 차원이 여기서 쓰인다).
                "status": agent_status.status_for_session(name),
            })
        return out

    sessions = await asyncio.to_thread(_collect)
    host_store.audit(grant["id"], "sessions", True, f"{len(sessions)}건")
    me = host_store.get_self()
    return {"ok": True, "id": me["id"], "label": me["label"], "sessions": sessions}


@router.get("/api/peer/ping")
async def peer_ping(request: Request):
    """연결 확인 + 시계 동기 + 버전 교환. 1단계에서 유일한 인증 엔드포인트다.

    `serverTime`으로 상대가 시계 오차를 계산해 저장한다 — 그래야 다음 요청의
    서명이 60초 창 안에 들어온다.
    """
    grant, err = _require(request)
    if err:
        return err
    me = host_store.get_self()
    host_store.audit(grant["id"], "ping", True)
    return {
        "ok": True,
        "id": me["id"],
        "label": me["label"],
        "version": _version(),
        "serverTime": time.time(),
        "level": grant["level"],
    }


# --- 3단계: 입력(control) · 출력 스트림 -------------------------------------------
#
# 여기부터가 "원격 pane을 실제로 조작"하는 부분이다. 두 가지 원칙을 지킨다:
#
# 1. **입력은 control 등급에서만.** 서명이 method+path에 묶여 있으므로 view용
#    GET 서명을 이 POST/WS에 돌려쓸 수 없다 — 등급 검사와 서명 검사가 서로를
#    보강한다.
# 2. **PTY를 소유한 호스트만 알린다.** 이 PTY는 여기(B)에 있으므로 출력 감시·
#    푸시 알림·스크롤백 영속화는 전부 B의 설정을 따른다. 프록시 쪽(A)은
#    아무것도 기록하지 않는다 — 그래야 알림이 두 번 가지 않는다.
#    다만 **이 연결은 output_watcher에 등록하지 않는다**: 같은 tmux 세션을
#    B의 사용자가 이미 자기 탭으로 보고 있으면 그쪽이 이미 감시 중이고,
#    여기서 또 등록하면 완료 알림이 두 번 울린다.


@router.post("/api/peer/input")
async def peer_input(request: Request):
    """원격 세션에 텍스트를 넣는다(control 등급).

    기본은 **Enter 없이 타이핑**이다(`POST /api/files/{id}/insert`와 같은 계약) —
    아직 완성 안 된 명령을 대신 실행시키지 않기 위해서다. `enter: true`면 Enter까지
    누른다: 큐 투입(A3)은 "실행돼야 하는 지시"라 Enter가 없으면 프롬프트에 영원히
    떠 있게 된다. 어느 쪽인지 **호출부가 명시**하게 하고 서버가 추측하지 않는다.
    """
    grant, err = _require(request, host_store.LEVEL_CONTROL)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    session = str(body.get("session", "")).strip()
    data = body.get("data")
    enter = bool(body.get("enter", False))
    if not session or not isinstance(data, str) or not data:
        return JSONResponse({"error": "bad_request", "reason": "session/data가 필요합니다"}, status_code=400)

    import tmux_target

    pane = tmux_target.session_pane(session)
    if not pane:
        host_store.audit(grant["id"], "input", False, f"세션 없음: {session}")
        return JSONResponse({"error": "session_not_found"}, status_code=404)
    send = tmux_target.send_to_tmux if enter else tmux_target.type_to_tmux
    ok = await asyncio.to_thread(send, pane, data)
    host_store.audit(grant["id"], "input", bool(ok),
                     f"{session} {len(data)}자{' +Enter' if enter else ''}")
    if not ok:
        return JSONResponse({"error": "input_failed"}, status_code=500)
    return {"ok": True}


@router.websocket("/api/peer/ws/{tmux_name}")
async def peer_ws(ws: WebSocket, tmux_name: str):
    """원격 pane의 출력 스트림(+ control이면 입력). 상대 서버(A)의 프록시가 연결한다.

    브라우저가 직접 여는 소켓이 아니다 — 서명 헤더를 붙일 수 있는 건 서버뿐이다
    (브라우저 WebSocket API는 커스텀 헤더를 못 보낸다). 그게 이 구조가 서버-서버인
    이유이기도 하다.

    수명: 이 연결 전용 PTY를 만들고 끊길 때 정리한다. B의 사용자가 자기 탭으로
    보고 있는 PTY를 공유하지 않는 이유는 크기(resize) 때문이다 — 두 화면의 크기가
    다르면 한쪽이 계속 찌그러진다. tmux가 같은 세션에 여러 클라이언트를 붙이는
    것을 이미 지원하므로 PTY를 따로 두는 게 맞다.
    """
    try:
        grant = _authenticate_raw("GET", ws.url.path, ws.headers)
    except PeerDenied as e:
        # accept 전에 닫으면 상대는 HTTP 403을 받는다 — 이유를 실을 수 없어
        # 일단 받아들인 뒤 코드와 함께 닫는다.
        await ws.accept()
        await ws.close(code=4401, reason=e.reason[:120])
        return

    import tmux_runner
    from routes.tmux import TMUX_SOCKET
    from deps import pty_mgr
    import platform_utils

    if not tmux_runner.has_session(tmux_name):
        await ws.accept()
        host_store.audit(grant["id"], "ws", False, f"세션 없음: {tmux_name}")
        await ws.close(code=4404, reason="tmux session not found")
        return

    await ws.accept()
    can_control = grant.get("level") == host_store.LEVEL_CONTROL
    session_id = f"peer-{grant['id']}-{time.time_ns()}"
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=2048)

    def _on_data(data: bytes) -> None:
        # PTY 리더 스레드에서 불린다 — 큐에 넣기만 하고 전송은 아래 태스크가 한다.
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(data)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    pty_mgr.create_session(
        session_id,
        cmd=platform_utils.find_tmux(),
        cmd_args=["tmux", "-L", TMUX_SOCKET, "attach-session", "-t", tmux_name],
        cols=80, rows=24,
    )
    pty_mgr.subscribe(session_id, _on_data)
    host_store.audit(grant["id"], "ws", True, f"{tmux_name} 등급 {grant.get('level')}")

    async def _pump_out():
        while True:
            data = await queue.get()
            await ws.send_bytes(data)

    out_task = asyncio.create_task(_pump_out())
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                if can_control:
                    pty_mgr.write(session_id, msg["bytes"])
                continue
            text = msg.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except ValueError:
                continue
            if payload.get("type") == "resize":
                # 크기는 등급과 무관하게 받는다 — 보기만 하는 화면도 자기 크기에
                # 맞게 그려져야 한다(입력이 아니다).
                pty_mgr.resize(session_id, int(payload.get("cols", 80)), int(payload.get("rows", 24)))
            elif payload.get("type") == "input" and can_control:
                pty_mgr.write(session_id, str(payload.get("data", "")).encode())
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001 — 어떤 이유로든 연결이 끝나면 정리가 우선
        logger.debug(f"[peer ws] {e}")
    finally:
        out_task.cancel()
        pty_mgr.unsubscribe(session_id, _on_data)
        pty_mgr.destroy_session(session_id)
        host_store.audit(grant["id"], "ws", True, f"{tmux_name} 종료")
