"""authz — 승인함·권한·감사 API와 디버그 엔드포인트.

라우터가 셋이다(이 구분이 보안 경계다):

  - `human_router`  **사람 전용**(`require_human_session`). 결정·회수·비상 정지·shadow 조회. 기계 토큰·소켓·peer 는 403.
  - `read_router`   **사람 또는 unix socket 의 CLI**(pane 밖 `local:cli`). 읽기뿐이다 — 승인함·권한·감사 조회.
                    CLI 는 보기만 하고 승인·회수는 하지 못한다(승인은 웹/폰의 사람 몫).
  - `router`        **에이전트/소켓용**. 호출자가 확인된 에이전트(소켓 peer 가 pane 안, 또는 TCP 의 확인된 pane)일 때
                    자기 요청 생성·조회와 자기 권한 조회. 승인할 수 있는 것은 없다.

옛 `/api/agent/share/*` 는 그대로 있고 같은 함수(`routes.agent_share.decide_core`, 옛 모듈의 revoke·list)로 일한다.
어느 것도 relay 허용 목록에 없다 — 다른 맥에서 쓸 수 없다. 응답에는 세션 화면·입력·메시지 같은 내용이 없다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException

import agent_share
import auth
from i18n import tr
from authz import audit, denylog, events as authz_events, inbox, shadow
from authz import host_grant_svc, msg_svc, saga
from authz import principals as P
from authz import scopes as S
from authz.errors import ShareError

logger = logging.getLogger(__name__)

human_router = APIRouter(dependencies=[Depends(auth.require_human_session)])


def _human_or_cli(request: Request) -> None:
    """읽기 전용 조회의 호출자: 로그인한 사람, 또는 unix socket 로 온 pane 밖 CLI. 그 밖(에이전트·svc·기계 토큰·peer)은 403."""
    if request.scope.get("fsh_sock") is True:
        p = getattr(request.state, "fsh_principal", None)
        if p is not None and P.is_cli(p):
            return
        raise HTTPException(status_code=403, detail={
            "error": "human_or_cli_required",
            "reason": tr("이 조회는 로그인한 사람 또는 터미널의 fsh 명령만 할 수 있습니다")})
    auth.require_human_session(request)


read_router = APIRouter(dependencies=[Depends(_human_or_cli)])
router = APIRouter()


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class ApprovalsResponse(_Loose):
    approvals: list[dict]


class DecisionResponse(_Loose):
    ok: bool
    id: str
    kind: str
    state: str


class GrantsResponse(_Loose):
    grants: list[dict]


class RevokeResponse(_Loose):
    ok: bool
    id: str
    kind: str


class LeasesAdminResponse(_Loose):
    leases: list[dict]
    enforced: bool


class LeaseRevokeResponse(_Loose):
    ok: bool
    id: str
    released: int


class AuditResponse(_Loose):
    entries: list[dict]
    chain: str


class PanicResponse(_Loose):
    ok: bool
    revoked: dict
    failed: list[dict]


class RequestCreated(_Loose):
    state: str
    requestId: str
    kind: str
    reason: str


class MyRequestsResponse(_Loose):
    requests: list[dict]


class MyGrantsResponse(_Loose):
    grants: list[dict]


def _err(e: ShareError) -> JSONResponse:
    return JSONResponse(e.body(), status_code=e.status)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return b if isinstance(b, dict) else {}


def _actor(request: Request) -> Optional[P.Principal]:
    try:
        return P.from_request(request)
    except Exception:  # noqa: BLE001 — 주체를 못 만들면 권한 없는 사람으로(승인 못 함)
        return None


async def _saga_after(fn, *args) -> None:
    """회수 뒤 정리 saga(`VT_AUTHZ_ENFORCE=lease` 일 때만 일한다). 정리가 실패해도 회수 응답은 막지 않는다 — 남은 것은 sweep 이 이어한다."""
    try:
        await asyncio.to_thread(fn, *args)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"회수 정리 saga 시작 실패(sweep 이 이어한다): {type(e).__name__}: {e}")


def _names_alive():
    import share_identity
    rows = share_identity.list_pane_rows()
    if rows is None:
        return None, {}
    return {f"{r.sid}:{r.created}" for r in rows}, {f"{r.sid}:{r.created}": r.name for r in rows}


# --- 사람 전용: shadow --------------------------------------------------------------------------


@human_router.get("/api/authz/shadow")
async def shadow_log(limit: int = Query(100, ge=1, le=1000)):
    records = await asyncio.to_thread(shadow.tail, limit)
    return {"enabled": shadow.enabled(), "source": shadow.source(), "stats": shadow.STATS.as_dict(),
            "summary": shadow.summary(records), "records": records}


# --- 사람 전용: 폴더 lease 목록 · 회수 --------------------------------------------------------------


@human_router.get("/api/authz/leases", response_model=LeasesAdminResponse)
async def list_leases_admin():
    """모든 보유자의 lease(보유자는 닉네임뿐). 사람은 lease 에 막히지 않지만, 남의 lease 경로에서 쓰면 감사와 알림이 남는다."""
    from authz import leases
    return {"leases": await asyncio.to_thread(leases.list_for, P.human("-"), everyone=True), "enforced": leases.enforced()}


@human_router.delete("/api/authz/leases/{lease_id}", response_model=LeaseRevokeResponse)
async def revoke_lease_admin(lease_id: str, request: Request):
    """관리자 회수 — 파생(상속) lease 도 함께 사라진다."""
    from authz import leases
    try:
        return await asyncio.to_thread(leases.revoke_admin, _actor(request) or P.human("-"), lease_id)
    except ShareError as e:
        return _err(e)


# --- 읽기: 승인함 · 권한 · 감사 (사람 또는 CLI) ---------------------------------------------------------


@read_router.get("/api/authz/approvals", response_model=ApprovalsResponse)
async def list_approvals():
    """승인 대기 중인 요청을 종류(share·msg·peer·generic) 구분 없이 한 목록으로(만든 순). 항목마다 요청자(계보 포함)·리소스·요청한
    scope와 조건·프리셋·elevated 사유·경과/남은 시간. 세션 화면·메시지 내용은 없다."""
    snap = await asyncio.to_thread(agent_share.snapshot)
    return {"approvals": await asyncio.to_thread(inbox.list_approvals, snap, names_alive=_names_alive)}


@read_router.get("/api/authz/grants", response_model=GrantsResponse)
async def list_grants(subject: str = Query("", max_length=200), resource: str = Query("", max_length=200),
                      kind: str = Query("", max_length=20)):
    """모든 종류의 grant(share·msg·peer·deleg·generic). 승인한 쪽/경로, 프리셋, 만료·남은 횟수, 위임 계보를 함께 싣는다."""
    snap = await asyncio.to_thread(agent_share.snapshot)
    return {"grants": await asyncio.to_thread(inbox.list_grants, snap, subject=subject, resource=resource, kind=kind)}


_chain_cache: dict = {"sig": None, "val": None}
_chain_lock = threading.Lock()


def _chain_sig() -> tuple:
    p = audit.path()
    out = []
    for fp in [p] + [audit._rotated(p, i) for i in range(1, audit.GENERATIONS + 1)]:
        try:
            st = os.stat(fp)
            out.append((str(fp), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(out)


def chain_state() -> dict:
    """해시 체인을 서버가 직접 검증한 결과. 파일이 바뀌지 않았으면 이전 결과를 쓴다."""
    sig = _chain_sig()
    with _chain_lock:
        if _chain_cache["sig"] == sig and _chain_cache["val"] is not None:
            return _chain_cache["val"]
    ok, seq, n = audit.verify()
    val = {"chain": "ok" if ok else "broken", "lines": n, "brokenSeq": seq}
    with _chain_lock:
        _chain_cache["sig"], _chain_cache["val"] = sig, val
    return val


@read_router.get("/api/authz/audit", response_model=AuditResponse)
async def read_audit(principal: str = Query("", max_length=200), resource: str = Query("", max_length=200),
                     since: float = Query(0.0, ge=0.0), limit: int = Query(100, ge=1, le=500)):
    """감사 기록(최근 순) — 주 체인(`source: chain`)과 거부 기록(`source: denials`)을 합친다. 내용은 없다(글자 수·sha8 뿐).
    `chain` 은 서버가 해시 체인을 검증한 결과(`ok`|`broken`)다. 필터는 접두어 일치(주체·리소스)와 시각(epoch 초)이다."""
    entries = await asyncio.to_thread(audit.query, principal=principal, resource=resource, since=since, limit=limit)
    state = await asyncio.to_thread(chain_state)
    return {"entries": entries, **state, "denials": {"dropped": denylog.STATE.total_dropped, "writeFailures": denylog.STATE.failed}}


# --- 사람 전용: 결정 · 회수 · 비상 정지 -----------------------------------------------------------------


def request_kind(request_id: str) -> str:
    from authz import pairs
    if pairs.owns_request(request_id):
        return inbox.KIND_PAIR
    if inbox.owns_request(request_id):
        return inbox.KIND_GENERIC
    if msg_svc.enforced() and msg_svc.owns_request(request_id):
        return inbox.KIND_MSG
    if host_grant_svc.enforced() and host_grant_svc.owns_request(request_id):
        return inbox.KIND_PEER
    return inbox.KIND_SHARE


def _level_from_scopes(scopes: Optional[list]) -> Optional[str]:
    """승인자가 줄여서 준 scope → 옛 공유 등급. 입력 scope 가 있으면 readwrite, 읽기 scope 뿐이면 read."""
    if not scopes:
        return None
    sc = set(scopes)
    from authz import approvals
    if sc <= set(approvals.READ_SCOPES):
        return approvals.LEVEL_READ
    if approvals.WRITE_SCOPE in sc and sc <= set(approvals.READ_SCOPES) | {approvals.WRITE_SCOPE}:
        return approvals.LEVEL_READWRITE
    raise ShareError("bad_scopes", tr("공유 요청에는 읽기 scope 와 한 줄 입력 scope 만 줄 수 있습니다"), 400)


@human_router.post("/api/authz/approvals/{approval_id}/decision", response_model=DecisionResponse)
async def decide_approval(approval_id: str, request: Request):
    """요청 하나를 결정한다. 본문 `{decision: allow|deny|later, choice?: once|session|deny, scopes?, ttl?}`.

    - `later` 는 아무것도 바꾸지 않는다(요청은 남는다).
    - `choice`: `once` = 한 번만(max_uses=1), `session` = 요청자·리소스 세션이 살아 있는 동안(기본), `deny` = 거절(5분 쿨다운).
    - `scopes`/`ttl` 은 요청보다 **좁게**만 줄 수 있다. 선착순·멱등(같은 결정의 재전송은 처음 결과 + `replayed`, 다른 결정은 409)과
      승인권자 확인(elevated 가 필요한 요청은 elevated 사람만)은 옛 결정 함수 그대로다."""
    import routes.agent_share as share_routes
    body = await _body(request)
    if request_kind(approval_id) == inbox.KIND_PAIR:
        return await _decide_pair(approval_id, body, request)
    try:
        decision, choice, scopes, ttl = inbox.check_choice(body.get("decision"), body.get("choice"), body.get("scopes"), body.get("ttl"))
    except ShareError as e:
        return _err(e)
    kind = request_kind(approval_id)
    if decision == "later":
        snap = await asyncio.to_thread(agent_share.snapshot)
        pend = await asyncio.to_thread(inbox.list_approvals, snap, names_alive=_names_alive)
        if not any(a["id"] == approval_id or f"ap-{a['id']}" == approval_id for a in pend):
            return _err(ShareError("not_found", tr("대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)"), 404))
        return {"ok": True, "id": approval_id, "kind": kind, "state": "pending", "later": True}
    actor = _actor(request)
    try:
        if kind == inbox.KIND_GENERIC:
            snap = await asyncio.to_thread(agent_share.snapshot)
            res = await asyncio.to_thread(inbox.decide_generic, approval_id, decision, choice, scopes, ttl, actor, inbox._alive(snap))
            out = {"ok": True, "request": res["request"], "grant": res["grant"], "grantId": res.get("grantId")}
            if res.get("replayed"):
                out["replayed"] = True
        elif kind in (inbox.KIND_MSG, inbox.KIND_PEER):
            if choice == "once" or ttl is not None or scopes:
                raise ShareError("choice_unsupported", tr("이 종류의 요청은 허용/거절만 받습니다 — 한 번만·기간·범위 조정은 지원하지 않습니다"), 400)
            out = await share_routes.decide_core(approval_id, decision, None, request)
        else:
            level = _level_from_scopes(scopes) if decision == "allow" else None
            if decision == "allow" and (choice == "once" or ttl is not None):
                if not agent_share.enforced():
                    raise ShareError("choice_unavailable", tr("한 번만·기간 허용은 VT_AUTHZ_ENFORCE=share 가 켜져 있을 때만 됩니다"), 409)
                snap = await asyncio.to_thread(agent_share.snapshot)
                res = await asyncio.to_thread(inbox.decide_share_limited, approval_id, choice or "session", scopes, ttl, snap, actor)
                if not res.get("replayed"):
                    await share_routes._resolved(approval_id, "share", "allowed")
                out = {"ok": True, "request": res["request"], "grant": res["grant"], "grantId": res.get("grantId")}
                if res.get("replayed"):
                    out["replayed"] = True
            else:
                out = await share_routes.decide_core(approval_id, decision, level, request)
    except ShareError as e:
        return _err(e)
    if isinstance(out, JSONResponse):
        return out
    if kind == inbox.KIND_GENERIC and not out.get("replayed"):
        await share_routes._resolved(approval_id, kind, "allowed" if out.get("grant") else "denied")
    return {**out, "id": approval_id, "kind": kind, "state": "allowed" if out.get("grant") else "denied"}


async def _decide_pair(approval_id: str, body: dict, request: Request):
    """pair 승인(kind=`pair`) — 한 화면에서 정한다. 본문 `{decision: allow|deny|later, elevated?: [항목…], phrase?}`. `elevated` 는 명시로 체크한 항목만 준다
    (체크가 있으면 재인증한 사람만). `phrase` 를 주면 두 에이전트가 보고 있는 확인 문구와 같아야 한다. 선택지(choice·scopes·ttl)는 받지 않는다."""
    from authz import pairs
    decision = body.get("decision")
    if decision not in ("allow", "deny", "later"):
        return _err(ShareError("bad_decision", tr("decision 은 allow, deny, later 중 하나입니다"), 400))
    if body.get("choice") is not None or body.get("scopes") or body.get("ttl") is not None:
        return _err(ShareError("choice_unsupported", tr("이 종류의 요청은 허용/거절만 받습니다 — 한 번만·기간·범위 조정은 지원하지 않습니다"), 400))
    try:
        pairs.require_enabled()
        if decision == "later":
            pend = await asyncio.to_thread(pairs.pending_approvals)
            if not any(a["id"] == approval_id or f"ap-{a['id']}" == approval_id for a in pend):
                return _err(ShareError("not_found", tr("대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)"), 404))
            return {"ok": True, "id": approval_id, "kind": inbox.KIND_PAIR, "state": "pending", "later": True}
        res = await asyncio.to_thread(pairs.decide, approval_id, decision, _actor(request), elevated=body.get("elevated"), phrase=body.get("phrase"))
    except ShareError as e:
        if e.code in ("phrase_mismatch_locked", "member_gone"):
            await authz_events.emit(authz_events.resolved_event(approval_id, inbox.KIND_PAIR, "denied"))
        return _err(e)
    if not res.get("replayed"):
        await authz_events.emit(authz_events.resolved_event(approval_id, inbox.KIND_PAIR, res["state"]))
    return {**res, "id": approval_id, "kind": inbox.KIND_PAIR}


@human_router.delete("/api/authz/grants/{grant_id}", response_model=RevokeResponse)
async def revoke_grant(grant_id: str, request: Request, scopes: str = Query("", max_length=400)):
    """권한 회수. 종류별 **기존 회수 경로**를 그대로 쓴다(공유 → `agent_share.revoke`, 메시지 → `msg_svc.revoke`, 호스트 → `fsh host deny-*`
    와 같은 경로, 위임 → 위임 닫기). `scopes=a,b` 는 그 scope 만 거둔다(공유는 입력 scope 만이면 읽기를 남긴다)."""
    sc = [x for x in scopes.split(",") if x] or None
    if sc and any(x not in S.CATALOG for x in sc):
        return _err(ShareError("bad_scopes", tr("알 수 없는 scope 입니다"), 400))
    snap = await asyncio.to_thread(agent_share.snapshot)
    try:
        res = await asyncio.to_thread(inbox.revoke, grant_id, sc, snap, _actor(request), _names_alive)
    except ShareError as e:
        return _err(e)
    except Exception as e:  # noqa: BLE001 — 위임 닫기 같은 다른 모듈의 오류도 같은 모양으로
        code = getattr(e, "code", None)
        if code:
            return JSONResponse({"error": code, "reason": getattr(e, "reason", str(e))}, status_code=getattr(e, "status", 400))
        raise
    await _saga_after(saga.after_grant_revoke, [grant_id, res["id"]], _actor(request))
    await authz_events.emit(authz_events.revoked_event(res["id"], res["kind"]))
    return {"ok": True, **res}


@human_router.post("/api/authz/panic", response_model=PanicResponse, dependencies=[Depends(auth.require_elevated)])
async def panic(request: Request):
    """비상 정지(elevated) — 사람이 아닌 주체의 grant 와 보류 요청을 전부 거둔다. 사람의 기본 권한·공유 정책·로그인은 그대로.
    종류별 기존 회수 경로를 쓰고 각각 감사된다. 일부가 실패해도 나머지는 계속하고 `failed` 에 담는다."""
    snap = await asyncio.to_thread(agent_share.snapshot)
    try:
        res = await asyncio.to_thread(inbox.panic, snap, _actor(request), _names_alive)
    except ShareError as e:
        return _err(e)
    await _saga_after(saga.after_panic, _actor(request))
    await authz_events.emit(authz_events.revoked_event("*", "all"))
    await authz_events.emit(authz_events.resolved_event("*", "all", "cleared"))
    return res


# --- 에이전트/소켓: 내가 누구인가 · 내 요청 · 내 권한 ---------------------------------------------------------


@router.get("/api/authz/whoami")
async def whoami(request: Request):
    """호출 주체. 소켓 요청은 게이트(`authz/sock.py`)가 앱에 닿기 전에 직접 답한다(주체 + 유효 scope·조건 요약 + 맞는 프리셋) —
    여기까지 오는 것은 TCP 요청뿐이고, TCP 에는 커널이 보증하는 peer 가 없어서 답하지 않는다."""
    return JSONResponse({"error": "socket_only", "reason": tr("unix socket 으로만 호출자를 확인할 수 있습니다")}, status_code=403)


async def _caller(request: Request) -> P.Principal:
    """확인된 에이전트(또는 소켓의 CLI). 소켓이면 게이트가 정한 주체, TCP 면 pane 을 `share_identity` 로 검증한 에이전트."""
    if request.scope.get("fsh_sock") is True:
        p = getattr(request.state, "fsh_principal", None)
        if p is not None and (p.kind == P.AGENT or P.is_cli(p)) and not P.is_hook(p):
            return p
        raise ShareError("request_not_allowed", tr("승인 요청을 만들 수 없는 호출자입니다"), 403)
    import routes.agent_share as share_routes
    me = await share_routes._identify(request, "authz")
    return P.from_caller(me)


@router.post("/api/authz/requests", response_model=RequestCreated, status_code=202)
async def create_request(request: Request):
    """에이전트가 (리소스, scope)를 직접 요청한다. 본문 `{resource, scopes:[…], reason?, ttl?, conds?}`. 항상 **202 보류**다 —
    승인은 사람이 한다(승인되면 같은 호출을 다시 하세요). 한도: 주체당 분당 6회·대기 20건·전체 200건. 같은 요청은 하나로 합치고,
    거절되면 5분 동안 같은 요청을 다시 못 한다. 에이전트가 적은 `reason` 은 승인 화면에만 보이고 감사에는 길이만 남는다."""
    import routes.agent_share as share_routes
    body = await _body(request)
    try:
        p = await _caller(request)
        snap = await asyncio.to_thread(agent_share.snapshot)
        view, created = await asyncio.to_thread(
            inbox.create_request, p, body.get("resource"), body.get("scopes"), body.get("conds"), body.get("reason"),
            body.get("ttl"), alive=inbox._alive(snap))
    except ShareError as e:
        return _err(e)
    if created:
        await share_routes.announce_view(view, tr("에이전트 접근 요청이 있습니다"), kind="generic", legacy=False)
    return JSONResponse({"state": "pending", "requestId": view["id"], "kind": "generic", "created": created,
                         "reason": tr("사용자 승인 대기 중 — 승인되면 같은 호출을 다시 하세요")}, status_code=202)


@router.get("/api/authz/requests", response_model=MyRequestsResponse)
async def my_requests(request: Request):
    """내가 낸 요청(generic)의 상태. 다른 주체의 요청은 보이지 않는다."""
    try:
        p = await _caller(request)
    except ShareError as e:
        return _err(e)
    return {"requests": await asyncio.to_thread(inbox.my_requests, p)}


@router.get("/api/authz/my-grants", response_model=MyGrantsResponse)
async def my_grants(request: Request):
    """내 주체에 붙은 grant(기본 권한은 `whoami`). 다른 주체의 것은 보이지 않는다."""
    try:
        p = await _caller(request)
    except ShareError as e:
        return _err(e)
    return {"grants": await asyncio.to_thread(inbox.my_grants, p)}
