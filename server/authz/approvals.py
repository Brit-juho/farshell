"""에이전트 세션 공유의 grant·승인 요청 — 엔진 저장소(`grants.json`·`approvals.json`) 위의 순수 변경 함수들.

모든 함수는 `store.txn()` 이 준 `docs` 를 **제자리에서** 고치고 값을 돌려줄 뿐, 파일·tmux·시계를 직접 읽지 않는다
(`now` 를 받는다). 락·dual-write·감사는 호출자(`share_svc`)가 한다.

## 표현
엔진 레코드는 `migrate.translate_agent_grant/_request` 의 출력과 **정확히 같은 모양**이다(원본 id 기준 origin
`legacy:agent_grants:<id>` / `…:req:<id>`, 엔진 id `ag-<id>` / `ap-<id>`). 그래서
  - `fsh migrate-auth --verify` 가 dual-write 상태를 "차이 없음"으로 보고,
  - 엔진 레코드 → 옛 모양(`grant_to_legacy`) → 다시 번역하면 같은 레코드가 되며(왕복 시험),
  - 옛 저장소 파일은 엔진 상태의 투영으로 언제든 다시 만들 수 있다.
옛 모양 dict 는 라우트·MCP 응답이 쓰는 모양(`{id, agent, target, read, write, …}`)이다 — 공개 계약이 안 바뀐다.

## 정책
  - R4.1 권한이 없을 때만 보류 요청. 같은 (주체, 세션, 종류) 는 하나로 합친다.
  - R4.6 거절은 300초 쿨다운. R4.7 대기 24시간 만료, 주체당 20·전체 200.
  - R4.8 승인자는 요청보다 좁게(읽기 요청에 읽기만) 줄 수 있고 넓게는 못 준다. 공유 상한(ceiling)보다 높게도 못 준다.
  - R4.3 elevated 가 필요한 scope 가 들어 있으면 elevated human 만 승인. R4.4 확대는 `scopes.escalation` 으로 찾아 감사에 남긴다.
  - 승인자는 요청한 (scope, 조건)을 **이미 가진** 서명된 human 세션이어야 한다: `check_authority` 가 요청의 (scope, 조건)마다
    `engine.decide(승인자, scope, 리소스, ctx+approving)` 를 묻는다. 비elevated human 은 elevated 사유(R4.3)인 요청에서 거부된다.
  - 결정은 **먼저 온 쪽이 이긴다**. 결정된 요청은 해결 기록(`authz:resolved:<종류>:<id>`)으로 `RESOLVED_RETENTION`(10분) 동안 남아,
    같은 결정을 다시 보내면 처음 결과를 그대로(`replayed`) 돌려주고 다른 결정이면 409 `already_decided`. 거절 쿨다운(R4.6)은 그대로.
    해결 기록은 옛 저장소(`legacy:` 원천)가 아니라서 `agent_grants.json` 투영과 `--verify` 에 나타나지 않는다.
  - 자동 승인 window는 4단계 — 여기서는 만들지도 읽지도 않는다. `auto_approve()` 는 항상 None 이다.
"""

from __future__ import annotations

import uuid
from typing import Iterable, Optional

from i18n import tr

from . import engine, migrate, store
from . import principals as P
from . import scopes as S
from .errors import ShareError

PREFIX = "legacy:agent_grants:"
PREFIX_REQ = "legacy:agent_grants:req:"
READ_SCOPES = ("session.view", "session.read")
WRITE_SCOPE = "session.input"              # 조건 modes=[line] (기본값)
WRITE_SCONDS = {"session.input": {"modes": ["line"]}}
SOURCE = "share"                           # 해결 기록의 종류(`authz:resolved:share:<id>`)
KIND_READ, KIND_WRITE = "read", "write"
LEVEL_READ, LEVEL_READWRITE = "read", "readwrite"


# --- 레코드 ↔ 옛 모양 ------------------------------------------------------------------------


def raw_id(rec: dict) -> str:
    return str(rec.get("origin", "")).rsplit(":", 1)[-1]


def _ref(key: str, name: str = "") -> dict:
    sid, _, created = key.rpartition(":")
    try:
        created_v = int(created)
    except ValueError:
        created_v = created
    ref = {"sid": sid, "created": created_v}
    if name:
        ref["name"] = name
    return ref


def is_grant(rec: dict) -> bool:
    o = str(rec.get("origin", ""))
    return o.startswith(PREFIX) and not o.startswith(PREFIX_REQ)


def is_request(rec: dict) -> bool:
    return str(rec.get("origin", "")).startswith(PREFIX_REQ)


