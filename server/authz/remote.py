"""다른 Mac(grant 기반) — 프로토콜 v2의 양쪽: 받는 쪽(B)이 에이전트에게 주는 권한과 보내는 쪽(A)의 호출·회수 알림.

`VT_AUTHZ_ENFORCE=peer` 가 켜져 있을 때만 존재하는 기능이다. 꺼짐에서는 `/api/peer/v2/*` 라우트가 **없는 것과 같은 404** 로 답하고, 서명에
`X-Peer-Obo` 를 싣지 않으며 이 모듈의 어떤 함수도 불리지 않는다(= 기존과 byte-identical). 위임(`/v2/deleg/*`)은 `deleg`, pair 참여는 `pair`
도 함께 켜져 있어야 한다.

## 신뢰 모델 (docs/ref/auth.md 「Other Macs, grant based」)
- 서명(HMAC)은 `peer:A` 를 **인증**만 한다. A 의 에이전트 X 를 대신한다는 말(`X-Peer-Obo: agent:A/<세션 key>`)은 A 의 **보증**이다 — 서명에 들어
  있어 바꾸거나 뗄 수 없고, A 는 자기 호스트의 에이전트만 보증할 수 있다. B 는 그 보증을 믿어 주체 `peer:A/obo:agent:A/<key>` 를 만들고,
  인가는 **B 의 grant** 가 한다(그 호스트의 사람이 B 의 승인함에서 승인한 것만). 요청 쪽 사람의 위임 승인은 없다(D12).
- obo 를 떼면(downgrade) 주체가 `peer:A` 하나로 줄 뿐이라 권한은 **줄기만** 한다: 대리 주체에게 준 grant 는 `peer:A` 에 닿지 않고, 호스트 단위
  grant 는 양쪽에 똑같이 닿는다. 서명에서 obo 를 떼면 서명 자체가 깨진다.
- 에이전트는 B 의 **공유된** 세션만 보고(읽기) 쓴다(한 줄 입력) — B 의 사람이 켠 공유 정책이 상한이다(`ceilings.applies` 가 대리 주체에 적용된다).
  처음 쓰려면 B 의 사람이 승인해야 하고(위험 등급이라 elevated), 승인 전에는 202 보류다.

## 회수 알림 (N-13, N-14)
B 가 A 의 권한을 거두면 A 에 `POST /api/peer/v2/revoke` 로 알린다. 알림은 **서명된 힌트**일 뿐 A 가 곧이곧대로 지우지 않는다: A 는 B 에 직접
물어(`GET /v2/authz/grants`) «B 가 인증된 응답을 하는데 그 grant 가 없을 때만» 회수로 인정한다(B 에 닿지 못하거나 5xx 면 인정하지 않고 보류).
그래서 서버 조작·재생으로 상대의 작업을 지울 수 없다. 알림은 `~/.vt/peer_outbox.json` 에 쌓았다가 성공할 때까지 backoff 로 재시도한다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from i18n import tr

from . import audit, engine, flags, inbox, store
from . import principals as P
from .errors import ShareError

logger = logging.getLogger(__name__)

SESSION_SCOPES_READ = ["session.view", "session.read"]
SESSION_SCOPES_WRITE = ["session.view", "session.read", "session.input"]
WRITE_SCONDS = {"session.input": {"modes": ["line"]}}
KIND_READ, KIND_WRITE = "read", "write"


# --- 플래그 ---------------------------------------------------------------------------------------------------------


def enabled() -> bool:
    """`VT_AUTHZ_ENFORCE=peer` — v2 라우트·대리 주체·회전·회수 알림의 마스터 스위치."""
    return flags.enforced("peer")


def deleg_enabled() -> bool:
    return enabled() and flags.enforced("deleg")


def pair_enabled() -> bool:
    return enabled() and flags.enforced("pair")


def require_enabled() -> None:
    if not enabled():
        raise ShareError("remote_disabled", tr("다른 Mac 권한(프로토콜 v2)이 꺼져 있습니다 — VT_AUTHZ_ENFORCE 에 peer 가 필요합니다"), 409)


# --- 주체 -----------------------------------------------------------------------------------------------------------


def principal(grant: dict) -> P.Principal:
    """서명을 통과한 요청의 주체. `grant["obo"]` 는 서명이 보증한 대리 주체(없으면 호스트 단위 `peer:A`)."""
    return P.peer(str(grant.get("id") or ""), obo=grant.get("obo") or None)


def obo_of_subject(subject: str) -> Optional[str]:
    """`peer:A/obo:agent:A/<key>` -> `agent:A/<key>` (대리 주체가 아니면 None)."""
    marker = "/obo:"
    return subject.split(marker, 1)[1] if subject.startswith("peer:") and marker in subject else None


def valid_obo_host(obo: str, peer_id: str) -> bool:
    """`obo` 가 **이 호스트의** 에이전트(`agent:<내 id>/…`)인가 — 상대가 알려 온 obo 를 받아들이기 전에, 회수 알림이 남의 에이전트를 지목하지 못하게."""
    import host_store
    return host_store.valid_obo(obo) and obo.split("/", 1)[0] == f"agent:{host_store.get_self()['id']}"


def obo_key(obo: str) -> str:
    """`agent:A/<key>` -> `<key>`"""
    return obo.split("/", 1)[1] if "/" in obo else ""


def self_obo(agent_key: str) -> str:
    """이 호스트의 에이전트(세션 key)를 다른 호스트에 보증하는 대리 주체 문자열."""
    import host_store
    return f"agent:{host_store.get_self()['id']}/{agent_key}"


def _fresh_snapshot(now: float) -> store.Snapshot:
    """저장소를 **캐시 없이** 읽는다 — 방금 회수한 grant 가 같은 mtime·크기 충돌로 낡게 보이면 안 된다(회수 후 즉시 거부)."""
    docs = {n: store._read(n) for n in ("grants", "approvals", "windows", "shares")}
    return store.Snapshot(grants=docs["grants"][0]["items"], approvals=docs["approvals"][0]["items"],
                          windows=docs["windows"][0]["items"], shares=docs["shares"][0]["items"],
                          corrupt=frozenset(n for n, (_d, bad) in docs.items() if bad), now=now, source="store").fenced()


def decide(p: P.Principal, scope: str, resource: str, ctx: Optional[dict] = None, now: Optional[float] = None,
           snap: Optional[store.Snapshot] = None) -> engine.Decision:
    """엔진 판정. 예외·손상된 저장소 = 닫힘(503 `authz_unavailable`). `snap` 이 없으면 저장소를 캐시 없이 새로 읽는다."""
    try:
        d = engine.decide(p, scope, resource, ctx or {}, snap if snap is not None else _fresh_snapshot(time.time() if now is None else now))
    except ShareError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"다른 Mac 권한 엔진을 쓸 수 없다 — 거부: {type(e).__name__}: {e}")
        raise ShareError("authz_unavailable", tr("권한 엔진을 쓸 수 없어 요청을 거부했습니다"), 503) from e
    if d.reason == "store_corrupt":
        raise ShareError("authz_unavailable", tr("권한 엔진을 쓸 수 없어 요청을 거부했습니다"), 503)
    return d


def audit_op(p: P.Principal, action: str, resource: str, result: str, *, scopes=(), reason: str = "", grant_id: Optional[str] = None,
             chars: Optional[int] = None, sha8: Optional[str] = None, detail: Optional[dict] = None) -> None:
    """`peer.<action>` 한 줄(데이터 경로 — 쓰기 실패가 동작을 막지 않는다). 내용(화면·입력·작업 지시)은 길이와 sha8 뿐이다."""
    try:
        rec = {"principal": p.short(), "action": f"peer.{action}", "resource": resource, "scopes": list(scopes),
               "decision": "deny" if result == "denied" else "allow", "result": result, "reason": reason}
        if grant_id:
            rec["grant_id"] = grant_id
        if chars is not None:
            rec["sizes"] = {"chars": int(chars)}
        if sha8:
            rec["sha8"] = sha8
        if detail:
            rec["detail"] = detail
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"다른 Mac 감사 기록 실패: {e}")


def audit_rotated(grant: dict) -> None:
    """B 쪽: 새 secret 이 정식이 됐다 — 마지막 회전 시각을 감사에 남긴다(authz 감사 체인 + peer 감사)."""
    import host_store
    when, prev = int(grant.get("rotatedAt") or 0), int(grant.get("previousRotatedAt") or 0)
    host_store.audit(grant["id"], "rotate", True, f"secret 회전 (이번 {when}, 이전 {prev or '없음'})")
    audit_op(P.peer(grant["id"]), "rotate", "host:local", "rotated", reason="secret_rotated",
             detail={"rotated_at": when, "previous_rotated_at": prev})


# --- 받는 쪽(B): 공유 세션 ----------------------------------------------------------------------------------------------------


def _agent_share():
    import agent_share
    return agent_share


def _tmux_snapshot() -> dict:
    snap = _agent_share().snapshot()
    if snap is None:
        raise ShareError("tmux_unavailable", tr("tmux 상태를 읽지 못했습니다"), 503)
    return snap


def _need_obo(p: P.Principal) -> None:
    if not p.obo:
        raise ShareError("obo_required", tr("이 요청에는 대리 주체(X-Peer-Obo)가 필요합니다"), 400)


def _ctx_session(target, snap: dict, kind: str) -> dict:
    ctx = {"share": target.share, "share_known": True, "prefer_live": False, "alive": set(snap)}
    if kind == KIND_WRITE:
        ctx["mode"] = "line"                       # 공유 세션에 보내는 것은 정제된 한 줄 입력뿐
    return ctx


def _res_session(key: str) -> str:
    return f"session:local/{key}"


def _scope_of(kind: str) -> str:
    return "session.read" if kind == KIND_READ else "session.input"


def _request_state(snap_store: store.Snapshot, p: P.Principal, resource: str, kind: str) -> str:
    """이 주체가 이 리소스에 낸 요청의 상태(`pending`|`denied`|`none`)."""
    for r in snap_store.approvals:
        if not inbox.is_req(r) or r.get("subject") != p.short() or r.get("resource") != resource:
            continue
        has_write = "session.input" in (r.get("scopes") or ())
        if (kind == KIND_WRITE) != has_write and not (kind == KIND_READ):
            continue
        if kind == KIND_READ and has_write:
            continue
        st = r.get("state")
        if st in ("pending", "denied"):
            return st
    return "none"


def shared_sessions(grant: dict) -> dict:
    """B 의 공유된 세션(닉네임·모드·상태)과 **이 대리 주체의** 권한 상태. 세션 이름·cwd 는 싣지 않는다."""
    import host_store
    p = principal(grant)
    _need_obo(p)
    snap = _tmux_snapshot()
    now = time.time()
    fresh = _fresh_snapshot(now)
    ag = _agent_share()
    out = []
    for s in snap.values():
        if not s.share:
            continue
        res = _res_session(s.key)
        read_ok = decide(p, "session.read", res, _ctx_session(s, snap, KIND_READ), now, fresh).allow
        if s.share["mode"] != ag.LEVEL_READWRITE:
            write = "unavailable"
        elif decide(p, "session.input", res, {**_ctx_session(s, snap, KIND_WRITE), "mode": "line"}, now, fresh).allow:
            write = "granted"
        else:
            write = _request_state(fresh, p, res, KIND_WRITE)
        out.append({"nick": s.share["nick"], "mode": s.share["mode"], "status": ag.agent_state_of(s.name), "command": s.command,
                    "permission": {"read": "granted" if read_ok else _request_state(fresh, p, res, KIND_READ), "write": write}})
    out.sort(key=lambda x: x["nick"].lower())
    me = host_store.get_self()
    return {"me": p.short(), "host": {"id": me["id"], "label": me["label"]}, "sessions": out}


def _pending_body(view: dict, kind: str) -> dict:
    what = tr("읽기") if kind == KIND_READ else tr("쓰기")
    return {"state": "pending", "requestId": view["id"], "kind": kind,
            "reason": tr("사용자 승인 대기 중({what}) — 승인되면 같은 호출을 다시 하세요", what=what)}


def _peer_label(grant: dict) -> str:
    return str(grant.get("label") or grant.get("id") or "")


def _make_request(grant: dict, p: P.Principal, target, kind: str, body: dict, snap: dict) -> tuple[dict, bool]:
    scopes = SESSION_SCOPES_READ if kind == KIND_READ else SESSION_SCOPES_WRITE
    return inbox.create_remote_request(
        p, _peer_label(grant), _res_session(target.key), scopes, WRITE_SCONDS if kind == KIND_WRITE else None,
        body.get("reason"), body.get("ttl"), agent_name=str(body.get("agentName") or ""),
        resource_session=target.key, alive=set(snap))


def session_read(grant: dict, body: dict) -> tuple[int, dict, Optional[dict]]:
    """공유 세션 읽기 -> (HTTP 상태, 본문, 알릴 새 요청 view|None). 권한이 없으면 202 보류 요청을 만든다."""
    ag = _agent_share()
    p = principal(grant)
    _need_obo(p)
    lines = ag.check_lines(body.get("lines"))
    nick = ag.check_nick(str(body.get("nickname") or ""))
    snap = _tmux_snapshot()
    target = ag.find_by_nick(snap, nick)
    res = _res_session(target.key)
    now = time.time()
    with store.txn() as docs:
        from . import approvals
        d = decide(p, "session.read", res, _ctx_session(target, snap, KIND_READ), now, approvals._snap(docs, now))
        if d.allow:
            ag.check_rate(ag.KIND_READ, p.short(), target.key, now)
            inbox.consume_use(docs, d.grant_id)
    if not d.allow:
        if d.reason == "ceiling":
            raise ShareError("read_only_share", tr("이 공유로는 허용되지 않는 권한입니다"), 403)
        view, created = _make_request(grant, p, target, KIND_READ, body, snap)
        audit_op(p, "session.read", res, "pending", scopes=["session.read"], reason="request" if created else "request_dup")
        return 202, _pending_body(view, KIND_READ), (view if created else None)
    res_pane = ag.read_pane(target, lines)
    if _sample_read(p.short(), res):
        audit_op(p, "session.read", res, "ok", scopes=["session.read"], grant_id=d.grant_id, chars=res_pane["bytes"])
    out = {"state": "ok", "nick": target.share["nick"], **res_pane}
    if d.grant_id:
        out["grantId"] = d.grant_id
    return 200, out, None


_read_seen: dict[tuple, float] = {}


def _sample_read(who: str, res: str, now: Optional[float] = None) -> bool:
    """읽기 감사는 (주체, 리소스)당 10분에 한 줄(R11.3)."""
    now = time.time() if now is None else now
    k = (who, res)
    if now - _read_seen.get(k, 0.0) < 600:
        return False
    if len(_read_seen) > 2048:
        _read_seen.clear()
    _read_seen[k] = now
    return True


def session_send(grant: dict, body: dict) -> tuple[int, dict, Optional[dict]]:
    """공유 세션에 한 줄 입력 -> (HTTP 상태, 본문, 알릴 새 요청 view|None)."""
    ag = _agent_share()
    p = principal(grant)
    _need_obo(p)
    text = ag.check_send_text(body.get("text"))
    enter = body.get("enter", False)
    if not isinstance(enter, bool):
        raise ShareError("bad_enter", tr("enter 는 true/false 입니다"), 400)
    nick = ag.check_nick(str(body.get("nickname") or ""))
    snap = _tmux_snapshot()
    target = ag.find_by_nick(snap, nick)
    res = _res_session(target.key)
    if target.share["mode"] != ag.LEVEL_READWRITE:
        raise ShareError("read_only_share", tr("이 공유는 읽기 전용입니다"), 403)    # 허용해 줄 수 없는 것을 묻지 않는다
    now = time.time()
    with store.txn() as docs:
        from . import approvals
        d = decide(p, "session.input", res, {**_ctx_session(target, snap, KIND_WRITE), "mode": "line"}, now, approvals._snap(docs, now))
        if d.allow:
            ag.check_rate(ag.KIND_WRITE, p.short(), target.key, now)
            inbox.consume_use(docs, d.grant_id)
    meta = {"chars": len(text), "sha8": ag.sha8(text)}
    if not d.allow:
        view, created = _make_request(grant, p, target, KIND_WRITE, body, snap)
        audit_op(p, "session.send", res, "pending", scopes=["session.input"], reason="request" if created else "request_dup")
        return 202, _pending_body(view, KIND_WRITE), (view if created else None)
    try:
        ag.check_send_allowed(target)

        def guard() -> None:
            # 입력 직전 재평가 — 판정~입력 사이의 회수·공유 해제·세션 교체를 막는다(횟수는 세지 않는다).
            fresh = ag.snapshot()
            cur = fresh.get(target.key) if fresh else None
            if cur is None or cur.pane_id != target.pane_id or not decide(
                    p, "session.input", res, {**_ctx_session(cur, fresh, KIND_WRITE), "mode": "line"}).allow:
                raise ShareError("grant_revoked", tr("입력 직전에 권한이 회수되어 보내지 않았습니다"), 403)

        ag.send_keys(target, text, enter, guard)
    except ShareError as e:
        audit_op(p, "session.send", res, "denied", scopes=["session.input"], reason=e.code, **meta)
        raise
    audit_op(p, "session.send", res, "ok", scopes=["session.input"], grant_id=d.grant_id, **meta)
    out = {"state": "ok", "nick": target.share["nick"], "chars": len(text), "enter": enter}
    if d.grant_id:
        out["grantId"] = d.grant_id
    return 200, out, None


# --- 받는 쪽(B): 명시 요청 · 상태 · 내 grant ------------------------------------------------------------------------------


def request_access(grant: dict, body: dict) -> tuple[int, dict, Optional[dict]]:
    """`POST /v2/authz/request` — 종류 `session`(닉네임 + access read|write) 또는 `deleg`(원격 위임 쿼터). 항상 202 보류다."""
    p = principal(grant)
    _need_obo(p)
    kind = str(body.get("kind") or "session")
    if kind == "session":
        ag = _agent_share()
        snap = _tmux_snapshot()
        target = ag.find_by_nick(snap, ag.check_nick(str(body.get("nickname") or "")))
        access = KIND_WRITE if str(body.get("access") or KIND_READ) == KIND_WRITE else KIND_READ
        if access == KIND_WRITE and target.share["mode"] != ag.LEVEL_READWRITE:
            raise ShareError("read_only_share", tr("이 공유는 읽기 전용입니다"), 403)
        view, created = _make_request(grant, p, target, access, body, snap)
        return 202, {**_pending_body(view, access), "created": created}, (view if created else None)
    if kind == "deleg":
        if not deleg_enabled():
            raise ShareError("deleg_disabled", tr("이 서버는 원격 위임이 꺼져 있습니다 — VT_AUTHZ_ENFORCE 에 deleg 가 필요합니다"), 409)
        view, created = inbox.create_remote_request(
            p, _peer_label(grant), f"deleg-quota:{p.short()}", ["deleg.spawn"], None, body.get("reason"), body.get("ttl"),
            agent_name=str(body.get("agentName") or ""))
        return 202, {"state": "pending", "requestId": view["id"], "kind": "deleg", "created": created,
                     "reason": tr("사용자 승인 대기 중 — 승인되면 같은 호출을 다시 하세요")}, (view if created else None)
    raise ShareError("bad_kind", tr("kind 는 session 또는 deleg 입니다"), 400)


def request_status(grant: dict, request_id: str) -> dict:
    """내가 낸 요청의 상태 — `pending` | `denied`(쿨다운 중) | `allowed` | `denied`(결정됨). 다른 주체의 요청은 404."""
    p = principal(grant)
    _need_obo(p)
    rid = request_id[3:] if request_id.startswith("ap-") else request_id
    snap = store.snapshot(time.time())
    for r in snap.approvals:
        if inbox.is_req(r) and inbox.raw_id(r) == rid and r.get("subject") == p.short():
            return {"requestId": rid, "state": r.get("state") or "pending", "expires": r.get("expires")}
    from . import approvals
    for r in snap.approvals:
        if approvals.is_resolved(r) and r.get("request_id") == rid and r.get("source") == inbox.SOURCE and r.get("subject") == p.short():
            return {"requestId": rid, "state": "allowed" if r.get("decision") == "allow" else "denied"}
    raise ShareError("not_found", tr("그런 요청이 없습니다(이미 지나갔거나 만료됐습니다)"), 404)


def grants_view(grant: dict) -> dict:
    """이 호스트가 B 에 가진 권한 — 대리 주체가 있으면 그 주체의 grant, 없으면(호스트 단위 호출) 이 호스트의 모든 에이전트 grant. 회수 확인(N-13)의
    근거이기도 하다: 인증된 응답에 grant 가 없다 = 회수됐다. 서명 검증을 통과한 호출자에게만 열려 있고 비밀·내용은 싣지 않는다."""
    p = principal(grant)
    now = time.time()
    prefix = f"peer:{grant['id']}/"
    mine = []
    for g in _fresh_snapshot(now).grants:
        sub = str(g.get("subject", ""))
        if g.get("effect") == "deny" or g.get("state", "active") != "active" or not (sub == p.short() if p.obo else sub.startswith(prefix)):
            continue
        if g.get("expires") is not None and now >= float(g["expires"]):
            continue
        mine.append({"id": g.get("id"), "resource": g.get("resource"), "scopes": list(g.get("scopes") or ()),
                     "expires": g.get("expires"), "obo": obo_of_subject(sub)})
    return {"registered": True, "level": grant.get("level"), "obo": p.obo, "grants": mine}


# --- 보내는 쪽(A): 호출 -------------------------------------------------------------------------------------------------------


def peer_of(host: str) -> dict:
    import host_store
    pid = host_store.normalize_id(host or "")
    peer = host_store.find_peer(pid) if pid else None
    if peer is None:
        raise ShareError("host_not_found", tr("'{host}' 호스트가 없습니다 — 'fsh host list' 로 확인하세요", host=str(host)[:40]), 404)
    return peer


def call(host: str, method: str, path: str, body: Optional[dict] = None, *, agent_key: str = "", timeout: float = 8.0) -> tuple[int, dict]:
    """다른 호스트의 v2 라우트를 부른다. `agent_key` 가 있으면 그 에이전트를 대리 주체로 보증한다. (상태, 본문). 닿지 못하면 502,
    상대가 v2 를 모르거나 꺼져 있으면(404) 501 `peer_outdated`."""
    import peer_client
    require_enabled()
    peer = peer_of(host)
    obo = self_obo(agent_key) if agent_key else ""
    if agent_key and path.startswith('/api/peer/v2/deleg/'):
        from session_catalog import identity
        if identity.enabled():
            binding = identity.current().get(agent_key)
            if not binding:
                raise ShareError('identity_unverified', tr('현재 실행 확인 불가'), 403)
            body = {**(body or {}), 'identity_version': 2, 'runtime_binding': binding}
    try:
        status, payload = peer_client.v2_call_sync(peer, method, path, body, obo=obo, timeout=timeout)
    except peer_client.PeerError as e:
        raise ShareError("peer_unreachable", tr("상대 호스트에 닿지 못했습니다: {reason}", reason=e.reason), 502) from e
    if status == 404 and payload.get("detail") == "Not Found":
        raise ShareError("peer_outdated", tr("상대 서버가 다른 Mac 권한(v2)을 지원하지 않거나 꺼져 있습니다 — 양쪽을 업데이트하고 상대에 VT_AUTHZ_ENFORCE=peer 를 켜세요"), 501)
    return status, payload


# --- 위임 (받는 쪽 B 의 핸들러 + 장부 outbox 전달) -------------------------------------------------------------------------


def _deleg_binding(grant, body, role=None):
    from session_catalog import identity
    from session_catalog.db import Refused
    if not identity.enabled():
        return None
    try:
        if body.get('identity_version') != 2:
            raise Refused('peer_identity_incompatible',403)
        binding = identity.peer_binding(body.get('runtime_binding'), obo_key(principal(grant).obo))
        if role:
            import delegation
            item = next((x for x in delegation.list_all() if x['id'] == body.get('delegation')), None)
            remote_key = f"{grant['id']}/{binding['physical_key']}"
            if (not item or item.get(role,{}).get('key') != remote_key
                    or item.get('identity_version') != 2
                    or item.get('runtime_bindings',{}).get(remote_key) != binding):
                raise Refused('identity_migration_required',403)
            identity.require_ledger(item)
        return binding
    except Refused as error:
        raise ShareError(error.code, tr('실행 권한을 다시 확인해야 합니다'), error.status) from error


def deleg_spawn_in(grant: dict, body: dict) -> tuple[int, dict, Optional[dict]]:
    """부모(A 의 에이전트)가 B 에 하위 세션을 만든다. B 의 grant(`deleg.spawn`, 원격이라 elevated 로 승인된 것)가 없으면 202 보류 요청."""
    import delegation
    from . import approvals
    p = principal(grant)
    _need_obo(p)
    binding = _deleg_binding(grant, body)
    res = f"deleg-quota:{p.short()}"
    now = time.time()
    with store.txn() as docs:
        d = decide(p, "deleg.spawn", res, {"deleg": {"enabled": delegation.enabled()}, "safe_mode": delegation._safe_mode()}, now,
                   approvals._snap(docs, now))
        if d.allow:
            inbox.consume_use(docs, d.grant_id)             # 「한 번만」으로 승인했다면 이 호출이 그 한 번이다(만드는 데 실패해도 소진 — 안전한 쪽)
    if not d.allow:
        if d.reason in ("spawn_disabled", "safe_mode"):
            raise delegation._spawn_error(d.reason)
        view, created = inbox.create_remote_request(p, _peer_label(grant), res, ["deleg.spawn"], None, body.get("reason"), body.get("ttl"),
                                                    agent_name=str(body.get("parentName") or ""))
        audit_op(p, "deleg.spawn", res, "pending", scopes=["deleg.spawn"], reason="request" if created else "request_dup")
        return 202, {"state": "pending", "requestId": view["id"], "kind": "deleg", "created": created,
                     "reason": tr("사용자 승인 대기 중 — 승인되면 같은 호출을 다시 하세요")}, (view if created else None)
    out = delegation.spawn_remote(grant["id"], p.obo, did=str(body.get("delegation") or ""), parent_name=str(body.get("parentName") or ""),
                                  task=str(body.get("task") or ""), title=str(body.get("title") or ""), agent=str(body.get("agent") or ""),
                                  principal=p.short(), runtime_binding=binding)
    return 200, {"ok": True, **out}, None


def deleg_send_in(grant: dict, body: dict) -> tuple[int, dict, Optional[dict]]:
    import delegation
    p = principal(grant)
    _need_obo(p)
    _deleg_binding(grant, body, 'parent')
    return 200, delegation.send_from_remote(grant["id"], p.obo, str(body.get("delegation") or "")[:40], str(body.get("text") or "")), None


def deleg_close_in(grant: dict, body: dict) -> tuple[int, dict, Optional[dict]]:
    import delegation
    p = principal(grant)
    _need_obo(p)
    _deleg_binding(grant, body, 'parent')
    return 200, delegation.close_from_remote(grant["id"], p.obo, str(body.get("delegation") or "")[:40]), None


def deleg_report_in(grant: dict, body: dict) -> tuple[int, dict, Optional[dict]]:
    import delegation
    p = principal(grant)
    _need_obo(p)
    _deleg_binding(grant, body, 'child')
    return 200, delegation.report_from_remote(
        grant["id"], p.obo, str(body.get("delegation") or "")[:40], str(body.get("kind") or ""), str(body.get("outcome") or ""),
        str(body.get("body") or ""), body.get("seq")), None


DELIVER_GIVE_UP = 24 * 3600          # 이만큼 못 보낸 위임 outbox 항목은 포기한다(닿지 않는 호스트를 영원히 두드리지 않는다)


def deliver_deleg(item: dict, o: dict) -> bool:
    """위임 장부의 outbox 항목(대상 `peer:<호스트>`) 하나를 보낸다. True = 처리 끝(보냈거나 더 보낼 수 없다), False = 다음 sweep 에서 다시.
    부모 호스트의 «추가 지시/닫아라» -> 자식 호스트, 자식 호스트의 «보고/알림» -> 부모 호스트. 닫혔거나 모르는 위임(404·409)·권한 없음(403)은 다시 해도 같다."""
    import delegation as D
    host = str(o.get("target", ""))[5:]
    did, kind = item["id"], o.get("kind")
    if time.time() - float(o.get("ts") or time.time()) > DELIVER_GIVE_UP:
        logger.warning(f"위임 {did} {kind} 전달 포기 — {host} 에 {DELIVER_GIVE_UP // 3600}시간 동안 닿지 못했다")
        return True
    try:
        from session_catalog.identity import require_ledger
        from session_catalog.db import Refused
        try:
            require_ledger(item)
        except Refused:
            return True
        if (item.get("child") or {}).get("host") == host:                  # 우리가 부모
            key = item["parent"]["key"]
            if kind == D.KIND_FOLLOWUP:
                status, _ = call(host, "POST", "/api/peer/v2/deleg/send", {"delegation": did, "text": o.get("body") or ""}, agent_key=key)
            elif kind == D.KIND_CLOSE:
                status, _ = call(host, "POST", "/api/peer/v2/deleg/close", {"delegation": did}, agent_key=key)
            else:
                return True
        elif (item.get("parent") or {}).get("host") == host:               # 우리가 자식
            key = item["child"]["key"]
            status, _ = call(host, "POST", "/api/peer/v2/deleg/report",
                             {"delegation": did, "kind": kind, "outcome": o.get("outcome") or "", "body": o.get("body") or "", "seq": o.get("ts")},
                             agent_key=key)
        else:
            return True
    except ShareError as e:
        return e.code == "host_not_found"                                  # 그 호스트를 지웠다 — 보낼 길이 없다
    if status == 200:
        return True
    if status in (403, 404, 409, 410):
        logger.info(f"위임 {did} {kind}: {host} 가 받지 않는다(HTTP {status}) — 더 보내지 않는다")
        return True
    return False


# --- pair 합류 (받는 쪽 B) -----------------------------------------------------------------------------------------------


def pair_join(grant: dict, body: dict) -> dict:
    """다른 Mac 의 에이전트가 B 의 pair 코드로 합류한다 -> pair view(+확인 문구). B 의 사람이 승인함에서 확인 문구를 보고 승인한다."""
    from . import pairs
    p = principal(grant)
    _need_obo(p)
    out = pairs.join(p, body.get("code"))
    return {"pair": out, "approvalId": (out.get("approval") or {}).get("id", "")}


def pair_status(grant: dict, pair_id: str) -> dict:
    """합류자가 자기 검증된 신원(대리 주체)으로 결과를 읽는다(D15: 응답 시점에 bearer 를 두지 않는다)."""
    from . import pairs
    p = principal(grant)
    _need_obo(p)
    return pairs.status(p, pair_id)


# --- 회수 알림 outbox (N-14) --------------------------------------------------------------------------------------------


OUTBOX_MAX = 200
OUTBOX_DEAD_MAX = 50
BACKOFF = (5, 15, 60, 300, 900, 3600)          # 초 — 마지막 값 이후는 계속 1시간
MAX_TRIES = 30
_outbox_guard = threading.Lock()


def _outbox_path() -> Path:
    import vt_paths
    return vt_paths.state_dir() / "peer_outbox.json"


@contextmanager
def _outbox_locked():
    import store_lock
    import vt_paths
    d = vt_paths.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    with _outbox_guard, store_lock.exclusive(d / "peer_outbox.lock"):
        yield


def _outbox_read() -> dict:
    p = _outbox_path()
    if not p.is_file():
        return {"items": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # 깨진 파일을 빈 것으로 덮으면 아직 못 보낸 회수 알림이 사라진다 — 거부하고 사람이 보게 둔다.
        raise ShareError("outbox_unreadable", f"peer_outbox.json 을 읽지 못했습니다: {e}", 500) from e
    if not isinstance(d, dict) or not isinstance(d.get("items"), list):
        raise ShareError("outbox_unreadable", "peer_outbox.json 형식이 올바르지 않습니다", 500)
    return d


def _outbox_write(d: dict) -> None:
    p = _outbox_path()
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(str(tmp), str(p))


def notify_revoked(peer_id: str, *, obo: Optional[str] = None, scopes=(), ref: str = "", whole: bool = True, sync: bool = False) -> bool:
    """이 호스트가 `peer_id` 에 준 권한을 거뒀다 — 그 호스트에만, 서명해서 알린다(outbox 에 적고 즉시 한 번 시도, 실패하면 재시도).
    `obo` 가 없으면 호스트 단위(그 호스트가 이 서버에 가진 권한 전체), 있으면 그 에이전트의 grant `ref` 하나. `sync=True` 는 호출 스레드에서 한 번 시도한다
    (CLI 처럼 곧 끝나는 프로세스용). 플래그가 꺼져 있거나 알릴 길(내가 그 호스트를 peer 로 등록했는가)이 없으면 아무것도 하지 않는다."""
    import host_store
    if not enabled():
        return False
    pid = host_store.normalize_id(peer_id or "")
    if pid is None:
        return False
    item = {"id": uuid.uuid4().hex[:12], "peer": pid, "kind": "revoke", "created": round(time.time(), 3), "tries": 0, "next": 0.0,
            "payload": {"scope": "grant" if obo else "host", "obo": obo, "scopes": sorted(set(map(str, scopes))), "ref": ref or None,
                        "whole": bool(whole), "at": int(time.time())}}
    try:
        with _outbox_locked():
            d = _outbox_read()
            d["items"].append(item)
            pending = [x for x in d["items"] if x.get("state") != "dead"]
            dead = [x for x in d["items"] if x.get("state") == "dead"][-OUTBOX_DEAD_MAX:]
            d["items"] = pending[-OUTBOX_MAX:] + dead
            _outbox_write(d)
    except (ShareError, OSError) as e:
        logger.warning(f"회수 알림을 outbox 에 쓰지 못함: {e}")
        return False
    if sync:
        flush_outbox(timeout=3.0)
    else:
        threading.Thread(target=flush_outbox, kwargs={"timeout": 4.0}, daemon=True, name="peer-outbox").start()
    return True


def outbox_items() -> list[dict]:
    if not _outbox_path().is_file():
        return []                      # 알릴 것이 한 번도 없었다 — 락 파일도 만들지 않는다(진단 명령이 상태 폴더를 건드리지 않게)
    try:
        with _outbox_locked():
            return list(_outbox_read()["items"])
    except ShareError:
        return []


def _deliver(item: dict, timeout: float) -> str:
    """-> `done` | `retry` | `dead`. 알릴 길이 없거나(내가 그 호스트를 등록하지 않음) 상대가 받지 않으면 `dead`."""
    import host_store
    import peer_client
    peer = host_store.find_peer(item["peer"])
    if peer is None:
        return "dead"
    try:
        status, _ = peer_client.v2_call_sync(peer, "POST", "/api/peer/v2/revoke", item["payload"], timeout=timeout)
    except peer_client.PeerError:
        return "retry"
    if status in (200, 202):
        return "done"
    if status in (401, 403, 404):          # 상대가 인증을 거부했거나(내 secret 이 낡음) v2 를 모른다 — 재시도해도 같다
        return "dead"
    return "retry"                         # 429·5xx


def flush_outbox(timeout: float = 4.0, now: Optional[float] = None) -> dict:
    """때가 된 알림을 보낸다. 성공은 지우고, 실패는 backoff 로 미루고, 길이 없거나 `MAX_TRIES` 를 넘으면 `dead` 로 표시한다. 반복해도 안전하다."""
    now = time.time() if now is None else now
    stats = {"sent": 0, "retry": 0, "dead": 0}
    if not _outbox_path().is_file():
        return stats
    try:
        with _outbox_locked():
            due = [dict(x) for x in _outbox_read()["items"] if x.get("state") != "dead" and float(x.get("next") or 0) <= now]
    except ShareError:
        return stats
    results: dict[str, str] = {}
    for it in due:
        results[it["id"]] = _deliver(it, timeout)
    if not results:
        return stats
    with _outbox_locked():
        d = _outbox_read()
        keep = []
        for x in d["items"]:
            r = results.get(x.get("id"))
            if r == "done":
                stats["sent"] += 1
                continue
            if r == "retry":
                x["tries"] = int(x.get("tries", 0)) + 1
                if x["tries"] >= MAX_TRIES:
                    x["state"], r = "dead", "dead"
                else:
                    x["next"] = now + BACKOFF[min(x["tries"] - 1, len(BACKOFF) - 1)]
                    stats["retry"] += 1
            if r == "dead":
                x["state"] = "dead"
                stats["dead"] += 1
            keep.append(x)
        d["items"] = keep
        _outbox_write(d)
    return stats


async def outbox_loop(interval: float = 30.0) -> None:
    """서버가 도는 동안 회수 알림을 재시도한다(lifespan 에서 시작)."""
    import asyncio
    while True:
        await asyncio.sleep(interval)
        if not enabled():
            continue
        try:
            await asyncio.to_thread(flush_outbox)
        except Exception as e:  # noqa: BLE001 — 한 번 실패해도 루프는 산다
            logger.warning(f"[peer outbox] 재시도 실패: {e}")


# --- 보내는 쪽(A): 회수 알림을 받았을 때 (N-13) -----------------------------------------------------------------------------


def on_revoke(grant: dict, body: dict) -> tuple[int, dict]:
    """B 가 «당신의 권한을 거뒀다»고 알려 왔다 — 서명은 B 라는 것만 보증한다. A 는 B 에 직접 물어 «B 가 인증된 응답을 하는데 그 grant 가 없을 때만»
    회수로 인정한다. 확인되면 그 호스트로 열린 스트림·위임을 정리한다. 확인하지 못하면(닿지 못함·5xx) 인정하지 않는다."""
    import host_store
    import peer_client
    scope = "grant" if body.get("scope") == "grant" else "host"
    ref = str(body.get("ref") or "")[:80]
    bid = grant["id"]
    peer = host_store.find_peer(bid)
    if peer is None:
        return 200, {"state": "ignored", "reason": "no_route"}      # 내가 그 호스트로 나가는 연결이 없다 — 지울 것이 없다
    try:
        status, payload = peer_client.v2_call_sync(peer, "GET", "/api/peer/v2/authz/grants", timeout=4.0)
    except peer_client.PeerError:
        return 202, {"state": "unconfirmed", "reason": "unreachable"}
    if status == 401 and payload.get("code") == "unregistered":
        confirmed = scope == "host"                                # B 가 «당신은 등록되지 않았다»고 인증된 응답을 한다
    elif status == 200:
        ids = {str(g.get("id")) for g in payload.get("grants") or ()}
        confirmed = scope == "grant" and bool(ref) and ref not in ids       # 호스트 단위 알림인데 아직 등록돼 있다 = 거짓
    else:
        return 202, {"state": "unconfirmed", "reason": f"http_{status}"}
    if not confirmed:
        audit_op(P.peer(bid), "revoke", f"host:{bid}", "ignored", reason="not_confirmed", detail={"scope": scope})
        return 200, {"state": "ignored", "reason": "not_revoked"}
    obo = str(body.get("obo") or "") or None
    apply_revoked(bid, scope=scope, scopes=body.get("scopes") or [], ref=ref, obo=obo)
    return 200, {"state": "revoked"}


def apply_revoked(peer_id: str, *, scope: str, scopes=(), ref: str = "", obo: Optional[str] = None) -> dict:
    """회수가 확인됐다 — 그 호스트로 열린 스트림을 닫고 원격 위임을 닫는다. 감사에 남긴다."""
    import host_store
    closed_streams = 0
    closed_delegs = []
    wide = scope == "host" or any(str(s).startswith("deleg.") for s in scopes)
    if scope == "host":
        try:
            import routes.peer_proxy as proxy
            closed_streams = proxy.close_streams(peer_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"원격 스트림 정리 실패: {type(e).__name__}: {e}")
        host_store.update_peer(peer_id, lastRevoke={"at": int(time.time()), "scope": scope})
    if wide:
        try:
            import delegation
            closed_delegs = delegation.close_remote(peer_id, reason="revoked",
                                                    parent_key=obo_key(obo) if (scope == "grant" and obo and valid_obo_host(obo, peer_id)) else None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"원격 위임 정리 실패: {type(e).__name__}: {e}")
    host_store.audit(peer_id, "revoked-by-peer", True, f"{scope} 스트림 {closed_streams}건 · 위임 {len(closed_delegs)}건")
    audit_op(P.peer(peer_id), "revoke", f"host:{peer_id}", "revoked", reason=scope,
             detail={"streams": closed_streams, "delegations": len(closed_delegs)})
    return {"streams": closed_streams, "delegations": closed_delegs}
