"""authz 디버그 엔드포인트(plan-2.6 1단계) — shadow 불일치 조회. **사람 전용·읽기 전용.**

`GET /api/authz/shadow?limit=` → 최근 shadow 기록(새 것 먼저) + 프로세스 안 카운터 + 묶음 요약.
기록에는 내용이 없다(주체 id·scope·리소스·판정 쌍뿐, shadow.py 머리말). relay 허용 목록에 없다.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query

import auth
from authz import shadow

human_router = APIRouter(dependencies=[Depends(auth.require_human_session)])


@human_router.get("/api/authz/shadow")
async def shadow_log(limit: int = Query(100, ge=1, le=1000)):
    records = await asyncio.to_thread(shadow.tail, limit)
    return {"enabled": shadow.enabled(), "source": shadow.source(), "stats": shadow.STATS.as_dict(),
            "summary": shadow.summary(records), "records": records}