def kind_of(rec: dict) -> str:
    return KIND_WRITE if _can_write(rec) else KIND_READ


def _can_write(rec: dict) -> bool:
    """한 줄 입력(직접)을 줄 수 있는 레코드인가."""
    e = S.caps_of(rec).get(WRITE_SCOPE)
    return bool(e) and "line" in e.get("modes", ()) and "direct" in e.get("via", ())


def grant_to_legacy(rec: dict) -> dict:
    scopes = rec.get("scopes") or ()
    agent = _ref(rec["subject_session"], rec.get("subject_name") or "")
    agent.setdefault("name", "")
    out = {"id": raw_id(rec), "agent": agent, "target": _ref(rec["resource_session"]),
           "read": "session.read" in scopes, "write": _can_write(rec), "granted": rec.get("created")}
    if rec.get("updated") is not None:
        out["updated"] = rec["updated"]
    return out


def request_to_legacy(rec: dict) -> dict:
    agent = _ref(rec["subject_session"], rec.get("subject_name") or "")
    agent.setdefault("name", "")
    out = {"id": raw_id(rec), "agent": agent, "target": _ref(rec["resource_session"]), "kind": kind_of(rec),
           "state": rec.get("state"), "created": rec.get("created"), "expires": rec.get("expires")}
    if rec.get("state") == "denied":
        out["decided"] = rec.get("decided")
    return out


def grant_listing(rec: dict) -> dict:
    """목록용 옛 모양 grant + 누가·어떻게 승인했나(`approvedBy`·`approvedVia`). 옛 파일 투영(`legacy_state`)에는 넣지 않는다."""
    return {**grant_to_legacy(rec), "approvedBy": rec.get("approved_by"), "approvedVia": S.via_of(rec)}


def legacy_state(docs: dict, *, provenance: bool = False) -> dict:
    """엔진 상태 → 옛 파일 모양 `{"grants": [...], "requests": [...]}`. `provenance=True` 는 목록 응답용(승인 출처 포함)."""
    to = grant_listing if provenance else grant_to_legacy
    return {"grants": [to(g) for g in docs["grants"]["items"] if is_grant(g)],
            "requests": [request_to_legacy(a) for a in docs["approvals"]["items"] if is_request(a)]}


def engine_grant(legacy: dict, *, approved_by: str, approved_via: str = S.VIA_MANUAL, stamp: str = "live") -> Optional[dict]:
    rec = migrate.translate_agent_grant(legacy, stamp=stamp)
    if rec is not None:
        rec["approved_by"] = approved_by
        rec["approved_via"] = approved_via if approved_via in S.APPROVED_VIA else S.VIA_MIGRATED
    return rec


def engine_request(legacy: dict, now: float) -> Optional[dict]:
    return migrate.translate_agent_request(legacy, now)


def agent_grants(docs: dict) -> list[dict]:
    return [g for g in docs["grants"]["items"] if is_grant(g)]


def agent_requests(docs: dict) -> list[dict]:
    return [a for a in docs["approvals"]["items"] if is_request(a)]


def find_grant(docs: dict, agent_key: str, target_key: str) -> Optional[dict]:
    return next((g for g in agent_grants(docs)
                 if g.get("subject_session") == agent_key and g.get("resource_session") == target_key), None)


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


# --- 정리 -----------------------------------------------------------------------------------


def purge(docs: dict, alive: Optional[Iterable[str]], now: float, cooldown: float) -> bool:
    """사라진 세션·만료된 요청·쿨다운이 끝난 거절을 걷어낸다. alive=None(tmux 를 못 읽음)이면 세션 정리는 건너뛴다."""
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
                if now - float(a.get("decided") or 0) >= cooldown:
                    continue
            elif now >= float(a.get("expires") or 0):
                continue
        keep.append(a)
    docs["approvals"]["items"] = keep
    pruned = prune_resolved(docs, now)
    return len(g0) != len(docs["grants"]["items"]) or len(a0) != len(docs["approvals"]["items"]) or pruned


# --- 요청 -----------------------------------------------------------------------------------


def auto_approve(*_a, **_kw) -> None:
    """자동 승인 window — 아직 없다. window 를 만들지도 읽지도 않으므로 항상 None(= 사람이 결정)."""
    return None


