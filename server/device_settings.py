"""N3 — 기기 스코프 설정 저장소.

60-settings-palette.md §1: 설정 항목 중 일부(폰트 크기 %·rail/dock 폭·keybar
접힘 등)는 "지금 이 기기에서 어떻게 보이는가"라서 `/api/workspace`(전역,
ADR-5)에 같이 두면 폰에서 조절한 값이 맥 터미널까지 커진다. 그래서 저장을
완전히 분리한다 — 스키마(어떤 키가 global인지 device인지)는 프런트
`core/settings.js`가 유일한 소스이고, 서버는 여기서도 값의 의미를 모르고
기기별로 보관만 한다(workspace.py의 같은 원칙).

기기 종류별 프로필(mobile/mac/windows/linux)은 로그인 쿠키와 독립적으로 보관한다.
기존 클라이언트는 쿠키별 저장소를 계속 사용한다. 프로필을 처음 읽을 때 현재
쿠키의 옛 설정에서 빠진 키만 이관하므로 재등록·쿠키 만료가 취향을 지우지 않는다.
프로필 파일은 저장소 밖 STATE_DIR 아래에 0600으로 저장한다.
"""

from __future__ import annotations

from pathlib import Path

import auth
import store_lock

DIR = auth.STATE_DIR / "device-settings"

# 파일명에 그대로 쓰이므로(`<id>.json`) 영숫자만 허용 — auth.verify_device()가
# 주는 id는 sha256 앞 16자리라 이미 이 형태지만, "local" 폴백까지 같은 검사를
# 통과해야 하므로 한 곳에서 검증한다.
import re

_SAFE_ID = re.compile(r"^[a-z0-9]{1,32}$")


def device_id_for(cookie_value: str) -> str:
    """`vt_device` 쿠키 원문 → 파일명으로 쓸 device_id. 미등록/없음이면 'local'."""
    known = auth.verify_device(cookie_value or "")
    did = known["id"] if known else "local"
    return did if _SAFE_ID.match(did) else "local"


def _path(device_id: str) -> Path:
    safe = device_id if _SAFE_ID.match(device_id or "") else "local"
    return DIR / f"{safe}.json"


def load(device_id: str) -> dict:
    return auth._read_json(_path(device_id), {})


PROFILES = frozenset({"mobile", "mac", "windows", "linux"})


def profile_id(profile: str) -> str:
    if profile not in PROFILES:
        raise ValueError("unknown device profile")
    return "profile" + profile


def load_profile(profile: str, legacy_id: str) -> dict:
    did = profile_id(profile)
    path = _path(did)
    with store_lock.exclusive(path.with_suffix(".lock")):
        cur = load(did)
        legacy = load(legacy_id)
        merged = {**legacy, **cur}
        if merged != cur:
            auth._write_json_secure(path, merged)
        return merged


def save(device_id: str, values: dict) -> dict:
    """부분 업데이트의 읽기·병합·원자적 저장을 같은 락으로 보호한다."""
    path = _path(device_id)
    with store_lock.exclusive(path.with_suffix(".lock")):
        cur = load(device_id)
        merged = {**cur, **(values or {})}
        auth._write_json_secure(path, merged)
        return merged
