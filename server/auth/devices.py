"""등록 기기 화이트리스트 — `~/.vt/devices.json`.

로그인은 **항상 비밀번호**로 한다. 기기 등록은 "처음 보는 기기인가"를 가리는
별개의 관문이고, 한 번 등록된 기기는 장기 쿠키로 이후 비밀번호만으로 통과한다
(폰이 LTE↔wifi를 오가도 안 끊기도록 IP가 아니라 기기 단위로 신뢰한다).

저장하는 건 쿠키 원문이 아니라 **sha256 해시뿐**이다 — devices.json이 통째로
새도 쿠키를 만들어낼 수 없다.
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

def _load_devices() -> list:
    data = fileio._read_json(auth.DEVICES_PATH, {})
    devices = data.get("devices") if isinstance(data, dict) else None
    return devices if isinstance(devices, list) else []


def _save_devices(devices: list) -> None:
    fileio._write_json_secure(auth.DEVICES_PATH, {"version": 1, "devices": devices})


def _find_device(device_id: str) -> Optional[dict]:
    for d in _load_devices():
        if d.get("id") == device_id:
            return d
    return None


def register_device(label: str = "") -> tuple[str, str]:
    """새 기기 등록 → (쿠키에 심을 secret 원문, device_id).

    저장하는 건 sha256 해시뿐이다. devices.json이 통째로 새도 쿠키를 만들어낼 수 없다
    — 비밀번호를 scrypt 해시로만 두는 것과 같은 원칙.
    """
    secret = secrets.token_hex(32)
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    device_id = digest[:16]
    now = int(time.time())
    devices = [d for d in _load_devices() if d.get("id") != device_id]
    devices.append({
        "id": device_id,
        "hash": digest,
        "label": (label or "기기")[:60],
        "added_at": now,
        "last_seen": now,
    })
    _save_devices(devices)
    return secret, device_id


def verify_device(secret: str) -> Optional[dict]:
    """`vt_device` 쿠키 값 → 등록된 기기 레코드. 미등록/만료면 None."""
    if not secret:
        return None
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    now = int(time.time())
    devices = _load_devices()
    for d in devices:
        stored = d.get("hash", "")
        if stored and hmac.compare_digest(stored, digest):
            if now - int(d.get("added_at", now)) > auth.DEVICE_TTL:
                return None
            # last_seen은 하루 단위로만 갱신 — 매 요청 디스크 쓰기를 피한다.
            if now - int(d.get("last_seen", 0)) > 86400:
                d["last_seen"] = now
                try:
                    _save_devices(devices)
                except OSError:
                    pass
            return d
    return None


def list_devices() -> list:
    """등록 기기 목록(해시 제외)."""
    return [
        {k: v for k, v in d.items() if k != "hash"}
        for d in sorted(_load_devices(), key=lambda x: x.get("added_at", 0))
    ]


def rename_device(prefix: str, label: str) -> Optional[dict]:
    """id 접두사로 기기 별명 변경. 못 찾거나 여럿이면 None.

    자동 라벨(`_device_label`이 UA에서 뽑는 "iPhone"/"Mac")만으로는 같은 기종이
    여럿이면 구분이 안 된다 — 목록에서 어느 행이 어느 기기인지 알 수 있어야
    `fsh device revoke`를 안심하고 쓸 수 있다.

    **CLI 전용이다.** 웹에서 바꾸게 하지 않는다 — routes/security.py가 "보안 탭은
    읽기 전용"을 불변식으로 두고 있고(프런트 테스트로도 고정), 별명이 인증 수단은
    아니지만 그 탭에 입력칸을 하나 여는 순간 그 불변식이 무너진다.
    """
    prefix = (prefix or "").strip().lower()
    if not prefix:
        return None
    devices = _load_devices()
    hits = [d for d in devices if d.get("id", "").startswith(prefix)]
    if len(hits) != 1:
        return None
    hits[0]["label"] = (label or "").strip()[:60] or hits[0].get("label", "기기")
    _save_devices(devices)
    return {k: v for k, v in hits[0].items() if k != "hash"}


def revoke_device(prefix: str) -> list:
    """id 접두사로 기기 폐기. 폐기된 기기 목록 반환(해당 기기의 세션도 함께 죽는다)."""
    prefix = (prefix or "").strip().lower()
    if not prefix:
        return []
    devices = _load_devices()
    removed = [d for d in devices if d.get("id", "").startswith(prefix)]
    if removed:
        _save_devices([d for d in devices if d not in removed])
    return [{k: v for k, v in d.items() if k != "hash"} for d in removed]