def request(docs: dict, agent_ref: dict, target_ref: dict, kind: str, now: float, *, ttl: float, per_agent: int,
            total: int, cooldown: float, new_id: Optional[str] = None, delegation: Optional[str] = None) -> tuple[dict, bool]:
    """보류 요청 하나. (옛 모양 요청, 새로 만들었는가). 쿨다운 중이면 403, 한도를 넘으면 429."""
    ak = f"{agent_ref['sid']}:{agent_ref['created']}"
    tk = f"{target_ref['sid']}:{target_ref['created']}"
    for r in agent_requests(docs):
        if kind_of(r) == kind and r.get("subject_session") == ak and r.get("resource_session") == tk:
            if r.get("state") == "denied":
                wait = int(cooldown - (now - float(r.get("decided") or 0)))
                raise ShareError("request_denied",
                                 f"사용자가 거부했습니다 — {max(wait, 1)}초 뒤에 다시 요청할 수 있습니다",
                                 403, state="denied", retryAfter=max(wait, 1))
            return request_to_legacy(r), False
    reqs = agent_requests(docs)
    mine = sum(1 for r in reqs if r.get("state") == "pending" and r.get("subject_session") == ak)
    if mine >= per_agent or len(reqs) >= total:
        raise ShareError("too_many_requests", "대기 중인 승인 요청이 너무 많습니다 — 사용자가 처리한 뒤 다시 하세요", 429)
    legacy = {"id": new_id or uuid.uuid4().hex[:12], "agent": dict(agent_ref), "target": {"sid": target_ref["sid"],
              "created": target_ref["created"]}, "kind": kind, "state": "pending", "created": now, "expires": now + ttl}
    rec = engine_request(legacy, now)
    if rec is None:                              # ttl<=0 같은 시험 값 — 요청은 만들어지지 않는다
        return legacy, True
    if delegation:                               # 자식의 요청이면 부모 위임 id 를 남긴다(계보 표시용 필드 — 판정에 쓰지 않는다)
        rec["delegation"] = delegation
    docs["approvals"]["items"].append(rec)
    return request_to_legacy(rec), True


# --- 결정 -----------------------------------------------------------------------------------


def _approver_ctxs(scopes: Iterable[str], sconds: Optional[dict]) -> list[tuple[str, dict]]:
    """요청의 (scope, 조건 값) 하나하나에 대한 판정 컨텍스트 `[(scope, ctx)]`. 조건이 여럿이면 다른 조건은 첫 값으로 채운다."""
    out = []
    for sc, entry in sorted(S.effective(scopes, sconds).items()):
        specs = S.COND_SPEC.get(sc, ())
        if not specs:
            out.append((sc, {}))
            continue
        base = {c.ctx: sorted(entry[c.key])[0] for c in specs if entry.get(c.key)}
        for c in specs:
            for v in sorted(entry.get(c.key, ())):
                out.append((sc, {**base, c.ctx: v}))
    return out


def check_authority(actor: Optional[P.Principal], scopes: Iterable[str], resource: str,
                    sconds: Optional[dict] = None, snap: Optional[store.Snapshot] = None, *, kind: str = "agent") -> None:
    """승인권자 확인 — 서명된 human 세션만(에이전트·훅·cli·svc·peer·기계 토큰은 안 된다). 그리고 승인자는 요청한 (scope, 조건)을
    **이미 가져야** 한다: 요청마다 엔진에 승인자 자신의 판정을 묻는다(`ctx.approving`). 비elevated human 은 elevated 사유(R4.3)인
    요청을 승인할 수 없다."""
    actor = actor or P.human("-")
    if actor.kind != P.HUMAN or not actor.authenticated:
        raise ShareError("human_session_required", tr("사람(브라우저 로그인 세션)만 승인할 수 있습니다"), 403)
    need = S.requires_elevated(kind, scopes, resource, sconds)
    for sc, ctx in _approver_ctxs(scopes, sconds):
        # `kind` 는 요청한 주체의 종류다 — 다른 Mac 의 에이전트(`peer`)는 보기·읽기를 넘는 것이 모두 elevated 사유다(R4.3).
        d = engine.decide(actor, sc, resource, {**ctx, "approving": True, **({"approving_kind": kind} if kind != "agent" else {})}, snap)
        if d.allow:
            continue
        if d.reason == "elevation_required":
            raise ShareError("elevation_required", tr("이 권한은 재인증(elevated)한 사람만 승인할 수 있습니다"), 403,
                             scopes=sorted(need))
        raise ShareError("approver_lacks_scope", tr("승인하는 사람이 요청된 권한을 가지고 있지 않아 승인할 수 없습니다"), 403,
                         scope=sc, why=d.reason)
    if need and not actor.elevated:               # 같은 사실을 엔진 밖에서 한 번 더(방어)
        raise ShareError("elevation_required", tr("이 권한은 재인증(elevated)한 사람만 승인할 수 있습니다"), 403,
                         scopes=sorted(need))


