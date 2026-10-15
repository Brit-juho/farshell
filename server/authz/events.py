"""승인함 이벤트 — `/ws-workspace` 로 내보내는 내용 없는 알림.

페이로드에는 id 와 종류(kind)만 싣는다 — 세션 이름·닉네임·요청 사유·메시지는 실리지 않는다(잠금화면·로그에 새지 않게).
화면은 이벤트를 받으면 `GET /api/authz/approvals` 를 다시 읽는다.

    authz_request   {type, id, kind}                  새 보류 요청
    authz_resolved  {type, id, kind, state}           결정됨(allowed|denied|cleared)
    authz_revoked   {type, id, kind}                  권한 회수(grant id)

옛 `agent_share_request*` 이벤트는 한 릴리스 동안 그대로 함께 나간다(`routes/agent_share.py`).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

REQUEST, RESOLVED, REVOKED = "authz_request", "authz_resolved", "authz_revoked"
KINDS = ("share", "msg", "peer", "generic")


def request_event(rid: str, kind: str) -> dict:
    return {"type": REQUEST, "id": str(rid), "kind": kind}


def resolved_event(rid: str, kind: str, state: str) -> dict:
    return {"type": RESOLVED, "id": str(rid), "kind": kind, "state": state}


def revoked_event(gid: str, kind: str) -> dict:
    return {"type": REVOKED, "id": str(gid), "kind": kind}


async def emit(msg: dict) -> None:
    """실패해도 호출을 막지 않는다."""
    try:
        import routes.system as system_routes
        await system_routes.broadcast_workspace_event(msg)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"승인함 이벤트 알림 실패: {e}")
