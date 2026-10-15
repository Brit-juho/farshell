"""`fsh host allow-control|deny-control|allow-agent|deny-agent` 의 승인 — `VT_AUTHZ_ENFORCE=peer` (H3).

CLI 는 unix socket 으로 서버에 변경을 **요청**한다(주체 `local`, 에이전트면 `agent`). 접근을 **줄이는** 변경(deny-*)은 바로 적용하고 감사한다.
**늘리는** 변경(allow-*)은 승인 요청(`approvals.json`, 원천 `authz:peer:req:`)이 되어 웹 승인함에서 사람이 승인해야 적용된다 — pane 없는 CLI 도
승인할 수 없다. 승인되면 `hosts.json`(`set_grant_level/agent`, 한 릴리스 동안 미러)과 저장소의 `legacy:hosts:` 사본에 함께 쓴다(dual-write).
같은 (peer, 변경)의 대기는 하나로 합치고, 거절은 5분 쿨다운, 대기는 24시간·주체당 20·전체 200.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from i18n import tr

from . import approvals, audit, defaults, flags, limits, migrate, store
from . import principals as P
from . import legacy_gate
from . import scopes as S
from .errors import ShareError

logger = logging.getLogger(__name__)

HOST_LOCAL = "host:local"


def enforced() -> bool:
    return flags.enforced("peer")


PREFIX_REQ = "authz:peer:req:"
SOURCE = "peer"                                  # 해결 기록의 종류(`authz:resolved:peer:<id>`)
REQ_PREFIX_ID = "pr-"
CHANGES = ("allow-control", "deny-control", "allow-agent", "deny-agent")
REQUEST_TTL = limits.PENDING_TTL
DENY_COOLDOWN = limits.DENY_COOLDOWN


def is_request(rec: dict) -> bool:
    return str(rec.get("origin", "")).startswith(PREFIX_REQ)


def _raw(rec: dict) -> str:
    return str(rec.get("origin", "")).rsplit(":", 1)[-1]


def owns_request(request_id: str) -> bool:
    return request_id.startswith(REQ_PREFIX_ID) or request_id.startswith(f"ap-{REQ_PREFIX_ID}")


def peer_requests(docs: dict) -> list[dict]:
    return [a for a in docs["approvals"]["items"] if is_request(a)]


def is_increase(action: str) -> bool:
    return action in ("allow-control", "allow-agent")


def _requested(action: str) -> tuple[list[str], Optional[dict], str]:
    """승인 대상 (scopes, sconds, resource) — 승인하는 사람이 이미 가져야 하는 것(`approvals.check_authority`)."""
    if action == "allow-control":
        sc, cd = defaults.peer_preset("control")
        return sc, cd, HOST_LOCAL
    return ["msg.send"], None, "msg:local/-"


def _current(action: str, g: dict) -> bool:
    """이 변경이 이미 적용된 상태인가."""
    if action == "allow-control":
        return g.get("level") == "control"
    if action == "deny-control":
        return g.get("level") != "control"
    if action == "allow-agent":
        return bool(g.get("agentMessages"))
    return not g.get("agentMessages")


def _apply(action: str, peer_id: str) -> Optional[dict]:
    """`hosts.json` 에 쓴다(`set_grant_level/agent` — 한 릴리스 동안 옛 저장소 미러). 없는 peer 면 None."""
    import host_store
    if action in ("allow-control", "deny-control"):
        return host_store.set_grant_level(peer_id, host_store.LEVEL_CONTROL if action == "allow-control"
                                          else host_store.LEVEL_VIEW)
    return host_store.set_grant_agent(peer_id, action == "allow-agent")


def mirror(docs: dict, peer_id: str, *, approved_by: str, approved_via: str) -> bool:
    """저장소의 `legacy:hosts:<id>` 사본을 hosts.json 의 지금 값에 맞춘다(이미 이전된 peer 만 — 이전 전이면 사본이 없으니 손대지 않는다).
    `approved_by/via` 는 이 변경을 승인한 쪽으로 남기고 `--verify` 가 이를 차이로 보지 않는다(`migrate._sync` 의 보존 필드)."""
    import host_store
    g = host_store.find_grant(peer_id)
    base = f"legacy:hosts:{peer_id}"
    items = docs["grants"]["items"]
    mine = {x.get("origin"): x for x in items if x.get("origin") in (base, f"{base}:agentMessages")}
    if base not in mine or g is None:
        return False
    out = [{**n, "approved_by": approved_by, "approved_via": approved_via} for n in migrate.translate_peer_grant(g)]
    docs["grants"]["items"] = [x for x in items if x.get("origin") not in mine] + out
    return True


def _audit_strict(rec: dict) -> None:
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        raise ShareError("audit_unavailable", tr("감사 기록을 쓰지 못해 변경을 중단했습니다"), 503) from e


def requester_name(p: P.Principal) -> str:
    if p.kind == P.AGENT:
        return str(p.attrs.get("name") or "agent")
    return "fsh host"


def request_view(rec: dict) -> dict:
    """승인함 화면용 — `/api/agent/share/requests` 의 모양(kind 만 `peer`). `change` 가 무엇을 허용하려는지다."""
    pid = rec.get("peer_id", "")
    label = rec.get("peer_label") or pid
    return {"id": _raw(rec), "kind": "peer", "state": rec.get("state"), "created": rec.get("created"),
            "expires": rec.get("expires"), "decided": rec.get("decided"), "change": rec.get("change"),
            "agent": {"name": rec.get("requester", ""), "id": ""},
            "target": {"nick": label, "mode": None, "name": pid, "id": ""}}


def purge(docs: dict, now: float) -> bool:
    a0 = list(docs["approvals"]["items"])
    keep = []
    for a in a0:
        if is_request(a):
            if a.get("state") == "denied":
                if now - float(a.get("decided") or 0) >= DENY_COOLDOWN:
                    continue
            elif now >= float(a.get("expires") or 0):
                continue
        keep.append(a)
    docs["approvals"]["items"] = keep
    pruned = approvals.prune_resolved(docs, now)
    return len(a0) != len(keep) or pruned


def _snap(docs: dict, now: float) -> store.Snapshot:
    return approvals._snap(docs, now)


def change(actor: P.Principal, peer_id: str, action: str) -> dict:
    """`fsh host allow-*/deny-*` 의 서버 쪽. 결과 `{state: applied|noop|pending, …}`.
    - 줄이는 변경(deny-*)은 바로 적용하고 감사한다. 이미 그 상태면 `noop`.
    - 늘리는 변경(allow-*)은 보류 요청을 만든다(같은 (peer, 변경)은 하나로 합침, 거절은 5분 쿨다운, 한도 초과는 429).
    호출자 종류는 라우트가 거른다."""
    import host_store
    if action not in CHANGES:
        raise ShareError("bad_action", tr("알 수 없는 변경입니다"), 400)
    pid = host_store.normalize_id(peer_id)
    g = host_store.find_grant(pid) if pid else None
    if g is None:
        raise ShareError("not_found", tr("'{id}' 를 허용 목록에서 찾을 수 없습니다", id=peer_id), 404)
    pid = g["id"]
    base = {"principal": actor.id, "resource": HOST_LOCAL, "scopes": _requested(action)[0] if is_increase(action) else [],
            "detail": {"peer": pid, "change": action}}
    if _current(action, g):
        return {"state": "noop", "change": action, "peer": {"id": pid, "label": g.get("label") or pid}}
    now = time.time()
    if not is_increase(action):
        with store.txn() as docs:
            _audit_strict({**base, "action": "peer.grant", "decision": "allow", "result": "applied",
                           "reason": f"{action} by {actor.short()}"})
            new = _apply(action, pid)
            if new is None:
                raise ShareError("not_found", tr("'{id}' 를 허용 목록에서 찾을 수 없습니다", id=peer_id), 404)
            mirror(docs, pid, approved_by=actor.id, approved_via=S.VIA_MANUAL)
        host_store.audit(pid, "grant", True, f"{action} ({actor.short()})")
        try:
            from . import saga
            saga.after_peer_deny(pid, actor)           # `lease` 플래그가 켜졌을 때만 일한다
        except Exception as e:  # noqa: BLE001 — 회수는 이미 적용됐다. 남은 정리는 sweep 이 이어한다
            logger.warning(f"peer 회수 정리 saga 시작 실패: {type(e).__name__}: {e}")
        return {"state": "applied", "change": action, "peer": {"id": pid, "label": g.get("label") or pid}}
    legacy_gate.require_approval_available()         # 승인할 사람(웹 비밀번호)이 없으면 보류로 두지 않고 이유를 알린다
    with store.txn() as docs:
        purge(docs, now)
        reqs = peer_requests(docs)
        for r in reqs:
            if r.get("peer_id") == pid and r.get("change") == action:
                if r.get("state") == "denied":
                    wait = int(DENY_COOLDOWN - (now - float(r.get("decided") or 0)))
                    raise ShareError("request_denied",
                                     f"사용자가 거부했습니다 — {max(wait, 1)}초 뒤에 다시 요청할 수 있습니다",
                                     403, state="denied", retryAfter=max(wait, 1))
                return {"state": "pending", "created": False, "request": request_view(r), "requestId": _raw(r),
                        "change": action}
        mine = sum(1 for r in reqs if r.get("state") == "pending" and r.get("subject") == actor.id)
        if mine >= limits.PENDING_PER_PRINCIPAL or len(reqs) >= limits.PENDING_TOTAL:
            raise ShareError("too_many_requests", "대기 중인 승인 요청이 너무 많습니다 — 사용자가 처리한 뒤 다시 하세요", 429)
        rid = f"{REQ_PREFIX_ID}{uuid.uuid4().hex[:12]}"
        sc, cd, res = _requested(action)
        rec = {"id": f"ap-{rid}", "origin": f"{PREFIX_REQ}{rid}", "kind": "peer", "subject": actor.id, "resource": res,
               "scopes": sc, "peer_id": pid, "peer_label": g.get("label") or pid, "change": action,
               "requester": requester_name(actor), "state": "pending", "created": now, "expires": now + REQUEST_TTL,
               "schema": S.SCHEMA}
        if cd:
            rec["sconds"] = cd
        _audit_strict({**base, "action": "peer.request", "decision": "pending", "result": "pending",
                       "approval_id": rec["id"], "reason": f"{action} by {actor.short()}"})
        docs["approvals"]["items"].append(rec)
        return {"state": "pending", "created": True, "request": request_view(rec), "requestId": rid, "change": action}


def list_requests() -> list[dict]:
    """승인 대기 중인 peer 변경 요청(화면용)."""
    now = time.time()
    with store.txn() as docs:
        purge(docs, now)
        return [request_view(r) for r in peer_requests(docs) if r.get("state") == "pending"]


def decide_request(request_id: str, decision: str, level: Optional[str], actor: Optional[P.Principal]) -> dict:
    """사람의 결정. allow 면 hosts.json 과 저장소 사본에 적용, deny 면 쿨다운 시작. pane 없는 cli·에이전트·peer·기계 토큰은 승인할 수 없다
    (`approvals.check_authority`)."""
    if decision not in ("allow", "deny"):
        raise ShareError("bad_decision", "decision 은 allow 또는 deny 입니다", 400)
    if level not in (None, "read", "peer"):
        raise ShareError("bad_level", tr("이 요청에는 level 이 필요 없습니다"), 400)
    now = time.time()
    rid = request_id[3:] if request_id.startswith("ap-") else request_id
    with store.txn() as docs:
        purge(docs, now)
        req = next((r for r in peer_requests(docs) if _raw(r) == rid and r.get("state") == "pending"), None)
        if req is None:
            if actor is None and approvals.find_resolved(docs, SOURCE, rid) is not None:
                raise ShareError("human_session_required", tr("사람(브라우저 로그인 세션)만 승인할 수 있습니다"), 403)
            again = approvals.replay(docs, SOURCE, rid, decision, None, actor, now)
            if again is not None:
                return again
            raise ShareError("not_found", "대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)", 404)
        if actor is None:
            raise ShareError("human_session_required", tr("사람(브라우저 로그인 세션)만 승인할 수 있습니다"), 403)
        action, pid = req["change"], req["peer_id"]
        sc, cd, res = _requested(action)
        approvals.check_authority(actor, sc if decision == "allow" else (), res, cd if decision == "allow" else None,
                                  _snap(docs, now))
        base = {"principal": actor.id, "resource": res, "scopes": sc if decision == "allow" else [],
                "approval_id": req["id"], "detail": {"peer": pid, "change": action, "request": rid},
                "reason": f"requester={req.get('requester', '')}"}
        if decision == "deny":
            denied = {**req, "state": "denied", "decided": now, "cooldown_until": now + DENY_COOLDOWN}
            _audit_strict({**base, "action": "peer.decide", "decision": "deny", "result": "denied"})
            _replace(docs["approvals"]["items"], req, denied)
            out = {"request": request_view(denied), "grant": None, "grantId": None}
            docs["approvals"]["items"].append(approvals.resolved_record(SOURCE, rid, req, "deny", None, now, out, ()))
            return out
        gid = f"pg-{pid}" if action.endswith("control") else f"pm-{pid}"
        _audit_strict({**base, "action": "peer.decide", "decision": "allow", "result": "allowed", "grant_id": gid})
        if _apply(action, pid) is None:                       # 승인하는 사이 peer 가 지워졌다
            docs["approvals"]["items"] = [a for a in docs["approvals"]["items"] if a is not req]
            raise ShareError("not_found", tr("'{id}' 를 허용 목록에서 찾을 수 없습니다", id=pid), 404)
        mirror(docs, pid, approved_by=actor.id, approved_via=S.VIA_MANUAL)
        docs["approvals"]["items"] = [a for a in docs["approvals"]["items"] if a is not req]
        out = {"request": request_view(req), "grant": {"id": gid, "kind": "peer", "peer": pid, "change": action},
               "grantId": gid}
        docs["approvals"]["items"].append(approvals.resolved_record(SOURCE, rid, req, "allow", None, now, out, sc, cd))
    try:
        import host_store
        host_store.audit(pid, "grant", True, f"{action} (승인 {actor.short()})")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"peer 감사 기록 실패: {e}")
    return out


def _replace(items: list, old: Optional[dict], new: Optional[dict]) -> None:
    if old is not None:
        i = next((k for k, x in enumerate(items) if x is old), None)
        if i is not None:
            if new is None:
                del items[i]
            else:
                items[i] = new
            return
    if new is not None:
        items.append(new)
