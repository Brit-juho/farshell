"""음성 입출력 + 출력 감시 + 푸시 알림."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess

from fastapi import APIRouter, Request
from fastapi.responses import Response

import notify
import platform_utils
import voice_handler
import local_mic
from deps import pty_mgr, output_watcher

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
    n = _cancel_tts_playback()
    return {"cancelled": n}


@router.post("/voice/input")
async def voice_input(request: Request):
    _cancel_tts_playback()

    content_type = request.headers.get("content-type", "")
    audio_bytes = await request.body()

    if "webm" in content_type:
        fmt = "webm"
    elif "wav" in content_type:
        fmt = "wav"
    elif "ogg" in content_type:
        fmt = "ogg"
    else:
        fmt = "webm"

    try:
        lang = request.query_params.get("lang", "") or None
        text = await voice_handler.transcribe(audio_bytes, input_format=fmt, language=lang)
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

    return {"text": text, "injected": injected, "session_id": session_id}


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


@router.post("/voice/stt/preload")
async def stt_preload():
    """음성 모드 on 시 STT 모델을 미리 로드 → 첫 음성 입력 지연 제거."""
    loop = asyncio.get_running_loop()
    engine = await loop.run_in_executor(None, voice_handler.preload_stt)
    return {"loaded": voice_handler.stt_loaded(), "engine": engine}


@router.post("/voice/stt/unload")
async def stt_unload():
    """음성 모드 off 시 STT 모델을 내려 메모리(~150MB) 회수."""
    return {"unloaded": voice_handler.unload_stt(), "loaded": voice_handler.stt_loaded()}


@router.post("/voice/local/start")
async def local_mic_start():
    # 로컬 마이크 녹음 시작 = 음성 모드 활성 신호 → STT 미리 로드(백그라운드)
    try:
        asyncio.get_running_loop().run_in_executor(None, voice_handler.preload_stt)
    except Exception:
        pass
    return local_mic.start_recording()


@router.post("/voice/local/stop")
async def local_mic_stop(request: Request):
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
    title = body.get("title", "voice-terminal 테스트")
    message = body.get("message", "푸시 알림이 정상 작동합니다 🎉")
    priority = body.get("priority", "default")
    ok = await notify.send(title, message, priority=priority, tags="mega")
    return {"ok": ok, "configured": notify.is_configured()}
