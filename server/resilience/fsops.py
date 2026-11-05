"""사용자 경로 파일 작업의 공통 실행 — `fs` 풀 + 기한 + 차단기.

사용자가 준 경로(파일 탭·코드 뷰어·git 화면의 path)는 어디든 될 수 있다. 홈 전체가 열람 경계면
`~/OrbStack`(NFS) 같은 곳도 들어온다. 그런 경로의 `resolve()`·`stat()`·`open()`은 이벤트 루프 위에서
부르면 서버 전체가, 기본 실행기에서 부르면 그 풀이 멈출 수 있다. 그래서
- 경로 검증(`fsguard.resolve_under_roots`)부터 읽기까지 전부 `fs` 풀에서 기한을 두고 하고,
- 기한을 넘긴 경로는 차단기로 잠시 막아 같은 곳을 반복해서 찌르지 않으며,
- 라우트는 `PathBlocked`·`OpTimeout`·`Busy`를 503 `fs_unavailable`로 바꿔 답한다(`unavailable_response`).
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

from . import breaker, pools

FS_TIMEOUT = float(os.environ.get("VT_FS_TIMEOUT", "10"))

UNAVAILABLE = (breaker.PathBlocked, pools.OpTimeout, pools.Busy)


async def call(fn: Callable, *args, path: str, op: str, timeout: Optional[float] = None, **kwargs) -> Any:
    breaker.check(path)
    t = FS_TIMEOUT if timeout is None else timeout
    try:
        result = await pools.run("fs", fn, *args, op=op, key=path, timeout=t, **kwargs)
    except pools.OpTimeout:
        breaker.trip(path, f"{op} {t:g}s 초과")
        raise
    breaker.success(path)
    return result


def unavailable_response(e: Exception):
    from fastapi.responses import JSONResponse
    from i18n import tr
    if isinstance(e, breaker.PathBlocked):
        reason = tr("이 위치가 응답하지 않아 잠시 열지 않습니다 ({sec}초 뒤 다시 시도)", sec=int(e.retry_in))
        retry = int(e.retry_in)
    elif isinstance(e, pools.Busy):
        reason = tr("파일 작업이 밀려 있습니다 — 잠시 뒤 다시 시도하세요")
        retry = 5
    else:
        reason = tr("이 위치가 응답하지 않습니다 — 네트워크·가상 드라이브라면 연결 상태를 확인하세요")
        retry = 30
    return JSONResponse({"error": "fs_unavailable", "reason": reason, "retry_after": retry},
                        status_code=503, headers={"Retry-After": str(retry)})
