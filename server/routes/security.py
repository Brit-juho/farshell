"""보안 상태 읽기 전용 조회 — 설정 → 「보안」 탭(60-settings-palette.md §2).

**읽기만 한다.** 비밀번호 설정·OTP 활성화·기기 폐기는 전부 `fsh` CLI 몫이다.
공개 터널 너머에서 인증 수단 자체를 바꿀 수 있게 만드는 건, 그 인증이 지키려던
것을 그 인증으로 들어와서 풀 수 있다는 뜻이라 설계상 배제한다(2.0 결정 유지).

두 경로 모두 AuthMiddleware 예외 목록에 없다 — 로그인한 세션만 본다.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

import auth

router = APIRouter()

# 기기 id는 sha256 해시의 앞 16자다. 해시 자체로는 쿠키를 만들 수 없지만,
# 화면에 식별용으로 필요한 건 사람이 목록에서 한 행을 골라낼 만큼뿐이라
# 더 짧게 자른다(`fsh device revoke`도 접두사 매칭이라 그대로 쓸 수 있다).
_ID_DISPLAY_LEN = 8


@router.get("/api/devices")
async def list_devices(request: Request):
    """등록 기기 목록. `fsh device list`와 같은 저장소(~/.vt/devices.json)를 읽는다."""
    current = auth.verify_device(request.cookies.get("vt_device", "")) or {}
    current_id = current.get("id", "")
    devices = []
    for d in auth.list_devices():
        did = str(d.get("id", ""))
        devices.append({
            "id": did[:_ID_DISPLAY_LEN],
            "label": d.get("label", ""),
            "added_at": int(d.get("added_at", 0) or 0),
            "last_seen": int(d.get("last_seen", 0) or 0),
            "current": bool(did and did == current_id),
        })
    return {"devices": devices}


@router.get("/api/auth/elevation")
async def elevation_status(request: Request):
    """현재 세션의 승격(N31) 상태.

    ADR-27로 push/PR을 만들지 않기로 하면서 승격을 요구하는 경로가 사라졌다.
    기능은 살아 있지만 지금은 아무도 쓰지 않는다 — 화면이 "항상 비활성"으로만
    보이면 고장으로 오해되므로 그 사실(`unused`)을 응답에 같이 실어 보낸다.
    """
    until = auth.session_elevated_until(request.cookies.get("vt_session", ""))
    return {
        "elevated": bool(until),
        "elevated_until": int(until),
        "unused": True,
        "unused_reason": "ADR-27",
    }
