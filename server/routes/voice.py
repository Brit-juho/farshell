"""음성 입출력 + 출력 감시 + 푸시 알림."""

from __future__ import annotations

import asyncio
import logging
import os
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

import notify
from stt_engines.base import NotReady
import platform_utils
import stt_service
import voice_handler
import auth
import hashlib
import voice_targets
from voice_sessions import coordinator
from deps import pty_mgr, output_watcher, notify_clients

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# TTS barge-in 헬퍼
# --------------------------------------------------------------------------

def _cancel_tts_playback() -> int:
    # 호환 함수. 다른 앱의 say/afplay는 절대 종료하지 않는다.
    return 0


@router.post("/voice/cancel")
async def voice_cancel(request: Request):
    body = await request.json()
    client = (voice_targets.principal(request), str(body.get("screen", "")))
    await coordinator.barge(client, bool(body.get("pause", True)))
    if body.get("server") and auth.request_kind(request) == "machine":
        await coordinator.barge("server", bool(body.get("pause", True)))
    return {"cancelled": True}


@router.post("/voice/target")
async def voice_target(request: Request):
    body = await request.json()
    try:
        if body.get("pane") and auth.request_kind(request) == "machine":
            target = await asyncio.to_thread(voice_targets.snapshot, "", str(body["pane"]))
        else:
            target = await asyncio.to_thread(voice_targets.from_web, str(body.get("session_id", "")))
        return voice_targets.issue(voice_targets.principal(request), target)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)


@router.get("/voice/defaults")
async def voice_defaults():
    return {"policy": coordinator.defaults()}


@router.put("/voice/defaults")
async def voice_defaults_update(request: Request):
    try:
        body = await request.json()
        return {"policy": coordinator.update_defaults(body.get("policy") or {})}
    except (ValueError, TypeError, AttributeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/voice/session")
async def voice_session(request: Request, session_id: str):
    try:
        target = await asyncio.to_thread(voice_targets.from_web, session_id)
        client = (voice_targets.principal(request), request.query_params.get("screen", ""))
        lease = coordinator.leases.get(target["key"])
        return {"target": target, "policy": coordinator.policy(target["key"]),
                "owns": bool(lease and lease[:2] == client), "claimed": bool(lease),
                "status": coordinator.diagnostics.get(target["key"]),
                "adapters": ["claude", "codex"]}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)


@router.put("/voice/session")
async def voice_session_update(request: Request):
    body = await request.json()
    try:
        target = await asyncio.to_thread(voice_targets.from_web, str(body.get("session_id", "")))
        cfg = await coordinator.update(target, body.get("policy") or {}, voice_targets.principal(request),
                                       str(body.get("screen", "")), bool(body.get("transfer")))
        return {"target": target, "policy": cfg}
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=409)


@router.post("/voice/bind")
async def voice_bind(request: Request):
    import pane_resolve
    body = await request.json()
    if auth.request_kind(request) != "machine" or not pane_resolve.is_our_socket(body.get("tmux")):
        return JSONResponse({"error": "pane에서 등록 명령을 실행하세요"}, status_code=403)
    tool, run = str(body.get("tool", "")), str(body.get("run", ""))
    if tool not in ("claude", "codex") or not run or len(run) > 200:
        return JSONResponse({"error": "지원 도구와 대화 ID가 필요합니다"}, status_code=400)
    try:
        target = await asyncio.to_thread(voice_targets.snapshot, "", str(body.get("pane", "")))
        coordinator.runs[(tool, run)] = {"target": target, "turn": __import__("secrets").token_hex(12)}
        return {"ok": True, "target": target}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)


@router.post("/voice/test")
async def voice_test(request: Request):
    body = await request.json()
    try:
        target = await asyncio.to_thread(voice_targets.from_web, str(body.get("session_id", "")))
        lease = coordinator.leases.get(target["key"])
        cfg = coordinator.policy(target["key"])
        client = (voice_targets.principal(request), str(body.get("screen", "")))
        if cfg["destination"] == "browser" and (not lease or lease[:2] != client):
            raise ValueError("먼저 이 기기에서 듣기를 켜 주세요")
        ok = await coordinator.enqueue(target, {"id": "test-" + str(time.monotonic()),
            "text": "FarShell 음성 재생 테스트입니다", "ok": True, "elapsed": None}, test=True)
        return {"ok": ok}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)


