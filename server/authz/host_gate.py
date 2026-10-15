"""이 서버에서 **다른 맥을 조작**하는 길의 권한 — `VT_AUTHZ_ENFORCE=peer` (H13).

`/api/hosts`·`/api/hosts/{id}/…`·`/api/hosts/{id}/relay/…`, `/ws/hosts/{id}/relay/…`, `/ws/remote/…` 는 이 서버가 저장된 secret 으로 **상대 맥에 서명해
대신 일을 시키는** 길이다. 예전에는 인증만 되면 기계 토큰과 에이전트도 쓸 수 있었다. 켜지면 호출자가 `host:<id>` 에 필요한 scope 를 가져야 한다:
로그인한 사람은 기본으로 가지고(`defaults.rules_for`), `local`(cli·svc)은 목록·ping 만, 에이전트와 명시한 기계 토큰은 못 가진다. 인증 수단이 없는
서버의 무인증 호출은 서버가 원래 다 열어 둔 것이라 그대로다.

거부 응답은 403 `host_access_denied`(WebSocket 은 close 4003)이고, 거부는 authz 감사에 남는다(요청 내용 없음, 같은 호출자·호스트·scope 는 60초에 한 줄).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from i18n import tr

from . import audit, engine, flags, store
from . import principals as P

logger = logging.getLogger(__name__)

_denied_at: dict[tuple, float] = {}


def enforced() -> bool:
    return flags.enforced("peer")


def local_principal(conn) -> P.Principal:
    """HTTP 요청·WebSocket 의 호출 주체. unix socket 게이트가 세운 주체가 있으면 그것, 아니면 요청의 인증(쿠키·토큰)."""
    try:
        if conn.scope.get("fsh_sock") is True:
            p = getattr(conn.state, "fsh_principal", None)
            if p is not None:
                return p
        return P.from_request(conn)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"호출 주체를 만들지 못했다 — 인증 안 된 것으로 본다: {e}")
        return P.unauthenticated()


def decide(conn, host_id: str, scope: str, ctx: Optional[dict] = None) -> engine.Decision:
    """호출자가 `host:<id>` 에 `scope`(+조건)를 가졌는가."""
    p = local_principal(conn)
    try:
        d = engine.decide(p, scope, f"host:{host_id}", ctx or {}, store.snapshot(time.time()))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"호스트 권한 엔진을 쓸 수 없다 — 거부: {e}")
        return engine.Decision(False, "authz_unavailable", None, "error")
    if not d.allow:
        _audit_denied(p, host_id, scope, d)
    return d


def _audit_denied(p: P.Principal, host_id: str, scope: str, d: engine.Decision) -> None:
    key = (p.id, host_id, scope, d.reason)
    now = time.time()
    if now - _denied_at.get(key, 0.0) < 60.0:
        return
    if len(_denied_at) > 1024:
        _denied_at.clear()
    _denied_at[key] = now
    try:
        audit.append({"principal": p.id, "action": "host.access", "resource": f"host:{host_id}", "scopes": [scope],
                      "decision": "deny", "result": "denied", "reason": d.reason})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"호스트 접근 거부 감사 기록 실패: {e}")


def denied_body() -> dict:
    return {"error": "host_access_denied",
            "reason": tr("이 호출자는 다른 맥을 조작할 수 없습니다 — 브라우저에서 로그인한 사람만 할 수 있습니다")}


def dependency(scope: str, ctx: Optional[dict] = None):
    """FastAPI 의존성 — 켜졌을 때만 호출자가 `host:<id>`(경로의 host_id, 없으면 목록 `host:*`)에 scope 를 가졌는지 본다."""
    from starlette.exceptions import HTTPException

    def dep(request):      # noqa: ANN001 — FastAPI 가 Request 로 주입(아래 어노테이션 교체)
        if not enforced():
            return
        hid = request.path_params.get("host_id") or "*"
        d = decide(request, hid, scope, ctx)
        if not d.allow:
            raise HTTPException(status_code=403, detail=denied_body())

    from fastapi import Request
    dep.__annotations__["request"] = Request
    return dep


