"""`/api/health/live`·`/api/health/ready` — 서버가 실제로 일을 받을 수 있는지.

`fsh status`/`fsh doctor`와 서버 워치독(`server_watchdog.py`)이 부른다. 예전에는 "포트가 열렸는가"만
봐서, 기본 스레드 풀이 막혀 인증된 API 가 전부 응답하지 않던 몇 시간 동안 `fsh status`는 정상이라고
했다(2026-11-05 M5).

- live 는 인증 없이 열린다(로그인 전 경로와 같은 취급). 돌려주는 것은 ok·pid·가동 시간뿐이다.
- ready 는 인증된 호출자(쿠키·기계 토큰·unix 소켓)만. 경로·작업 이름이 담기므로 공개하지 않는다.
판정은 이벤트 루프 위에서 하고 스레드를 쓰지 않는다(빈 작업 확인에도 기한이 있다) — 풀이 전부
막혀도 이 응답은 온다.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from resilience import health

router = APIRouter()


@router.get("/api/health/live")
async def health_live():
    return health.live()


@router.get("/api/health/ready")
async def health_ready():
    data = await health.ready()
    return JSONResponse(data, status_code=200 if data["ok"] else 503,
                        headers={"Cache-Control": "no-store"})