@router.websocket("/ws-voice")
async def ws_voice(ws: WebSocket):
    from routes.pty import _ws_auth_token
    token = _ws_auth_token(ws)
    screen = ws.query_params.get("screen", "")
    if token is None or not screen or len(screen) > 64:
        await ws.close(code=4001)
        return
    await ws.accept()
    owner = voice_targets.principal(ws)
    generation = coordinator.register(owner, screen, ws)
    watchdog = auth.spawn_session_watchdog(ws, token)
    client = (owner, screen)
    try:
        await ws.send_json({"type": "voice_connected", "generation": generation})
        while True:
            msg = await ws.receive_json()
            if coordinator.clients.get(client, {}).get("generation") != generation:
                break
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "voice_ack":
                await coordinator.ack(client, msg)
            elif msg.get("type") == "voice_barge":
                await coordinator.barge(client, bool(msg.get("pause")))
            elif msg.get("type") == "voice_visible":
                keys = msg.get("keys") or []
                if isinstance(keys, list):
                    coordinator.clients[client]["visible"] = set(str(k) for k in keys[:64])
    except (WebSocketDisconnect, ValueError, KeyError):
        pass
    finally:
        watchdog.cancel()
        await coordinator.disconnect(owner, screen, generation)


# 같은 녹음의 재전송(응답만 유실돼 클라이언트가 다시 보낸 경우)이 터미널에 두 번 입력되지 않게,
# 클라이언트가 녹음마다 붙이는 X-Voice-Id로 최근 결과를 잠깐 기억한다.
_VOICE_ID_TTL = 120.0
_VOICE_ID_MAX = 64
_voice_done: "dict[str, tuple[float, dict]]" = {}
_voice_inflight: "dict[str, asyncio.Future]" = {}
_voice_fingerprints: dict[str, str] = {}


def _voice_id(request: Request) -> str:
    vid = (request.headers.get("x-voice-id") or "").strip()
    return vid[:64] if vid and vid.isascii() and all(c.isalnum() or c in "-_" for c in vid) else ""


def _remember_voice(vid: str, result: dict) -> None:
    now = time.monotonic()
    for k in [k for k, (ts, _) in _voice_done.items() if now - ts > _VOICE_ID_TTL]:
        _voice_done.pop(k, None)
        _voice_fingerprints.pop(k, None)
    _voice_done[vid] = (now, result)
    while len(_voice_done) > _VOICE_ID_MAX:
        old = next(iter(_voice_done))
        _voice_done.pop(old, None)
        _voice_fingerprints.pop(old, None)


# Content-Type → 디코더 형식. 브라우저가 고른 녹음 형식(webm/ogg/mp4/wav)을 그대로 받는다.
def _audio_format(content_type: str) -> str:
    ct = content_type.lower()
    if "webm" in ct:
        return "webm"
    if "wav" in ct:
        return "wav"
    if "ogg" in ct:
        return "ogg"
    if "mp4" in ct or "m4a" in ct or "aac" in ct:
        return "mp4"
    if "mpeg" in ct or "mp3" in ct:
        return "mp3"
    return "webm"


@router.post("/voice/input")
async def voice_input(request: Request):
    vid = _voice_id(request)
    try:
        return await _voice_input_dedup(request, vid)
    except stt_service.Busy as e:                       # 줄 선 요청이 너무 많다 — 중복 방지 경로에서도 429로 돌려준다
        return JSONResponse({"text": "", "error": str(e), "injected": False}, status_code=429)


