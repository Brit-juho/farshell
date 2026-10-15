"""authz 디버그 엔드포인트 — shadow 불일치 조회. **사람 전용·읽기 전용.**

`GET /api/authz/shadow?limit=` → 최근 shadow 기록(새 것 먼저) + 프로세스 안 카운터 + 묶음 요약.
기록에는 내용이 없다(주체 id·scope·리소스·판정 쌍뿐, shadow.py 머리말). relay 허용 목록에 없다.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

import auth
from i18n import tr
from authz import shadow

human_router = APIRouter(dependencies=[Depends(auth.require_human_session)])
router = APIRouter()


@human_router.get("/api/authz/shadow")
async def shadow_log(limit: int = Query(100, ge=1, le=1000)):
    records = await asyncio.to_thread(shadow.tail, limit)
    return {"enabled": shadow.enabled(), "source": shadow.source(), "stats": shadow.STATS.as_dict(),
            "summary": shadow.summary(records), "records": records}


@router.get("/api/authz/whoami")
async def whoami(request: Request):
    """호출 주체. 소켓 요청은 게이트(`authz/sock.py`)가 앱에 닿기 전에 직접 답한다 —
    여기까지 오는 것은 TCP 요청뿐이고, TCP 에는 커널이 보증하는 peer 가 없어서 답하지 않는다."""
    return JSONResponse({"error": "socket_only", "reason": tr("unix socket 으로만 호출자를 확인할 수 있습니다")}, status_code=403)
