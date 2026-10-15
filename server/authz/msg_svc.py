"""에이전트 간 메시지의 권한 판정·승인·감사 — `VT_AUTHZ_ENFORCE=msg` 일 때 엔진이 판정권자다.

`agent_msg.py` 가 동작부(큐·기록·hop·머리글)를, `routes/agent_msg.py`·`routes/peer.py` 가 HTTP 면을 쥐고, 이 파일은 판정·
승인 요청/grant 저장·감사를 한다. 꺼짐에서는 이 모듈을 거치지 않는다(옛 동작 그대로).

## 보내는 쪽 (로컬 agent → agent)
주체는 **인증된 호출자**다: unix socket 이면 peer pid 체인이 닿는 pane 의 세션(`request.state.fsh_principal`), 소켓이 아니면
`share_identity.verify_caller`(`X-Fsh-Pane/Tmux/Pid` + 소켓 소유 프로세스 확인). 본문의 pane/tmux/cwd 주장은 판정에 들어가지
않는다 — 어긋나면 `msg.claim` 감사 줄(거부 사유 `claim_mismatch`, 내용 없음)만 남기고 무시한다. 확인되지 않은 호출자는 403
`identity_unverified`.

판정은 `msg.send` on `msg:local/<받는 세션 key>`:
  - 관계가 있으면 승인 없이 허용: 위임 부모↔자식, 같은 부모의 형제 — 위임 장부(`delegations.json`)에서 **열린** 위임
    (running·reported)만. 닫힘·고아(orphaned)는 관계가 아니다. pair 구성원 관계는 5단계 자리(`pair_related`, 지금은 항상 False).
  - 그 밖은 (보내는 세션 → 받는 세션) grant 가 있어야 한다. 없으면 보류 요청(`approvals.json`, 원천 `authz:msg:req:`)을 만들고
    HTTP 202 `{state: pending, requestId}` — 사람만 승인함에서 결정한다(에이전트는 승인할 수 없다). 거절은 5분 쿨다운,
    대기는 24시간·주체당 20·전체 200. 승인되면 grant(원천 `authz:msg:grant:`)가 생기고 세션이 사라지면 함께 정리된다.
  - 속도 제한(보내는 호스트당 분당 20)은 엔진이 센다 — 카운터는 `agent_msg._rate` 그대로(`check_rate`).

## 받는 쪽 (다른 맥 → 이 맥)
peer 주체 `peer:<id>/obo:agent:<id>/-` 가 `msg.send` on `msg:local/-`. 판정에 쓰는 grant 는 `hosts.json` 의 등급·`agentMessages` 에서
**그 자리에서 번역**한 것이다(`migrate.translate_peer_grant`) — `hosts.json` 이 아직 소유 저장소라 저장소 사본이 낡아 결과가
갈라질 수 없다. 등급(control)은 `@capability` 가 그대로 본다. 본문 해시 서명은 건드리지 않는다. 승인은 리소스를 가진 호스트의
사람만 한다(여기서는 거부만, 원격 요청을 만들지 않는다).

## 감사
`audit/authz.jsonl`. 메시지 **내용**은 남기지 않는다 — 글자 수(`sizes.chars`)와 sha8 만. 승인 결정·철회는 커밋 전에 쓰고
못 쓰면 중단한다(503 `audit_unavailable`). 판정·요청 줄은 데이터 경로라 쓰기 실패가 전달을 막지 않는다.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional

from i18n import tr

from . import approvals, audit, engine, flags, legacy_gate, limits, migrate, store
from . import principals as P
from . import scopes as S
from .errors import ShareError

logger = logging.getLogger(__name__)

PREFIX_REQ = "authz:msg:req:"
PREFIX_GRANT = "authz:msg:grant:"
SCOPE = "msg.send"
SOURCE = "msg"                               # 해결 기록의 종류(`authz:resolved:msg:<id>`)
REQ_PREFIX_ID = "ms-"
GRANT_ID_PREFIX = "gm-"
REQUEST_TTL = limits.PENDING_TTL
DENY_COOLDOWN = limits.DENY_COOLDOWN
MAX_PENDING_PER_AGENT = limits.PENDING_PER_PRINCIPAL
MAX_REQUESTS = limits.PENDING_TOTAL


def enforced() -> bool:
    """`VT_AUTHZ_ENFORCE` 에 `msg` 가 있는가."""
    return flags.enforced("msg")


def _am():
    import agent_msg
    return agent_msg


def _err(code: str, reason: str, status: int):
    return _am().MessageError(code, reason, status)


def _unavailable(e: BaseException):
    logger.warning(f"메시지 권한 엔진을 쓸 수 없다 — 거부: {type(e).__name__}: {e}")
    return _err("authz_unavailable", "권한 엔진을 쓸 수 없어 요청을 거부했습니다", 503)


# --- 관계 (위임 장부 · pair) ---------------------------------------------------------------------


def pair_related(a_key: str, b_key: str) -> bool:
    """STUB(5단계) — 같은 pair 의 구성원끼리는 승인 없이 보낼 수 있어야 한다. pair 저장소(`pairs.json`)가 아직 없어 항상 False.
    5단계가 이 함수만 채우면 된다(판정 지점·엔진 조건은 이미 `relation()` 을 거친다)."""
    return False


def _ledger_items() -> list[dict]:
    import delegation
    try:
        return delegation.list_all()
    except delegation.DelegationError as e:
        logger.warning(f"위임 장부를 읽지 못해 관계를 없는 것으로 본다({e.code})")
        return []


def relation(a_key: str, b_key: str, items: Optional[Iterable[dict]] = None) -> Optional[str]:
    """두 세션 key 의 관계 — `parent_child` | `sibling` | `pair` | None. 열린 위임(running·reported)만 센다."""
    if not a_key or not b_key or a_key == b_key:
        return None
    import delegation
    open_ = [x for x in (items if items is not None else _ledger_items())
             if isinstance(x, dict) and x.get("state") in delegation.OPEN_STATES]

    def key(x: dict, side: str) -> str:
        return str((x.get(side) or {}).get("key") or "")

    for x in open_:
        pk, ck = key(x, "parent"), key(x, "child")
        if pk and ck and {pk, ck} == {a_key, b_key}:
            return "parent_child"
    pa = {key(x, "parent") for x in open_ if key(x, "child") == a_key} - {""}
    pb = {key(x, "parent") for x in open_ if key(x, "child") == b_key} - {""}
    if pa & pb:
        return "sibling"
    if pair_related(a_key, b_key):
        return "pair"
    return None


# --- 레코드 -------------------------------------------------------------------------------------


def is_grant(rec: dict) -> bool:
    return str(rec.get("origin", "")).startswith(PREFIX_GRANT)


def is_request(rec: dict) -> bool:
    return str(rec.get("origin", "")).startswith(PREFIX_REQ)


def _raw(rec: dict) -> str:
    return str(rec.get("origin", "")).rsplit(":", 1)[-1]


def msg_grants(docs: dict) -> list[dict]:
    return [g for g in docs["grants"]["items"] if is_grant(g)]


def msg_requests(docs: dict) -> list[dict]:
    return [a for a in docs["approvals"]["items"] if is_request(a)]


def _safe_key(k: str) -> bool:
    return isinstance(k, str) and migrate._SAFE.fullmatch(k) is not None


def purge(docs: dict, alive: Optional[Iterable[str]], now: float) -> bool:
    """사라진 세션·만료된 대기·쿨다운이 끝난 거절을 걷어낸다. alive=None(tmux 를 못 읽음)이면 세션 정리는 건너뛴다."""
    live = None if alive is None else set(alive)

    def gone(r: dict) -> bool:
        return live is not None and (r.get("subject_session") not in live or r.get("resource_session") not in live)

    g0, a0 = list(docs["grants"]["items"]), list(docs["approvals"]["items"])
    docs["grants"]["items"] = [g for g in g0 if not (is_grant(g) and gone(g))]
    keep = []
    for a in a0:
        if is_request(a):
            if gone(a):
                continue
            if a.get("state") == "denied":
                if now - float(a.get("decided") or 0) >= DENY_COOLDOWN:
                    continue
            elif now >= float(a.get("expires") or 0):
                continue
        keep.append(a)
    docs["approvals"]["items"] = keep
    pruned = approvals.prune_resolved(docs, now)
    return len(g0) != len(docs["grants"]["items"]) or len(a0) != len(docs["approvals"]["items"]) or pruned


def _request_rec(rid: str, ak: str, tk: str, a_name: str, t_name: str, now: float, delegation: Optional[str] = None) -> dict:
    rec = {"id": f"ap-{rid}", "origin": f"{PREFIX_REQ}{rid}", "kind": "msg", "subject": f"agent:local/{ak}",
           "resource": f"msg:local/{tk}", "scopes": [SCOPE], "subject_session": ak, "resource_session": tk,
           "subject_name": a_name, "resource_name": t_name, "state": "pending", "created": now,
           "expires": now + REQUEST_TTL}
    if delegation:                               # 자식의 요청이면 부모 위임 id (계보 표시용 필드 — 판정에 쓰지 않는다)
        rec["delegation"] = delegation
    return rec


def _grant_rec(gid: str, req: dict, approved_by: str, now: float) -> dict:
    return {"id": f"{GRANT_ID_PREFIX}{gid}", "origin": f"{PREFIX_GRANT}{gid}", "kind": "msg",
            "subject": req["subject"], "resource": req["resource"], "scopes": [SCOPE],
            "subject_session": req["subject_session"], "resource_session": req["resource_session"],
            "subject_name": req.get("subject_name", ""), "resource_name": req.get("resource_name", ""),
            "state": "active", "approved_by": approved_by, "approved_via": S.VIA_MANUAL, "expires": None, "conds": {}, "created": now, "updated": now}


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


# --- 판정 ---------------------------------------------------------------------------------------


@dataclass
class Ticket:
    """통과한 판정의 요약 — `agent_msg.receive` 가 속도 제한을 엔진으로 다시 물을 때 쓴다(같은 주체·리소스)."""
    principal: P.Principal
    resource: str
    ctx: dict = field(default_factory=dict)
    peer_grant: Optional[dict] = None       # 받는 쪽: hosts.json grant(그 자리 번역의 원본)


@dataclass
class Verdict:
    allow: bool
    reason: str = "ok"
    grant_id: Optional[str] = None
    relation: Optional[str] = None
    ticket: Optional[Ticket] = None


def _snapshot(now: float, peer_grant: Optional[dict] = None) -> store.Snapshot:
    """저장소를 **캐시 없이** 읽는다(방금 쓴 grant 가 같은 mtime·크기로 낡게 보이면 안 된다). `peer_grant` 가 있으면 hosts.json 에서
    번역한 grant 가 저장소의 `legacy:hosts:` 사본을 대신한다."""
    docs = {n: store._read(n) for n in ("grants", "approvals", "windows", "shares")}
    grants = docs["grants"][0]["items"]
    src = "store"
    if peer_grant is not None:
        grants = [g for g in grants if not str(g.get("origin", "")).startswith("legacy:hosts:")] \
            + migrate.translate_peer_grant(peer_grant)
        src = "live"
    return store.Snapshot(grants=grants, approvals=docs["approvals"][0]["items"], windows=docs["windows"][0]["items"],
                          shares=docs["shares"][0]["items"],
                          corrupt=frozenset(n for n, (_d, bad) in docs.items() if bad), now=now, source=src)


def _decide(p: P.Principal, resource: str, ctx: dict, peer_grant: Optional[dict] = None) -> engine.Decision:
    try:
        d = engine.decide(p, SCOPE, resource, ctx, _snapshot(time.time(), peer_grant))
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e) from e
    if d.reason == "store_corrupt":
        raise _unavailable(RuntimeError("authz 저장소가 손상됨"))
    return d


def authorize_local(sender_key: str, sender_name: str, recv_key: str, alive: Optional[Iterable[str]]) -> Verdict:
    """`msg.send` on `msg:local/<받는 key>` — 관계가 있거나 grant 가 있으면 허용. 거부 사유 `not_related`(= grant 도 없음)."""
    p = P.agent(sender_key, name=sender_name)
    rel = relation(sender_key, recv_key)
    resource = f"msg:local/{recv_key}"
    ctx = {"msg": {"related": rel is not None, "relation": rel}, "alive": None if alive is None else set(alive)}
    d = _decide(p, resource, ctx)
    return Verdict(d.allow, d.reason, d.grant_id, rel, Ticket(p, resource, ctx) if d.allow else None)


def authorize_peer(grant: dict) -> Verdict:
    """받는 쪽 — peer 의 `msg.send`(= 옛 `agentMessages`). 등급(control)은 `@capability` 가 먼저 본다."""
    pid = str(grant.get("id") or "")
    p = P.from_peer_grant(grant, obo=f"agent:{pid}/-")
    resource = "msg:local/-"
    d = _decide(p, resource, {}, grant)
    return Verdict(d.allow, d.reason, d.grant_id, None, Ticket(p, resource, {}, grant) if d.allow else None)


def queue_authz(ticket: Optional[Ticket]) -> Optional[dict]:
    """통과한 판정 → 큐 항목 `source.authz` 에 실을 재평가 단서(`recheck_queued` 가 읽는다). 단서는 보낸 쪽의 신원뿐이다 —
    내용·권한 판정 결과는 싣지 않는다(입력 직전에 새로 판정한다)."""
    if ticket is None:
        return None
    if ticket.principal.kind == P.PEER:
        return {"peer": ticket.principal.attrs.get("peer_id", "")}
    return {"from": ticket.principal.attrs.get("key", ""), "to": ticket.resource.removeprefix("msg:local/")}


def recheck_queued(src: dict, item_id: str = "") -> Optional[str]:
    """큐에 쌓인 에이전트 메시지를 **입력 직전에** 다시 판정한다. 통과면 None, 막히면 `grant_revoked`(관계·grant·허용 스위치가 사라짐).
    단서(`source.authz`)가 없는 항목(플래그가 꺼진 동안 쌓인 것)은 건드리지 않는다. 거부는 감사한다(내용 없음)."""
    a = src.get("authz")
    if not isinstance(a, dict):
        return None
    mid = str(src.get("agentMsg") or "")
    if a.get("peer") is not None:
        import host_store
        grant = host_store.find_grant(str(a["peer"]))
        v = authorize_peer(grant) if grant else None
        ok, principal, resource = bool(v and v.allow), f"peer:{a['peer']}", "msg:local/-"
    else:
        sender, recv = str(a.get("from") or ""), str(a.get("to") or "")
        v = authorize_local(sender, "", recv, None)
        ok, principal, resource = v.allow, f"agent:local/{sender}", f"msg:local/{recv}"
    if ok:
        return None
    audit_op("queue", "denied", principal=principal, resource=resource, reason="grant_revoked", decision="deny",
             detail={"id": mid, "item": item_id})
    return "grant_revoked"


def check_rate(ticket: Ticket, from_host: str, now: Optional[float] = None) -> None:
    """보내는 호스트당 분당 한도(`agent_msg.RATE_PER_MIN`) — 같은 판정을 카운터와 함께 엔진에 다시 묻는다. 허용이면 한 건을 센다.
    카운터 저장소·메시지는 옛 `agent_msg.check_rate` 와 같다(`_rate`, 429 `rate_limited`)."""
    am = _am()
    now = time.time() if now is None else now
    with am._rate_lock:
        hits = limits.recent(am._rate, from_host, now)
        ctx = {**ticket.ctx, "counts": {SCOPE: len(hits)}, "limits": {SCOPE: am.RATE_PER_MIN}}
        d = _decide(ticket.principal, ticket.resource, ctx, ticket.peer_grant)
        if d.allow:
            limits.record(am._rate, from_host, hits, now)
            return
        am._rate[from_host] = hits
    if d.reason == "rate_limited":
        raise _err("rate_limited", f"{from_host}에서 온 메시지가 너무 많습니다 (분당 {am.RATE_PER_MIN}건)", 429)
    raise _err("forbidden", "메시지를 보낼 권한이 없습니다", 403)


# --- 감사 (데이터 경로 — 실패해도 전달을 막지 않는다) --------------------------------------------------


def text_meta(text: str) -> dict:
    return {"chars": len(text), "sha8": audit.sha8(text)}


def audit_op(action: str, result: str, *, principal: Optional[str] = None, resource: Optional[str] = None,
             reason: Optional[str] = None, chars: Optional[int] = None, sha8: Optional[str] = None,
             grant_id: Optional[str] = None, approval_id: Optional[str] = None, detail: Optional[dict] = None,
             decision: Optional[str] = None) -> None:
    """`msg.<action>` 한 줄. 내용은 받지 않는다 — 글자 수와 sha8 뿐."""
    try:
        rec = {"principal": principal or "agent:unverified", "action": f"msg.{action}",
               "resource": resource or "msg:*", "scopes": [SCOPE],
               "decision": decision or ("deny" if result in ("denied", "unverified", "rate_limited") else "allow"),
               "result": result, "reason": reason, "grant_id": grant_id, "approval_id": approval_id}
        if chars is not None:
            rec["sizes"] = {"chars": int(chars)}
        if sha8:
            rec["sha8"] = sha8
        if detail:
            rec["detail"] = detail
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"authz 메시지 감사 기록 실패: {e}")


def _audit_strict(rec: dict) -> None:
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        raise ShareError("audit_unavailable", tr("감사 기록을 쓰지 못해 변경을 중단했습니다"), 503) from e


# --- 요청 · 결정 · 철회 (사람이 보는 면) ----------------------------------------------------------------


def request_view(rec: dict, names: Optional[dict] = None) -> dict:
    """승인함·`decision` 응답용 — `/api/agent/share/requests` 의 모양(kind 만 `msg`). 세션 이름은 지금 값(`names`)이 우선이다."""
    names = names or {}
    ak, tk = rec["subject_session"], rec["resource_session"]
    a_name = names.get(ak) or rec.get("subject_name", "")
    t_name = names.get(tk) or rec.get("resource_name", "")
    v = {"id": _raw(rec), "kind": "msg",
         "state": rec.get("state"), "created": rec.get("created"), "expires": rec.get("expires"),
         "decided": rec.get("decided"),
         "agent": {"name": a_name, "id": ak.rpartition(":")[0]},
         "target": {"nick": t_name, "mode": None, "name": t_name, "id": tk.rpartition(":")[0]}}
    return v


def grant_view(rec: dict, names: Optional[dict] = None) -> dict:
    names = names or {}
    ak, tk = rec["subject_session"], rec["resource_session"]
    return {"id": rec["id"], "kind": "msg", "granted": rec.get("created"), "updated": rec.get("updated"),
            "approvedBy": rec.get("approved_by"), "approvedVia": S.via_of(rec),
            "agent": {"name": names.get(ak) or rec.get("subject_name", ""), "id": ak.rpartition(":")[0]},
            "target": {"name": names.get(tk) or rec.get("resource_name", ""), "id": tk.rpartition(":")[0]}}


def _rid(rec: dict) -> str:
    return _raw(rec)


def request_access(sender_key: str, sender_name: str, recv_key: str, recv_name: str,
                   alive: Optional[Iterable[str]]) -> tuple[dict, bool]:
    """보류 요청 하나. (요청 레코드, 새로 만들었는가). 같은 (보낸→받는) 쌍은 하나로 합친다. 쿨다운 중이면 403, 한도 초과면 429."""
    now = time.time()
    if not (_safe_key(sender_key) and _safe_key(recv_key)):
        raise ShareError("bad_target", tr("세션 key 가 올바르지 않습니다"), 400)
    legacy_gate.require_approval_available()      # 승인할 사람(웹 비밀번호)이 없으면 보류로 두지 않고 이유를 알린다
    from . import deleg_svc
    lineage = deleg_svc.lineage_of(sender_key)            # 락 밖에서 — 장부를 읽는다
    with store.txn() as docs:
        snap = store.Snapshot(grants=docs["grants"]["items"], approvals=docs["approvals"]["items"],
                              windows=docs["windows"]["items"], shares=docs["shares"]["items"],
                              corrupt=frozenset(n for n in ("grants", "approvals", "windows", "shares", "meta")
                                                if store._read(n)[1]), now=now, source="store")
        d = engine.decide(P.agent(sender_key, name=sender_name), "authz.request", "authz:*", {}, snap)
        if not d.allow:                          # authz.request 가 없으면 보류 요청도 없다
            raise ShareError("request_not_allowed", tr("승인 요청을 만들 수 없는 호출자입니다"), 403)
        purge(docs, alive, now)
        for r in msg_requests(docs):
            if r.get("subject_session") == sender_key and r.get("resource_session") == recv_key:
                if r.get("state") == "denied":
                    wait = int(DENY_COOLDOWN - (now - float(r.get("decided") or 0)))
                    raise ShareError("request_denied",
                                     f"사용자가 거부했습니다 — {max(wait, 1)}초 뒤에 다시 요청할 수 있습니다",
                                     403, state="denied", retryAfter=max(wait, 1))
                return r, False
        reqs = msg_requests(docs)
        mine = sum(1 for r in reqs if r.get("state") == "pending" and r.get("subject_session") == sender_key)
        if mine >= MAX_PENDING_PER_AGENT or len(reqs) >= MAX_REQUESTS:
            raise ShareError("too_many_requests", "대기 중인 승인 요청이 너무 많습니다 — 사용자가 처리한 뒤 다시 하세요", 429)
        rec = _request_rec(f"{REQ_PREFIX_ID}{uuid.uuid4().hex[:12]}", sender_key, recv_key, sender_name, recv_name, now, lineage)
        docs["approvals"]["items"].append(rec)
        return dict(rec), True


def list_requests(alive: Optional[Iterable[str]], names: Optional[dict] = None) -> list[dict]:
    """승인 대기 중인 메시지 요청(화면용). 만료·세션 종료는 걷어낸다."""
    now = time.time()
    with store.txn() as docs:
        purge(docs, alive, now)
        return [request_view(r, names) for r in msg_requests(docs) if r.get("state") == "pending"]


def list_grants(alive: Optional[Iterable[str]], names: Optional[dict] = None) -> list[dict]:
    now = time.time()
    with store.txn() as docs:
        purge(docs, alive, now)
        return [grant_view(g, names) for g in msg_grants(docs)]


def owns_request(request_id: str) -> bool:
    return request_id.startswith(REQ_PREFIX_ID) or request_id.startswith(f"ap-{REQ_PREFIX_ID}")


def owns_grant(grant_id: str) -> bool:
    return grant_id.startswith(GRANT_ID_PREFIX)


def decide(request_id: str, decision: str, level: Optional[str], actor: Optional[P.Principal],
           alive: Optional[Iterable[str]], names: Optional[dict] = None) -> dict:
    """사람의 결정. allow(grant 생성) 또는 deny(쿨다운 시작). 결과 `{request, grant, grantId}`.
    에이전트·cli·peer·무인증은 승인할 수 없다(`approvals.check_authority`)."""
    if decision not in ("allow", "deny"):
        raise ShareError("bad_decision", "decision 은 allow 또는 deny 입니다", 400)
    if level not in (None, "read", "msg"):
        raise ShareError("bad_level", tr("메시지 요청에는 level 이 필요 없습니다"), 400)
    now = time.time()
    rid = request_id[3:] if request_id.startswith("ap-") else request_id
    with store.txn() as docs:
        purge(docs, alive, now)
        req = next((r for r in msg_requests(docs) if _raw(r) == rid and r.get("state") == "pending"), None)
        if req is None:
            if actor is None and approvals.find_resolved(docs, SOURCE, rid) is not None:
                raise ShareError("human_session_required", tr("사람(브라우저 로그인 세션)만 승인할 수 있습니다"), 403)
            again = approvals.replay(docs, SOURCE, rid, decision, None, actor, now)   # 이미 결정된 요청: 처음 결과 또는 409
            if again is not None:
                return again
            raise ShareError("not_found", "대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)", 404)
        if actor is None:                        # 주체를 만들지 못했다 = 사람이 확인되지 않았다
            raise ShareError("human_session_required", tr("사람(브라우저 로그인 세션)만 승인할 수 있습니다"), 403)
        approvals.check_authority(actor, (SCOPE,) if decision == "allow" else (), req["resource"], None,
                                  approvals._snap(docs, now))
        who = actor.id
        base_audit = {"principal": who, "resource": req["resource"], "scopes": [SCOPE], "approval_id": req["id"],
                      "reason": f"agent={req.get('subject_name', '')}"}
        if decision == "deny":
            denied = {**req, "state": "denied", "decided": now, "cooldown_until": now + DENY_COOLDOWN}
            _audit_strict({**base_audit, "action": "msg.decide", "decision": "deny", "result": "denied",
                           "detail": {"request": _rid(req)}})
            _replace(docs["approvals"]["items"], req, denied)
            out = {"request": request_view(denied, names), "grant": None, "grantId": None}
            docs["approvals"]["items"].append(approvals.resolved_record(SOURCE, rid, req, "deny", None, now, out, ()))
            return out
        old = next((g for g in msg_grants(docs) if g.get("subject_session") == req["subject_session"]
                    and g.get("resource_session") == req["resource_session"]), None)
        new = old or _grant_rec(uuid.uuid4().hex[:12], req, who, now)
        _audit_strict({**base_audit, "action": "msg.decide", "decision": "allow", "result": "allowed",
                       "grant_id": new["id"], "detail": {"request": _rid(req), "escalation": ["new_grant"] if old is None else []}})
        if old is None:
            docs["grants"]["items"].append(new)
        docs["approvals"]["items"] = [a for a in docs["approvals"]["items"] if a is not req]
        out = {"request": request_view(req, names), "grant": grant_view(new, names), "grantId": new["id"]}
        docs["approvals"]["items"].append(approvals.resolved_record(SOURCE, rid, req, "allow", None, now, out, (SCOPE,)))
        return out


def revoke(grant_id: str, actor: Optional[P.Principal], names: Optional[dict] = None) -> dict:
    """권한 철회 — 지우면 다음 보내기는 다시 보류 요청이 된다."""
    with store.txn() as docs:
        g = next((x for x in msg_grants(docs) if x["id"] == grant_id or _raw(x) == grant_id), None)
        if g is None:
            raise ShareError("not_found", "그런 권한이 없습니다", 404)
        _audit_strict({"principal": (actor or P.human("-")).id, "action": "msg.revoke", "resource": g["resource"],
                       "scopes": [SCOPE], "decision": "allow", "result": "ok", "grant_id": g["id"],
                       "reason": f"agent={g.get('subject_name', '')}"})
        _replace(docs["grants"]["items"], g, None)
        return grant_view(g, names)
