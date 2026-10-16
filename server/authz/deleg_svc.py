"""에이전트 위임의 권한 판정·파생·감사 — `VT_AUTHZ_ENFORCE=deleg` 일 때 엔진이 판정권자다.

`delegation.py` 가 동작부(tmux·큐·outbox·상태 전이)를 쥐고, 판정·grant 파생·감사는 여기로 넘어온다. 공개 함수의
서명·반환·오류 모양은 그대로다(라우트·MCP·시험이 그대로 돈다).

## 소유 원장과 파생 grant
`~/.vt/delegations.json` 은 **소유 원장**으로 남는다(계속 쓴다). 엔진의 grant 는 거기서 **파생**한다:
  - 부모 → `deleg.control`(role=parent), 자식 → `deleg.control`(role=child) (`deleg:local/<id>`, `approved_by: system:spawn`)
  - 위임이 닫히거나(closed) 부모가 사라지면(orphaned) `state: "ended"`. 원장에서 항목이 사라지면 grant 도 사라진다.
  - 파일 기록(상속 규칙, `migrate.translate_delegation`): 새 worktree 경로의 자식 전용 grant, 또는 부모 fs grant 의 공유 참조.
    이 grant 들은 기록·동반 회수(부모 grant 가 끝나면 상속분도 `revoked`)까지만 한다. **쓰기를 막는 것은 `VT_AUTHZ_ENFORCE=lease` 가 발급하는 lease** 다(`delegation._issue_leases`, `docs/ref/auth.md` 「Folder leases」).
번역은 `migrate.translate_delegation` 하나다(`fsh migrate-auth --apply/--verify` 와 같은 함수) — 그래서 dual-write 상태를
`--verify` 가 "차이 없음"으로 본다. 명시적 회수(`state: revoked`)는 다시 파생해도 되살아나지 않는다(`migrate._sync`).

## 락 순서와 dual-write
`delegation._locked()` 는 켜짐에서 **authz 락 → 장부 락** 순서로 둘을 쥔다(`migrate.restore` 와 같은 순서). 그 안에서:
  - 들어올 때 장부 파일의 sha256 이 `meta.deleg_ledger_sig` 와 다르면(플래그를 끈 사이 옛 코드가 썼거나 손으로 고침)
    장부를 **채택** — 전체를 다시 파생한다(= 껐다 켜기·롤백이 스스로 맞춘다).
  - 장부를 쓸 때(`delegation._write`) 같은 임계 구역에서 grant 를 **먼저** 동기화하고 장부를 쓴다. authz 저장이나 감사가
    실패하면 장부는 바뀌지 않는다(503, 감사를 못 쓰면 닫힌다). 장부가 늦게 실패하면 grant 가 앞서지만 다음 동기화가 맞춘다.
꺼짐에서는 이 모듈을 거치지 않는다: 장부만 쓰고 authz 저장소는 건드리지 않는다(= 즉시 롤백).

## 판정
주체 `agent:local/<sid>:<created>`(share_identity 가 pane 소속을 확인한 호출자), scope `deleg.spawn`/`deleg.control`(role=parent|child).
spawn 은 `deleg-quota:<주체>` + 조건(열린 자식 ≤3, 깊이 1, 5/분 전역[장부 `spawns`], `VT_AGENT_SPAWN`, 그리고 **safe_mode**).
호출자가 대는 헤더·본문은 판정에 들어가지 않는다. 엔진 예외·손상된 저장소 = 닫힘(503 `authz_unavailable`).
닫힌 위임의 grant 는 `ended` 라 엔진은 거부하지만, 장부가 소유를 확인해 주면 공개 계약대로 상태 오류(409)를 낸다
(`Verdict.ended`) — 어느 쪽이든 아무 일도 일어나지 않는 경로다.

## 감사
`audit/authz.jsonl`(hash chain). 위임 내용(작업 지시·보고 요약·후속 지시)은 **내용**이라 길이와 sha8 만 남긴다.
grant 발급·종료·동반 회수는 커밋 전에 쓰고 못 쓰면 중단한다. 판정·동작 줄(`deleg.spawn/send/close/report`)은 데이터 경로라
쓰기 실패가 동작을 막지 않는다. 거부는 항상 남긴다.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Optional

from . import audit, engine, migrate, store
from . import principals as P
from . import scopes as S

logger = logging.getLogger(__name__)

SIG_KEY = "deleg_ledger_sig"
FS_SCOPES = ("fs.read", "fs.write")
FS_REQUEST = {"scopes": list(FS_SCOPES), "sconds": {"fs.write": {"ops": list(S.FS_WRITE_OPS)}}}   # 자식 fs 몫으로 요청하는 것(전부)
MAX_INHERIT_REFS = 8

_held = threading.local()


def held() -> bool:
    """이 스레드가 authz 락 → 장부 락을 쥐고 있는가(= `locked()` 안인가)."""
    return getattr(_held, "n", 0) > 0


def _err(code: str, reason: str, status: int):
    import delegation
    return delegation.DelegationError(code, reason, status)


def _unavailable(e: BaseException):
    logger.warning(f"위임 권한 엔진을 쓸 수 없다 — 거부: {type(e).__name__}: {e}")
    return _err("authz_unavailable", "권한 엔진을 쓸 수 없어 요청을 거부했습니다", 503)


def is_deleg(rec: dict) -> bool:
    return str(rec.get("origin", "")).startswith(migrate.DELEG_ORIGIN)


# --- 락 · 채택 ------------------------------------------------------------------------------


def _sig(raw: Optional[bytes]) -> str:
    return hashlib.sha256(raw).hexdigest() if raw is not None else "absent"


def sig_of(blob: bytes) -> str:
    return _sig(blob)


@contextmanager
def locked(ledger_lock):
    """authz 락 → 장부 락. 들어올 때 장부가 마지막 동기화와 다르면 채택한다."""
    import store_lock
    with ExitStack() as stack:
        try:
            stack.enter_context(store.locked())
        except (TimeoutError, OSError) as e:
            raise _unavailable(e) from e
        stack.enter_context(store_lock.exclusive(ledger_lock))
        _held.n = getattr(_held, "n", 0) + 1
        try:
            adopt_if_stale()
            yield
        finally:
            _held.n -= 1


def adopt_if_stale() -> None:
    import delegation
    try:
        raw = delegation._path().read_bytes()
    except OSError:
        raw = None
    sig = _sig(raw)
    try:
        meta, _bad = store._read("meta")
        if meta["items"].get(SIG_KEY) == sig:
            return
        items = delegation._read()["items"]
    except delegation.DelegationError:
        return                      # 장부가 손상 — 이 락 안의 다음 `_read()` 가 정식 오류를 낸다. 채택하지 않는다.
    try:
        sync(items, sig=sig, adopt=True)
    except delegation.DelegationError as e:
        # 읽기까지 막지 않는다 — 판정(`decide`)과 장부 쓰기(`sync`)가 같은 이유로 닫힌다.
        logger.warning(f"위임 장부 채택 실패({e.code}) — 다음 쓰기·판정 때 다시 시도: {e.reason}")


# --- 파생 · 동기화 ----------------------------------------------------------------------------


def _expired(g: dict, now: float) -> bool:
    ex = g.get("expires")
    return ex is not None and now >= float(ex)


def cascade_revoke(grants: list, parent_grant_id: str, reason: str, now: float) -> list[str]:
    """부모 fs grant `parent_grant_id` 를 공유 참조하는 상속 레코드를 회수(`revoked`)한다. 회수한 id 들.
    5단계 saga 와 `sync` 가 같은 함수를 쓴다."""
    out = []
    for g in grants:
        if is_deleg(g) and g.get("inherit_from") == parent_grant_id and g.get("state") == "active":
            g.update(state="revoked", revoked_at=now, revoked_reason=reason)
            out.append(g["id"])
    return out


def _apply(docs: dict, items: list, now: float) -> dict:
    derived: list[dict] = []
    for it in items:
        derived += migrate.translate_delegation(it)
    cur = docs["grants"]["items"]
    mine = [g for g in cur if is_deleg(g)]
    others = [g for g in cur if not is_deleg(g)]
    merged, stats = migrate._sync(mine, derived)
    merged = [g for g in merged if is_deleg(g)]
    docs["grants"]["items"] = others + merged
    # 부모 fs grant 가 사라졌거나 끝났으면 상속분도 끝낸다(상속분 동반 해제)
    live = {g.get("id"): g for g in others}
    cascaded: list[str] = []
    for g in merged:
        if g.get("inherit_from") and g.get("state") == "active":
            p = live.get(g["inherit_from"])
            if p is None or p.get("state", "active") != "active" or _expired(p, now):
                cascaded += cascade_revoke([g], g["inherit_from"], "parent_grant_ended", now)
    return {"stats": stats, "old": mine, "new": merged, "cascaded": cascaded}


def _audit_strict(rec: dict) -> None:
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        raise _err("audit_unavailable", "감사 기록을 쓰지 못해 변경을 중단했습니다", 503) from e


def _audit_sync(r: dict, adopt: bool) -> None:
    st = r["stats"]
    if adopt:
        if st["added"] or st["updated"] or st["removed"]:
            _audit_strict({"principal": "system:spawn", "action": "deleg.adopt", "resource": "deleg:*",
                           "decision": "allow", "result": "ok", "reason": "ledger_changed_outside",
                           "detail": {"added": st["added"], "updated": st["updated"], "removed": st["removed"]}})
        return
    old = {g["id"]: g for g in r["old"]}
    issued: dict[str, list] = {}
    ended: dict[str, list] = {}
    for g in r["new"]:
        o = old.get(g["id"])
        if g["id"] in r["cascaded"]:
            continue
        if o is None and g["state"] == "active" and g["id"].startswith(("dr-", "dw-", "di-")):
            issued.setdefault(g.get("deleg", "-"), []).append(g["id"])     # 자식이 확정된 뒤(starting 의 부모 grant 는 건너뜀)
        elif o is not None and o.get("state") == "active" and g["state"] != "active":
            ended.setdefault(g.get("deleg", "-"), []).append(g["id"])
    for did, ids in sorted(issued.items()):
        _audit_strict({"principal": "system:spawn", "action": "deleg.grant", "resource": f"deleg:local/{did}",
                       "scopes": ["deleg.control"], "decision": "allow", "result": "issued",
                       "reason": "approved_by system:spawn", "detail": {"grants": [f"dc-{did}", *ids]}})
    for did, ids in sorted(ended.items()):
        _audit_strict({"principal": "system:spawn", "action": "deleg.end", "resource": f"deleg:local/{did}",
                       "decision": "allow", "result": "ended", "detail": {"grants": ids}})
    for gid in r["cascaded"]:
        g = next(x for x in r["new"] if x["id"] == gid)
        _audit_strict({"principal": "system:spawn", "action": "deleg.cascade",
                       "resource": f"deleg:local/{g.get('deleg', '-')}", "decision": "allow", "result": "revoked",
                       "grant_id": gid, "reason": "parent_grant_ended", "detail": {"inherit_from": g.get("inherit_from")}})


def sync(items: list, *, sig: str, adopt: bool = False, now: Optional[float] = None) -> None:
    """장부 항목 → authz grant 로 동기화하고 `meta.deleg_ledger_sig` 를 갱신한다. `locked()` 안에서만 부른다."""
    now = time.time() if now is None else now
    for n in ("grants", "meta"):                 # 손상된 문서는 덮어쓰지 않고 닫는다(사람이 복구/롤백)
        if store._read(n)[1]:
            raise _unavailable(RuntimeError(f"authz {n}.json 이 손상됨"))
    try:
        with store.txn(held=True) as docs:
            r = _apply(docs, items, now)
            _audit_sync(r, adopt)
            docs["meta"]["items"][SIG_KEY] = sig
    except Exception as e:  # noqa: BLE001
        import delegation
        if isinstance(e, delegation.DelegationError):
            raise
        raise _unavailable(e) from e


# --- 판정 -------------------------------------------------------------------------------------


@dataclass
class Verdict:
    allow: bool
    reason: str = "ok"
    ended: bool = False          # 거부지만 이 주체가 이 위임의 (이미 끝난) grant 를 가졌었다 — 상태 오류(409)로 돌려야 한다
    grant_id: Optional[str] = None


def _snapshot(now: float) -> store.Snapshot:
    """저장소를 **캐시 없이** 읽는다 — 이 스레드가 방금 쓴 grant 가 같은 mtime·크기 충돌로 낡게 보이면 안 된다."""
    docs = {n: store._read(n) for n in ("grants", "approvals", "windows", "shares")}
    return store.Snapshot(grants=docs["grants"][0]["items"], approvals=docs["approvals"][0]["items"],
                          windows=docs["windows"][0]["items"], shares=docs["shares"][0]["items"],
                          corrupt=frozenset(n for n, (_d, bad) in docs.items() if bad), now=now, source="store")


def decide(p: P.Principal, scope: str, resource: str, ctx: Optional[dict] = None) -> engine.Decision:
    """엔진 판정. 예외·손상 저장소 = 닫힘(503)."""
    try:
        d = engine.decide(p, scope, resource, ctx or {}, _snapshot(time.time()))
    except Exception as e:  # noqa: BLE001
        raise _unavailable(e) from e
    if d.reason == "store_corrupt":
        raise _unavailable(RuntimeError("authz 저장소가 손상됨"))
    return d


def spawn_verdict(data: dict, parent_key: str, now: float, *, enabled: bool, safe_mode: bool) -> Verdict:
    """`deleg.spawn` on `deleg-quota:<주체>` — 열린 자식·깊이·분당 횟수(원장 `spawns`)·killswitch(VT_AGENT_SPAWN, safe_mode)."""
    items = data.get("items") or []
    open_states = ("running", "reported")
    ctx = {"deleg": {
        "enabled": enabled,
        "is_child": any((x.get("child") or {}).get("key") == parent_key for x in items),
        "open_children": sum(1 for x in items
                             if (x.get("parent") or {}).get("key") == parent_key and x.get("state") in open_states),
        "recent_spawns": sum(1 for t in data.get("spawns") or [] if now - t < 60)}, "safe_mode": bool(safe_mode)}
    p = P.agent(parent_key)
    d = decide(p, "deleg.spawn", f"deleg-quota:{p.id}", ctx)
    if not d.allow:
        audit_op("spawn", "denied", principal=p.id, scopes=["deleg.spawn"], reason=d.reason,
                 resource=f"deleg-quota:{p.id}")
    return Verdict(d.allow, d.reason, grant_id=d.grant_id)


def _ended_grant(p: P.Principal, role: str, did: str) -> bool:
    docs, bad = store._read("grants")
    if bad:
        return False
    res = f"deleg:local/{did}"
    return any(g.get("state") == "ended" and g.get("subject") == p.short() and g.get("resource") == res
               and role in S.caps_of(g).get("deleg.control", {}).get("role", ()) for g in docs["items"])


def side_principal(item: dict, role: str) -> P.Principal:
    """위임 항목의 `parent`/`child` 쪽 주체. 다른 호스트의 에이전트면(`host`) 그 호스트가 보증하는 대리 주체 `peer:<호스트>/obo:agent:<호스트>/<key>`."""
    side = item.get(role) or {}
    host, key = side.get("host"), str(side.get("key") or "")
    if host and key.startswith(f"{host}/"):
        return P.peer(host, obo=f"agent:{host}/{key[len(host) + 1:]}")
    return P.agent(key)


def authorize(caller_key: str, did: str, role: str, *, op: str) -> Verdict:
    """`deleg.control` on `deleg:local/<id>` — `role=parent`(보내기·닫기) 또는 `role=child`(보고). 거부는 감사한다."""
    return authorize_p(P.agent(caller_key), did, role, op=op)


def authorize_p(p: P.Principal, did: str, role: str, *, op: str) -> Verdict:
    """`authorize` 의 주체 일반형 — 다른 호스트의 에이전트(대리 주체)도 같은 판정(`deleg.control`)을 받는다."""
    d = decide(p, "deleg.control", f"deleg:local/{did}", {"role": role, "op": op})
    v = Verdict(d.allow, d.reason, grant_id=d.grant_id)
    if not d.allow:
        v.ended = _ended_grant(p, role, did)
        audit_op(op, "denied", principal=p.id, did=did, scopes=["deleg.control"], reason="ended" if v.ended else d.reason)
    return v


def authorize_actor(actor: P.Principal, did: str, *, op: str) -> Verdict:
    """사람(human)·cli 가 위임을 닫는 경로(`deleg.control`, role=parent, op=close). cli 는 기본 규칙의 `ops:[close]` 만 통과한다."""
    d = decide(actor, "deleg.control", f"deleg:local/{did}", {"role": "parent", "op": op})
    if not d.allow:
        audit_op(op, "denied", principal=actor.id, did=did, scopes=["deleg.control"], reason=d.reason)
    return Verdict(d.allow, d.reason, grant_id=d.grant_id)


def deny_unknown(caller_key: str, did: str, op: str, reason: str = "no_such_delegation") -> None:
    audit_op(op, "denied", principal=P.agent(caller_key).id, did=did, scopes=["deleg.control"], reason=reason)


def lineage_of(child_key: str) -> Optional[str]:
    """이 세션이 **자식으로서** 맡고 있는 열린 위임의 id(실행 중 우선). 자식이 아니거나 장부를 읽지 못하면 None.
    자식의 승인 요청(`authz.request`)에 부모 위임 id 를 남겨 받은편지함이 계보를 보여 줄 수 있게 한다(필드만 — 판정에 쓰지 않는다).
    authz 락을 쥔 채로 부르면 안 된다(장부를 읽는다)."""
    import delegation
    try:
        mine = [x for x in delegation.list_all() if (x.get("child") or {}).get("key") == child_key
                and x.get("state") in delegation.OPEN_STATES]
    except Exception as e:  # noqa: BLE001 — 계보 표시 때문에 요청이 실패하면 안 된다
        logger.warning(f"위임 계보를 읽지 못함({type(e).__name__}) — 요청에 남기지 않는다")
        return None
    mine.sort(key=lambda x: x.get("state") != delegation.RUNNING)
    return str(mine[0]["id"]) if mine else None


# --- 입력 직전 재평가 -----------------------------------------------------------------


def recheck_queued(src: dict, item_id: str = "") -> Optional[str]:
    """큐에 쌓인 위임 항목(`source = {delegation, kind}`)을 **입력 직전에** 다시 판정한다. 통과면 None, 막히면 안정 사유 코드:
    `relationship_gone`(장부에 그 위임이 없다) · `delegation_closed`(닫히거나 고아가 됐다) · `grant_revoked`(보내는 쪽의 grant 가 없다).

    보내는 쪽은 종류가 정한다: 작업·후속 지시 = 부모(`deleg.control`, role=parent, op=send), 결과 보고 = 자식(role=child, op=report).
    턴 종료·승인 대기·실행 시간·소멸 알림은 서버가 장부에서 만든 것이라 관계(위임이 있고 열려 있다. 소멸 알림은 닫힌 뒤에 오는 게 정상이다)만 본다.
    거부는 감사한다(내용 없음). 읽지 못하거나 엔진이 못 쓰면 예외를 올린다 — 호출자가 닫는다."""
    import delegation
    did = str(src.get("delegation") or "")
    kind = str(src.get("kind") or "")
    item = next((x for x in delegation.list_all() if x.get("id") == did), None)
    reason: Optional[str] = None
    principal: Optional[str] = None
    if item is None:
        reason = "relationship_gone"
    elif kind != delegation.KIND_GONE and item.get("state") not in delegation.OPEN_STATES:
        reason = "delegation_closed"
    else:
        role = {delegation.KIND_TASK: ("parent", "send"), delegation.KIND_FOLLOWUP: ("parent", "send"),
                delegation.KIND_REPORTED: ("child", "report")}.get(kind)
        if role is not None:
            key = str((item.get(role[0]) or {}).get("key") or "")
            p = side_principal(item, role[0])
            principal = p.short()
            if not key or not decide(p, "deleg.control", f"deleg:local/{did}", {"role": role[0], "op": role[1]}).allow:
                reason = "grant_revoked"
    if reason:
        audit_op("queue", "denied", principal=principal or "system:queue", did=did or None, scopes=["deleg.control"],
                 reason=reason, detail={"item": item_id, "kind": kind})
    return reason


# --- 위임 cwd · worktree 기준 = fs.read + fsguard 루트 ---------------------------------------------


def check_fs(caller_key: str, raw: str, purpose: str) -> str:
    """`fs.read` on `fs:local/<경로>` — fsguard 의 루트(VT_BROWSE_ROOTS)·거부 목록 안일 때만. 통과하면 realpath.
    밖이면 403 `cwd_outside_roots`. (`lease` 강제가 켜지면 남의 lease 폴더도 막는다 — `delegation._lease_guard`.)"""
    import fsguard
    from . import hooks
    try:
        real, ok, why = str(fsguard.resolve_under_roots(raw)), True, ""
    except fsguard.FsDenied as e:
        real, ok, why = raw, False, e.reason
    p = P.agent(caller_key)
    d = decide(p, "fs.read", "fs:local" + real, {"fs": {"purpose": purpose, "in_roots": ok}})
    hooks.deleg_cwd(d.allow, caller_key, real, purpose, ok)
    if not d.allow:
        audit_op("cwd", "denied", principal=p.id, scopes=["fs.read"], reason=f"{purpose}:{d.reason}",
                 resource="fs:local" + real)
        raise _err("cwd_outside_roots",
                   f"허용된 루트(VT_BROWSE_ROOTS) 밖의 경로라 하위 세션을 만들 수 없습니다: {why or d.reason}", 403)
    return real


def fs_record(parent_key: str, start_dir: str, *, worktree: bool) -> Optional[dict]:
    """자식의 fs grant 기록(상속 규칙)(원장 `item["fs"]`). worktree 로 띄웠으면 그 경로 한 곳을 자식에게만, 아니면 부모가 그 폴더를
    덮는 fs grant 를 가졌을 때만 공유 참조(부모 grant 가 없으면 상속할 것이 없다 → None). 강제는 5단계."""
    real = os.path.realpath(start_dir)
    if worktree:
        return {"mode": "worktree", "path": real} if migrate._fs_path_ok(real) else None
    now = time.time()
    p = P.agent(parent_key)
    res = "fs:local" + real.rstrip("/")
    refs = []
    for g in _snapshot(now).grants:
        if g.get("effect") == "deny" or g.get("state", "active") != "active" or is_deleg(g):
            continue
        if not engine._subject_matches(str(g.get("subject", "")), p) or not engine._time_ok(g, now):
            continue
        pat = str(g.get("resource", ""))
        if S.resource_matches(pat, res) or S.resource_matches(pat, res + "/x"):
            # 자식의 몫 = 부모 grant ∩ 요청(파일 읽기·쓰기) − 상속 불가. 위임이 자식에게 grant 를 파생하는 자리는 이 함수를 거친다.
            cg = S.child_grants([g], FS_REQUEST)
            caps = S.effective(cg["scopes"], cg["sconds"])
            scopes = [x for x in FS_SCOPES if x in caps]
            if not scopes:
                continue
            ref = {"grant": g.get("id"), "resource": pat, "scopes": scopes, "schema": S.SCHEMA, "expires": g.get("expires")}
            if "fs.write" in caps:
                ref["sconds"] = {"fs.write": {"ops": [o for o in S.FS_WRITE_OPS if o in caps["fs.write"]["ops"]]}}
            refs.append(ref)
    return {"mode": "inherit", "from": refs[:MAX_INHERIT_REFS]} if refs else None


# --- 감사 (동작·판정 줄 — 데이터 경로, 실패해도 동작을 막지 않는다) -------------------------------------


def text_sha8(text: str) -> str:
    return audit.sha8(text)


def audit_op(action: str, result: str, *, principal: Optional[str] = None, did: Optional[str] = None,
             scopes=(), reason: Optional[str] = None, chars: Optional[int] = None,
             sha8: Optional[str] = None, resource: Optional[str] = None, detail: Optional[dict] = None) -> None:
    """`deleg.<action>` 한 줄. 내용(지시·요약)은 받지 않는다 — 길이(`chars`)와 sha8 뿐."""
    try:
        rec = {"principal": principal or "agent:unverified", "action": f"deleg.{action}",
               "resource": resource or (f"deleg:local/{did}" if did else "deleg:*"), "scopes": list(scopes),
               "decision": "deny" if result == "denied" else "allow", "result": result, "reason": reason}
        if chars is not None:
            rec["sizes"] = {"chars": int(chars)}
        if sha8:
            rec["sha8"] = sha8
        if detail:
            rec["detail"] = detail
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"authz 위임 감사 기록 실패: {e}")