async def _voice_input_dedup(request: Request, vid: str):
    if hasattr(request, 'stream'):
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > 4 * 1024 * 1024:
                return JSONResponse({"error": "녹음 파일이 너무 큽니다", "injected": False}, status_code=413)
        audio = bytes(chunks)
    else:
        audio = await request.body()
    if len(audio) > 4 * 1024 * 1024:
        return JSONResponse({"error": "녹음 파일이 너무 큽니다", "injected": False}, status_code=413)
    owner = voice_targets.principal(request)
    token = request.headers.get("x-voice-target", "")
    sid = request.query_params.get("session_id", request.headers.get("x-session-id", ""))
    fingerprint = hashlib.sha256(audio + (token or sid).encode()).hexdigest()
    key = owner + ":" + vid if token or getattr(request, "cookies", {}) or request.headers.get("authorization") else vid  # 구 클라이언트의 호출 형태도 계속 받는다.
    now = time.monotonic()
    if key in _voice_done:
        ts, result = _voice_done[key]
        if now - ts > _VOICE_ID_TTL:
            return JSONResponse({"error": "재전송 시간이 만료됐습니다", "injected": False}, status_code=409)
        if _voice_fingerprints.get(key) != fingerprint:
            return JSONResponse({"error": "녹음 ID가 다른 입력에 사용됐습니다", "injected": False}, status_code=409)
        return {**result, "duplicate": True}
    if vid and key in _voice_inflight:
        if _voice_fingerprints.get(key) != fingerprint:
            return JSONResponse({"error": "녹음 ID 충돌", "injected": False}, status_code=409)
        return {**(await asyncio.shield(_voice_inflight[key])), "duplicate": True}
    target = None
    if token:
        try:
            target = voice_targets.get(token, owner)
        except ValueError as e:
            return JSONResponse({"error": str(e), "injected": False}, status_code=409)
    if vid:
        _voice_fingerprints[key] = fingerprint
        _voice_inflight[key] = asyncio.get_running_loop().create_future()
    try:
        result = await _voice_input_once(request, audio, target)
    except BaseException as e:
        if vid:
            _voice_fingerprints.pop(key, None)
            fut = _voice_inflight.pop(key, None)
            if fut is not None and not fut.done():
                fut.set_exception(e)
                fut.exception()
        raise
    if vid:
        fut = _voice_inflight.pop(key, None)
        if fut is not None and not fut.done():
            fut.set_result(result)
        if result.get("text"):
            _remember_voice(key, result)
        else:
            _voice_fingerprints.pop(key, None)
    return result


async def _voice_input_once(request: Request, audio_bytes=None, target=None):
    fmt = _audio_format(request.headers.get("content-type", ""))
    if audio_bytes is None:
        audio_bytes = await request.body()
    try:
        lang = request.query_params.get("lang", "") or None
        text, timing = await voice_handler.transcribe_timed(audio_bytes, input_format=fmt, language=lang)
    except NotReady as e:
        return {"text": "", "error": str(e), "injected": False}
    except stt_service.Busy:
        raise
    except Exception as e:
        logger.error("STT failed: %s", e)
        return {"text": "", "error": str(e), "injected": False}
    sid = request.query_params.get("session_id", request.headers.get("x-session-id", ""))
    injected = False
    if text and target:
        if await asyncio.to_thread(voice_targets.valid, target):
            from tmux_target import send_to_tmux
            injected = await asyncio.to_thread(send_to_tmux, target["pane"], text)
    elif text and sid in pty_mgr.sessions:
        pty_mgr.write(sid, (text + "\n").encode())
        injected = True
    result = {"text": text, "injected": injected, "session_id": sid, "timing": timing}
    if target and text and not injected:
        result["error"] = "입력 대상이 사라졌습니다. 인식 텍스트를 복사해 주세요"
    return result


@router.post("/voice/output")
async def voice_output(request: Request):
    body = await request.json()
    text = (body.get("text") or "").strip()
    voice = body.get("voice", "ko-KR-SunHiNeural")

    # 빈 텍스트는 200 + 0 bytes 대신 400으로 명시적으로 거절 (TEST_REPORT.md Bug #7).
    if not text:
        return Response(
            content=b'{"error":"empty text"}',
            status_code=400,
            media_type="application/json",
        )

    try:
        audio = await voice_handler.synthesize_result(text, voice=voice, engine=body.get("engine", "auto"))
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        return Response(content=b"", status_code=500)

    return Response(content=audio.data, media_type=audio.media_type)


@router.get("/voice/notify/clients")
async def notify_web_clients():
    """지금 웹 화면(/ws-notify)에 붙어 있는 클라이언트 수 — Stop 훅이 「웹 접속 중이면
    알리지 않기」를 판정할 때 쓴다."""
    return {"web_clients": len(notify_clients)}


@router.get("/voice/stt/status")
async def stt_status():
    """STT 상태 — 선택된 모델, 올라간 모델·엔진, 메모리, 준비(받기) 진행, 마지막 오류. 모델을 올리지 않는다."""
    return await asyncio.to_thread(stt_service.status_payload)


