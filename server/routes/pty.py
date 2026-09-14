"""PTY 세션 CRUD + WebSocket 터미널 + 파일 업로드/다운로드."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import auth
import crypto_channel
import file_store
import scrollback_persist
import tmux_runner
from deps import pty_mgr, session_store, output_watcher, _auto_responder, _prompt_detector
from session_store import new_session_id

logger = logging.getLogger(__name__)

router = APIRouter()

# Phase 8 G2: 연결 한도 + 백프레셔 + 하트비트
WS_MAX_PER_SESSION = int(os.environ.get("VT_WS_MAX_PER_SESSION", "8"))
WS_MAX_TOTAL = int(os.environ.get("VT_WS_MAX_TOTAL", "32"))
WS_HEARTBEAT_INTERVAL = float(os.environ.get("VT_WS_HEARTBEAT_INTERVAL", "15.0"))
WS_HEARTBEAT_TIMEOUT = float(os.environ.get("VT_WS_HEARTBEAT_TIMEOUT", "45.0"))
WS_QUEUE_HIGH = 200
WS_QUEUE_LOW = 50

# scrollback replay 종료 표식 — send_queue는 보통 bytes만 나르지만, 이 객체가
# 큐에서 나오면 _send_worker가 bytes 대신 JSON 텍스트 메시지로 클라이언트에
# "여기까지가 재생분이다"를 알린다. 클라이언트는 이 신호를 받기 전까지 OSC52
# 클립보드 동기화(selection.js)를 건너뛴다 — 안 그러면 재접속마다 세션 도중
# 쌓인 과거 OSC52 시퀀스가 scrollback과 함께 통째로 재생되며 그때마다 다시
# 발화해 "새로고침하면 클립보드 동기화 토스트가 한꺼번에 여러 개" 뜨는 버그가 났다.
_SCROLLBACK_END = object()

import deps as _deps  # 전역 카운터 직접 수정용

def _ws_auth_token(ws: WebSocket) -> Optional[str]:
    """WS 인증: HTTP 미들웨어와 동일한 다중 소스(cookie/query/Bearer)를 수용.

    서명 세션 쿠키(사람) 또는 기계 토큰(데몬)을 auth.check_request로 판정한다.
    통과한 실제 토큰 문자열을 반환한다(호출부가 연결 유지 중 재검사할 수
    있도록 — `auth._ws_session_watchdog` 참고). 인증이 아예 꺼져 있으면(비밀번호도
    기계 토큰도 없는 로컬 전용 환경) 빈 문자열 ""을 반환한다 — 재검사할 대상이
    없다는 뜻. 실패하면 None.
    """
    if not auth.is_protected():
        return ""
    # 1) HttpOnly 세션 쿠키 (/api/auth 후)
    cookie = ws.cookies.get("vt_session", "")
    if auth.check_request(cookie):
        return cookie
    # 2) query string (QR/URL 기계 토큰)
    q = ws.query_params.get("token", "")
    if auth.check_request(q):
        return q
    # 3) Authorization: Bearer (데몬)
    auth_hdr = ws.headers.get("authorization", "")
    if auth_hdr.startswith("Bearer "):
        bearer = auth_hdr[7:]
        if auth.check_request(bearer):
            return bearer
    return None


def _ws_auth(ws: WebSocket) -> bool:
    """`_ws_auth_token`의 불리언 판정만 필요한 호출부용."""
    return _ws_auth_token(ws) is not None


# --------------------------------------------------------------------------
# PTY 세션 CRUD
# --------------------------------------------------------------------------

@router.api_route("/api/sessions", methods=["GET", "HEAD"])
async def list_sessions():
    # tmux가 `detach-on-destroy off`면 세션이 kill돼도 web의 attach 클라이언트가 다른
    # 세션으로 전환되어 살아남아 PTY가 EOF되지 않는다 → 죽은 tmux를 가리키는 web 세션이
    # 목록·메모리(PTY·scrollback)·클라이언트 터미널로 계속 쌓인다. 여기서 실제 tmux 존재를
    # 검증해 좀비 세션을 정리하고, 살아있는 것만 반환한다.
    live_tmux_names = {p.session for p in tmux_runner.get_all_panes()}
    result = []
    for s in list(pty_mgr.sessions.values()):
        info = session_store.get(s.session_id)
        tmux_name = info.tmux_name if info else None
        if tmux_name and tmux_name not in live_tmux_names:
            pty_mgr.destroy_session(s.session_id)
            session_store.remove(s.session_id)
            output_watcher.remove_session(s.session_id)
            continue
        result.append({
            "id": s.session_id,
            "name": info.name if info else s.session_id,
            "cols": s.cols,
            "rows": s.rows,
            # 클라이언트가 "이 세션 맥에서 열기" 등 tmux 전용 기능을 판단하는 데 필요.
            # 예전엔 이 필드가 없어서 페이지 로드 시 복원된 세션은 항상 "tmux 아님"으로
            # 오판됐다(sessions[id].tmuxName을 채울 소스 자체가 없었음).
            "tmux_name": tmux_name,
        })
    return result


@router.post("/api/sessions")
async def create_session(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    cols = body.get("cols", 80)
    rows = body.get("rows", 24)
    name = body.get("name", "")
    session_id = new_session_id()
    pty_mgr.create_session(session_id, cols=cols, rows=rows)
    session_store.add(session_id, name=name)
    output_watcher.add_session(session_id)
    return {"id": session_id, "name": name or session_id}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    info = session_store.get(session_id)
    tmux_name = info.tmux_name if info else None
    pty_mgr.destroy_session(session_id)
    session_store.remove(session_id)
    output_watcher.remove_session(session_id)
    _auto_responder.remove(session_id)  # 세션별 윈도우 dict 정리 (누수 방지)
    _prompt_detector.remove(session_id)
    return {"ok": True, "tmux_detached": tmux_name}


@router.get("/api/sessions/{session_id}/scrollback")
async def session_scrollback(session_id: str, before: Optional[int] = Query(None), limit: int = Query(64 * 1024)):
    """N13(80-multihost-agents.md §3) — 영속 스크롤백 「더 불러오기」.

    WS 재접속 시 자동 복원되는 인메모리 scrollback(최근 256KB, pty_manager)과는
    다른 경로다. 이건 scrollback.persist가 켜져 있을 때만 쌓인 디스크 로그를
    역방향(최신 → 과거)으로 페이지네이션해서 읽는다. persist가 꺼져 있었으면
    로그 자체가 없어 빈 결과가 나간다 — 별도 에러가 아니라 "더 없음"과 같은 모양.
    """
    limit = max(1, min(limit, 1024 * 1024))
    result = await asyncio.to_thread(scrollback_persist.read_before, session_id, before, limit)
    return {
        "data_b64": base64.b64encode(result["data"]).decode("ascii"),
        "next_before": result["next_before"],
        "total": result["total"],
    }


@router.get("/api/scrollback/usage")
async def scrollback_usage():
    return {
        "enabled": scrollback_persist.is_enabled(),
        "bytes": await asyncio.to_thread(scrollback_persist.disk_usage_bytes),
        "retention_days": scrollback_persist.RETENTION_DAYS,
    }


@router.post("/api/sessions/{session_id}/keys")
async def send_keys(session_id: str, request: Request):
    """N38(70-mobile.md §2) — 모바일 플릿 홈의 인라인 승인 버튼이 부르는 경로.

    `{"text": "1\\r"}` 형태로 받아 그대로 PTY에 쓴다 — WS 입력 경로(위 ws_pty의
    bytes 분기)와 동급 권한이라 신규 엔드포인트지만 승격은 요구하지 않는다
    (터미널에 타이핑하는 것과 다를 바 없다, ADR-22의 승격 요구는 git 쓰기
    한정). 인증은 TokenAuthMiddleware가 이 경로도 이미 보호한다(예외 목록에
    없음) — 여기서 별도 처리 없음.
    """
    if session_id not in pty_mgr.sessions:
        return JSONResponse({"error": "not_found"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    text = body.get("text")
    if not isinstance(text, str) or not text:
        return JSONResponse({"error": "bad_request", "reason": "text required"}, status_code=400)
    try:
        # WS 입력 경로와 같은 해제 판정: 이 세션에 뭔가 써 넣는 것 자체가
        # "사람이 답했다"는 가장 확실한 신호다(§2: 버튼도 답이다).
        _prompt_detector.on_user_input(session_id)
        pty_mgr.write(session_id, text.encode())
    except ValueError:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"ok": True}


@router.post("/api/sessions/{session_id}/paste")
async def paste_session(session_id: str, request: Request):
    """N24(2.1.5 2/n) — 붙여넣기 전용 HTTP 경로. WS의 `{"type":"paste"}`와
    같은 함수를 부른다(`/keys`가 WS bytes 프레임과 짝인 것과 같은 관계).
    WS가 닫힌 사이 붙여넣는 경우(모바일 백그라운드 복귀 등)를 위한 대체 경로.
    """
    if session_id not in pty_mgr.sessions:
        return JSONResponse({"error": "not_found"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    text = body.get("text")
    if not isinstance(text, str) or not text:
        return JSONResponse({"error": "bad_request", "reason": "text required"}, status_code=400)
    try:
        _prompt_detector.on_user_input(session_id)
        info = session_store.get(session_id)
        pty_mgr.paste(session_id, text, is_tmux=bool(info and info.tmux_name),
                      tmux_name=info.tmux_name if info else None)
    except ValueError:
        return JSONResponse({"error": "not_found"}, status_code=404)
    except RuntimeError as e:
        # tmux paste-buffer 실패 — 조용히 삼키지 않는다(계획서 4-4 위험 3).
        return JSONResponse({"error": "paste_failed", "reason": str(e)}, status_code=502)
    return {"ok": True}


@router.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, request: Request):
    body = await request.json()
    name = body.get("name", "").strip()
    info = session_store.get(session_id)
    if not info:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if not name:
        return {"id": session_id, "name": info.name, "tmux_name": info.tmux_name}

    # Wave 1 W1-1: tmux 세션 이름도 같이 변경 (이전엔 메타데이터만 변경)
    tmux_renamed = False
    warning = None
    if info.tmux_name and info.tmux_name != name:
        import re
        import tmux_runner
        # tmux 세션명 안전 문자 검증 (영숫자, dash, underscore만 허용)
        if re.fullmatch(r"[A-Za-z0-9_\-]+", name):
            # 충돌 검사
            if tmux_runner.has_session(name):
                return JSONResponse(
                    {"error": "tmux session name already exists", "name": name},
                    status_code=409,
                )
            rc, _, err = tmux_runner.run(
                ["rename-session", "-t", info.tmux_name, name],
                timeout=2.0,
            )
            if rc != 0:
                return JSONResponse(
                    {"error": "tmux rename-session failed", "detail": err.decode("utf-8", errors="ignore")},
                    status_code=500,
                )
            session_store.update_tmux_name(session_id, name)
            tmux_renamed = True
        else:
            # 안전하지 않은 문자 포함 — tmux는 안 건드리고 메타데이터만 변경 + 경고 명시
            warning = (
                "tmux 세션 이름은 변경되지 않음 — 영숫자/dash/underscore만 허용. "
                "웹 라벨만 변경됨."
            )
            logger.warning(f"rename {session_id}: unsafe chars in '{name}' — tmux unchanged")
    info.name = name
    resp = {"id": session_id, "name": info.name, "tmux_name": info.tmux_name, "tmux_renamed": tmux_renamed}
    if warning:
        resp["warning"] = warning
    return resp


# --------------------------------------------------------------------------
# 파일 업로드 (다운로드는 routes/files.py의 /api/files/{id}/download — N19)
# --------------------------------------------------------------------------

@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...), session_id: str = Query("")):
    # 저장소는 file_store가 관리(0700 디렉토리 + id 기반 실경로). 여기서는 스트리밍
    # 수신 + 크기 상한만 담당하고, 다 받은 뒤 file_store.add_from_upload로 편입한다.
    file_store.files_dir().mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(file_store.files_dir(), 0o700)
    except OSError:
        pass
    tmp = file_store.files_dir() / f".tmp-{uuid.uuid4().hex}"
    # 청크 단위로 받으면서 상한을 건다. 예전엔 await file.read()로 전체를 메모리에
    # 올려서 큰 파일 하나로 서버(=내 맥)를 OOM으로 밀어낼 수 있었다.
    size = 0
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > file_store.MAX_UPLOAD_BYTES:
                    out.close()
                    tmp.unlink(missing_ok=True)
                    return JSONResponse(
                        {"error": "file too large", "max_mb": file_store.MAX_UPLOAD_BYTES // (1024 * 1024)},
                        status_code=413,
                    )
                out.write(chunk)
    except OSError as e:
        logger.warning(f"upload 실패: {e}")
        tmp.unlink(missing_ok=True)
        return JSONResponse({"error": "write failed"}, status_code=500)
    item = file_store.add_from_upload(tmp, file.filename or "", size, session=session_id or None)
    return {"ok": True, "id": item["id"], "path": str(file_store.real_path_for(item["id"])), "size": size}


# --------------------------------------------------------------------------
# WebSocket 터미널
# --------------------------------------------------------------------------

async def _safe_send(ws: WebSocket, data: bytes) -> None:
    try:
        await ws.send_bytes(data)
    except Exception:
        pass


@router.websocket("/ws/{session_id}")
async def ws_terminal(ws: WebSocket, session_id: str):
    _auth_token = _ws_auth_token(ws)
    if _auth_token is None:
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()

    if session_id not in pty_mgr.sessions:
        await ws.close(code=4004, reason="Session not found")
        return

    # 연결 한도 검사
    if _deps.ws_total_count >= WS_MAX_TOTAL:
        await ws.close(code=1013, reason="Max total connections")
        return
    per_session = _deps.ws_count_per_session.get(session_id, 0)
    if per_session >= WS_MAX_PER_SESSION:
        await ws.close(code=1013, reason="Max per-session connections")
        return

    # A2: 한도 검사 통과 직후 카운터를 즉시 증가시킨다. 예전엔(A1) 증가를 E2E 협상
    # 성공 이후로 미뤘는데, E2E 핸드셰이크에는 실제 await(최대 10초 receive_text)가
    # 있어 그 사이 동시에 들어온 여러 ?e2e=1 연결이 전부 한도 검사를 통과해버리는
    # TOCTOU 레이스가 있었다 — WS_MAX_PER_SESSION/WS_MAX_TOTAL을 넘겨 연결이 쌓였다.
    # 이제는 한도 검사 직후(await 없는 구간)에 증가시키고, 아래 try/finally가
    # 핸드셰이크 실패를 포함한 모든 종료 경로에서 감소를 보장한다.
    _deps.ws_count_per_session[session_id] = _deps.ws_count_per_session.get(session_id, 0) + 1
    _deps.ws_total_count += 1

    loop = asyncio.get_running_loop()
    last_pong = loop.time()
    send_queue: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_HIGH * 2)
    pty_paused = False
    ws_id = id(ws)
    send_task: Optional[asyncio.Task] = None
    hb_task: Optional[asyncio.Task] = None
    on_data = None  # subscribe() 여부의 표식 겸 finally에서 쓸 콜백 레퍼런스
    # 실사용 중 발견 — 세션 쿠키가 24시간 뒤 만료돼도 이미 열린 WS는 계속
    # 살아있었다(auth.py의 `_ws_session_watchdog` 설명 참고). 핸드셰이크 인증에
    # 쓰인 토큰을 들고 연결 내내 주기적으로 재검사한다.
    session_watchdog = auth.spawn_session_watchdog(ws, _auth_token)

    try:
        # E2E 협상
        e2e_requested = (
            ws.query_params.get("e2e", "") in ("1", "true", "yes")
            or crypto_channel.is_enabled()
        )
        channel = None
        if e2e_requested and crypto_channel.is_available():
            server_kp = crypto_channel.new_server_keypair()
            if server_kp is None:
                await ws.close(code=4500, reason="E2E unavailable")
                return
            await ws.send_text(json.dumps({
                "type": "e2e-hello",
                "pub": server_kp.public_b64,
                "identity_pub": server_kp.identity_pub_b64,
                "sig": server_kp.sig_b64,
            }))
            try:
                first = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
                handshake = json.loads(first)
                if handshake.get("type") == "e2e-ack" and handshake.get("pub"):
                    channel = crypto_channel.Channel.derive(server_kp.private, handshake["pub"])
                    logger.info(f"[E2E] 핸드셰이크 성공 sid={session_id}")
                else:
                    await ws.close(code=4400, reason="E2E handshake invalid")
                    return
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"[E2E] 핸드셰이크 실패: {e}")
                await ws.close(code=4400, reason="E2E handshake failed")
                return

        def _on_data(data: bytes):
            nonlocal pty_paused
            output_watcher.feed_output(session_id, data)
            _auto_responder.feed(session_id, data)
            # A3: 같은 스트림의 세 번째 소비자. 아무것도 쓰지 않고 상태만 본다
            # (auto_responder가 방금 응답한 세션은 그쪽 cooldown 창 동안 억제).
            _prompt_detector.feed(session_id, data)
            out = channel.encrypt_simple(data) if channel else data
            try:
                send_queue.put_nowait(out)
            except asyncio.QueueFull:
                try:
                    send_queue.get_nowait()
                    send_queue.put_nowait(out)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
            qs = send_queue.qsize()
            if qs > WS_QUEUE_HIGH and not pty_paused:
                pty_paused = True
                pty_mgr.pause_read(session_id, ws_id)
            elif qs < WS_QUEUE_LOW and pty_paused:
                pty_paused = False
                pty_mgr.resume_read(session_id, ws_id)

        async def _send_worker():
            while True:
                try:
                    data = await send_queue.get()
                    if data is _SCROLLBACK_END:
                        await ws.send_text(json.dumps({"type": "scrollback_end"}))
                        continue
                    await ws.send_bytes(data)
                except (WebSocketDisconnect, RuntimeError):
                    break
                except Exception as e:
                    logger.debug(f"[ws] send error: {e}")
                    break

        async def _heartbeat_loop():
            while True:
                try:
                    await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
                    if loop.time() - last_pong > WS_HEARTBEAT_TIMEOUT:
                        logger.info(f"[ws] heartbeat timeout sid={session_id}")
                        try:
                            await ws.close(code=1001, reason="heartbeat timeout")
                        except Exception:
                            pass
                        return
                    try:
                        await ws.send_text(json.dumps({"type": "ping"}))
                    except Exception:
                        return
                except asyncio.CancelledError:
                    return

        # C3: scrollback을 live 데이터와 같은 send_queue로 흘려보낸다. 예전엔 subscribe로
        # live 데이터가 큐에 쌓이는 동안 scrollback을 _safe_send로 직접 보내 두 전송 경로가
        # 경쟁했다(재접속 tail 중복/역전). scrollback을 먼저 큐에 넣고 subscribe하면 단일
        # FIFO 경로로 순서가 보장된다.
        # S1: get_scrollback()은 시간순(오래된→최신)으로 반환하는데, 청크 수가 많으면
        # (256KB 안에 작은 청크가 수천 개) 큐(maxsize 400)가 중간에 꽉 차 break로
        # 잘렸다 — 잘려나가는 뒤쪽이 하필 가장 최신 출력이었다. 큐 여유분에 맞게
        # 뒤(최신)에서부터만 남겨서 채운다.
        # _send_worker를 먼저 띄워 큐를 동시에 비우게 한다 — 이래야 아래
        # scrollback_end sentinel을 (큐가 꽉 찬 경우에도) 안전하게 `await put()`으로
        # 흘려보낼 수 있다. worker가 없으면 큐가 안 비워져 deadlock난다.
        send_task = asyncio.create_task(_send_worker())

        scrollback_chunks = pty_mgr.get_scrollback(session_id)
        available = send_queue.maxsize - send_queue.qsize()
        if 0 <= available < len(scrollback_chunks):
            scrollback_chunks = scrollback_chunks[-available:] if available > 0 else []
        for chunk in scrollback_chunks:
            out = channel.encrypt_simple(chunk) if channel else chunk
            try:
                send_queue.put_nowait(out)
            except asyncio.QueueFull:
                break
        # scrollback 큐잉이 끝났다는 신호 — put_nowait이 아니라 put()을 쓴다:
        # 큐가 방금 꽉 찼더라도(스크롤백 자체가 maxsize를 채운 경우) worker가
        # 비우는 대로 자리를 얻어 반드시 전송되게 한다.
        await send_queue.put(_SCROLLBACK_END)
        on_data = _on_data
        pty_mgr.subscribe(session_id, on_data)

        hb_task = asyncio.create_task(_heartbeat_loop())

        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.receive":
                if "text" in msg:
                    data = json.loads(msg["text"])
                    msg_type = data.get("type")
                    if msg_type == "resize":
                        try:
                            pty_mgr.resize(session_id, data["cols"], data["rows"])
                        except ValueError:
                            # 세션이 이미 kill/destroy된 뒤 도착한 지연 메시지 — 클라이언트가
                            # 이미 아는 상황(4004)으로 정리해서 재연결 스톰 대신 깔끔히 중단시킨다.
                            await ws.close(code=4004, reason="Session destroyed")
                            return
                    elif msg_type == "pong":
                        last_pong = loop.time()
                    elif msg_type == "render_pause":
                        # R2: WS 송신 큐(_on_data)만으로는 "네트워크로는 다 나갔는데
                        # 클라이언트 xterm.js 렌더링이 못 따라가는" 상황을 못 잡는다.
                        # 클라이언트가 xterm write() 완료 콜백 기준으로 직접 신호를
                        # 보낸다 — pause_read는 requester별 카운트라 _on_data의
                        # 큐 기반 pause와 독립적으로 겹쳐도 안전하다(둘 다 resume해야
                        # 재개).
                        pty_mgr.pause_read(session_id, f"{ws_id}-render")
                    elif msg_type == "render_resume":
                        pty_mgr.resume_read(session_id, f"{ws_id}-render")
                    elif msg_type == "paste":
                        # N24(2.1.5 2/n) — 붙여넣기 전용 경로. 클라이언트는
                        # 원문만 보내고 마커·개행·제어문자 처리는 서버가
                        # 한다(input_mode.py의 판정을 따른다). tmux 세션은
                        # N25(3/n)가 tmux 자신에게 위임한다.
                        text = data.get("text")
                        if isinstance(text, str) and text:
                            _prompt_detector.on_user_input(session_id)
                            info = session_store.get(session_id)
                            try:
                                pty_mgr.paste(session_id, text, is_tmux=bool(info and info.tmux_name),
                                              tmux_name=info.tmux_name if info else None)
                            except ValueError:
                                await ws.close(code=4004, reason="Session destroyed")
                                return
                            except RuntimeError as e:
                                # tmux paste-buffer 실패(죽은 pane 등) — 조용히
                                # 삼키지 않는다. 세션 자체는 살아있으니 WS는
                                # 안 끊고 클라이언트에 알리기만 한다.
                                try:
                                    await ws.send_json({"type": "paste_failed", "reason": str(e)})
                                except Exception:
                                    pass
                elif "bytes" in msg:
                    payload = msg["bytes"]
                    if channel:
                        try:
                            payload = channel.decrypt(payload)
                        except Exception as e:
                            logger.warning(f"[E2E] 복호화 실패: {e}")
                            continue
                    try:
                        # A3 해제 판정: 사용자가 이 pane에 직접 입력했다면 승인
                        # 대기는 끝났다(사람이 실제로 답한 가장 확실한 신호).
                        _prompt_detector.on_user_input(session_id)
                        pty_mgr.write(session_id, payload)
                    except ValueError:
                        # kill 버튼으로 세션이 방금 destroy된 것과 클라이언트의 마지막 입력이
                        # 경쟁하면 여기서 터진다 — 예전엔 이게 잡히지 않아 전체 핸들러가
                        # traceback과 함께 죽고 "서버 연결 끊김"으로 보였다.
                        await ws.close(code=4004, reason="Session destroyed")
                        return
            elif msg["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        # E2E 핸드셰이크 실패 등으로 subscribe()에 도달하지 못했으면 on_data는 None —
        # 그 경우 unsubscribe할 대상이 없으므로 건너뛴다.
        if on_data is not None:
            pty_mgr.unsubscribe(session_id, on_data)
        if send_task is not None:
            send_task.cancel()
        if hb_task is not None:
            hb_task.cancel()
        session_watchdog.cancel()
        if pty_paused:
            pty_mgr.resume_read(session_id, ws_id)
        # R2: 클라이언트가 render_pause만 보내고 render_resume 전에 끊긴 경우
        # _pause_requesters에 requester가 영영 남아 read loop이 계속 막힌다 —
        # resume_read는 없는 requester를 지워도 안전(discard)하므로 무조건 호출.
        pty_mgr.resume_read(session_id, f"{ws_id}-render")
        # A2: 핸드셰이크 실패를 포함한 모든 종료 경로에서 위에서 올린 카운터를 되돌린다.
        _deps.ws_count_per_session[session_id] = max(0, _deps.ws_count_per_session.get(session_id, 1) - 1)
        if _deps.ws_count_per_session[session_id] == 0:
            _deps.ws_count_per_session.pop(session_id, None)
        _deps.ws_total_count = max(0, _deps.ws_total_count - 1)


# --------------------------------------------------------------------------
# 알림 WebSocket + on_task_complete 콜백
# --------------------------------------------------------------------------

import platform_utils as _platform_utils


async def on_task_complete(session_id: str, summary: str, audio: bytes):
    from deps import notify_clients
    meta = json.dumps({
        "type": "task_complete",
        "session_id": session_id,
        "summary": summary,
        "has_audio": len(audio) > 0,
    })
    dead = set()
    for ws in list(notify_clients):
        try:
            await ws.send_text(meta)
            if audio:
                await ws.send_bytes(audio)
        except Exception:
            dead.add(ws)
    notify_clients -= dead
    if not notify_clients:
        # 붙어 있는 클라이언트가 하나도 없다 = 앱이 닫혀 있다.
        # 맥 앞에 있으면 TTS로 듣고, 폰이라면 Web Push로 받는다.
        # WS가 살아 있을 때는 푸시를 보내지 않는다 — 같은 알림이 두 번 온다.
        _platform_utils.tts_speak(summary)
        try:
            import push
            if push.available():
                # 잠금화면에 뜨는 내용이다. 요약을 그대로 싣지 않고 사실만 보낸다
                # (명령어·경로·코드가 새지 않도록).
                await asyncio.to_thread(push.send, "작업 완료", "터미널에서 확인하세요", "/")
        except Exception as e:
            logger.warning(f"web push 발송 실패: {e}")


@router.websocket("/ws-notify")
async def ws_notify(ws: WebSocket):
    from deps import notify_clients
    token = _ws_auth_token(ws)
    if token is None:
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()
    notify_clients.add(ws)
    session_watchdog = auth.spawn_session_watchdog(ws, token)
    try:
        while True:
            msg = await ws.receive_text()
            data = json.loads(msg)
            if data.get("type") == "set_watch":
                sid = data.get("session_id")
                output_watcher.set_enabled(sid, data.get("enabled", True))
            elif data.get("type") == "set_timeout":
                sid = data.get("session_id")
                output_watcher.set_idle_timeout(sid, data.get("timeout", 3.0))
    except WebSocketDisconnect:
        pass
    finally:
        session_watchdog.cancel()
        notify_clients.discard(ws)
