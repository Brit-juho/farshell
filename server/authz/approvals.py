"""에이전트 세션 공유의 grant·승인 요청 — 엔진 저장소(`grants.json`·`approvals.json`) 위의 순수 변경 함수들 (3a).

모든 함수는 `store.txn()` 이 준 `docs` 를 **제자리에서** 고치고 값을 돌려줄 뿐, 파일·tmux·시계를 직접 읽지 않는다
(`now` 를 받는다). 락·dual-write·감사는 호출자(`share_svc`)가 한다.

## 표현
엔진 레코드는 `migrate.translate_agent_grant/_request` 의 출력과 **정확히 같은 모양**이다(원본 id 기준 origin
`legacy:agent_grants:<id>` / `…:req:<id>`, 엔진 id `ag-<id>` / `ap-<id>`). 그래서
  - `fsh migrate-auth --verify` 가 dual-write 상태를 "차이 없음"으로 보고,
  - 엔진 레코드 → 옛 모양(`grant_to_legacy`) → 다시 번역하면 같은 레코드가 되며(왕복 시험),
  - 옛 저장소 파일은 엔진 상태의 투영으로 언제든 다시 만들 수 있다.
옛 모양 dict 는 라우트·MCP 응답이 쓰는 모양(`{id, agent, target, read, write, …}`)이다 — 공개 계약이 안 바뀐다.

## 정책 (10-policy.md)
  - R4.1 권한이 없을 때만 보류 요청. 같은 (주체, 세션, 종류) 는 하나로 합친다.
  - R4.6 거절은 300초 쿨다운. R4.7 대기 24시간 만료, 주체당 20·전체 200.
  - R4.8 승인자는 요청보다 좁게(읽기 요청에 읽기만) 줄 수 있고 넓게는 못 준다. 공유 상한(ceiling)보다 높게도 못 준다.
  - R4.3 elevated 가 필요한 scope 가 들어 있으면 elevated human 만 승인. R4.4 확대는 `scopes.escalation` 으로 찾아 감사에 남긴다.
  - 자동 승인 window(R4.5)는 4단계 — 여기서는 만들지도 읽지도 않는다. `auto_approve()` 는 항상 None 이다.
"""

from __future__ import annotations

import uuid
from typing import Iterable, Optional

from i18n import tr

from . import migrate
from . import principals as P
from . import scopes as S
from .errors import ShareError

PREFIX = "legacy:agent_grants:"
PREFIX_REQ = "legacy:agent_grants:req:"
READ_SCOPES = ("session.list", "session.read")
WRITE_SCOPE = "session.input.line"
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
    return KIND_WRITE if WRITE_SCOPE in (rec.get("scopes") or ()) else KIND_READ


def grant_to_legacy(rec: dict) -> dict:
    scopes = rec.get("scopes") or ()
    agent = _ref(rec["subject_session"], rec.get("subject_name") or "")
    agent.setdefault("name", "")
    out = {"id": raw_id(rec), "agent": agent, "target": _ref(rec["resource_session"]),
           "read": "session.read" in scopes, "write": WRITE_SCOPE in scopes, "granted": rec.get("created")}
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


def legacy_state(docs: dict) -> dict:
    """엔진 상태 → 옛 파일 모양 `{"grants": [...], "requests": [...]}`."""
    return {"grants": [grant_to_legacy(g) for g in docs["grants"]["items"] if is_grant(g)],
            "requests": [request_to_legacy(a) for a in docs["approvals"]["items"] if is_request(a)]}


def engine_grant(legacy: dict, *, approved_by: str, stamp: str = "live") -> Optional[dict]:
    rec = migrate.translate_agent_grant(legacy, stamp=stamp)
    if rec is not None:
        rec["approved_by"] = approved_by
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
    """사라진 세션·만료된 요청·쿨다운이 끝난 거절을 걷어낸다. alive=None(tmux 를 못 읽음)이면 세션 정리는 건너뛴다(F3)."""
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
    return len(g0) != len(docs["grants"]["items"]) or len(a0) != len(keep)


