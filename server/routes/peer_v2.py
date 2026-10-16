"""다른 Mac(grant 기반) — 프로토콜 v2 라우트 `/api/peer/v2/*`. 본체는 `authz/remote.py`(공유 세션·요청·회수 알림)와 `delegation.py`(위임 원장).

모든 라우트는 `peer_protocol.capability_v2` 를 지난다: 서명 + 본문 해시 + (대부분) 대리 주체(`X-Peer-Obo`) 확인, peer 당 분당 600건, 감사.
인가는 등급이 아니라 **grant** 다 — 핸들러가 리소스를 정해 엔진에 묻고, 없으면 B 의 사람에게 가는 승인 요청(202)을 만든다.
`VT_AUTHZ_ENFORCE` 에 `peer` 가 없으면 전부 «없는 라우트»와 같은 404 로 답한다(위임은 `deleg`, pair 참여는 `pair` 도 필요).

  POST /v2/authz/request            대리 주체가 B 의 세션·원격 위임 쿼터에 대한 권한을 요청 -> 202 {requestId}
  GET  /v2/authz/status/{id}        내 요청의 상태(pending | allowed | denied)
  GET  /v2/authz/grants             이 호스트가 B 에 가진 권한(대리 주체가 있으면 그 주체의 것). 회수 확인(N-13)의 근거
  GET  /v2/session/shared           B 의 공유된 세션과 내 권한 상태
  POST /v2/session/read|send        공유 세션 읽기·한 줄 입력
  POST /v2/deleg/spawn|send|close   부모(A 의 에이전트)가 B 에 자식 세션을 만들고 지시·닫기
  POST /v2/deleg/report             자식(B 의 에이전트)의 결과를 부모 호스트(A)로
  POST /v2/pair/join, GET /v2/pair/status/{id}   다른 Mac 의 에이전트가 B 의 pair 에 합류하고 결과를 읽는다
  POST /v2/revoke                   B 가 A 의 권한을 거뒀다는 서명된 알림(A 가 B 에 되물어 확인한다)
  POST /v2/rotate, /v2/rotate/confirm   연결 secret 회전(`fsh host rotate`)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import host_store
from authz import remote
from authz.errors import ShareError
from i18n import tr
from routes.peer_protocol import capability_v2

logger = logging.getLogger(__name__)

router = APIRouter()


def _body(data: bytes) -> dict:
    try:
        b = json.loads(data.decode("utf-8") or "{}")
    except ValueError:
        return {}
    return b if isinstance(b, dict) else {}


def _err(e: ShareError):
    return JSONResponse(e.body(), status_code=e.status), e.code


async def _announce(view: dict | None) -> None:
    """새 보류 요청을 이 서버의 사람에게 — 열린 브라우저가 있으면 이벤트, 없으면 웹 푸시(세션 이름·내용 없이)."""
    if not view:
        return
    try:
        import routes.agent_share as share_routes
        await share_routes.announce_view(view, tr("에이전트 접근 요청이 있습니다"), kind="generic", legacy=False)
    except Exception as e:  # noqa: BLE001 — 알림 실패가 요청을 막으면 안 된다
        logger.debug(f"원격 요청 알림 실패: {e}")


async def _pending(call, grant, body):
    """(상태, 본문, 새 요청 view) 를 돌려주는 remote 함수를 돌려 응답으로 바꾼다."""
    try:
        status, out, view = await asyncio.to_thread(call, grant, body)
    except ShareError as e:
        return _err(e)
    await _announce(view)
    if status == 200:
        return out
    return JSONResponse(out, status_code=status), out.get("state") or "pending"


# --- 요청 · 상태 · grant ----------------------------------------------------------------------------------------


@router.post("/api/peer/v2/authz/request")
@capability_v2("v2/authz/request", obo_required=True)
async def v2_authz_request(request: Request, grant: dict, data: bytes):
    return await _pending(remote.request_access, grant, _body(data))


@router.get("/api/peer/v2/authz/status/{request_id}")
@capability_v2("v2/authz/status", obo_required=True)
async def v2_authz_status(request: Request, grant: dict, data: bytes, request_id: str):
    try:
        return await asyncio.to_thread(remote.request_status, grant, request_id[:80])
    except ShareError as e:
        return _err(e)


@router.get("/api/peer/v2/authz/grants")
@capability_v2("v2/authz/grants")
async def v2_authz_grants(request: Request, grant: dict, data: bytes):
    return await asyncio.to_thread(remote.grants_view, grant)


# --- 공유 세션 ---------------------------------------------------------------------------------------------------


@router.get("/api/peer/v2/session/shared")
@capability_v2("v2/session/shared", obo_required=True)
async def v2_session_shared(request: Request, grant: dict, data: bytes):
    try:
        return await asyncio.to_thread(remote.shared_sessions, grant)
    except ShareError as e:
        return _err(e)


@router.post("/api/peer/v2/session/read")
@capability_v2("v2/session/read", obo_required=True)
async def v2_session_read(request: Request, grant: dict, data: bytes):
    return await _pending(remote.session_read, grant, _body(data))


@router.post("/api/peer/v2/session/send")
@capability_v2("v2/session/send", obo_required=True)
async def v2_session_send(request: Request, grant: dict, data: bytes):
    return await _pending(remote.session_send, grant, _body(data))


# --- 원격 위임 ---------------------------------------------------------------------------------------------------


def _deleg_off():
    return JSONResponse({"error": "deleg_disabled", "reason": tr("이 서버는 원격 위임이 꺼져 있습니다 — VT_AUTHZ_ENFORCE 에 deleg 가 필요합니다")},
                        status_code=409), "위임 꺼짐"


async def _deleg(call, grant, body):
    import delegation
    if not remote.deleg_enabled():
        return _deleg_off()
    try:
        status, out, view = await asyncio.to_thread(call, grant, body)
    except ShareError as e:
        return _err(e)
    except delegation.DelegationError as e:
        return JSONResponse(e.body(), status_code=e.status), e.code
    await _announce(view)
    if status == 200:
        try:
            import routes.system as system_routes
            system_routes.kick_sessions()
        except Exception:  # noqa: BLE001
            pass
        return out
    return JSONResponse(out, status_code=status), out.get("state") or "pending"


@router.post("/api/peer/v2/deleg/spawn")
@capability_v2("v2/deleg/spawn", obo_required=True)
async def v2_deleg_spawn(request: Request, grant: dict, data: bytes):
    return await _deleg(remote.deleg_spawn_in, grant, _body(data))


@router.post("/api/peer/v2/deleg/send")
@capability_v2("v2/deleg/send", obo_required=True)
async def v2_deleg_send(request: Request, grant: dict, data: bytes):
    return await _deleg(remote.deleg_send_in, grant, _body(data))


@router.post("/api/peer/v2/deleg/close")
@capability_v2("v2/deleg/close", obo_required=True)
async def v2_deleg_close(request: Request, grant: dict, data: bytes):
    return await _deleg(remote.deleg_close_in, grant, _body(data))


@router.post("/api/peer/v2/deleg/report")
@capability_v2("v2/deleg/report", obo_required=True)
async def v2_deleg_report(request: Request, grant: dict, data: bytes):
    return await _deleg(remote.deleg_report_in, grant, _body(data))


# --- pair 합류 ---------------------------------------------------------------------------------------------------


@router.post("/api/peer/v2/pair/join")
@capability_v2("v2/pair/join", obo_required=True)
async def v2_pair_join(request: Request, grant: dict, data: bytes):
    from authz import pairs
    if not remote.pair_enabled():
        return JSONResponse({"error": "pair_disabled", "reason": tr("이 서버는 pair 기능이 꺼져 있습니다")}, status_code=409), "pair 꺼짐"
    try:
        out = await asyncio.to_thread(remote.pair_join, grant, _body(data))
    except (ShareError, pairs.PairError) as e:
        return _err(e)
    try:
        from authz import events as authz_events
        await authz_events.emit(authz_events.request_event(out.get("approvalId", ""), "pair"))
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "pair": out["pair"]}, f"합류 {out['pair'].get('id', '')}"


@router.get("/api/peer/v2/pair/status/{pair_id}")
@capability_v2("v2/pair/status", obo_required=True)
async def v2_pair_status(request: Request, grant: dict, data: bytes, pair_id: str):
    from authz import pairs
    if not remote.pair_enabled():
        return JSONResponse({"error": "pair_disabled", "reason": tr("이 서버는 pair 기능이 꺼져 있습니다")}, status_code=409), "pair 꺼짐"
    try:
        return {"ok": True, "pair": await asyncio.to_thread(remote.pair_status, grant, pair_id[:80])}
    except (ShareError, pairs.PairError) as e:
        return _err(e)


# --- 회수 알림 · secret 회전 ---------------------------------------------------------------------------------------


@router.post("/api/peer/v2/revoke")
@capability_v2("v2/revoke")
async def v2_revoke(request: Request, grant: dict, data: bytes):
    """B 가 보낸 «당신의 권한을 거뒀다» — 서명은 B 라는 것만 보증한다. 곧이곧대로 믿지 않고 B 에 되물어 확인한다(`remote.on_revoke`)."""
    status, out = await asyncio.to_thread(remote.on_revoke, grant, _body(data))
    if status == 200:
        return out, out.get("state", "")
    return JSONResponse(out, status_code=status), out.get("state", "")


@router.post("/api/peer/v2/rotate")
@capability_v2("v2/rotate")
async def v2_rotate(request: Request, grant: dict, data: bytes):
    """연결 secret 회전 1단계 — 새 secret 을 만들어 10분간 **보류**하고(옛 secret 도 계속 받는다) 응답에 싣는다. A 가 새 secret 으로 서명한
    첫 요청(`/v2/rotate/confirm`)을 보내면 그때 정식이 된다. 절차 전체는 `host_store` 「secret 회전」."""
    secret = await asyncio.to_thread(host_store.begin_rotation, grant["id"])
    if secret is None:
        return JSONResponse({"error": "not_found"}, status_code=404), "없는 호스트"
    return {"ok": True, "secret": secret, "expiresIn": host_store.NEXT_TTL, "serverTime": time.time()}, "새 secret 보류"


@router.post("/api/peer/v2/rotate/confirm")
@capability_v2("v2/rotate/confirm")
async def v2_rotate_confirm(request: Request, grant: dict, data: bytes):
    """회전 2단계 — 이 요청이 새 secret 으로 서명돼 인증을 통과하면 이미 정식이 됐다(`peer_protocol` 이 올린다). 아직 보류가 남아 있으면
    옛 secret 으로 서명된 요청이라 확인이 아니다(409)."""
    if host_store.pending_secret(grant):
        return JSONResponse({"error": "not_promoted", "reason": tr("새 secret 으로 서명된 요청이 아닙니다")}, status_code=409), "미확인"
    return {"ok": True, "rotatedAt": grant.get("rotatedAt") or 0, "previousRotatedAt": grant.get("previousRotatedAt") or 0}, "회전 확인"