@router.get("/voice/stt/catalog")
async def stt_catalog(custom: str = ""):
    """이 서버에서 고를 수 있는 STT 모델(권장·상태·측정치 포함)과 못 쓰는 모델. `custom=<id>`를 주면 그 직접 입력
    모델의 준비 상태도 models에 함께 담는다."""
    return await asyncio.to_thread(stt_service.catalog_payload, custom or None)


@router.post("/voice/stt/setup")
async def stt_setup(request: Request):
    """준비 한 단계를 백그라운드로 시작한다(엔진 실행 파일 받기 또는 모델 받기) — **사용자가 확인한 뒤에만** 부른다.
    body: {"model": "<id>", "step": "model" | "req:<항목 id>"}. 진행은 GET /voice/stt/status 의 setup."""
    try:
        body = await request.json()
        model, step = str(body.get("model", "")), str(body.get("step", ""))
    except Exception:
        return JSONResponse({"error": "JSON body가 필요합니다"}, status_code=400)
    try:
        return await asyncio.to_thread(stt_service.start_setup, model, step)
    except stt_service.SetupError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except stt_service.setup_jobs.SetupBusy as e:
        return JSONResponse({"error": str(e)}, status_code=409)


@router.post("/voice/stt/preload")
async def stt_preload():
    """선택된 모델을 미리 올린다 → 첫 음성 입력 지연 제거. 몰래 받지 않는다 — 없으면 이유를 error로 돌려준다."""
    engine = await asyncio.to_thread(stt_service.preload)
    out = {"loaded": stt_service.loaded(), "engine": engine}
    if not out["loaded"] and stt_service.last_error():
        out["error"] = stt_service.last_error()
    return out


@router.post("/voice/stt/unload")
async def stt_unload():
    """올라간 모델을 내려 메모리를 돌려준다."""
    return {"unloaded": await asyncio.to_thread(stt_service.unload), "loaded": stt_service.loaded()}


@router.get("/voice/stt/model")
async def stt_model_list():
    """받아 둔 모델·엔진 실행 파일 목록(크기, 선택 중 표시) — 설정 「음성」의 관리 구역이 쓴다."""
    return await asyncio.to_thread(stt_service.downloads_payload)


@router.delete("/voice/stt/model")
async def stt_model_delete(request: Request):
    """받아 둔 것을 지운다. body: {"model_id": "..."} 또는 {"binary": "llama-server"}. 폴더 밖 공유 가중치까지 지운다."""
    try:
        body = await request.json()
        model_id, binary = body.get("model_id"), body.get("binary")
    except Exception:
        return JSONResponse({"error": "JSON body가 필요합니다"}, status_code=400)
    try:
        freed = await asyncio.to_thread(stt_service.delete_download, model_id, binary)
    except stt_service.SetupError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except OSError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"deleted": True, "freed_bytes": freed}


@router.post("/voice/local/start")
async def local_mic_start():
    return JSONResponse({"error": "local_mic_removed", "reason": "브라우저 마이크 또는 fsh voice를 사용하세요"}, status_code=410)


@router.post("/voice/local/stop")
async def local_mic_stop(request: Request):
    return await local_mic_start()


# --------------------------------------------------------------------------
# 출력 감시 + 푸시 알림
# --------------------------------------------------------------------------

@router.post("/api/watch/{session_id}")
async def toggle_watch(session_id: str, request: Request):
    body = await request.json()
    enabled = body.get("enabled", True)
    timeout = body.get("timeout", 3.0)
    output_watcher.set_enabled(session_id, enabled)
    output_watcher.set_idle_timeout(session_id, timeout)
    return {"session_id": session_id, "enabled": enabled, "timeout": timeout}


@router.get("/api/notify/status")
async def notify_status():
    return {
        "configured": notify.is_configured(),
        "ntfy": bool(os.environ.get("VT_NOTIFY_URL", "").strip()),
        "telegram": bool(
            os.environ.get("VT_TELEGRAM_TOKEN", "").strip()
            and os.environ.get("VT_TELEGRAM_CHAT_ID", "").strip()
        ),
    }


@router.post("/api/notify/test")
async def notify_test(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    title = body.get("title", "FarShell 테스트")
    message = body.get("message", "푸시 알림이 정상 작동합니다 🎉")
    priority = body.get("priority", "default")
    ok = await notify.send(title, message, priority=priority, tags="mega")
    return {"ok": ok, "configured": notify.is_configured()}
