"""PTY 세션 CRUD + WebSocket 터미널 + 파일 업로드/다운로드."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

import auth
import crypto_channel
import tmux_runner
from deps import pty_mgr, session_store, output_watcher, _auto_responder
from deps import ws_count_per_session, ws_total_count
from session_store import new_session_id

logger = logging.getLogger(__name__)

router = APIRouter()

UPLOAD_DIR = Path("/tmp/vt-uploads")
MAX_UPLOAD_BYTES = int(os.environ.get("VT_MAX_UPLOAD_MB", "200")) * 1024 * 1024

# Phase 8 G2: 연결 한도 + 백프레셔 + 하트비트
WS_MAX_PER_SESSION = int(os.environ.get("VT_WS_MAX_PER_SESSION", "8"))
WS_MAX_TOTAL = int(os.environ.get("VT_WS_MAX_TOTAL", "32"))
WS_HEARTBEAT_INTERVAL = float(os.environ.get("VT_WS_HEARTBEAT_INTERVAL", "15.0"))
WS_HEARTBEAT_TIMEOUT = float(os.environ.get("VT_WS_HEARTBEAT_TIMEOUT", "45.0"))
WS_QUEUE_HIGH = 200
WS_QUEUE_LOW = 50

import deps as _deps  # 전역 카운터 직접 수정용

def _ws_auth(ws: WebSocket) -> bool:
    """WS 인증: HTTP 미들웨어와 동일한 다중 소스(cookie/query/Bearer)를 수용.

    서명 세션 쿠키(사람) 또는 기계 토큰(데몬)을 auth.check_request로 판정한다.
    """
    if not auth.is_protected():
        return True
    # 1) HttpOnly 세션 쿠키 (/api/auth 후)
    if auth.check_request(ws.cookies.get("vt_session", "")):
        return True
    # 2) query string (QR/URL 기계 토큰)
    if auth.check_request(ws.query_params.get("token", "")):
        return True
    # 3) Authorization: Bearer (데몬)
    auth_hdr = ws.headers.get("authorization", "")
    if auth_hdr.startswith("Bearer ") and auth.check_request(auth_hdr[7:]):
        return True
    return False


# --------------------------------------------------------------------------
# PTY 세션 CRUD
# --------------------------------------------------------------------------

@router.api_route("/api/sessions", methods=["GET", "HEAD"])
async def list_sessions():
    # tmux가 `detach-on-destroy off`면 세션이 kill돼도 web의 attach 클라이언트가 다른
    # 세션으로 전환되어 살아남아 PTY가 EOF되지 않는다 → 죽은 tmux를 가리키는 web 세션이
    # 목록·메모리(PTY·scrollback)·클라이언트 터미널로 계속 쌓인다. 여기서 실제 tmux 존재를
    # 검증해 좀비 세션을 정리하고, 살아있는 것만 반환한다.
    result = []
    for s in list(pty_mgr.sessions.values()):
        info = session_store.get(s.session_id)
        tmux_name = info.tmux_name if info else None
        if tmux_name and not tmux_runner.has_session(tmux_name):
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
    return {"ok": True, "tmux_detached": tmux_name}


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
# 파일 업로드 / 다운로드
# --------------------------------------------------------------------------

@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...), session_id: str = Query("")):
    # 0700으로 만든다 — 예전엔 기본 퍼미션(0755)이라 같은 머신의 다른 계정이
    # 업로드한 파일을 그대로 읽을 수 있었다.
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(UPLOAD_DIR, 0o700)
    except OSError:
        pass
    safe_name = Path(file.filename or "").name.replace("..", "").strip()
    if not safe_name:
        safe_name = f"upload-{uuid.uuid4().hex[:8]}"
    dest = UPLOAD_DIR / safe_name
    # 청크 단위로 받으면서 상한을 건다. 예전엔 await file.read()로 전체를 메모리에
    # 올려서 큰 파일 하나로 서버(=내 맥)를 OOM으로 밀어낼 수 있었다.
    size = 0
    try:
        fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    return JSONResponse(
                        {"error": "file too large", "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024)},
                        status_code=413,
                    )
                out.write(chunk)
    except OSError as e:
        logger.warning(f"upload 실패: {e}")
        return JSONResponse({"error": "write failed"}, status_code=500)
    return {"ok": True, "path": str(dest), "size": size}


@router.get("/api/download")
async def download_file(path: str = Query(...)):
    fp = Path(path).resolve()
    # is_relative_to로 검사한다. 예전 startswith 방식은 문자열 접두사 비교라
    # /tmp/vt-uploads-evil/… 같은 형제 디렉토리가 통과했다(/tmp는 누구나 만들 수 있다).
    if not fp.is_relative_to(UPLOAD_DIR.resolve()):
        return Response(content="Access denied", status_code=403)
    if not fp.is_file():
        return Response(content="File not found", status_code=404)
    return FileResponse(str(fp), filename=fp.name)


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
    if not _ws_auth(ws):
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
    # A1: 연결 카운터 증가는 E2E 협상 성공 이후로 미룬다. 예전엔 여기서 증가시켰는데,
    # 아래 E2E 핸드셰이크 실패 return(4500/4400)은 try/finally 이전이라 감소를 못 타서
    # 카운터가 샜다 → ?e2e=1로 실패 핸드셰이크를 반복하면 WS_MAX_TOTAL이 소진돼
    # 정상 접속이 거부되는 DoS. 증가/감소를 finally와 확실히 페어링한다.

    loop = asyncio.get_running_loop()
    last_pong = loop.time()
    send_queue: asyncio.Queue = asyncio.Queue(maxsize=WS_QUEUE_HIGH * 2)
    pty_paused = False
    ws_id = id(ws)

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
        await ws.send_text(json.dumps({"type": "e2e-hello", "pub": server_kp.public_b64}))
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

    def on_data(data: bytes):
        nonlocal pty_paused
        output_watcher.feed_output(session_id, data)
        _auto_responder.feed(session_id, data)
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

    # A1: E2E 협상까지 성공한 지금 카운터를 올린다 — 아래 try/finally가 감소를 보장.
    # 여기~try 사이에는 raise 가능한 await가 없어 증가/감소 페어링이 유지된다.
    _deps.ws_count_per_session[session_id] = _deps.ws_count_per_session.get(session_id, 0) + 1
    _deps.ws_total_count += 1

    # C3: scrollback을 live 데이터와 같은 send_queue로 흘려보낸다. 예전엔 subscribe로
    # live 데이터가 큐에 쌓이는 동안 scrollback을 _safe_send로 직접 보내 두 전송 경로가
    # 경쟁했다(재접속 tail 중복/역전). scrollback을 먼저 큐에 넣고 subscribe하면 단일
    # FIFO 경로로 순서가 보장된다.
    for chunk in pty_mgr.get_scrollback(session_id):
        out = channel.encrypt_simple(chunk) if channel else chunk
        try:
            send_queue.put_nowait(out)
        except asyncio.QueueFull:
            break
    pty_mgr.subscribe(session_id, on_data)

    send_task = asyncio.create_task(_send_worker())
    hb_task = asyncio.create_task(_heartbeat_loop())

    try:
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
                elif "bytes" in msg:
                    payload = msg["bytes"]
                    if channel:
                        try:
                            payload = channel.decrypt(payload)
                        except Exception as e:
                            logger.warning(f"[E2E] 복호화 실패: {e}")
                            continue
                    try:
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
        pty_mgr.unsubscribe(session_id, on_data)
        send_task.cancel()
        hb_task.cancel()
        if pty_paused:
            pty_mgr.resume_read(session_id, ws_id)
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
    if not _ws_auth(ws):
        await ws.close(code=4001, reason="Unauthorized")
        return
    await ws.accept()
    notify_clients.add(ws)
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
        notify_clients.discard(ws)
