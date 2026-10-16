"""회수 saga — 권한을 거둘 때 그것에서 흘러나온 모든 것(스트림·큐 항목·메시지·위임·lease)을 빠짐없이 정리한다.

문서 `~/.vt/authz/sagas.json`. 한 saga 는 단계 목록을 **순서대로** 돌고, 끝낸 단계를 그때그때 저장한다. 모든 단계는 **멱등**이라
서버가 어느 지점에서 죽어도 같은 saga 를 다시 돌리면 이어서 끝난다(lifespan sweep 이 시작할 때와 30초마다 이어한다).

    mark_revoking     grant(들)를 `revoking` 으로 — 엔진은 active 만 쓰므로 이 순간부터 거부된다
    close_streams     회수 버스 발행 — 구독자(열린 WS·스트림을 닫는 쪽)가 알아챈다
    drop_queued       그 출처의 큐 항목 제거(`queue_store.remove_pending_by_source`)
    cancel_messages   미전달 에이전트 메시지 취소(`failed`, 사유 `revoked`)
    close_delegations 관련 위임 닫기(`reason=revoked`)
    release_leases    folder lease 해제
    verify            모든 저장소를 다시 읽어 남은 것이 없음을 확인 — 남았으면 saga 는 `running` 으로 남고 sweep 이 다시 돈다
    mark_revoked      grant 를 `revoked` 로, saga 를 `done` 으로

다음 단계(pair·브라우저·peer)는 `register(name, fn, before="verify")` 로 단계를 끼워 넣는다.

대상(`target`)은 다음 키의 조합이다: `grant_ids`(grant 레코드 id 들), `subject`(주체 id), `key`(에이전트 세션 key),
`names`(그 세션의 tmux 이름들 — 보낸 사람 일치용), `did`(위임 id), `peer`(peer id), `all`(사람이 아닌 모든 주체: 비상 정지).

`VT_AUTHZ_ENFORCE` 에 `lease` 가 있을 때만 기존 회수 경로(grant 회수·비상 정지·호스트 deny-*)가 이 saga 를 부른다.
시험은 `FAULT = {"step": 이름, "when": "before"|"after"}` 로 단계 앞/뒤에서 프로세스가 죽은 것을 흉내 낸다(`InjectedCrash`).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Callable, Optional

from i18n import tr

from . import audit, store
from . import principals as P

logger = logging.getLogger(__name__)

KEEP_DONE = 50
MAX_ATTEMPTS = 20
SWEEP_INTERVAL = 30.0
RUNNING, DONE, STUCK = "running", "done", "stuck"


class SagaIncomplete(Exception):
    """verify 가 남은 것을 찾았다 — saga 는 running 으로 남아 다시 돈다."""


class InjectedCrash(BaseException):
    """시험용 — 프로세스가 죽은 것처럼 saga 실행기를 빠져나간다(일반 `except Exception` 이 삼키지 못하게 BaseException)."""


FAULT: dict = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
SUBSCRIBERS: list[Callable[[dict], None]] = []


def subscribe(fn: Callable[[dict], None]) -> None:
    """회수 버스 구독 — 열린 소켓·스트림을 가진 쪽이 대상(target)을 받아 자기 연결을 닫는다."""
    if fn not in SUBSCRIBERS:
        SUBSCRIBERS.append(fn)


def publish(target: dict) -> int:
    n = 0
    for fn in list(SUBSCRIBERS):
        try:
            fn(dict(target))
            n += 1
        except Exception as e:  # noqa: BLE001 — 한 구독자의 실패가 회수를 막으면 안 된다
            logger.warning(f"회수 버스 구독자 실패: {type(e).__name__}: {e}")
    return n


# --- 대상 해석 -------------------------------------------------------------------------------


def _agent_subject(key: str) -> str:
    return f"agent:local/{key}"


def _is_human(subject: str) -> bool:
    return str(subject or "").startswith("human:")


def _grant_match(g: dict, t: dict) -> bool:
    if g.get("effect") == "deny":
        return False
    from . import migrate
    if str(g.get("origin", "")).startswith(migrate.DELEG_ORIGIN):
        return False                      # 위임 장부에서 파생되는 grant 는 위임 닫기가 정리한다
    if g.get("id") in set(t.get("grant_ids") or ()):
        return True
    since = t.get("_since")
    born = g.get("created") or g.get("approved_at") or g.get("issued") or 0
    if since and isinstance(born, (int, float)) and born > since:
        return False                      # saga 가 시작된 뒤에 새로 준 grant 는 이 회수와 상관없다
    subj = str(g.get("subject", ""))
    if t.get("key") and subj == _agent_subject(t["key"]):
        return True
    if t.get("subject") and subj == t["subject"]:
        return True
    return bool(t.get("all")) and subj.startswith("agent:")        # 비상 정지: 에이전트 grant 만(호스트 view 등급은 기존 경로가 정한 대로)


def _delegations(t: dict) -> list:
    """대상과 관련된 열린 위임 id 들(원장을 읽는다)."""
    import delegation
    try:
        items = delegation.list_all()
    except Exception as e:  # noqa: BLE001
        raise SagaIncomplete(f"위임 장부를 읽지 못함: {type(e).__name__}") from e
    open_states = tuple(delegation.OPEN_STATES) + ("starting",)
    out = []
    for x in items:
        if x.get("state") not in open_states:
            continue
        pk, ck = (x.get("parent") or {}).get("key"), (x.get("child") or {}).get("key")
        if t.get("all") or x.get("id") == t.get("did") or (t.get("key") and t["key"] in (pk, ck)):
            out.append(x["id"])
    return out


def _messages(t: dict) -> list:
    """취소 대상 — 아직 전달되지 않은(queued) 들어온 메시지."""
    import agent_msg
    names = set(t.get("names") or ())
    out = []
    for m in agent_msg.list_messages(limit=agent_msg.MAX_KEEP):
        if m.get("dir") != "in" or m.get("status") != agent_msg.STATUS_QUEUED:
            continue
        frm = m.get("from") or {}
        host = frm.get("host")
        if (t.get("peer") and host == t["peer"]) or (host == "local" and frm.get("session") in names) \
                or (t.get("all") and host == "local"):
            out.append(m["id"])
    return out


def _src_match(src: dict, t: dict, mids: set, dids: set) -> bool:
    if src.get("agentMsg") in mids:
        return True
    if src.get("grant_id") and src["grant_id"] in set(t.get("grant_ids") or ()):
        return True
    return bool(src.get("delegation") and src["delegation"] in dids)


# --- 단계 -------------------------------------------------------------------------------------


def _mark_revoking(t: dict) -> None:
    if not (t.get("grant_ids") or t.get("key") or t.get("subject") or t.get("all")):
        return
    now = time.time()
    with store.txn() as docs:
        for g in docs["grants"]["items"]:
            if g.get("state", "active") == "active" and _grant_match(g, t):
                g.update(state="revoking", revoking_at=now)


def _close_streams(t: dict) -> None:
    publish(t)


def _drop_queued(t: dict) -> None:
    import queue_store
    mids = set(_messages(t))
    dids = set(_delegations(t)) | ({t["did"]} if t.get("did") else set())
    if not (mids or dids or t.get("grant_ids")):
        return
    queue_store.remove_pending_by_source(lambda s: _src_match(s, t, mids, dids))


def _cancel_messages(t: dict) -> None:
    import agent_msg
    for mid in _messages(t):
        agent_msg.set_status(mid, agent_msg.STATUS_FAILED, "revoked")


def _close_delegations(t: dict) -> None:
    import delegation
    for did in _delegations(t):
        try:
            delegation.close(did, reason="revoked", system=True)
        except delegation.DelegationError as e:
            if e.status != 404:
                raise


def _lease_match(t: dict) -> Callable[[dict], bool]:
    gids = set(t.get("grant_ids") or ())

    def match(l: dict) -> bool:
        return bool((gids and l.get("grant_id") in gids)
                    or (t.get("key") and l.get("holder_key") == t["key"])
                    or (t.get("subject") and l.get("holder") == t["subject"])
                    or (t.get("did") and l.get("deleg") == t["did"])
                    or (t.get("all") and not _is_human(l.get("holder", ""))))
    return match


def _release_leases(t: dict) -> None:
    from . import leases
    leases.release_where(_lease_match(t), who="system:saga", reason="revoked")


def _verify(t: dict) -> None:
    import queue_store
    from . import leases
    left: dict = {}
    d, bad = store._read("grants")
    if bad:
        raise SagaIncomplete("grants 문서를 읽지 못함")
    n = sum(1 for g in d["items"] if g.get("state", "active") == "active" and _grant_match(g, t))
    if n:
        left["grants_active"] = n
    items, lbad = leases.read_items()
    if lbad:
        raise SagaIncomplete("leases 문서를 읽지 못함")
    m = _lease_match(t)
    n = sum(1 for l in items if m(l))
    if n:
        left["leases"] = n
    mids, dids = set(_messages(t)), set(_delegations(t))
    if mids:
        left["messages"] = len(mids)
    if dids:
        left["delegations"] = len(dids)
    pend = [s for s in queue_store.sources() if _src_match(s, t, mids, dids | ({t["did"]} if t.get("did") else set()))]
    if pend:
        left["queued"] = len(pend)
    if left:
        raise SagaIncomplete(", ".join(f"{k}={v}" for k, v in sorted(left.items())))


def _mark_revoked(t: dict) -> None:
    now = time.time()
    with store.txn() as docs:
        for g in docs["grants"]["items"]:
            if g.get("state") == "revoking" and _grant_match({**g, "state": "active"}, t):
                g.update(state="revoked", revoked_at=now)


_STEPS: list[tuple[str, Callable[[dict], None]]] = [
    ("mark_revoking", _mark_revoking),
    ("close_streams", _close_streams),
    ("drop_queued", _drop_queued),
    ("cancel_messages", _cancel_messages),
    ("close_delegations", _close_delegations),
    ("release_leases", _release_leases),
    ("verify", _verify),
    ("mark_revoked", _mark_revoked),
]


def step_names() -> list[str]:
    return [n for n, _ in _STEPS]


def register(name: str, fn: Callable[[dict], None], *, before: str = "verify") -> None:
    """다음 단계들(pair·브라우저·peer)이 자기 정리 단계를 끼워 넣는다. `before` 단계 앞에 들어간다. 같은 이름은 바꿔 끼운다."""
    global _STEPS
    _STEPS = [(n, f) for n, f in _STEPS if n != name]
    idx = next((i for i, (n, _) in enumerate(_STEPS) if n == before), len(_STEPS))
    _STEPS.insert(idx, (name, fn))


def unregister(name: str) -> None:
    global _STEPS
    _STEPS = [(n, f) for n, f in _STEPS if n != name]


# --- 실행 -------------------------------------------------------------------------------------


def _lock(sid: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(sid, threading.Lock())


def _fault(name: str, when: str) -> None:
    if FAULT and FAULT.get("step") == name and FAULT.get("when", "before") == when:
        if FAULT.get("once", True):
            FAULT.clear()
        raise InjectedCrash(f"{name}:{when}")


def _audit(action: str, rec: dict, result: str, reason: str = "") -> None:
    try:
        audit.append({"principal": rec.get("actor") or "system:saga", "action": f"authz.{action}", "resource": f"saga:{rec['id']}",
                      "decision": "allow", "result": result, "reason": reason,
                      "detail": {"kind": rec.get("kind"), "steps": len(rec.get("steps") or {})}})
    except Exception as e:  # noqa: BLE001 — 회수는 감사가 안 써진다고 멈추지 않는다
        logger.warning(f"saga 감사 기록 실패: {e}")


def _norm(target: dict) -> dict:
    t = {k: v for k, v in (target or {}).items() if v not in (None, "", [], False)}
    if "grant_ids" in t:
        t["grant_ids"] = sorted({str(x) for x in t["grant_ids"] if x})
    return t


def start(kind: str, target: dict, *, actor: Optional[P.Principal] = None, run_now: bool = True) -> dict:
    """saga 를 만들고(같은 종류·대상의 진행 중인 것이 있으면 그것을 이어 쓴다) 돌린다 → 레코드."""
    t = _norm(target)
    now = time.time()
    with store.txn() as docs:
        items = docs["sagas"]["items"]
        rec = next((s for s in items if s.get("state") == RUNNING and s.get("kind") == kind and s.get("target") == t), None)
        if rec is None:
            rec = {"id": f"sg-{uuid.uuid4().hex[:10]}", "kind": kind, "target": t, "state": RUNNING, "steps": {},
                   "created": now, "updated": now, "attempts": 0, "actor": (actor.id if actor else "system:saga")}
            items.append(rec)
            done = [s for s in items if s.get("state") == DONE]
            if len(done) > KEEP_DONE:
                drop = {s["id"] for s in sorted(done, key=lambda s: s.get("updated", 0))[:-KEEP_DONE]}
                docs["sagas"]["items"] = [s for s in items if s["id"] not in drop]
            fresh = True
        else:
            fresh = False
    if fresh:
        _audit("saga.start", rec, "started", reason=kind)
    return run(rec["id"]) if run_now else rec


def get(sid: str) -> Optional[dict]:
    d, _bad = store._read_cached("sagas")
    return next((s for s in d["items"] if s.get("id") == sid), None)


def list_all() -> list:
    d, _bad = store._read_cached("sagas")
    return list(d["items"])


def _persist(sid: str, mutate: Callable[[dict], None]) -> dict:
    with store.txn() as docs:
        rec = next(s for s in docs["sagas"]["items"] if s["id"] == sid)
        mutate(rec)
        rec["updated"] = time.time()
        return dict(rec)


def run(sid: str) -> dict:
    """saga 의 남은 단계를 돈다. 단계가 실패하면(일반 예외) 거기서 멈추고 오류를 적는다 — 다음 sweep 이 이어한다.
    `InjectedCrash` 같은 BaseException 은 그대로 올라간다(저장된 것까지만 남는다)."""
    with _lock(sid):
        rec = get(sid)
        if rec is None:
            raise KeyError(sid)
        if rec.get("state") != RUNNING:
            return rec
        t = {**rec["target"], "_since": rec.get("created")}
        for name, fn in list(_STEPS):
            if name in (rec.get("steps") or {}):
                continue
            try:
                _fault(name, "before")
                fn(t)
                _fault(name, "after")
            except InjectedCrash:
                raise
            except Exception as e:  # noqa: BLE001 — 멈추고 다음 sweep 에 이어한다
                err = f"{name}: {type(e).__name__}: {str(e)[:160]}"
                logger.warning(f"[saga {sid}] {err}")

                incomplete = isinstance(e, SagaIncomplete)

                def fail(r, err=err, incomplete=incomplete):
                    r["attempts"] = int(r.get("attempts", 0)) + 1
                    r["error"] = err
                    if incomplete:
                        r["steps"] = {}           # 확인에서 남은 것이 보였다 — 처음부터 다시 정리한다(모든 단계가 멱등)
                    if r["attempts"] >= MAX_ATTEMPTS:
                        r["state"] = STUCK
                rec = _persist(sid, fail)
                if rec["state"] == STUCK:
                    _audit("saga.stuck", rec, "stuck", reason=err)
                return rec
            rec = _persist(sid, lambda r, name=name: (r["steps"].__setitem__(name, time.time()), r.pop("error", None)))
        rec = _persist(sid, lambda r: r.update(state=DONE, completed=time.time()))
        _audit("saga.done", rec, "done", reason=rec.get("kind", ""))
        return rec


def sweep(now: Optional[float] = None) -> list[str]:
    """진행 중인(running) saga 를 이어서 돌린다 → 이어한 saga id 들. 실패한 것은 시도 횟수에 따라 늦춰 다시(최대 5분 간격)."""
    now = time.time() if now is None else now
    d, _bad = store._read_cached("sagas")
    out = []
    for s in d["items"]:
        if s.get("state") != RUNNING:
            continue
        wait = min(5 * 2 ** int(s.get("attempts", 0)), 300) if s.get("attempts") else 0
        if wait and now - float(s.get("updated", 0)) < wait:
            continue
        try:
            run(s["id"])
            out.append(s["id"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[saga {s.get('id')}] 이어하기 실패: {type(e).__name__}: {e}")
    return out


async def sweep_loop(interval: float = SWEEP_INTERVAL) -> None:
    import asyncio
    first = True
    while True:
        if not first:
            await asyncio.sleep(interval)
        first = False
        try:
            await asyncio.to_thread(sweep)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"saga sweep 실패: {type(e).__name__}: {e}")


def pending() -> list:
    """끝나지 않은 saga(running·stuck) — `fsh doctor` 가 보인다."""
    return [s for s in list_all() if s.get("state") in (RUNNING, STUCK)]


# --- 기존 회수 경로가 부르는 입구 -----------------------------------------------------------------


def enabled() -> bool:
    from . import flags
    return flags.enforced("lease")


def after_grant_revoke(grant_ids: list, actor: Optional[P.Principal] = None) -> Optional[dict]:
    """grant 회수(사람) 뒤에 — 그 grant 에서 흘러나온 것을 정리하고 확인한다. 플래그가 꺼져 있으면 아무것도 하지 않는다."""
    if not enabled():
        return None
    ids = sorted({str(x) for x in grant_ids if x})
    did = None
    for x in ids:
        if x[:3] in ("dc-", "dr-", "dw-"):
            did = x[3:]
        elif x[:3] == "di-":
            did = x[3:].split("-", 1)[0]
    return start("grant", {"grant_ids": ids, "did": did}, actor=actor)


def after_panic(actor: Optional[P.Principal] = None) -> Optional[dict]:
    if not enabled():
        return None
    return start("panic", {"all": True}, actor=actor)


def after_peer_deny(peer_id: str, actor: Optional[P.Principal] = None) -> Optional[dict]:
    if not enabled():
        return None
    return start("peer", {"peer": peer_id}, actor=actor)


def why_incomplete() -> str:
    return tr("회수 정리가 끝나지 않았습니다")
