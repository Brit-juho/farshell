"""N3 — 기기 스코프 설정 저장소.

60-settings-palette.md §1: 설정 항목 중 일부(폰트 크기 %·rail/dock 폭·keybar
접힘 등)는 "지금 이 기기에서 어떻게 보이는가"라서 `/api/workspace`(전역,
ADR-5)에 같이 두면 폰에서 조절한 값이 맥 터미널까지 커진다. 그래서 저장을
완전히 분리한다 — 스키마(어떤 키가 global인지 device인지)는 프런트
`core/settings.js`가 유일한 소스이고, 서버는 여기서도 값의 의미를 모르고
기기별로 보관만 한다(workspace.py의 같은 원칙).

기기 식별: `vt_device` 쿠키(auth 패키지가 이미 관리하는 그 쿠키 — 기기 등록은
매 비밀번호 로그인마다 조용히 일어나므로, OTP를 켜지 않은 사용자도 쿠키를
이미 갖고 있다). 쿠키가 없는 접속(비밀번호 미설정 로컬 전용 등)은 파일
하나(`local.json`)를 공유한다 — 그 경우 "기기가 여럿"이라는 개념 자체가
없다.
"""

from __future__ import annotations

from pathlib import Path

import auth

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


def save(device_id: str, values: dict) -> dict:
    """얕은 병합(workspace.py의 settings 병합과 같은 이유 — 다른 탭/새로고침이
    동시에 다른 키를 바꿔도 한쪽이 통째로 안 지워진다)."""
    cur = load(device_id)
    merged = {**cur, **(values or {})}
    auth._write_json_secure(_path(device_id), merged)
    return merged
