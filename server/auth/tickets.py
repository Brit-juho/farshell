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


# 티켓 용도. 기기 등록(`fsh mobile` QR)과 노출 포트 `session` 모드 입장이 한 저장소를 쓰므로, 소비하는 쪽이
# 자기 용도의 티켓만 받아야 한다 — 안 그러면 노출 앱 주소(`/__fsh/cb?t=`)에 실려 다니는 60초짜리 포트 티켓으로
# 메인 `/api/auth`에서 기기를 등록해 비밀번호·OTP 없이 셸을 얻는다.
PURPOSE_DEVICE = "device"
PURPOSE_PORT_SHARE = "port-share"
_PURPOSES = (PURPOSE_DEVICE, PURPOSE_PORT_SHARE)
_RESERVED = ("hash", "exp", "label", "purpose")


def _purpose_of(rec: dict) -> str:
    """기록된 용도. 이 필드가 생기기 전에 발급된 티켓(최대 5분 수명)은 share 묶음 여부로 가린다."""
    p = rec.get("purpose")
    if p in _PURPOSES:
        return p
    return PURPOSE_PORT_SHARE if "share" in rec else PURPOSE_DEVICE


def issue_ticket(label: str = "", ttl: int | None = None, *, purpose: str = PURPOSE_DEVICE, **extra) -> str:
    """1회용 기기 등록 티켓 발급 → URL/QR에 실을 원문 반환.

    QR을 띄우는 시점에 맥에 대한 물리적 접근이 이미 증명된 것이므로, 스캔을
    기기 등록 승인으로 인정한다(상시 토큰을 URL에 박는 기존 방식의 대체).

    `ttl`·`extra`는 2.2의 노출 포트 `session` 모드가 쓴다: 티켓을
    특정 share와 기기에 **묶어서** 발급하고, 수명을 기본 5분이 아니라 60초로
    줄인다. 티켓이 URL에 실리는 것은 감수하는 대신 창을 좁히는 쪽이다.
    묶인 값은 `consume_ticket`이 그대로 돌려주므로 호출부가 대조할 수 있다.
    """
    if purpose not in _PURPOSES:
        raise ValueError(f"unknown ticket purpose: {purpose!r}")
    raw = secrets.token_urlsafe(24)
    tickets = _load_tickets()
    rec = {
        "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "exp": int(time.time()) + int(ttl if ttl is not None else auth.TICKET_TTL),
        "label": (label or "")[:60],
        "purpose": purpose,
    }
    # 예약어를 덮어쓰지 못하게 한다 — 호출부가 exp를 늘려버리면 수명 제한이 무의미해진다.
    for k, v in extra.items():
        if k not in _RESERVED:
            rec[k] = v
    tickets.append(rec)
    fileio._write_json_secure(auth.TICKETS_PATH, {"version": 1, "tickets": tickets})
    return raw


def consume_ticket(raw: str, purpose: str) -> Optional[dict]:
    """티켓 검증 + 즉시 소멸(1회용). 유효하고 `purpose`가 맞으면 티켓 레코드, 아니면 None.

    용도가 다른 티켓은 **소멸시키지 않고** None — 엉뚱한 곳에 넣어 본 것만으로 정상 티켓을 태우지 않는다.
    `purpose`는 필수 인자다(기본값을 두면 새 호출부가 용도 검사를 빠뜨려도 조용히 통과한다)."""
    if purpose not in _PURPOSES:
        raise ValueError(f"unknown ticket purpose: {purpose!r}")
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    tickets = _load_tickets()
    hit = None
    for t in tickets:
        if hmac.compare_digest(t.get("hash", ""), digest):
            hit = t
            break
    if hit is None or _purpose_of(hit) != purpose:
        return None
    tickets.remove(hit)
    fileio._write_json_secure(auth.TICKETS_PATH, {"version": 1, "tickets": tickets})
    return hit
