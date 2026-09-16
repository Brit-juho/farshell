"""1회용 기기 등록 티켓 — `~/.vt/tickets.json`.

`fsh mobile`의 QR/URL은 상시 토큰 대신 이 티켓(5분)을 싣는다. QR을 띄우는
시점에 맥에 대한 **물리적 접근이 이미 증명된 것**이므로 스캔을 기기 등록
승인으로 인정한다. 상시 토큰을 URL에 박던 예전 방식은 그 값이 로그·히스토리·
QR 이미지에 영구히 남는 문제가 있었다.

여기도 저장하는 건 해시뿐이고, 검증에 성공하면 **즉시 소멸**한다(1회용).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Optional

# ⚠ 경로·설정은 **호출 시점에** `auth.X`로 읽는다. 모듈 상단에서
# `from auth import DEVICES_PATH`로 당겨오면 그 순간 값이 굳어서, 테스트가
# `auth.DEVICES_PATH`를 monkeypatch해도 여기는 계속 진짜 ~/.vt를 본다 —
# **테스트는 통과하면서 사용자의 실제 파일을 건드린다.** 조용히 틀리는
# 종류라 가장 나쁘고, server/tests/test_auth_isolation.py가 이 성질을
# 실제로 확인한다.
import auth
from auth import fileio

def _load_tickets() -> list:
    data = fileio._read_json(auth.TICKETS_PATH, {})
    items = data.get("tickets") if isinstance(data, dict) else None
    now = int(time.time())
    return [t for t in (items or []) if int(t.get("exp", 0)) > now]


def issue_ticket(label: str = "") -> str:
    """1회용 기기 등록 티켓 발급 → URL/QR에 실을 원문 반환.

    QR을 띄우는 시점에 맥에 대한 물리적 접근이 이미 증명된 것이므로, 스캔을
    기기 등록 승인으로 인정한다(상시 토큰을 URL에 박는 기존 방식의 대체).
    """
    raw = secrets.token_urlsafe(24)
    tickets = _load_tickets()
    tickets.append({
        "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "exp": int(time.time()) + auth.TICKET_TTL,
        "label": (label or "")[:60],
    })
    fileio._write_json_secure(auth.TICKETS_PATH, {"version": 1, "tickets": tickets})
    return raw


def consume_ticket(raw: str) -> Optional[dict]:
    """티켓 검증 + 즉시 소멸(1회용). 유효하면 티켓 레코드, 아니면 None."""
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    tickets = _load_tickets()
    hit = None
    for t in tickets:
        if hmac.compare_digest(t.get("hash", ""), digest):
            hit = t
            break
    if hit is None:
        return None
    tickets.remove(hit)
    fileio._write_json_secure(auth.TICKETS_PATH, {"version": 1, "tickets": tickets})
    return hit