# --- 요청 -----------------------------------------------------------------------------------


def auto_approve(*_a, **_kw) -> None:
    """R4.5 자동 승인 window — 4단계. 3a 는 window 를 만들지도 읽지도 않으므로 항상 None(= 사람이 결정)."""
    return None


def request(docs: dict, agent_ref: dict, target_ref: dict, kind: str, now: float, *, ttl: float, per_agent: int,
            total: int, cooldown: float, new_id: Optional[str] = None) -> tuple[dict, bool]:
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
    docs["approvals"]["items"].append(rec)
    return request_to_legacy(rec), True


# --- 결정 -----------------------------------------------------------------------------------


def check_authority(actor: Optional[P.Principal], scopes: Iterable[str], resource: str) -> None:
    """승인권자 확인 — human 만, 그리고 R4.3 scope 가 있으면 elevated human 만(R1.3)."""
    actor = actor or P.human("-")
    if actor.kind != P.HUMAN or not actor.authenticated:
        raise ShareError("human_session_required", tr("사람(브라우저 로그인 세션)만 승인할 수 있습니다"), 403)
    need = S.requires_elevated("agent", scopes, resource)
    if need and not actor.elevated:
        raise ShareError("elevation_required", tr("이 권한은 재인증(elevated)한 사람만 승인할 수 있습니다"), 403,
                         scopes=sorted(need))


def decide(docs: dict, request_id: str, decision: str, level: Optional[str], *, actor: Optional[P.Principal],
           share_mode: Optional[str], now: float) -> dict:
    """사람의 결정. allow(level=요청 종류와 같거나 낮게) 또는 deny. 결과 `{request, grant, grantId, escalation, …}`.

    share_mode: 대상 세션의 지금 공유 모드(`read`/`readwrite`/"" 미공유). None 이면 모른다 → 상한 검사를 건너뛴다(옛 동작)."""
    if decision not in ("allow", "deny"):
        raise ShareError("bad_decision", "decision 은 allow 또는 deny 입니다", 400)
    rid = request_id[3:] if request_id.startswith("ap-") else request_id
    req = next((r for r in agent_requests(docs) if raw_id(r) == rid and r.get("state") == "pending"), None)
    if req is None:
        raise ShareError("not_found", "대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)", 404)
    kind = kind_of(req)
    resource = f"session:local/{req['resource_session']}"
    if decision == "deny":
        check_authority(actor, (), resource)
        gone = {**request_to_legacy(req), "state": "denied", "decided": now}
        denied = engine_request(gone, now)
        _replace(docs["approvals"]["items"], req, denied)
        return {"request": request_to_legacy(denied), "grant": None, "grantId": None, "approvalId": denied["id"],
                "escalation": [], "resource": resource, "kind": kind}
    level = level or (LEVEL_READ if kind == KIND_READ else LEVEL_READWRITE)
    if level not in (LEVEL_READ, LEVEL_READWRITE) or (kind == KIND_READ and level != LEVEL_READ):
        raise ShareError("bad_level", "요청보다 높은 권한은 줄 수 없습니다(읽기 요청에는 read 만)", 400)
    if level == LEVEL_READWRITE and share_mode is not None and share_mode != LEVEL_READWRITE:
        raise ShareError("read_only_share", "이 공유는 읽기 전용입니다 — 공유 모드를 읽기·쓰기로 올린 뒤 허용하세요", 409)
    granted_scopes = list(READ_SCOPES) + ([WRITE_SCOPE] if level == LEVEL_READWRITE else [])
    check_authority(actor, granted_scopes, resource)
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
    return {"request": request_to_legacy(req), "grant": grant_to_legacy(new), "grantId": new["id"],
            "approvalId": req["id"], "escalation": escalation, "resource": resource, "kind": kind, "scopes": granted_scopes}


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
        new = engine_grant(legacy, approved_by=g.get("approved_by") or "system")
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
                                  if not (is_request(a) and a.get("resource_session") == target_key)]
    return n - len(agent_grants(docs)), gone
