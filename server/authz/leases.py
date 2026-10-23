"""폴더 lease — 「지금 이 경로는 내가 쓰는 중」이라는 표시. 문서 `~/.vt/authz/leases.json`.

    lease = {id, holder(주체 id), holder_key(에이전트 세션 key), holder_name, resource("fs:local/<realpath glob>"), glob,
             mode(read|write), exclusive, ttl, reason, created, expires, max_expires, derived_from, deleg, grant_id,
             approved_via}

## 무엇이 강제이고 무엇이 권고인가
  - **강제**(`VT_AUTHZ_ENFORCE` 에 `lease`): FarShell 이 대신 하는 쓰기 — git stage·unstage·commit, 파일을 세션 작업 폴더로 넘기는
    업로드·삽입·peer 파일 수신, 위임 spawn 의 cwd — 가 비human 주체에게 남의 lease 경로에서 403 `lease_conflict` 로 막힌다. 켜지 않으면 이 쓰기들은 그대로다.
  - **권고**: 에이전트가 자기 도구(셸·편집기)로 직접 쓰는 것. 같은 계정의 셸은 FarShell 을 거치지 않으니 막을 수 없다. 설치형 pre-commit
    guard(`fsh lease guard install`)는 커밋 시점에 알려 줄 뿐이다. **같은 uid 의 프로세스는 이 표시를 무시할 수 있다**(권한 저장소 전체가 가진 한계와 같다).
  - 사람은 lease 에 막히지 않는다(사람 우선). 남의 lease 경로 안에서 사람이 쓰면 감사 한 줄과 `lease_notice` 이벤트만 남긴다.

## 규칙
  - 정규화: 정적 prefix(첫 glob 문자 앞 경로 요소들)를 realpath 로 고정하고 `..` 을 거부한다. 정적 prefix 는 `VT_BROWSE_ROOTS` 안이고
    거부 목록·숨김 상태 디렉터리 밖이어야 한다(`fsguard`). 나머지 요소에 숨김 상태 디렉터리·거부 이름이 literal 로 있어도 거부.
    glob 문자가 없는 경로는 디렉터리 전체(하위 포함)를 뜻한다.
  - 겹침은 **보수적**이다: 정적 prefix 가 서로 조상 관계가 아니면 겹치지 않고, literal 대 glob 은 fnmatch(조상 포함)로 확정하며,
    glob 대 glob 은 머리 문자열이 겹치면 겹친다고 본다(모르면 충돌).
  - 충돌: 다른 보유자와 write 끼리 겹치면 충돌. write 와 read 는 둘 중 하나가 exclusive 일 때만. read 끼리는 없다.
    같은 활성 pair 의 둘 다 non-exclusive 인 write 는 예외 — pair 기능이 켜져 있을 때만(`same_pair`).
    같은 위임 계보(derived_from 로 이어진 것)는 충돌하지 않는다.
  - 수명: 기본 30분, 최대 8시간(갱신해도 만든 때부터 8시간을 못 넘긴다 — 확대가 아니다). 보유 세션이 사라지면(tmux 를 못 읽으면 유지),
    만료되면, 관리자가 회수하면, 근거 grant 가 회수되면 사라진다. 파생 lease 는 부모와 함께 사라진다.
  - 잡으려면 밑바닥 권한이 있어야 한다: write 는 `fs.write`(ops=[write]), read 는 `fs.read` — 엔진이 판정한다. 시스템이 발급하는
    위임 lease(`approved_via=system`)만 예외다.
  - 변경은 분당 30회(주체당)이고 모든 변경은 감사된다(실패하면 503 `audit_unavailable`). reason 은 길이만 감사에 남는다.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional

from i18n import tr

from . import audit, engine, flags, limits, store
from . import principals as P
from . import scopes as S
from .errors import ShareError

logger = logging.getLogger(__name__)

DEFAULT_TTL = 30 * 60
MAX_TTL = 8 * 3600
MAX_PER_HOLDER = 50
MAX_GLOB = 1000
MAX_REASON = 200
MAX_CHECK_PATHS = 5000
MAX_CONFLICT_ROWS = 200
GLOB_CHARS = "*?["
MODES = ("read", "write")
SYSTEM_VIA = "system"


class LeaseError(ShareError):
    pass


def _err(code: str, reason: str, status: int, **extra) -> LeaseError:
    return LeaseError(code, reason, status, **extra)


def enforced() -> bool:
    """`VT_AUTHZ_ENFORCE=lease` — 강제 쓰기 차단과 회수 saga 를 켜는가."""
    return flags.enforced("lease")


# --- glob ------------------------------------------------------------------------------------


def is_glob(s: str) -> bool:
    return any(c in s for c in GLOB_CHARS)


def _head(g: str) -> str:
    idx = [g.find(c) for c in GLOB_CHARS if c in g]
    return g[:min(idx)] if idx else g


def canonicalize(raw: str) -> str:
    """사용자가 준 glob → 정규형(`/<realpath 정적 prefix>[/<나머지 glob>]`). 거부하면 LeaseError."""
    import fsguard
    s = str(raw or "").strip()
    if not s or "\x00" in s or len(s) > MAX_GLOB:
        raise _err("bad_glob", tr("경로가 비었거나 올바르지 않습니다"), 400)
    s = os.path.expanduser(s)
    if not os.path.isabs(s):
        raise _err("bad_glob", tr("절대 경로(또는 ~/…)여야 합니다"), 400)
    parts = [x for x in s.split("/") if x not in ("", ".")]
    if ".." in parts:
        raise _err("bad_glob", tr("'..' 은 쓸 수 없습니다"), 400)
    i = 0
    while i < len(parts) and not is_glob(parts[i]):
        i += 1
    static, rest = parts[:i], parts[i:]
    try:
        real = fsguard.resolve_under_roots("/" + "/".join(static))
    except fsguard.FsDenied as e:
        raise _err("outside_roots", tr("lease 는 열람 루트(VT_BROWSE_ROOTS) 안, 거부 목록·숨김 상태 폴더 밖의 경로에만 걸 수 있습니다: {why}",
                                       why=e.reason), 403)
    try:                                           # 아직 없는 경로여도 숨김 상태 폴더 이름이면 거부(fsguard 는 존재하는 디렉터리만 본다)
        rel = real.relative_to(fsguard._matching_root(real, fsguard.get_roots())).parts
    except (ValueError, OSError):
        rel = ()
    for i, part in enumerate(rel):
        is_file = i == len(rel) - 1 and not rest and real.exists() and not real.is_dir()
        if fsguard.is_hidden_state_dir_name(part) and not is_file:
            raise _err("outside_roots", tr("lease 는 열람 루트(VT_BROWSE_ROOTS) 안, 거부 목록·숨김 상태 폴더 밖의 경로에만 걸 수 있습니다: {why}",
                                           why=tr("거부된 디렉토리: {name}", name=part)), 403)
    for comp in rest:
        bad = None
        if not is_glob(comp):
            bad = fsguard._check_denylist(Path(comp))
        if bad is None and fsguard.is_hidden_state_dir_name(comp):
            bad = tr("거부된 디렉토리: {name}", name=comp)
        if bad:
            raise _err("outside_roots", tr("lease 는 열람 루트(VT_BROWSE_ROOTS) 안, 거부 목록·숨김 상태 폴더 밖의 경로에만 걸 수 있습니다: {why}",
                                           why=bad), 403)
    return str(real) + ("/" + "/".join(rest) if rest else "")


def covers(glob: str, path: str) -> bool:
    """lease glob 이 path 를 덮는가 — path 자신 또는 조상 중 하나가 glob 과 맞으면(디렉터리 lease 는 하위 포함)."""
    cur = path.rstrip("/") or "/"
    while True:
        if cur == glob or fnmatch.fnmatchcase(cur, glob):
            return True
        parent = cur.rsplit("/", 1)[0]
        if not parent or parent == cur:
            return False
        cur = parent


def _under(p: str, anc: str) -> bool:
    anc = anc.rstrip("/")
    return p == anc or p.startswith(anc + "/")


def _lit_vs_glob(p: str, g: str) -> bool:
    if covers(g, p):
        return True
    hg = _head(g)
    pp = p.rstrip("/") + "/"
    return hg.startswith(pp) or pp.startswith(hg)      # 모르면 겹침


def overlaps(a: str, b: str) -> bool:
    """두 lease glob 이 같은 경로를 덮을 수 있는가 — **보수적**(모르면 True)."""
    la, lb = not is_glob(a), not is_glob(b)
    if la and lb:
        return _under(a, b) or _under(b, a)
    if la:
        return _lit_vs_glob(a, b)
    if lb:
        return _lit_vs_glob(b, a)
    ha, hb = _head(a), _head(b)
    return ha.startswith(hb) or hb.startswith(ha)


def same_pair(holder_a: str, holder_b: str) -> bool:
    """두 보유자(주체 id)가 같은 활성 pair 의 구성원인가. pair 기능이 꺼져 있으면(`VT_AUTHZ_ENFORCE` 에 `pair` 없음) 항상 False."""
    from . import pairs
    return pairs.same_pair_holders(holder_a, holder_b)


def clash(new: dict, old: dict) -> bool:
    """new 를 old 가 있는 채로 둘 수 있는가의 반대 — 두 lease 가 충돌하는가(보유자가 같거나 같은 계보면 충돌 아님)."""
    if new["holder"] == old["holder"]:
        return False
    if not overlaps(new["glob"], old["glob"]):
        return False
    nw, ow = new["mode"] == "write", old["mode"] == "write"
    if nw and ow:
        return not (not new.get("exclusive") and not old.get("exclusive") and same_pair(new["holder"], old["holder"]))
    if nw or ow:
        return bool(new.get("exclusive") or old.get("exclusive"))
    return False


# --- 저장소 ----------------------------------------------------------------------------------

_alive_cache: dict = {"t": 0.0, "v": None}
_alive_lock = threading.Lock()
ALIVE_TTL = 2.0


def alive_keys(fresh: bool = False) -> Optional[set]:
    """살아 있는 에이전트 세션 key 집합. tmux 를 못 읽으면 None(= 모두 산 것으로 본다)."""
    now = time.monotonic()
    with _alive_lock:
        if not fresh and _alive_cache["t"] and now - _alive_cache["t"] < ALIVE_TTL:
            return _alive_cache["v"]
    try:
        import share_identity
        rows = share_identity.list_pane_rows()
        val = None if rows is None else {f"{r.sid}:{r.created}" for r in rows}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"lease 생존 확인 실패: {e}")
        val = None
    with _alive_lock:
        _alive_cache["t"], _alive_cache["v"] = now, val
    return val


def _grant_live(g: Optional[dict], now: float) -> bool:
    if g is None or g.get("state", "active") != "active":
        return False
    ex = g.get("expires")
    return ex is None or now < float(ex)


def active(items: list, now: float, alive: Optional[set], grants: Optional[dict]) -> list:
    """수명이 남은 lease — 만료·보유 세션 소멸(tmux 를 못 읽으면 유지)·근거 grant 회수·부모 lease 소멸을 거른 것."""
    from session_catalog.identity import filter_grants
    records = [{**l, "bound_sessions": [l["holder_key"]]} if l.get("holder_key") else l for l in items]
    out = []
    for l in filter_grants(records):
        ex = l.get("expires")
        if ex is not None and now >= float(ex):
            continue
        key = l.get("holder_key")
        if key and alive is not None and key not in alive:
            continue
        gid = l.get("grant_id")
        if gid and not str(gid).startswith("default:") and grants is not None and not _grant_live(grants.get(gid), now):
            continue
        out.append(l)
    changed = True
    while changed:
        ids = {l["id"] for l in out}
        keep = [l for l in out if not l.get("derived_from") or l["derived_from"] in ids]
        changed = len(keep) != len(out)
        out = keep
    return out


def _grants_by_id() -> Optional[dict]:
    d, bad = store._read_cached("grants")
    return None if bad else {g.get("id"): g for g in d["items"]}


def read_items() -> tuple[list, bool]:
    d, bad = store._read_cached("leases")
    return d["items"], bad


def active_now(alive="probe") -> list:
    """지금 유효한 lease 전부(락 없이 읽는다 — 강제 판정의 빠른 길)."""
    items, _bad = read_items()
    if not items:
        return []
    if alive == "probe":
        alive = alive_keys()
    return active(items, time.time(), alive, _grants_by_id())


# --- 보기 ------------------------------------------------------------------------------------


def holder_nick(l: dict) -> str:
    """보유자 표시 이름 — 닉네임(세션 이름)뿐이다. 주체 id·key·pid 는 내보내지 않는다."""
    return str(l.get("holder_name") or ("human" if str(l.get("holder", "")).startswith("human:") else "agent"))[:64]


def view(l: dict, now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    return {"id": l["id"], "holder": holder_nick(l), "glob": l["glob"], "mode": l["mode"], "exclusive": bool(l.get("exclusive")),
            "reason": l.get("reason") or "", "created": l.get("created"), "expires": l.get("expires"),
            "remaining": max(0, int(float(l.get("expires") or now) - now)), "maxExpires": l.get("max_expires"),
            "derivedFrom": l.get("derived_from"), "delegation": l.get("deleg"), "approvedVia": l.get("approved_via"),
            "ttl": l.get("ttl")}


def conflict_row(l: dict) -> dict:
    return {"holder": holder_nick(l), "expires": l.get("expires"), "mode": l["mode"], "exclusive": bool(l.get("exclusive")),
            "glob": l["glob"]}


# --- 감사 · 속도 -----------------------------------------------------------------------------

_RATE = limits.Window(limits.LIMITS["lease"][0], limits.LIMITS["lease"][1])


def reset_rate() -> None:
    global _RATE
    _RATE = limits.Window(limits.LIMITS["lease"][0], limits.LIMITS["lease"][1])


def _rate(p: P.Principal) -> None:
    if not _RATE.hit(p.id):
        raise _err("rate_limited", tr("lease 변경이 너무 잦습니다 — 분당 {n}회까지", n=limits.LIMITS["lease"][0]), 429, retryAfter=60)


def _audit_strict(rec: dict) -> None:
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        raise _err("audit_unavailable", tr("감사 기록을 쓰지 못해 변경을 중단했습니다"), 503) from e


def _audit_lease(action: str, p_id: str, l: dict, result: str = "ok", reason: str = "", scopes=()) -> None:
    rec = {"principal": p_id, "action": f"lease.{action}", "resource": l.get("resource"), "scopes": list(scopes),
           "decision": "allow", "result": result, "grant_id": l.get("grant_id"),
           "detail": {"lease": l.get("id"), "mode": l.get("mode"), "exclusive": bool(l.get("exclusive")),
                      "via": l.get("approved_via"), "derived_from": l.get("derived_from"), "deleg": l.get("deleg")}}
    if reason:
        rec["reason"] = reason
    if l.get("reason"):
        rec["sizes"] = {"chars": len(str(l["reason"]))}
    _audit_strict(rec)


# --- 엔진 ------------------------------------------------------------------------------------


def _fresh_snapshot(now: float) -> store.Snapshot:
    docs = {n: store._read(n) for n in ("grants", "approvals", "windows", "shares")}
    return store.Snapshot(grants=docs["grants"][0]["items"], approvals=docs["approvals"][0]["items"],
                          windows=docs["windows"][0]["items"], shares=docs["shares"][0]["items"],
                          corrupt=frozenset(n for n, (_d, bad) in docs.items() if bad), now=now, source="store").fenced()


def _decide(p: P.Principal, mode: str, glob: str, alive: Optional[set]):
    """밑바닥 권한 — write 는 `fs.write`(ops=[write]), read 는 `fs.read`. 디렉터리 lease 는 `<경로>/x` 로도 한 번 더 묻는다."""
    scope = "fs.write" if mode == "write" else "fs.read"
    ctx = {"alive": alive, "op": "write"} if mode == "write" else {"alive": alive}
    snap = _fresh_snapshot(time.time())
    last = None
    for res in ("fs:local" + glob, "fs:local" + glob.rstrip("/") + "/x"):
        try:
            last = engine.decide(p, scope, res, ctx, snap)
        except Exception as e:  # noqa: BLE001 — 엔진 예외 = 닫힘
            logger.warning(f"lease 권한 엔진을 쓸 수 없다 — 거부: {type(e).__name__}: {e}")
            raise _err("authz_unavailable", tr("권한 엔진을 쓸 수 없어 요청을 거부했습니다"), 503) from e
        if last.reason == "store_corrupt":
            raise _err("authz_unavailable", tr("권한 엔진을 쓸 수 없어 요청을 거부했습니다"), 503)
        if last.allow:
            return scope, last
    return scope, last


def _forbidden(scope: str, glob: str, d) -> LeaseError:
    return _err("forbidden", tr("이 폴더에 lease 를 잡을 권한이 없습니다 — {scope} 권한이 필요합니다(request_access 로 요청)", scope=scope), 403,
                need={"resource": "fs:local" + glob.rstrip("/") + ("/*" if not is_glob(glob) else ""), "scopes": [scope]},
                why=getattr(d, "reason", ""))


def _holder_fields(p: P.Principal) -> dict:
    key = p.attrs.get("key") if p.kind == P.AGENT else None
    name = p.attrs.get("name") or ""
    return {"holder": p.id, "holder_key": key or None, "holder_name": name or ("human" if p.kind == P.HUMAN else (key or ""))}


# --- 잡기 · 갱신 · 놓기 ----------------------------------------------------------------------


def _check_ttl(ttl) -> int:
    if ttl is None or ttl == "":
        return DEFAULT_TTL
    try:
        t = int(ttl)
    except (TypeError, ValueError):
        raise _err("bad_ttl", tr("ttl 은 초 단위 정수여야 합니다"), 400)
    if t <= 0:
        raise _err("bad_ttl", tr("ttl 은 1초 이상이어야 합니다"), 400)
    if t > MAX_TTL:
        raise _err("ttl_too_long", tr("ttl 은 최대 {h}시간입니다", h=MAX_TTL // 3600), 400, max=MAX_TTL)
    return t


def _reap_in_txn(docs: dict, now: float, alive: Optional[set], *, who: str = "system:sweep") -> int:
    """txn 안에서 수명이 끝난 lease 를 지운다(감사 한 줄씩). 지운 수."""
    items = docs["leases"]["items"]
    grants = None if store._read("grants")[1] else {g.get("id"): g for g in docs["grants"]["items"]}
    live = {l["id"] for l in active(items, now, alive, grants)}
    gone = [l for l in items if l["id"] not in live]
    for l in gone:
        _audit_lease("expire", who, l, result="ended", reason="expired_or_holder_gone")
    if gone:
        docs["leases"]["items"] = [l for l in items if l["id"] in live]
    return len(gone)


def acquire(p: P.Principal, glob: str, *, mode: str = "write", exclusive: bool = False, ttl=None, reason: str = "",
            system: bool = False, derived_from: Optional[str] = None, deleg: Optional[str] = None,
            alive="probe", expires: Optional[float] = None, max_expires: Optional[float] = None) -> dict:
    """lease 를 잡는다 → 보기(dict). 충돌 409 `lease_conflict`, 권한 없음 403 `forbidden`.
    `system=True` 는 서버가 위임 규칙으로 발급하는 경우 — 밑바닥 권한 판정과 속도 제한을 건너뛰고 `approved_via=system` 이다."""
    if mode not in MODES:
        raise _err("bad_mode", tr("mode 는 read 또는 write 입니다"), 400)
    t = _check_ttl(ttl)
    canon = canonicalize(glob)
    if not system:
        _rate(p)
    if alive == "probe":
        alive = alive_keys(fresh=True)
    scope, grant_id = ("fs.write" if mode == "write" else "fs.read"), None
    if not system:
        scope, d = _decide(p, mode, canon, alive)
        if not d.allow:
            _deny_log(p, "acquire", canon, d.reason)
            raise _forbidden(scope, canon, d)
        grant_id = d.grant_id
    now = time.time()
    holder = _holder_fields(p)
    rec = {"id": f"ls-{uuid.uuid4().hex[:10]}", **holder, "resource": "fs:local" + canon, "glob": canon, "mode": mode,
           "exclusive": bool(exclusive), "ttl": t, "reason": str(reason or "")[:MAX_REASON], "created": now,
           "expires": expires if expires is not None else now + t, "max_expires": max_expires if max_expires is not None else now + MAX_TTL,
           "derived_from": derived_from, "deleg": deleg, "grant_id": grant_id,
           "approved_via": SYSTEM_VIA if system else S.VIA_MANUAL, "state": "active"}
    from session_catalog.identity import stamp_approved
    if rec.get("holder_key"):
        rec["bound_sessions"] = [rec["holder_key"]]
        stamp_approved(rec)
    with store.txn() as docs:
        _reap_in_txn(docs, now, alive)
        items = docs["leases"]["items"]
        mine = [l for l in items if l["holder"] == p.id]
        if len(mine) >= MAX_PER_HOLDER and not system:
            raise _err("too_many_leases", tr("lease 는 주체당 최대 {n}개입니다", n=MAX_PER_HOLDER), 429)
        clashing = [l for l in items if clash(rec, l) and not _same_lineage(rec, l, items)]
        if clashing:
            _deny_log(p, "acquire", canon, "lease_conflict")
            first = min(clashing, key=lambda x: float(x.get("expires") or 0))
            raise _err("lease_conflict", tr("다른 세션이 이 경로를 쓰는 중입니다"), 409, holder=holder_nick(first),
                       expires=first.get("expires"), conflicts=[conflict_row(x) for x in clashing[:10]])
        _audit_lease("acquire", p.id, rec, scopes=[scope])
        items.append(rec)
    return view(rec, now)


def insert_in_txn(docs: dict, rec: dict, *, who: str, alive=None) -> None:
    """이미 `store.txn()` 안에 있는 호출자(pair 승인·handoff)가 lease 레코드를 같은 트랜잭션에서 넣는다 — 수명이 끝난 것을 걷어내고,
    다른 보유자와 충돌하면 409 `lease_conflict`(트랜잭션 전체가 취소된다), 아니면 감사 한 줄과 함께 더한다. 밑바닥 권한·속도 제한은 건너뛴다(시스템 발급)."""
    now = time.time()
    _reap_in_txn(docs, now, alive)
    items = docs["leases"]["items"]
    clashing = [l for l in items if clash(rec, l) and not _same_lineage(rec, l, items)]
    if clashing:
        first = min(clashing, key=lambda x: float(x.get("expires") or 0))
        raise _err("lease_conflict", tr("다른 세션이 이 경로를 쓰는 중입니다"), 409, holder=holder_nick(first),
                   expires=first.get("expires"), conflicts=[conflict_row(x) for x in clashing[:10]])
    _audit_lease("acquire", who, rec)
    items.append(rec)


def _root_of(l: dict, byid: dict) -> str:
    seen = set()
    cur = l
    while cur.get("derived_from") and cur["derived_from"] in byid and cur["id"] not in seen:
        seen.add(cur["id"])
        cur = byid[cur["derived_from"]]
    return cur["id"]


def _same_lineage(a: dict, b: dict, items: list) -> bool:
    """a(아직 저장 전일 수 있다)와 b 가 같은 위임 계보(derived_from 로 이어진 같은 뿌리)인가."""
    byid = {l["id"]: l for l in items}
    ra = _root_of(byid[a["derived_from"]], byid) if a.get("derived_from") in byid else _root_of(a, byid)
    return ra == _root_of(b, byid)


def _deny_log(p: P.Principal, op: str, glob: str, reason: str) -> None:
    try:
        from . import denylog
        denylog.record({"principal": p.id, "action": f"lease.{op}", "resource": "fs:local" + glob, "decision": "deny",
                        "result": "denied", "reason": reason})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"lease 거부 기록 실패: {e}")


def _find(items: list, lid: str) -> Optional[dict]:
    return next((l for l in items if l.get("id") == lid), None)


def _descendants(items: list, lid: str) -> list:
    out, frontier = [], {lid}
    while frontier:
        nxt = [l for l in items if l.get("derived_from") in frontier and l not in out]
        out += nxt
        frontier = {l["id"] for l in nxt}
    return out


def _can_manage(p: P.Principal, l: dict) -> bool:
    return p.kind == P.HUMAN or l["holder"] == p.id


def renew(p: P.Principal, lid: str, ttl=None) -> dict:
    """남은 시간을 `ttl`(기본 처음 ttl)로 다시 맞춘다 — 만든 때부터 최대 8시간을 못 넘긴다."""
    _rate(p)
    t = _check_ttl(ttl) if ttl not in (None, "") else None
    now = time.time()
    alive = alive_keys()
    with store.txn() as docs:
        _reap_in_txn(docs, now, alive)
        l = _find(docs["leases"]["items"], lid)
        if l is None or not _can_manage(p, l):
            raise _err("not_found", tr("그런 lease 가 없습니다"), 404)
        if l.get("derived_from"):
            raise _err("derived_lease", tr("상속받은 lease 는 갱신할 수 없습니다 — 부모 lease 를 갱신하세요"), 409)
        new = min(now + (t or int(l.get("ttl") or DEFAULT_TTL)), float(l.get("max_expires") or (float(l["created"]) + MAX_TTL)))
        capped = new < now + (t or int(l.get("ttl") or DEFAULT_TTL)) - 1
        l["expires"] = max(new, float(l.get("expires") or 0))     # 갱신은 줄이지 않는다
        for c in _descendants(docs["leases"]["items"], lid):
            c["expires"] = l["expires"]
        _audit_lease("renew", p.id, l, reason="capped_at_max" if capped else "")
        out = view(l, now)
    out["capped"] = capped
    return out


def release(p: P.Principal, lid: str, *, reason: str = "released") -> dict:
    """lease 를 놓는다(보유자 또는 사람). 파생 lease 도 함께."""
    _rate(p)
    now = time.time()
    with store.txn() as docs:
        items = docs["leases"]["items"]
        l = _find(items, lid)
        if l is None or not _can_manage(p, l):
            raise _err("not_found", tr("그런 lease 가 없습니다"), 404)
        gone = [l] + _descendants(items, lid)
        for g in gone:
            _audit_lease("release", p.id, g, result="ended", reason=reason if g is l else "parent_released")
        ids = {g["id"] for g in gone}
        docs["leases"]["items"] = [x for x in items if x["id"] not in ids]
    return {"ok": True, "id": lid, "released": len(gone)}


def release_where(match, *, who: str, reason: str) -> list[str]:
    """조건에 맞는 lease 를 전부 놓는다(saga·위임 닫힘·관리자 회수가 쓴다). 지운 id 목록. 멱등."""
    with store.txn() as docs:
        items = docs["leases"]["items"]
        hit = [l for l in items if match(l)]
        ids = {l["id"] for l in hit}
        frontier = set(ids)
        while frontier:                      # 파생 lease 는 부모와 함께
            nxt = {l["id"] for l in items if l.get("derived_from") in frontier and l["id"] not in ids}
            ids |= nxt
            frontier = nxt
        gone = [l for l in items if l["id"] in ids]
        for g in gone:
            _audit_lease("revoke", who, g, result="ended", reason=reason)
        if gone:
            docs["leases"]["items"] = [x for x in items if x["id"] not in ids]
    return [g["id"] for g in gone]


def revoke_admin(actor: P.Principal, lid: str) -> dict:
    """관리자(사람)의 회수 — 속도 제한 없이."""
    gone = release_where(lambda l: l.get("id") == lid, who=actor.id, reason="revoked_by_admin")
    if not gone:
        raise _err("not_found", tr("그런 lease 가 없습니다"), 404)
    return {"ok": True, "id": lid, "released": len(gone)}


def release_by_deleg(did: str, reason: str = "delegation_closed") -> list[str]:
    return release_where(lambda l: l.get("deleg") == did, who="system:spawn", reason=reason)


def release_by_holder_key(key: str, reason: str) -> list[str]:
    return release_where(lambda l: l.get("holder_key") == key, who="system:saga", reason=reason)


def release_by_grant(gid: str, reason: str = "grant_revoked") -> list[str]:
    return release_where(lambda l: l.get("grant_id") == gid, who="system:saga", reason=reason)


def reap() -> int:
    """수명이 끝난 lease 를 지운다(lifespan sweep). 문서가 없거나 비어 있으면 아무것도 쓰지 않는다."""
    items, bad = read_items()
    if bad or not items:
        return 0
    now = time.time()
    alive = alive_keys(fresh=True)
    if len(active(items, now, alive, _grants_by_id())) == len(items):
        return 0
    with store.txn() as docs:
        return _reap_in_txn(docs, now, alive)


async def sweep_loop(interval: float = 30.0) -> None:
    import asyncio
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(reap)
        except Exception as e:  # noqa: BLE001 — 다음 주기에 다시
            logger.warning(f"lease 정리 실패: {type(e).__name__}: {e}")


# --- 조회 ------------------------------------------------------------------------------------


def list_for(p: P.Principal, *, everyone: bool) -> list[dict]:
    now = time.time()
    act = active_now()
    mine = act if everyone else [l for l in act if l["holder"] == p.id]
    mine.sort(key=lambda l: float(l.get("created") or 0))
    return [view(l, now) for l in mine]


def _family_ids(actor_id: Optional[str], act: list) -> set:
    if not actor_id:
        return set()
    byid = {l["id"]: l for l in act}
    roots = {_root_of(l, byid) for l in act if l["holder"] == actor_id}
    return {l["id"] for l in act if _root_of(l, byid) in roots}


def _real(path: str) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def blocking(actor_id: Optional[str], paths: Iterable[str], act: Optional[list] = None) -> list[dict]:
    """각 path 에서 `actor_id` 를 막는 lease — 다른 계보 보유자의 write 또는 exclusive lease 가 path 를 덮는 것.
    반환: `[{path, holder, expires, mode, exclusive, glob, lease}]` (path 마다, 최대 MAX_CONFLICT_ROWS)."""
    act = active_now() if act is None else act
    fam = _family_ids(actor_id, act)
    others = [l for l in act if l["id"] not in fam and (l["mode"] == "write" or l.get("exclusive"))]
    out = []
    if not others:
        return out
    for path in paths:
        rp = _real(path)
        for l in others:
            if covers(l["glob"], rp):
                out.append({"path": rp, **conflict_row(l), "lease": l["id"]})
                break
        if len(out) >= MAX_CONFLICT_ROWS:
            break
    return out


def check(p: P.Principal, paths: list, holder: Optional[str] = None) -> dict:
    """순수 조회(pre-commit guard 가 쓴다) — path 마다 충돌. 에이전트는 자기 자신 기준이고 `holder` 를 무시한다.
    사람·cli 가 `holder`(주체 id)를 주면 그 주체 기준, 없으면 보유자가 아닌 호출자(모든 write lease 가 막는다)."""
    if not isinstance(paths, list) or not all(isinstance(x, str) and x and "\x00" not in x for x in paths):
        raise _err("bad_paths", tr("paths 는 경로 문자열 목록이어야 합니다"), 400)
    if len(paths) > MAX_CHECK_PATHS:
        raise _err("too_many_paths", tr("한 번에 {n}개까지 확인할 수 있습니다", n=MAX_CHECK_PATHS), 413)
    actor = p.id if p.kind == P.AGENT else (str(holder) if holder else None)
    rows = blocking(actor, paths)
    return {"ok": not rows, "checked": len(paths), "conflicts": rows, "enforced": enforced()}


# --- 강제 (FarShell 이 대신 하는 쓰기) --------------------------------------------------------------


def _scan_blocked(paths: list) -> Optional[list]:
    """빠른 길: 어떤 경로든 write/exclusive lease 에 덮이는가. 아니면 None. 이어서 보유자를 가려야 하면 lease 목록."""
    items, bad = read_items()
    if bad:
        return []
    if not items:
        return None
    act = active_now()
    cands = [l for l in act if l["mode"] == "write" or l.get("exclusive")]
    if not cands:
        return None
    rps = [_real(x) for x in paths]
    if not any(covers(l["glob"], rp) for l in cands for rp in rps):
        return None
    return act


def corrupt() -> bool:
    return read_items()[1]


def enforce_write_sync(actor: Optional[P.Principal], paths: list, *, op: str, resolve=None):
    """쓰기 직전 판정(동기). 반환 `(blocked: dict|None, notice: dict|None)`.
    `blocked` = 403 본문(`lease_conflict`), `notice` = 사람의 쓰기가 남의 lease 안에서 일어났다는 사실(`{lease, paths}`).
    `resolve` 는 보유자를 가릴 필요가 생겼을 때만 부르는 주체 확인 함수(비싼 tmux 호출을 미룬다)."""
    if not enforced() or not paths:
        return None, None
    if corrupt():
        if resolve is not None:
            actor = resolve()
        if actor is not None and actor.kind == P.HUMAN:
            return None, None
        return {"error": "lease_unavailable", "reason": tr("lease 저장소를 읽을 수 없어 쓰기를 거부했습니다")}, None
    act = _scan_blocked(paths)
    if act is None:
        return None, None
    if resolve is not None:
        actor = resolve()
    if actor is not None and actor.kind == P.HUMAN:
        rows = blocking(actor.id, paths, act)
        return None, ({"lease": rows[0]["lease"], "paths": len(rows)} if rows else None)
    rows = blocking(actor.id if actor is not None and actor.kind == P.AGENT else None, paths, act)
    if not rows:
        return None, None
    first = min(rows, key=lambda r: float(r.get("expires") or 0))
    return {"error": "lease_conflict", "reason": tr("다른 세션의 lease 가 걸린 경로라 쓸 수 없습니다"), "holder": first["holder"],
            "expires": first["expires"], "paths": len(rows), "conflicts": rows[:10], "op": op}, None


async def enforce_write(request, paths: list, *, op: str):
    """라우트가 쓰기 직전에 부른다 → 막으면 JSONResponse(403), 아니면 None. 플래그가 꺼져 있으면 첫 줄에서 None(아무것도 안 한다)."""
    if not enforced() or not paths:
        return None
    import asyncio
    from fastapi.responses import JSONResponse

    def work():
        cache = {}

        def resolve():
            if "p" not in cache:
                cache["p"] = identify(request)
            return cache["p"]
        return enforce_write_sync(None, paths, op=op, resolve=resolve)

    blocked, notice = await asyncio.to_thread(work)
    if notice:
        await _human_override(notice, op)
    if blocked:
        try:
            from . import denylog
            denylog.record({"principal": "-", "action": f"lease.{op}", "resource": "fs:local", "decision": "deny",
                            "result": "denied", "reason": blocked["error"], "sizes": {"paths": len(paths)}})
        except Exception:  # noqa: BLE001
            pass
        return JSONResponse(blocked, status_code=503 if blocked["error"] == "lease_unavailable" else 403)
    return None


async def _human_override(notice: dict, op: str) -> None:
    import asyncio
    from . import events as authz_events

    def rec():
        try:
            audit.append({"principal": "human", "action": "lease.override", "resource": "fs:local", "decision": "allow",
                          "result": "ok", "reason": f"human_write_in_leased_path op={op}",
                          "detail": {"lease": notice.get("lease"), "paths": notice.get("paths")}})
        except Exception as e:  # noqa: BLE001 — 사람의 쓰기는 막지 않는다
            logger.warning(f"lease override 감사 실패: {e}")
    await asyncio.to_thread(rec)
    await authz_events.emit(authz_events.lease_notice_event(str(notice.get("lease") or "-")))


def identify(request) -> Optional[P.Principal]:
    """요청 호출자 → 주체. 소켓이면 게이트가 정한 주체, 아니면 인증 판정(`P.from_request`). 기계 토큰·무인증 서버의 호출은
    `X-Fsh-*` 헤더로 에이전트 pane 을 확인해 본다(확인 못 하면 익명 — 어느 보유자도 아니다)."""
    try:
        if request.scope.get("fsh_sock") is True:
            p = getattr(request.state, "fsh_principal", None)
            if p is not None:
                return p
        p = P.from_request(request)
    except Exception:  # noqa: BLE001
        return None
    if p.kind in (P.LEGACY,):
        try:
            import share_identity
            from urllib.parse import unquote
            h = request.headers
            server = request.scope.get("server")
            c = share_identity.verify_caller(
                unquote(h.get("x-fsh-pane", "")), unquote(h.get("x-fsh-tmux", "")), unquote(h.get("x-fsh-pid", "")),
                client=tuple(request.client) if request.client else None, headers={k.lower(): v for k, v in h.items()},
                server_port=server[1] if server else None, peer_pid=((request.scope.get("fsh_peer") or {}).get("pid")))
            return P.from_caller(c)
        except Exception:  # noqa: BLE001
            return p
    return p


def session_cwd(session: str) -> Optional[str]:
    """세션(tmux 이름)의 활성 pane 작업 폴더 — 파일을 그 세션으로 넘기는 쓰기의 대상 경로. 모르면 None."""
    if not session:
        return None
    try:
        import tmux_runner
        import tmux_target
        pane = tmux_target.session_pane(session)
        if not pane:
            return None
        rc, out, _ = tmux_runner.run(["display-message", "-p", "-t", pane, "#{pane_current_path}"], timeout=3.0)
        cwd = out.decode("utf-8", "replace").strip() if rc == 0 else ""
        return cwd or None
    except Exception:  # noqa: BLE001
        return None
