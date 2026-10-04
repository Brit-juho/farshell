"""음성 입출력 + 출력 감시 + 푸시 알림."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import notify
from stt_engines.base import NotReady
import platform_utils
import stt_service
import voice_handler
import local_mic
from deps import pty_mgr, output_watcher, notify_clients

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# TTS barge-in 헬퍼
# --------------------------------------------------------------------------

def _cancel_tts_playback() -> int:
    killed = 0
    targets = ["afplay", "say"] if platform_utils.IS_MACOS else ["aplay", "paplay", "ffplay"]
    for tgt in targets:
        try:
            r = subprocess.run(["pkill", "-x", tgt], capture_output=True, timeout=2)
            if r.returncode == 0:
                killed += 1
        except Exception:
            pass
    return killed


# --------------------------------------------------------------------------
# 음성 엔드포인트
# --------------------------------------------------------------------------

@router.post("/voice/cancel")
async def voice_cancel():
    # pkill을 대상마다(최대 3개) timeout=2로 돈다 — 최악 6초 블로킹이다.
    n = await asyncio.to_thread(_cancel_tts_playback)
    return {"cancelled": n}


# 같은 녹음의 재전송(응답만 유실돼 클라이언트가 다시 보낸 경우)이 터미널에 두 번 입력되지 않게,
# 클라이언트가 녹음마다 붙이는 X-Voice-Id로 최근 결과를 잠깐 기억한다.
_VOICE_ID_TTL = 120.0
_VOICE_ID_MAX = 64
_voice_done: "dict[str, tuple[float, dict]]" = {}
_voice_inflight: "dict[str, asyncio.Future]" = {}


def _voice_id(request: Request) -> str:
    vid = (request.headers.get("x-voice-id") or "").strip()
    return vid[:64] if vid and vid.isascii() and all(c.isalnum() or c in "-_" for c in vid) else ""


def _remember_voice(vid: str, result: dict) -> None:
    now = time.monotonic()
    for k in [k for k, (ts, _) in _voice_done.items() if now - ts > _VOICE_ID_TTL]:
        _voice_done.pop(k, None)
    _voice_done[vid] = (now, result)
    while len(_voice_done) > _VOICE_ID_MAX:
        _voice_done.pop(next(iter(_voice_done)), None)


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
    if vid:
        if vid in _voice_done:
            return {**_voice_done[vid][1], "duplicate": True}
        if vid in _voice_inflight:                      # 첫 요청이 아직 처리 중 — 같은 결과를 기다린다
            return {**(await asyncio.shield(_voice_inflight[vid])), "duplicate": True}
        _voice_inflight[vid] = asyncio.get_running_loop().create_future()
    try:
        result = await _voice_input_once(request)
    except BaseException as e:
        if vid:
            fut = _voice_inflight.pop(vid, None)
            if fut is not None and not fut.done():
                fut.set_exception(e)
                fut.exception()                          # 기다리는 쪽이 없어도 경고가 안 나게
        raise
    if vid:
        fut = _voice_inflight.pop(vid, None)
        if fut is not None and not fut.done():
            fut.set_result(result)
        if result.get("text") and not result.get("error"):
            _remember_voice(vid, result)                 # 실패한 건 기억하지 않는다 — 재시도가 다시 처리해야 한다
    return result


async def _voice_input_once(request: Request):
    await asyncio.to_thread(_cancel_tts_playback)
    fmt = _audio_format(request.headers.get("content-type", ""))
    audio_bytes = await request.body()

    try:
        lang = request.query_params.get("lang", "") or None
        text, timing = await voice_handler.transcribe_timed(audio_bytes, input_format=fmt, language=lang)
    except NotReady as e:
        return {"text": "", "error": str(e), "injected": False}
    except stt_service.Busy:
        raise                                            # voice_input이 429로 바꾼다(중복 방지 경로 포함)
    except Exception as e:
        logger.error(f"STT failed: {e}")
        return {"text": "", "error": str(e), "injected": False}

    session_id = request.query_params.get(
        "session_id",
        request.headers.get("x-session-id", ""),
    )

    injected = False
    if text and session_id and session_id in pty_mgr.sessions:
        pty_mgr.write(session_id, (text + "\n").encode())
        injected = True

    return {"text": text, "injected": injected, "session_id": session_id, "timing": timing}


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
        audio = await voice_handler.synthesize(text, voice=voice)
    except Exception as e:
        logger.error(f"TTS failed: {e}")
        return Response(content=b"", status_code=500)

    ct = "audio/aiff" if voice_handler._tts_engine == "macos-say" else "audio/mpeg"
    return Response(content=audio, media_type=ct)


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
    if not local_mic.available():
        return JSONResponse(
            {"error": "local_mic_unavailable", "reason": "sounddevice 또는 numpy가 설치되지 않았습니다"},
            status_code=503,
        )
    # 로컬 마이크 녹음 시작 = 음성 모드 활성 신호 → STT 미리 로드(백그라운드)
    try:
        asyncio.get_running_loop().run_in_executor(None, voice_handler.preload_stt)
    except Exception:
        pass
    return local_mic.start_recording()


@router.post("/voice/local/stop")
async def local_mic_stop(request: Request):
    if not local_mic.available():
        return JSONResponse(
            {"error": "local_mic_unavailable", "reason": "sounddevice 또는 numpy가 설치되지 않았습니다"},
            status_code=503,
        )
    result = await local_mic.stop_recording()
    text = result.get("text", "")

    session_id = request.query_params.get("session_id", "")
    injected = False
    if text and session_id and session_id in pty_mgr.sessions:
        pty_mgr.write(session_id, (text + "\n").encode())
        injected = True

    result["injected"] = injected
    result["session_id"] = session_id
    return result


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
