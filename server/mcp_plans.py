"""짧게 유효한 서버 보관 MCP 계획. 원문은 브라우저 토큰에 넣지 않는다."""
from __future__ import annotations

import secrets
import threading
import time

TTL = 300
MAX_PLANS = 256
_lock = threading.RLock()
_plans: dict[str, dict] = {}


def issue(kind: str, payload: dict) -> dict:
    now = time.time()
    with _lock:
        for token in list(_plans):
            if _plans[token]["expires_at"] <= now:
                del _plans[token]
        while len(_plans) >= MAX_PLANS:
            del _plans[next(iter(_plans))]
        token = secrets.token_urlsafe(32)
        expiry = now + TTL
        _plans[token] = {"kind": kind, "payload": payload, "expires_at": expiry}
    return {"token": token, "expires_at": expiry}


def take(token: str, kind: str) -> dict | None:
    with _lock:
        plan = _plans.pop(token, None)
    if not plan or plan["kind"] != kind or plan["expires_at"] <= time.time():
        return None
    return plan["payload"]
