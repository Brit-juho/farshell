"""macOS 클립보드 폴링 데몬 → 웹 브라우저 클립보드 push.

OSC52(터미널 프로그램 내부 복사)로 못 잡는 경우(Safari/Finder 등 임의 앱에서
복사) 보완용. clipboard_daemon.py가 NSPasteboard 변경을 감지해 이 엔드포인트로
POST하면, 이미 붙어 있는 /ws-notify 클라이언트 전원에게 브로드캐스트한다.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from deps import notify_clients

router = APIRouter()

# 과도한 페이로드로 브로드캐스트 채널을 막지 않도록 상한.
MAX_TEXT_LEN = 200_000


@router.post("/api/clipboard/push")
async def push_clipboard(request: Request):
    body = await request.json()
    text = body.get("text", "")
    if not isinstance(text, str) or not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    if len(text) > MAX_TEXT_LEN:
        return JSONResponse({"error": "text too large"}, status_code=413)

    payload = json.dumps({"type": "clipboard_push", "text": text})
    dead = set()
    for ws in list(notify_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    notify_clients.difference_update(dead)
    return {"ok": True, "delivered": len(notify_clients)}
