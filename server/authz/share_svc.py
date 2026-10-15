"""에이전트 세션 공유의 권한 판정·저장·감사 — `VT_AUTHZ_ENFORCE=share` 일 때 엔진이 진실이다.

`agent_share.py` 가 동작부(`read_pane`·`send_keys`·`check_send_*`·tmux 옵션)를 쥐고, 저장·판정·감사는 여기로 넘어온다.
`agent_share` 의 공개 함수(`get_grant`·`request_access`·`decide`·`revoke`·`drop_target`·`list_state`·`snapshot`·`audit` …)는
플래그를 보고 옛 구현 또는 이 모듈로 분기한다. 서명과 반환 모양은 같다(라우트·MCP·시험이 그대로 돈다).

## 저장과 dual-write
  - 진실: `~/.vt/authz/{grants,approvals,shares,meta}.json` (`store.txn`, 단일 flock `authz.lock`).
  - 옛 저장소 `agent_grants.json` 은 **엔진 상태의 투영**으로 같은 트랜잭션 안에서 다시 쓴다(락 순서 authz → agent_grants;
    `migrate.restore` 와 같다). 투영은 `approvals.legacy_state` — 엔진 레코드가 옛 레코드의 번역이라 왕복해도 같다.
    플래그를 끄면 옛 코드가 그 파일을 그대로 이어받는다(롤백).
  - 옛 파일이 엔진이 마지막으로 쓴 것과 달라졌으면(플래그를 끈 사이 옛 코드가 썼거나 손으로 고침) 트랜잭션 처음에
    **채택**한다: 옛 레코드를 번역해 엔진에 동기화(원본 id 기준, `migrate._sync`). 옛 파일이 손상이면 채택하지 않는다
    (엔진 상태를 지우지 않고 원본도 건드리지 않는다). 옛 파일을 투영해야 하는 변경은 **거부**한다(503 `store_unreadable`,
    복사본만 남긴다) — 엔진만 앞서 쓰면 둘이 갈라지고, 빈 상태로 덮으면 옛 권한이 사라진다.
    `meta.share_legacy_sig` 가 마지막으로 맞춘 옛 파일의 sha256 이다.
  - 엔진 저장이 옛 파일 쓰기보다 늦게 실패하면 옛 파일이 앞선다 → 다음 트랜잭션이 그 상태를 채택해 스스로 맞춘다.

## 판정
`authorize()` 가 `engine.decide` 를 부른다: 주체 `agent:local/<sid>:<created>`, scope `session.read`/`session.input`(mode=line),
리소스 `session:local/<key>`, ctx 에 공유 정책(ceiling)·살아 있는 세션·분당 횟수(limits). 호출자가 대는 헤더·본문은 판정에
들어가지 않는다 — 주체는 `share_identity` 가 확인한 pane 소속뿐이다.

## 감사
변경(결정·철회·공유 지정/해제)은 **커밋 전에** `audit/authz.jsonl` 에 쓰고, 못 쓰면 503 `audit_unavailable` 로 중단한다.
데이터 경로(읽기·쓰기·목록)는 기록이 실패해도 계속한다. 성공한 읽기는 (주체, 리소스)당 10분에 한 줄 + 건너뛴 횟수.
내용은 남기지 않는다(`audit._scrub`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

from i18n import tr

from . import approvals, audit, engine, limits, shares, store
from . import principals as P
from . import scopes as S
from .errors import ShareError

logger = logging.getLogger(__name__)

SIG_KEY = "share_legacy_sig"
READ_SAMPLE_SEC = 600.0


def _ag():
    import agent_share
    return agent_share


# --- 옛 저장소 투영·채택 -------------------------------------------------------------------


def _legacy_bytes() -> Optional[bytes]:
    try:
        return _ag()._path().read_bytes()
    except OSError:
        return None


def _sig(raw: Optional[bytes]) -> str:
    return hashlib.sha256(raw).hexdigest() if raw is not None else "absent"


def _adopt(docs: dict, data: dict, now: float) -> None:
    """옛 파일 내용을 엔진에 동기화(원본 id 기준). 엔진 쪽 legacy agent 레코드만 건드린다."""
    from . import migrate
    g_new = [r for r in (approvals.engine_grant(g, approved_by="migrated:live") for g in data["grants"]) if r]
    a_new = [r for r in (approvals.engine_request(x, now) for x in data["requests"]) if r]
    items = docs["grants"]["items"]
    mine = [g for g in items if approvals.is_grant(g)]
    merged, _ = migrate._sync(mine, g_new)
    docs["grants"]["items"] = [g for g in items if not approvals.is_grant(g)] + merged
    items = docs["approvals"]["items"]
    mine = [a for a in items if approvals.is_request(a)]
    merged, _ = migrate._sync(mine, a_new)
    docs["approvals"]["items"] = [a for a in items if not approvals.is_request(a)] + merged


def _write_legacy(docs: dict) -> None:
    ag = _ag()
    state = approvals.legacy_state(docs)
    with ag._locked():
        _, corrupt = ag._load()
        if corrupt and ag._path().exists():
            ag.refuse_corrupt_write()            # 읽지 못한 옛 파일은 덮지 않는다 — ShareError(503), 엔진 저장도 중단
        ag._save(state)
    docs["meta"]["items"][SIG_KEY] = _sig(_legacy_bytes())


def _fp(docs: dict) -> str:
    return json.dumps([approvals.agent_grants(docs), approvals.agent_requests(docs)], sort_keys=True, default=str)


@contextmanager
def _txn(snap):
    """락 안에서 (옛 파일 채택 → 정리 → 호출자 변경 → 옛 파일 투영) 후 엔진 저장. `with txn(snap) as docs:`.
    예외로 나가면 아무것도 저장하지 않는다."""
    ag = _ag()
    now = time.time()
    with store.txn() as docs:
        docs["_corrupt"] = frozenset(n for n in ("grants", "approvals", "windows", "shares", "meta")
                                     if store._read(n)[1])
        before = {n: json.dumps(docs[n], sort_keys=True) for n in docs["_corrupt"]}
        meta = docs["meta"]["items"]
        sig = _sig(_legacy_bytes())
        if meta.get(SIG_KEY) != sig:
            data, corrupt = ag._load()
            if not corrupt:
                _adopt(docs, data, now)
                meta[SIG_KEY] = sig
        base = _fp(docs)
        approvals.purge(docs, None if snap is None else set(snap), now, ag.DENY_COOLDOWN)
        yield docs
        # 읽지 못한 엔진 문서를 바꾸는 변경은 옛 파일에 투영하기 **전에** 거부한다(옛 파일만 앞서가면 안 된다)
        bad = [n for n in docs["_corrupt"] if json.dumps(docs[n], sort_keys=True) != before[n]]
        if bad:
            for n in bad:
                store.preserve_copy(n)
            raise store.unreadable_error(bad)
        if _fp(docs) != base:
            _write_legacy(docs)


def _snapshot(docs: dict, now: float) -> store.Snapshot:
    return store.Snapshot(grants=docs["grants"]["items"], approvals=docs["approvals"]["items"],
                          windows=docs["windows"]["items"], shares=docs["shares"]["items"],
                          corrupt=docs.get("_corrupt", frozenset()), now=now, source="store")


def agent_key(a) -> str:
    return f"{a.sid}:{a.created}"


# --- 공유 정책 (shares.json) ----------------------------------------------------------------


def resolve_shares(tmux_shares: dict) -> dict:
    """`{세션 key: tmux 에서 읽은 {nick, mode} 또는 None}` → 유효 공유(낮은 쪽). 처음이면 tmux 값을 채택(snapshot).
    저장소를 못 읽으면 어느 세션도 공유로 보지 않는다(fail closed)."""
    try:
        items, adopted, bad = shares.load()
        if bad:
            return {k: None for k in tmux_shares}
        if not adopted and tmux_shares:
            with store.txn() as docs:
                meta = docs["meta"]["items"]
                if not meta.get(shares.ADOPTED_KEY):
                    for k, sh in tmux_shares.items():
                        if sh and k not in docs["shares"]["items"]:
                            docs["shares"]["items"][k] = shares.entry(k, sh["nick"], sh["mode"])
                    meta[shares.ADOPTED_KEY] = True
            items, adopted, bad = shares.load()
        return {k: shares.resolve(sh, items.get(k), adopted=adopted) for k, sh in tmux_shares.items()}
    except Exception as e:  # noqa: BLE001 — 저장소 오류에서 공유가 열려 있으면 안 된다
        logger.warning(f"공유 정책 저장소 읽기 실패({e}) — 이번 조회는 공유 없음으로 본다")
        return {k: None for k in tmux_shares}


def put_share(target, nick: str, mode: str, snap, actor: Optional[P.Principal]) -> None:
    """공유 지정·변경을 정책 저장소에 쓴다(tmux 미러보다 먼저 — 실패해도 낮은 쪽으로 안전). 감사 실패 시 503."""
    with store.txn() as docs:
        d = docs["shares"]["items"]
        if snap is not None:
            for k in [k for k in d if k not in snap]:
                del d[k]
        d[target.key] = shares.entry(target.key, nick, mode)
        _audit_change("share", "ok", actor, f"session:local/{target.key}", "allow", scopes=["session.view"],
                      legacy={"action": "share", "result": "ok", "by": "user", "nick": nick, "mode": mode})


def drop_share_entry(docs: dict, key: str) -> None:
    docs["shares"]["items"].pop(key, None)


# --- 감사 --------------------------------------------------------------------------------------


def _audit_change(action: str, result: str, actor: Optional[P.Principal], resource: str, decision: str, *,
                  scopes=(), grant_id=None, approval_id=None, reason: Optional[str] = None,
                  legacy: Optional[dict] = None) -> None:
    """authz 변경 감사 — 커밋 전에 쓰고 실패하면 fail closed."""
    try:
        audit.append({"principal": (actor or P.human("-")).id, "action": f"share.{action}", "resource": resource,
                      "scopes": list(scopes), "decision": decision, "grant_id": grant_id, "approval_id": approval_id,
                      "result": result, "reason": reason, "detail": legacy})
    except Exception as e:  # noqa: BLE001
        raise ShareError("audit_unavailable", tr("감사 기록을 쓰지 못해 변경을 중단했습니다"), 503) from e


def mirror_audit(action: str, result: str, rec: dict, agent, nick: Optional[str]) -> None:
    """`agent_share.audit()` 한 줄을 authz 감사에 옮긴다(데이터 경로 — 실패해도 호출을 막지 않는다)."""
    ag = _ag()
    try:
        if agent is not None:
            principal = f"agent:local/{agent.sid}:{agent.created}"
        elif rec.get("by") == "agent":
            principal = "agent:unverified"
        else:
            principal = "human:-"
        resource = f"session:local/nick:{nick}" if nick else "authz:*"
        sizes = {k: rec[k] for k in ("lines", "chars", "truncated") if k in rec} or None
        suppressed = None
        if action in ("read", "list") and result == "ok":      # 읽기는 10분에 한 줄 + 건너뛴 횟수
            k = (principal, resource, action)
            now = time.time()
            with ag._rate_lock:
                last, n = ag._read_seen.get(k, (0.0, 0))
                if now - last < READ_SAMPLE_SEC:
                    ag._read_seen[k] = (last, n + 1)
                    return
                ag._read_seen[k] = (now, 0)
                suppressed = n or None
        decision = "allow" if result in ("ok", "allowed") else ("pending" if result.startswith("pending") else "deny")
        detail = {k: v for k, v in rec.items() if k != "ts"}
        if suppressed:
            detail["suppressedSince"] = suppressed
        audit.append({"principal": principal, "action": f"share.{action}", "resource": resource,
                      "scopes": ["session.input" if action == "send" else "session.read"]
                      if action in ("read", "send") else [], "decision": decision, "grant_id": rec.get("grant"),
                      "approval_id": rec.get("request"), "result": result, "reason": rec.get("reason"),
                      "sizes": sizes, "sha8": rec.get("sha8"), "detail": detail})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"authz 감사 기록 실패: {e}")


def read_audit(limit: int) -> list[dict]:
    """`GET /api/agent/share/audit` — authz 감사에서 공유 줄만, 옛 모양으로(최근 순)."""
    limit = max(1, min(int(limit), 500))
    out = []
    for r in audit.read_recent(limit, "share."):
        d = r.get("detail")
        if isinstance(d, dict):
            out.append({"ts": r.get("ts"), **d})
    return out


# --- 판정·상태 ---------------------------------------------------------------------------------


@dataclass
class Access:
    allowed: bool                 # 상한·grant·저장소 상태를 모두 통과했는가(속도 제한 제외 — 그건 rate_limited)
    rate_limited: bool = False
    limit: int = 0
    grant: Optional[dict] = None  # 옛 모양 grant (라우트·shadow 가 쓴다)
    grant_id: Optional[str] = None
    reason: str = ""
    decision: dict = field(default_factory=dict)


def authorize(kind: str, snap, agent, target, now: Optional[float] = None) -> Access:
    """`kind`=`read`|`write` 의 접근 판정. 엔진이 정한다. 허용이면 분당 횟수에 한 번 더한다."""
    ag = _ag()
    now = time.time() if now is None else now
    scope = "session.read" if kind == "read" else "session.input"
    rkind = "read" if kind == "read" else "send"
    limit = ag.READ_PER_MIN if kind == "read" else ag.SEND_PER_MIN
    p = P.from_caller(agent)
    with _txn(snap) as docs:
        g = approvals.find_grant(docs, agent_key(agent), target.key)
        key = f"{rkind}|{agent_key(agent)}|{target.key}"
        with ag._rate_lock:
            hits = limits.recent(ag._rate, key, now)
            ctx = {"share": target.share, "share_known": True, "prefer_live": False,
                   "alive": set(snap) if snap is not None else None,
                   "counts": {scope: len(hits)}, "limits": {scope: limit}}
            if kind != "read":
                ctx["mode"] = "line"             # 공유 세션에 보내는 것은 정제된 한 줄 입력뿐

            d = engine.decide(p, scope, f"session:local/{target.key}", ctx, _snapshot(docs, now))
            if d.allow:
                limits.record(ag._rate, key, hits, now)
            else:
                ag._rate[key] = hits
        limited = (not d.allow) and d.reason == "rate_limited"
        return Access(allowed=d.allow or limited, rate_limited=limited, limit=limit,
                      grant=approvals.grant_to_legacy(g) if g else None, grant_id=g["id"] if g else None,
                      reason=d.reason, decision=d.as_dict())


def get_grant(snap, agent, target) -> Optional[dict]:
    with _txn(snap) as docs:
        g = approvals.find_grant(docs, agent_key(agent), target.key)
        return approvals.grant_to_legacy(g) if g else None


def list_state(snap) -> dict:
    with _txn(snap) as docs:
        return approvals.legacy_state(docs)


def request_access(snap, agent, target, kind: str) -> tuple[dict, bool]:
    ag = _ag()
    now = time.time()
    with _txn(snap) as docs:
        d = engine.decide(P.from_caller(agent), "authz.request", "authz:*", {}, _snapshot(docs, now))
        if not d.allow:                          # authz.request 가 없으면 보류 요청도 없다(403)
            raise ShareError("request_not_allowed", tr("승인 요청을 만들 수 없는 호출자입니다"), 403)
        return approvals.request(
            docs, {"sid": agent.sid, "created": agent.created, "name": agent.name},
            {"sid": target.sid, "created": target.created}, kind, now, ttl=ag.REQUEST_TTL,
            per_agent=ag.MAX_PENDING_PER_AGENT, total=ag.MAX_REQUESTS, cooldown=ag.DENY_COOLDOWN)


def decide(request_id: str, decision: str, level: Optional[str], snap, actor: Optional[P.Principal] = None) -> dict:
    now = time.time()
    with _txn(snap) as docs:
        req = next((r for r in approvals.agent_requests(docs)
                    if approvals.raw_id(r) == (request_id[3:] if request_id.startswith("ap-") else request_id)
                    and r.get("state") == "pending"), None)
        mode = None
        tgt = None
        if req is not None and snap is not None:
            tgt = snap.get(req["resource_session"])
            if tgt is not None:
                mode = (tgt.share or {}).get("mode") or ""
        res = approvals.decide(docs, request_id, decision, level, actor=actor, share_mode=mode, now=now)
        allowed = res["grant"] is not None
        view_agent = (snap or {}).get(req["subject_session"]) if req else None
        who = view_agent.name if view_agent else (req or {}).get("subject_name", "")
        _audit_change("decide", "allowed" if allowed else "denied", actor, res["resource"],
                      "allow" if allowed else "deny", scopes=res.get("scopes") or (), grant_id=res["grantId"],
                      approval_id=res["approvalId"], reason=f"agent={who}",
                      legacy={"action": "decide", "result": "allowed" if allowed else "denied", "by": "user",
                              "nick": ((tgt.share or {}).get("nick") if tgt else None), "kind": res["kind"],
                              "level": (("readwrite" if res["grant"]["write"] else "read") if allowed else None),
                              "request": res["request"]["id"], "grant": res["grant"]["id"] if allowed else None,
                              "reason": f"agent={who}", "escalation": res["escalation"] or None})
        return res


def revoke(grant_id: str, scope: str, snap, actor: Optional[P.Principal] = None) -> dict:
    now = time.time()
    with _txn(snap) as docs:
        legacy, meta = approvals.revoke(docs, grant_id, scope, now)
        tk = meta["resource"][len("session:local/"):]
        t = (snap or {}).get(tk)
        a = (snap or {}).get(f"{legacy['agent']['sid']}:{legacy['agent']['created']}")
        who = a.name if a else legacy["agent"].get("name", "")
        _audit_change("revoke", "ok", actor, meta["resource"], "allow", scopes=(),
                      grant_id=meta["id"], reason=f"agent={who}",
                      legacy={"action": "revoke", "result": "ok", "by": "user",
                              "nick": ((t.share or {}).get("nick") if t else None), "grant": legacy["id"],
                              "level": "write" if scope == "write" else "all", "reason": f"agent={who}"})
        return legacy


def drop_target(target, snap) -> tuple[int, list[str]]:
    with _txn(snap) as docs:
        return approvals.drop_target(docs, target.key)


def unshare(target, snap, actor: Optional[P.Principal]) -> tuple[int, list[str]]:
    """공유 해제 — 정책 항목과 그 세션의 권한·요청을 한 트랜잭션에서 지운다. 감사 실패 시 503(아무것도 지우지 않음)."""
    with _txn(snap) as docs:
        n, gone = approvals.drop_target(docs, target.key)
        drop_share_entry(docs, target.key)
        _audit_change("unshare", "ok", actor, f"session:local/{target.key}", "allow",
                      legacy={"action": "unshare", "result": "ok", "by": "user",
                              "nick": (target.share or {}).get("nick")})
        return n, gone