def _snap(docs: dict, now: float) -> store.Snapshot:
    return store.Snapshot(grants=docs["grants"]["items"], approvals=docs["approvals"]["items"],
                          windows=docs["windows"]["items"], shares=docs["shares"]["items"], now=now, source="store")


# --- 해결 기록 (N-1: 먼저 온 결정이 이긴다) -------------------------------------------------------

RESOLVED_PREFIX = "authz:resolved:"
RESOLVED_RETENTION = 600.0                    # 결정된 요청을 답할 수 있게 남겨 두는 시간(초)


def is_resolved(rec: dict) -> bool:
    return str(rec.get("origin", "")).startswith(RESOLVED_PREFIX)


def resolved_record(source: str, rid: str, req: dict, decision: str, level: Optional[str], now: float, outcome: dict,
                    scopes: Iterable[str], sconds: Optional[dict] = None) -> dict:
    """결정된 요청의 해결 기록. `outcome` 은 처음 결과(JSON) — 같은 결정의 재전송에 그대로 돌려준다."""
    rec = {"id": f"ar-{source}-{rid}", "origin": f"{RESOLVED_PREFIX}{source}:{rid}", "kind": "resolved", "source": source,
           "request_id": rid, "state": "resolved", "decision": decision, "level": level, "decided": now,
           "expires": now + RESOLVED_RETENTION, "subject": req.get("subject"), "resource": req.get("resource"),
           "subject_session": req.get("subject_session"), "resource_session": req.get("resource_session"),
           "scopes": list(scopes), "outcome": outcome, "schema": S.SCHEMA}
    if sconds:
        rec["sconds"] = sconds
    return rec


def find_resolved(docs: dict, source: str, rid: str) -> Optional[dict]:
    o = f"{RESOLVED_PREFIX}{source}:{rid}"
    return next((a for a in docs["approvals"]["items"] if a.get("origin") == o), None)


def prune_resolved(docs: dict, now: float) -> bool:
    items = docs["approvals"]["items"]
    keep = [a for a in items if not (is_resolved(a) and now >= float(a.get("expires") or 0))]
    docs["approvals"]["items"] = keep
    return len(keep) != len(items)


def replay(docs: dict, source: str, rid: str, decision: str, level: Optional[str], actor: Optional[P.Principal],
           now: float) -> Optional[dict]:
    """이미 결정된 요청에 온 결정. 해결 기록이 없으면 None(= 처음 보는 요청). 같은 결정이면 처음 결과(`replayed: true`),
    다른 결정이면 409 `already_decided`. 응답하기 전에 승인권자를 다시 확인한다(결정 내용을 아무에게나 알려 주지 않는다)."""
    rec = find_resolved(docs, source, rid)
    if rec is None:
        return None
    if rec.get("decision") == "allow":
        check_authority(actor, rec.get("scopes") or (), rec.get("resource") or "", rec.get("sconds"), _snap(docs, now))
    else:
        check_authority(actor, (), rec.get("resource") or "")
    if decision != rec.get("decision") or (decision == "allow" and level is not None and level != rec.get("level")):
        raise ShareError("already_decided", tr("이미 처리된 요청입니다 — 먼저 온 결정이 적용됐습니다"), 409,
                         decision=rec.get("decision"), level=rec.get("level"))
    return {**rec["outcome"], "replayed": True}


def decide(docs: dict, request_id: str, decision: str, level: Optional[str], *, actor: Optional[P.Principal],
           share_mode: Optional[str], now: float) -> dict:
    """사람의 결정. allow(level=요청 종류와 같거나 낮게) 또는 deny. 결과 `{request, grant, grantId, escalation, …}`.

    share_mode: 대상 세션의 지금 공유 모드(`read`/`readwrite`/"" 미공유). None 이면 모른다 → 상한 검사를 건너뛴다(옛 동작)."""
    if decision not in ("allow", "deny"):
        raise ShareError("bad_decision", "decision 은 allow 또는 deny 입니다", 400)
    rid = request_id[3:] if request_id.startswith("ap-") else request_id
    req = next((r for r in agent_requests(docs) if raw_id(r) == rid and r.get("state") == "pending"), None)
    if req is None:
        again = replay(docs, SOURCE, rid, decision, level, actor, now)      # 이미 결정된 요청이면 처음 결과 또는 409
        if again is not None:
            return again
        raise ShareError("not_found", "대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)", 404)
    kind = kind_of(req)
    resource = f"session:local/{req['resource_session']}"
    if decision == "deny":
        check_authority(actor, (), resource, None, _snap(docs, now))
        gone = {**request_to_legacy(req), "state": "denied", "decided": now}
        denied = engine_request(gone, now)
        _replace(docs["approvals"]["items"], req, denied)
        out = {"request": request_to_legacy(denied), "grant": None, "grantId": None, "approvalId": denied["id"],
               "escalation": [], "resource": resource, "kind": kind}
        docs["approvals"]["items"].append(resolved_record(SOURCE, rid, req, "deny", None, now, out, ()))
        return out
    level = level or (LEVEL_READ if kind == KIND_READ else LEVEL_READWRITE)
    if level not in (LEVEL_READ, LEVEL_READWRITE) or (kind == KIND_READ and level != LEVEL_READ):
        raise ShareError("bad_level", "요청보다 높은 권한은 줄 수 없습니다(읽기 요청에는 read 만)", 400)
    if level == LEVEL_READWRITE and share_mode is not None and share_mode != LEVEL_READWRITE:
        raise ShareError("read_only_share", "이 공유는 읽기 전용입니다 — 공유 모드를 읽기·쓰기로 올린 뒤 허용하세요", 409)
    granted_scopes = list(READ_SCOPES) + ([WRITE_SCOPE] if level == LEVEL_READWRITE else [])
    wconds = WRITE_SCONDS if level == LEVEL_READWRITE else None
    check_authority(actor, granted_scopes, resource, wconds, _snap(docs, now))
    ak, tk = req["subject_session"], req["resource_session"]
    old = find_grant(docs, ak, tk)
    legacy = grant_to_legacy(old) if old else {
        "id": uuid.uuid4().hex[:12], "agent": _ref(ak, req.get("subject_name") or ""), "target": _ref(tk),
        "read": False, "write": False, "granted": now}
    if old is None:
        legacy["agent"].setdefault("name", "")
    legacy["read"] = True
    if level == LEVEL_READWRITE:
        legacy["write"] = True
    legacy["updated"] = now
    approved_by = (actor or P.human("-")).id
    new = engine_grant(legacy, approved_by=approved_by)
    escalation = S.escalation(old, new) if old is not None else ["new_grant"]
    _replace(docs["grants"]["items"], old, new)
    docs["approvals"]["items"] = [a for a in docs["approvals"]["items"] if a is not req]
    out = {"request": request_to_legacy(req), "grant": grant_to_legacy(new), "grantId": new["id"],
           "approvalId": req["id"], "escalation": escalation, "resource": resource, "kind": kind, "scopes": granted_scopes}
    docs["approvals"]["items"].append(resolved_record(SOURCE, rid, req, "allow", level, now, out, granted_scopes, wconds))
    return out


def revoke(docs: dict, grant_id: str, scope: str, now: float) -> tuple[dict, dict]:
    """권한 철회. scope `all`(전부) | `write`(쓰기만 거두고 읽기는 남긴다). (옛 모양 grant, 엔진 grant id)."""
    if scope not in ("all", "write"):
        raise ShareError("bad_scope", "scope 는 all 또는 write 입니다", 400)
    gid = grant_id[3:] if grant_id.startswith("ag-") else grant_id
    g = next((x for x in agent_grants(docs) if raw_id(x) == gid), None)
    if g is None:
        raise ShareError("not_found", "그런 권한이 없습니다", 404)
    if scope == "write":
        legacy = grant_to_legacy(g)
        legacy["write"], legacy["updated"] = False, now
        new = engine_grant(legacy, approved_by=g.get("approved_by") or "system", approved_via=S.via_of(g))
        _replace(docs["grants"]["items"], g, new)
        return grant_to_legacy(new), {"id": g["id"], "resource": f"session:local/{g['resource_session']}"}
    _replace(docs["grants"]["items"], g, None)
    return grant_to_legacy(g), {"id": g["id"], "resource": f"session:local/{g['resource_session']}"}


def drop_target(docs: dict, target_key: str) -> tuple[int, list[str]]:
    """공유 해제 — 그 세션에 대한 모든 권한과 대기·거부 요청. (지운 권한 수, 지운 요청 id들[옛 id])."""
    n = len(agent_grants(docs))
    gone = [raw_id(r) for r in agent_requests(docs) if r.get("resource_session") == target_key]
    docs["grants"]["items"] = [g for g in docs["grants"]["items"]
                               if not (is_grant(g) and g.get("resource_session") == target_key)]
    docs["approvals"]["items"] = [a for a in docs["approvals"]["items"]
                                  if not ((is_request(a) or (is_resolved(a) and a.get("source") == SOURCE))
                                          and a.get("resource_session") == target_key)]
    return n - len(agent_grants(docs)), gone
