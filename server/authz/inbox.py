"""승인함·권한 목록·회수·비상 정지 — `/api/authz` 사람용 면(`routes/authz.py`)의 본체.

승인 요청은 네 종류가 한 목록에 보인다(kind): `share`(에이전트 → 공유 세션 읽기/쓰기), `msg`(에이전트 간 메시지),
`peer`(`fsh host allow-*` 가 만든 호스트 권한 변경), `generic`(에이전트가 `POST /api/authz/requests` 로 직접 요청한 임의의
(리소스, scope)). 앞의 셋은 옛 모듈(`agent_share`·`msg_svc`·`host_grant_svc`)이 저장·판정·감사를 그대로 쥐고, 이 모듈은 목록을
합치고 결정을 그쪽 함수로 **위임**한다(선착순·멱등·승인권자 확인이 거기 있다). `generic` 만 이 모듈이 `approvals.json`/`grants.json`
에 직접 쓴다 — 어떤 집행 플래그와도 무관하다(레코드를 쓰기만 하고, 집행 지점이 엔진을 부를 때 비로소 읽힌다).

## 선택지
  - `session`(기본): 주체·리소스 세션이 살아 있는 동안. 세션에 묶이지 않는 주체/리소스면 TTL(없으면 1시간)로 닫는다.
  - `once`: `conds.max_uses = 1` 인 grant. 한 번 쓰면 끝난다. 쓴 횟수는 `uses` 에 세고 집행 지점이 올린다.
  - `deny`: 거절(쿨다운 300초).
  `share` 요청의 once/ttl 은 `share` 집행이 켜져 있을 때만 된다(옛 `agent_grants.json` 에는 조건을 담을 곳이 없어, 켜짐에서 별도 grant 로 낸다).
  `msg`/`peer` 는 once/ttl 을 받지 않는다(400 `choice_unsupported`).

## 감사
요청 생성·결정·회수·비상 정지는 커밋 전에 `audit/authz.jsonl` 에 쓰고, 못 쓰면 503 `audit_unavailable` 로 중단한다. 에이전트가 적은 사유
문구는 기록하지 않는다(길이와 sha8 만).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Iterable, Optional

from i18n import tr

from . import approvals, audit, defaults, engine, flags, limits, store
from . import principals as P
from . import scopes as S
from .errors import ShareError

logger = logging.getLogger(__name__)

SOURCE = "generic"                          # 해결 기록의 종류(`authz:resolved:generic:<id>`)
PREFIX_REQ = "authz:req:"
PREFIX_GRANT = "authz:grant:"
REQ_ID_PREFIX = "gq-"
GRANT_ID_PREFIX = "gg-"
KIND_SHARE, KIND_MSG, KIND_PEER, KIND_GENERIC = "share", "msg", "peer", "generic"
KIND_PAIR = "pair"
CHOICES = ("once", "session", "deny")
DECISIONS = ("allow", "deny", "later")
REQUEST_TTL = limits.PENDING_TTL
DENY_COOLDOWN = limits.DENY_COOLDOWN
PER_PRINCIPAL = limits.PENDING_PER_PRINCIPAL
TOTAL = limits.PENDING_TOTAL
RATE_PER_MIN = limits.LIMITS["authz.request"][0]
TTL_MIN, TTL_MAX = 60, 7 * 24 * 3600
DEFAULT_SESSIONLESS_TTL = 3600
MAX_SCOPES = 20
REASON_MAX = 200
# 에이전트가 요청할 수 없는 scope — 승인 채널·권한 관리·기기 연결은 사람의 일이다(자동 승인 구간도 덮지 못하는 집합과 같다).
UNREQUESTABLE = frozenset(S.NEVER_WINDOW)
_RES_SAFE = re.compile(r"[A-Za-z0-9_:$%.@/\-*]{1,200}")

_rate: dict[str, list[float]] = {}          # 주체당 분당 6회 — 메모리, 재시작하면 비워진다


def reset_rate() -> None:
    _rate.clear()


# --- 표시용 보조 ---------------------------------------------------------------------------------


def preset_label(pid: str) -> str:
    if pid == "view":
        return tr("보기")
    if pid == "collab":
        return tr("협업")
    if pid == "drive":
        return tr("운전")
    if pid == "control":
        return tr("제어")
    return pid


def preset_of(scopes: Iterable[str], sconds: Optional[dict] = None) -> Optional[dict]:
    """이 (scope, 조건)을 **덮는 가장 작은** 프리셋 `{id, label, exact}`. 어느 프리셋도 못 덮으면 None.
    exact 는 프리셋과 같을 때(더 좁으면 false — UI 는 「… 일부」로 보인다)."""
    want = S.effective(list(scopes), sconds)
    if not want:
        return None
    for pid in S.PRESET_ORDER:
        sc, cd = S.preset(pid)
        have = S.effective(sc, cd)
        if S.caps_subset(want, have):
            return {"id": pid, "label": preset_label(pid), "exact": S.caps_subset(have, want)}
    return None


def presets_within(caps: dict) -> list[dict]:
    """이 유효 집합이 **전부 포함하는** 프리셋들(보기 < 협업 < 운전 < 제어 순)."""
    out = []
    for pid in S.PRESET_ORDER:
        sc, cd = S.preset(pid)
        if S.caps_subset(S.effective(sc, cd), caps):
            out.append({"id": pid, "label": preset_label(pid)})
    return out


def _view_conds(sconds: Optional[dict]) -> dict:
    return {s: {k: sorted(v) for k, v in e.items()} for s, e in sorted((sconds or {}).items())
            if isinstance(e, dict)}


def _names(snap) -> dict:
    return {k: v.name for k, v in (snap or {}).items()}


def _session_key(resource: str) -> Optional[str]:
    return resource[len("session:local/"):] if resource.startswith("session:local/") else None


def _fmt_scopes(scopes: Iterable[str]) -> list[str]:
    return sorted(str(x) for x in scopes)


def _alive(snap) -> Optional[set]:
    return None if snap is None else set(snap)


# --- 감사 ------------------------------------------------------------------------------------------


def _audit_strict(rec: dict) -> None:
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        raise ShareError("audit_unavailable", tr("감사 기록을 쓰지 못해 변경을 중단했습니다"), 503) from e


# --- generic 레코드 ------------------------------------------------------------------------------


def is_req(rec: dict) -> bool:
    return str(rec.get("origin", "")).startswith(PREFIX_REQ)


def is_grant(rec: dict) -> bool:
    return str(rec.get("origin", "")).startswith(PREFIX_GRANT)


def raw_id(rec: dict) -> str:
    return str(rec.get("origin", "")).rsplit(":", 1)[-1]


def owns_request(request_id: str) -> bool:
    return request_id.startswith(REQ_ID_PREFIX) or request_id.startswith(f"ap-{REQ_ID_PREFIX}")


def owns_grant(grant_id: str) -> bool:
    return grant_id.startswith(GRANT_ID_PREFIX)


def _generic_reqs(docs: dict) -> list[dict]:
    return [a for a in docs["approvals"]["items"] if is_req(a)]


def _generic_grants(docs: dict) -> list[dict]:
    return [g for g in docs["grants"]["items"] if is_grant(g)]


def _grant_dead(g: dict, alive: Optional[set], now: float) -> bool:
    if g.get("expires") is not None and now >= float(g["expires"]):
        return True
    mu = (g.get("conds") or {}).get("max_uses")
    if mu is not None and int(g.get("uses", 0)) >= int(mu):
        return True
    if alive is not None:
        for k in (g.get("subject_session"), g.get("resource_session")):
            if k and k not in alive:
                return True
    return False


def purge(docs: dict, alive: Optional[set], now: float) -> bool:
    """만료·소진·세션이 사라진 generic grant 와 만료·쿨다운이 끝난 generic 요청을 걷어낸다."""
    g0, a0 = list(docs["grants"]["items"]), list(docs["approvals"]["items"])
    docs["grants"]["items"] = [g for g in g0 if not (is_grant(g) and _grant_dead(g, alive, now))]
    keep = []
    for a in a0:
        if is_req(a):
            gone = alive is not None and any(a.get(k) and a[k] not in alive for k in ("subject_session", "resource_session"))
            if gone:
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


def _snap(docs: dict, now: float) -> store.Snapshot:
    return approvals._snap(docs, now)


def _scoped(rec: dict, scopes, sconds) -> dict:
    out = {**rec, "scopes": list(scopes), "schema": S.SCHEMA}
    if sconds:
        out["sconds"] = sconds
    return out


def request_view(rec: dict, names: Optional[dict] = None) -> dict:
    """승인함 항목(generic). 사유는 에이전트가 적은 글이다 — 화면은 이스케이프해서 보이고 판단 근거로만 쓴다."""
    names = names or {}
    sk = rec.get("subject_session")
    scopes = list(rec.get("scopes") or ())
    return _item(kind=KIND_GENERIC, rid=raw_id(rec), rec=rec,
                 subject={"kind": "agent" if sk else "local", "id": rec.get("subject"),
                          "name": names.get(sk) or rec.get("subject_name") or ""},
                 resource={"id": rec.get("resource"), "label": names.get(rec.get("resource_session") or "") or rec.get("resource_name") or ""},
                 scopes=scopes, sconds=rec.get("sconds"), extra={"reason": rec.get("note") or "", "ttl": rec.get("ttl")})


def _item(*, kind: str, rid: str, rec: dict, subject: dict, resource: dict, scopes, sconds, extra: Optional[dict] = None,
          now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    scopes = _fmt_scopes(scopes)
    el = sorted(S.requires_elevated("agent", scopes, str(resource.get("id") or ""), sconds))
    out = {"id": rid, "kind": kind, "state": rec.get("state") or "pending", "subject": subject, "resource": resource,
           "scopes": scopes, "conds": _view_conds(sconds), "preset": preset_of(scopes, sconds), "elevated": el,
           "lineage": ({"delegation": rec["delegation"]} if rec.get("delegation") else None),
           "created": rec.get("created"), "expires": rec.get("expires"),
           "age": max(0, int(now - float(rec.get("created") or now))),
           "expiresIn": max(0, int(float(rec.get("expires") or now) - now))}
    out.update(extra or {})
    return out


# --- 에이전트 쪽: 내가 누구인가 · 내 권한 · 요청 -----------------------------------------------------------


def _subject_ok(sel: str, p: P.Principal) -> bool:
    return engine._subject_matches(sel, p)


def _grants_for(p: P.Principal, snap: store.Snapshot, now: float) -> list[dict]:
    out = []
    for g in list(defaults.rules_for(p)) + [x for x in snap.grants if x.get("effect") != "deny"]:
        if g.get("effect") == "deny" or g.get("state", "active") != "active":
            continue
        if not _subject_ok(str(g.get("subject", "")), p) or not engine._time_ok(g, now):
            continue
        out.append(g)
    return out


def whoami(p: P.Principal, now: Optional[float] = None) -> dict:
    """호출 주체 + 유효 scope·조건 요약 + 맞는 프리셋. 소켓 게이트가 부른다(`sock.Gate`)."""
    now = time.time() if now is None else now
    from . import sock as _sock
    view = _sock.principal_view(p)
    try:
        snap = store.snapshot(now)
        mine = _grants_for(p, snap, now)
        union: dict = {}
        for g in mine:
            for s, e in S.caps_of(g).items():
                cur = union.setdefault(s, {})
                for k, v in e.items():
                    cur[k] = cur.get(k, frozenset()) | v
        caps = {s: {k: frozenset(v) for k, v in e.items()} for s, e in union.items()}
        return {"principal": view, "scopes": sorted(caps), "caps": S.caps_view(caps),
                "grants": [{"id": g.get("id"), "resource": g.get("resource"), "scopes": _fmt_scopes(S.caps_of(g)),
                            "default": str(g.get("id", "")).startswith("default:"),
                            "conditional": sorted(k for k, v in (g.get("conds") or {}).items() if v)} for g in mine],
                "presets": presets_within(caps), "authz": "ok"}
    except Exception as e:  # noqa: BLE001 — 저장소 문제로 호출자 확인까지 막지 않는다
        logger.warning(f"whoami 유효 권한 계산 실패: {type(e).__name__}: {e}")
        return {"principal": view, "scopes": None, "caps": None, "grants": [], "presets": [], "authz": "unavailable"}


def _check_scopes(scopes, conds) -> tuple[list[str], Optional[dict]]:
    if not isinstance(scopes, (list, tuple)) or not scopes or len(scopes) > MAX_SCOPES:
        raise ShareError("bad_scopes", tr("scopes 는 1~{n}개의 목록입니다", n=MAX_SCOPES), 400)
    out: list[str] = []
    for x in scopes:
        if not isinstance(x, str) or x not in S.CATALOG:
            raise ShareError("bad_scopes", tr("알 수 없는 scope 입니다"), 400)
        if x in UNREQUESTABLE and not (x == "pair.manage" and flags.enforced("pair")):
            # pair 기능이 켜진 서버에서만 에이전트가 `pair.manage` 를 요청할 수 있다(사람이 승인함에서 정한다). 자동 승인 구간은 여전히 못 덮는다.
            raise ShareError("scope_not_requestable", tr("이 scope 는 요청할 수 없습니다 — 사람이 직접 정하는 권한입니다"), 400, scope=x)
        if x not in out:
            out.append(x)
    cd = None
    if conds not in (None, {}):
        if not isinstance(conds, dict):
            raise ShareError("bad_conds", tr("conds 는 {scope: {조건: [값]}} 입니다"), 400)
        cd = {}
        for s, e in conds.items():
            if s not in out or not isinstance(e, dict) or any(k not in {c.key for c in S.COND_SPEC.get(s, ())} for k in e):
                raise ShareError("bad_conds", tr("conds 는 {scope: {조건: [값]}} 입니다"), 400)
            cd[s] = {k: ([str(v) for v in vals] if isinstance(vals, list) else [str(vals)]) for k, vals in e.items()}
    return out, cd


def _check_resource(resource) -> str:
    if not isinstance(resource, str) or not _RES_SAFE.fullmatch(resource) or not S.resource_ok(resource):
        raise ShareError("bad_resource", tr("알 수 없는 리소스입니다"), 400)
    return resource


def _check_ttl(ttl) -> Optional[int]:
    if ttl in (None, ""):
        return None
    try:
        t = int(ttl)
    except (TypeError, ValueError):
        raise ShareError("bad_ttl", tr("ttl 은 초 단위 정수입니다"), 400) from None
    if t < TTL_MIN or t > TTL_MAX:
        raise ShareError("bad_ttl", tr("ttl 은 {lo}~{hi}초입니다", lo=TTL_MIN, hi=TTL_MAX), 400)
    return t


def create_request(p: P.Principal, resource, scopes, conds=None, reason=None, ttl=None, *, snap=None, alive: Optional[set] = None,
                   now: Optional[float] = None) -> tuple[dict, bool]:
    """에이전트의 요청 하나 → (옛 `request_view` 모양이 아닌 승인함 항목, 새로 만들었는가). 같은 (주체, 리소스, scope 집합)은 하나로 합친다.

    판정: `authz.request` 가 허용되어야 하고(에이전트 기본), 한도(주체당 분당 6, 대기 20, 전체 200)와 거절 쿨다운(300초)을 지킨다."""
    now = time.time() if now is None else now
    if p.kind != P.AGENT or not p.attrs.get("key"):
        raise ShareError("request_not_allowed", tr("승인 요청을 만들 수 없는 호출자입니다"), 403)
    resource = _check_resource(resource)
    sc, cd = _check_scopes(scopes, conds)
    ttl_v = _check_ttl(ttl)
    if reason is not None and not isinstance(reason, str):
        raise ShareError("bad_reason", tr("reason 은 문자열입니다"), 400)
    note = " ".join((reason or "").split())[:REASON_MAX]
    from . import deleg_svc, legacy_gate
    legacy_gate.require_approval_available()              # 승인할 사람(웹 비밀번호)이 없으면 보류로 두지 않고 이유를 알린다
    lineage = deleg_svc.lineage_of(p.attrs["key"])         # 락 밖에서 — 장부를 읽는다
    key = p.attrs["key"]
    rk = _session_key(resource)
    sig = (resource, tuple(sorted(sc)), json_sig(cd))
    with store.txn() as docs:
        purge(docs, alive, now)
        d = engine.decide(p, "authz.request", "authz:*", {}, _snap(docs, now))
        if not d.allow:
            raise ShareError("request_not_allowed", tr("승인 요청을 만들 수 없는 호출자입니다"), 403)
        for r in _generic_reqs(docs):
            if r.get("subject") == p.id and (r.get("resource"), tuple(sorted(r.get("scopes") or ())), json_sig(r.get("sconds"))) == sig:
                if r.get("state") == "denied":
                    wait = int(DENY_COOLDOWN - (now - float(r.get("decided") or 0)))
                    raise ShareError("request_denied",
                                     tr("사용자가 거부했습니다 — {sec}초 뒤에 다시 요청할 수 있습니다", sec=max(wait, 1)),
                                     403, state="denied", retryAfter=max(wait, 1))
                return request_view(r), False
        hits = limits.recent(_rate, p.id, now)
        if len(hits) >= RATE_PER_MIN:
            _rate[p.id] = hits
            raise ShareError("rate_limited", tr("요청이 너무 잦습니다 — 잠시 뒤에 다시 하세요"), 429, retryAfter=60)
        pending = [a for a in docs["approvals"]["items"] if a.get("state") == "pending"
                   and str(a.get("origin", "")).startswith((PREFIX_REQ, "authz:msg:req:", "authz:peer:req:", "legacy:agent_grants:req:"))]
        mine = sum(1 for a in pending if a.get("subject") == p.id)
        if mine >= PER_PRINCIPAL or len(pending) >= TOTAL:
            raise ShareError("too_many_requests", tr("대기 중인 승인 요청이 너무 많습니다 — 사용자가 처리한 뒤 다시 하세요"), 429)
        rid = f"{REQ_ID_PREFIX}{uuid.uuid4().hex[:12]}"
        rec = _scoped({"id": f"ap-{rid}", "origin": f"{PREFIX_REQ}{rid}", "kind": "generic", "subject": p.id, "resource": resource,
                       "subject_session": key, "resource_session": rk, "subject_name": p.attrs.get("name") or "",
                       "state": "pending", "created": now, "expires": now + REQUEST_TTL,
                       "note": note, "ttl": ttl_v}, sc, cd)
        if lineage:
            rec["delegation"] = lineage
        _audit_strict({"principal": p.id, "action": "authz.request", "resource": resource, "scopes": sc, "decision": "pending",
                       "result": "pending", "approval_id": rec["id"], "sizes": {"chars": len(note)}, "sha8": audit.sha8(note)})
        docs["approvals"]["items"].append(rec)
        limits.record(_rate, p.id, hits, now)
        return request_view(rec), True


def json_sig(v) -> str:
    import json
    return json.dumps(v, sort_keys=True) if v else ""


def my_requests(p: P.Principal, now: Optional[float] = None) -> list[dict]:
    """내가 낸 요청(generic) — 상태와 남은 시간. 내용 없는 요약."""
    now = time.time() if now is None else now
    snap = store.snapshot(now)
    out = []
    for r in snap.approvals:
        if is_req(r) and r.get("subject") == p.id:
            out.append({"requestId": raw_id(r), "state": r.get("state"), "resource": r.get("resource"),
                        "scopes": _fmt_scopes(r.get("scopes") or ()), "created": r.get("created"),
                        "expires": r.get("expires")})
    return out


def my_grants(p: P.Principal, now: Optional[float] = None) -> list[dict]:
    """내 주체에 붙은 저장소 grant(기본 규칙은 빼고). 다른 주체의 것은 보이지 않는다."""
    now = time.time() if now is None else now
    snap = store.snapshot(now)
    out = []
    for g in snap.grants:
        if g.get("effect") == "deny" or g.get("state", "active") != "active":
            continue
        if not _subject_ok(str(g.get("subject", "")), p) or not engine._time_ok(g, now):
            continue
        out.append(grant_view(g, kind_of_grant(g)))
    return out


# --- 승인함 목록 --------------------------------------------------------------------------------------


def list_approvals(snap, *, names_alive=None) -> list[dict]:
    """네 종류의 보류 요청을 한 목록으로(만든 순). 앞의 셋은 옛 목록 함수를 그대로 부른다(만료·사라진 세션 정리 포함)."""
    import agent_share
    from . import host_grant_svc, msg_svc
    now = time.time()
    names = _names(snap)
    out: list[dict] = []
    recs = {raw_id_any(a): a for a in store.snapshot(now).approvals}
    state = agent_share.list_state(snap)
    for r in state["requests"]:
        if r.get("state") != "pending":
            continue
        rec = recs.get(r["id"], {})
        t = (snap or {}).get(f"{r['target']['sid']}:{r['target']['created']}")
        scopes = list(approvals.READ_SCOPES) + ([approvals.WRITE_SCOPE] if r["kind"] == agent_share.KIND_WRITE else [])
        sconds = approvals.WRITE_SCONDS if r["kind"] == agent_share.KIND_WRITE else None
        a = (snap or {}).get(f"{r['agent']['sid']}:{r['agent']['created']}")
        out.append(_item(kind=KIND_SHARE, rid=r["id"], rec={**r, "delegation": rec.get("delegation")},
                         subject={"kind": "agent", "id": f"agent:local/{r['agent']['sid']}:{r['agent']['created']}",
                                  "name": a.name if a else r["agent"].get("name", "")},
                         resource={"id": f"session:local/{r['target']['sid']}:{r['target']['created']}",
                                   "label": ((t.share or {}).get("nick") if t else None) or (t.name if t else "")},
                         scopes=scopes, sconds=sconds, extra={"access": r["kind"]}, now=now))
    if msg_svc.enforced():
        alive_names = names_alive() if names_alive else (None, {})
        for v in msg_svc.list_requests(alive_names[0], alive_names[1] or names):
            rec = recs.get(v["id"], {})
            out.append(_item(kind=KIND_MSG, rid=v["id"], rec={**v, "delegation": rec.get("delegation")},
                             subject={"kind": "agent", "id": rec.get("subject"), "name": v["agent"]["name"]},
                             resource={"id": rec.get("resource"), "label": v["target"]["name"]},
                             scopes=[msg_svc.SCOPE], sconds=None, now=now))
    if host_grant_svc.enforced():
        for v in host_grant_svc.list_requests():
            rec = recs.get(v["id"], {})
            sc, cd, res = host_grant_svc._requested(rec.get("change") or v.get("change") or "")
            out.append(_item(kind=KIND_PEER, rid=v["id"], rec=v,
                             subject={"kind": "local", "id": rec.get("subject"), "name": v["agent"]["name"]},
                             resource={"id": res, "label": v["target"]["nick"]},
                             scopes=sc, sconds=cd, extra={"change": v.get("change")}, now=now))
    alive = _alive(snap)
    gen = [request_view(r, names) for r in store.snapshot(now).approvals
           if is_req(r) and r.get("state") == "pending" and now < float(r.get("expires") or 0)
           and not (alive is not None and any(r.get(k) and r[k] not in alive for k in ("subject_session", "resource_session")))]
    out += gen
    from . import pairs
    out += pairs.pending_approvals(now)           # pair 가 꺼져 있으면 비어 있다
    out.sort(key=lambda r: r.get("created") or 0)
    return out


def raw_id_any(rec: dict) -> str:
    return str(rec.get("origin", "")).rsplit(":", 1)[-1]


# --- 권한 목록 ----------------------------------------------------------------------------------------


def kind_of_grant(g: dict) -> str:
    o = str(g.get("origin", ""))
    if o.startswith("legacy:agent_grants:"):
        return KIND_SHARE
    if o.startswith("authz:msg:grant:"):
        return KIND_MSG
    if o.startswith("legacy:hosts:"):
        return KIND_PEER
    if o.startswith(PREFIX_GRANT):
        return KIND_GENERIC
    if o.startswith("legacy:delegations:"):
        return "deleg"
    if o.startswith("pair:grant:"):
        return KIND_PAIR
    return "other"


def grant_view(g: dict, kind: str, names: Optional[dict] = None) -> dict:
    names = names or {}
    scopes = _fmt_scopes(S.caps_of(g))
    mu = (g.get("conds") or {}).get("max_uses")
    sk = g.get("subject_session")
    return {"id": g.get("id"), "kind": kind, "effect": g.get("effect") or "allow", "state": g.get("state") or "active",
            "subject": {"id": g.get("subject"), "name": names.get(sk or "") or g.get("subject_name") or ""},
            "resource": g.get("resource"), "scopes": scopes, "conds": _view_conds(g.get("sconds")),
            "preset": preset_of(list(g.get("scopes") or ()), g.get("sconds")),
            "approvedBy": g.get("approved_by"), "approvedVia": S.via_of(g), "created": g.get("created"),
            "expires": g.get("expires"), "uses": int(g.get("uses", 0)) if mu is not None else None,
            "maxUses": mu, "lineage": ({"delegation": g.get("delegation") or g.get("deleg")} if (g.get("delegation") or g.get("deleg")) else None)}


def list_grants(snap, *, subject: str = "", resource: str = "", kind: str = "") -> list[dict]:
    """모든 종류의 grant 를 한 목록으로. 필터: subject(주체 id·이름 접두/부분 일치), resource(접두), kind."""
    import agent_share
    import host_store
    from . import migrate
    now = time.time()
    names = _names(snap)
    st = store.snapshot(now)
    out: list[dict] = []
    # 세션 공유 — 플래그와 무관하게 옛 목록 함수가 진실을 안다(꺼짐: agent_grants.json, 켜짐: 엔진 저장소)
    recs = {g.get("id"): g for g in st.grants}
    for g in agent_share.list_state(snap, True)["grants"]:
        gid = f"ag-{g['id']}"
        rec = recs.get(gid) or {}
        scopes = list(approvals.READ_SCOPES) + ([approvals.WRITE_SCOPE] if g.get("write") else [])
        sconds = approvals.WRITE_SCONDS if g.get("write") else None
        a = (snap or {}).get(f"{g['agent']['sid']}:{g['agent']['created']}")
        out.append({"id": gid, "kind": KIND_SHARE, "effect": "allow", "state": "active",
                    "subject": {"id": f"agent:local/{g['agent']['sid']}:{g['agent']['created']}",
                                "name": a.name if a else g["agent"].get("name", "")},
                    "resource": f"session:local/{g['target']['sid']}:{g['target']['created']}", "scopes": _fmt_scopes(S.effective(scopes, sconds)),
                    "conds": _view_conds(sconds), "preset": preset_of(scopes, sconds),
                    "approvedBy": g.get("approvedBy") or rec.get("approved_by"),
                    "approvedVia": g.get("approvedVia") or (S.via_of(rec) if rec else None),
                    "created": g.get("granted"), "expires": None, "uses": None, "maxUses": None,
                    "lineage": ({"delegation": rec["delegation"]} if rec.get("delegation") else None)})
    # 호스트(peer) — 소유 저장소 hosts.json 에서 그 자리에서 번역한다(저장소 사본이 낡아도 맞게)
    copies = {g.get("origin"): g for g in st.grants}
    for hg in host_store.list_grants():
        for rec in migrate.translate_peer_grant(hg):
            cp = copies.get(rec["origin"]) or {}
            out.append(grant_view({**rec, "approved_by": cp.get("approved_by") or rec.get("approved_by"),
                                   "approved_via": cp.get("approved_via") or rec.get("approved_via")}, KIND_PEER, names))
    for g in st.grants:
        k = kind_of_grant(g)
        if k in (KIND_SHARE, KIND_PEER) or g.get("state", "active") != "active":     # 끝난(ended) 위임 grant 는 쓸 수 없다 — 목록에서 뺀다
            continue
        if k in (KIND_GENERIC,) and _grant_dead(g, _alive(snap), now):
            continue
        out.append(grant_view(g, k, names))

    def keep(v: dict) -> bool:
        if kind and v["kind"] != kind:
            return False
        if subject and subject not in str(v["subject"].get("id") or "") and subject.lower() not in str(v["subject"].get("name") or "").lower():
            return False
        if resource and not str(v.get("resource") or "").startswith(resource):
            return False
        return True

    out = [v for v in out if keep(v)]
    out.sort(key=lambda v: (v["kind"], -(v.get("created") or 0)))
    return out


# --- 결정 (generic · 한정 선택지) -----------------------------------------------------------------------


def check_choice(decision, choice, scopes, ttl) -> tuple[str, Optional[str], Optional[list], Optional[int]]:
    """본문 → (decision, choice, scopes, ttl). `decision` 은 allow|deny|later, 선택지는 once|session|deny."""
    if decision is not None and decision not in DECISIONS:
        raise ShareError("bad_decision", tr("decision 은 allow, deny, later 중 하나입니다"), 400)
    if choice is not None and choice not in CHOICES:
        raise ShareError("bad_choice", tr("choice 는 once, session, deny 중 하나입니다"), 400)
    if decision is None:
        if choice is None:
            raise ShareError("bad_decision", tr("decision 이 필요합니다"), 400)
        decision = "deny" if choice == "deny" else "allow"
    if decision == "allow" and choice == "deny":
        raise ShareError("bad_choice", tr("허용하면서 choice 를 deny 로 할 수 없습니다"), 400)
    if decision == "deny" and choice in ("once", "session"):
        raise ShareError("bad_choice", tr("거절하면서 choice 를 {c} 로 할 수 없습니다", c=choice), 400)
    if decision == "allow" and choice is None:
        choice = "session"
    if scopes is not None and (not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes) or not scopes):
        raise ShareError("bad_scopes", tr("scopes 는 문자열 목록입니다"), 400)
    ttl_v = _check_ttl(ttl)
    return decision, choice, scopes, ttl_v


def _narrow(want_scopes: list[str], want_conds: Optional[dict], given: Optional[list]) -> tuple[list[str], Optional[dict]]:
    """승인자가 요청보다 **좁게**만 줄 수 있다. 주어진 scope 가 요청의 부분집합이어야 하고 조건은 요청의 것을 따른다."""
    if not given:
        return want_scopes, want_conds
    if not set(given) <= set(want_scopes):
        raise ShareError("wider_than_requested", tr("요청보다 넓은 권한은 줄 수 없습니다"), 400)
    cd = {s: v for s, v in (want_conds or {}).items() if s in given} or None
    return [s for s in want_scopes if s in given], cd


def _make_grant(*, req: dict, scopes: list[str], sconds: Optional[dict], choice: str, ttl: Optional[int], approved_by: str,
                now: float, resource: Optional[str] = None, subject: Optional[str] = None) -> dict:
    gid = f"{GRANT_ID_PREFIX}{uuid.uuid4().hex[:12]}"
    sk, rk = req.get("subject_session"), req.get("resource_session")
    exp = now + ttl if ttl else None
    if choice == "session" and exp is None and not (sk or rk):
        exp = now + DEFAULT_SESSIONLESS_TTL          # 세션에 묶이지 않으면 닫을 끈이 TTL 뿐이다
    rec = _scoped({"id": gid, "origin": f"{PREFIX_GRANT}{gid[len(GRANT_ID_PREFIX):]}", "kind": "generic",
                   "subject": subject or req.get("subject"), "resource": resource or req.get("resource"),
                   "subject_session": sk, "resource_session": rk, "subject_name": req.get("subject_name") or "",
                   "state": "active", "approved_by": approved_by, "approved_via": S.VIA_MANUAL, "expires": exp,
                   "conds": ({"max_uses": 1} if choice == "once" else {}), "created": now, "updated": now}, scopes, sconds)
    if choice == "once":
        rec["uses"] = 0
    if req.get("delegation"):
        rec["delegation"] = req["delegation"]
    return rec


def decide_generic(request_id: str, decision: str, choice: Optional[str], scopes: Optional[list], ttl: Optional[int],
                   actor: Optional[P.Principal], alive: Optional[set]) -> dict:
    """generic 요청의 결정(선착순·멱등). 결과 `{request, grant, grantId, escalation, replayed?}`."""
    now = time.time()
    rid = request_id[3:] if request_id.startswith("ap-") else request_id
    with store.txn() as docs:
        purge(docs, alive, now)
        req = next((r for r in _generic_reqs(docs) if raw_id(r) == rid and r.get("state") == "pending"), None)
        if req is None:
            again = approvals.replay(docs, SOURCE, rid, decision, None, actor, now)
            if again is not None:
                return again
            raise ShareError("not_found", tr("대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)"), 404)
        want_sc = list(req.get("scopes") or ())
        want_cd = req.get("sconds")
        if decision == "deny":
            approvals.check_authority(actor, (), req["resource"], None, _snap(docs, now))
            denied = {**req, "state": "denied", "decided": now, "cooldown_until": now + DENY_COOLDOWN}
            _audit_strict({"principal": (actor or P.human("-")).id, "action": "authz.decide", "resource": req["resource"],
                           "scopes": want_sc, "decision": "deny", "result": "denied", "approval_id": req["id"],
                           "reason": "kind=generic"})
            approvals._replace(docs["approvals"]["items"], req, denied)
            out = {"request": request_view(denied), "grant": None, "grantId": None, "approvalId": denied["id"], "escalation": []}
            docs["approvals"]["items"].append(approvals.resolved_record(SOURCE, rid, req, "deny", None, now, out, ()))
            return out
        sc, cd = _narrow(want_sc, want_cd, scopes)
        approvals.check_authority(actor, sc, req["resource"], cd, _snap(docs, now))
        eff_ttl = ttl if ttl is not None else req.get("ttl")
        if ttl is not None and req.get("ttl") is not None and ttl > int(req["ttl"]):
            raise ShareError("wider_than_requested", tr("요청한 기간보다 길게는 줄 수 없습니다"), 400)
        approver = (actor or P.human("-")).id
        grant = _make_grant(req=req, scopes=sc, sconds=cd, choice=choice or "session", ttl=eff_ttl, approved_by=approver, now=now)
        _audit_strict({"principal": approver, "action": "authz.decide", "resource": req["resource"], "scopes": sc,
                       "decision": "allow", "result": "allowed", "approval_id": req["id"], "grant_id": grant["id"],
                       "reason": f"kind=generic choice={choice or 'session'}"})
        docs["grants"]["items"].append(grant)
        docs["approvals"]["items"] = [a for a in docs["approvals"]["items"] if a is not req]
        out = {"request": request_view({**req, "state": "allowed"}), "grant": grant_view(grant, KIND_GENERIC),
               "grantId": grant["id"], "approvalId": req["id"], "escalation": ["new_grant"]}
        docs["approvals"]["items"].append(approvals.resolved_record(SOURCE, rid, req, "allow", choice, now, out, sc, cd))
        return out


def decide_share_limited(request_id: str, choice: str, scopes: Optional[list], ttl: Optional[int], snap,
                         actor: Optional[P.Principal]) -> dict:
    """`share` 요청에 한 번만(once)/기간(ttl)으로 허용. 옛 공유 grant 는 조건을 담지 못해(`agent_grants.json` 투영) 별도 generic grant 로 낸다.
    `share` 집행이 켜져 있을 때만(집행 지점이 그 grant 를 읽고 `uses` 를 올린다) — 호출자(라우트)가 확인한다."""
    import agent_share
    from . import share_svc
    now = time.time()
    rid = request_id[3:] if request_id.startswith("ap-") else request_id
    with share_svc._txn(snap) as docs:
        req = next((r for r in approvals.agent_requests(docs) if approvals.raw_id(r) == rid and r.get("state") == "pending"), None)
        if req is None:
            again = approvals.replay(docs, approvals.SOURCE, rid, "allow", None, actor, now)
            if again is not None:
                return again
            raise ShareError("not_found", tr("대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)"), 404)
        kind = approvals.kind_of(req)
        want_sc = list(approvals.READ_SCOPES) + ([approvals.WRITE_SCOPE] if kind == agent_share.KIND_WRITE else [])
        want_cd = approvals.WRITE_SCONDS if kind == agent_share.KIND_WRITE else None
        sc, cd = _narrow(want_sc, want_cd, scopes)
        if not set(approvals.READ_SCOPES) <= set(sc):
            raise ShareError("bad_scopes", tr("공유 요청에는 읽기 scope 가 필요합니다"), 400)
        resource = f"session:local/{req['resource_session']}"
        tgt = (snap or {}).get(req["resource_session"])
        if approvals.WRITE_SCOPE in sc and snap is not None and tgt is not None and (tgt.share or {}).get("mode") != approvals.LEVEL_READWRITE:
            raise ShareError("read_only_share", tr("이 공유는 읽기 전용입니다 — 공유 모드를 읽기·쓰기로 올린 뒤 허용하세요"), 409)
        approvals.check_authority(actor, sc, resource, cd, approvals._snap(docs, now))
        approver = (actor or P.human("-")).id
        grant = _make_grant(req=req, scopes=sc, sconds=cd, choice=choice, ttl=ttl, approved_by=approver, now=now,
                            resource=resource, subject=req["subject"])
        if req.get("delegation"):
            grant["delegation"] = req["delegation"]
        share_svc._audit_change("decide", "allowed", actor, resource, "allow", scopes=sc, grant_id=grant["id"],
                                approval_id=req["id"], reason=f"choice={choice}",
                                legacy={"action": "decide", "result": "allowed", "by": "user", "kind": kind, "request": approvals.raw_id(req),
                                        "grant": grant["id"], "level": "readwrite" if approvals.WRITE_SCOPE in sc else "read"})
        docs["grants"]["items"].append(grant)
        docs["approvals"]["items"] = [a for a in docs["approvals"]["items"] if a is not req]
        out = {"request": approvals.request_to_legacy(req), "grant": grant_view(grant, KIND_SHARE), "grantId": grant["id"],
               "approvalId": req["id"], "escalation": ["new_grant"], "resource": resource, "kind": kind, "scopes": sc}
        docs["approvals"]["items"].append(approvals.resolved_record(approvals.SOURCE, rid, req, "allow", choice, now, out, sc, cd))
        return out


def consume_use(docs: dict, grant_id: Optional[str]) -> None:
    """허용된 판정이 `max_uses` 가 있는 generic grant 를 썼으면 `uses` 를 하나 올린다(`share_svc.authorize` 의 트랜잭션 안에서)."""
    if not grant_id or not str(grant_id).startswith(GRANT_ID_PREFIX):
        return
    for g in docs["grants"]["items"]:
        if g.get("id") == grant_id and (g.get("conds") or {}).get("max_uses") is not None:
            g["uses"] = int(g.get("uses", 0)) + 1
            return


# --- 회수 ---------------------------------------------------------------------------------------------


def revoke_generic(grant_id: str, scopes: Optional[list], actor: Optional[P.Principal], alive: Optional[set]) -> dict:
    """generic grant 회수. `scopes` 가 있으면 그것만 거두고(남는 게 없으면 통째로), 없으면 통째로."""
    now = time.time()
    with store.txn() as docs:
        g = next((x for x in _generic_grants(docs) if x["id"] == grant_id or raw_id(x) == grant_id), None)
        if g is None:
            raise ShareError("not_found", tr("그런 권한이 없습니다"), 404)
        remain = None
        if scopes:
            keep = [s for s in g.get("scopes") or () if s not in set(scopes)]
            if keep:
                remain = {**g, "scopes": keep, "updated": now}
                if g.get("sconds"):
                    remain["sconds"] = {s: v for s, v in g["sconds"].items() if s in keep}
        _audit_strict({"principal": (actor or P.human("-")).id, "action": "authz.revoke", "resource": g.get("resource"),
                       "scopes": scopes or list(g.get("scopes") or ()), "decision": "allow", "result": "ok", "grant_id": g["id"],
                       "reason": "kind=generic"})
        approvals._replace(docs["grants"]["items"], g, remain)
        return grant_view(g, KIND_GENERIC)


def _share_scope(scopes: Optional[list]) -> str:
    """`scopes` 쿼리 → 옛 철회 범위. 입력 scope 만 거두면 `write`(읽기는 남긴다), 그 밖은 통째로 `all`."""
    if scopes and set(scopes) <= {approvals.WRITE_SCOPE}:
        return "write"
    return "all"


def revoke(grant_id: str, scopes: Optional[list], snap, actor: Optional[P.Principal], names_alive=None) -> dict:
    """grant 종류에 맞는 **기존 회수 경로**로 거둔다 → `{kind, id, grant}`."""
    import agent_share
    import delegation
    from . import host_grant_svc, msg_svc
    if owns_grant(grant_id):
        g = revoke_generic(grant_id, scopes, actor, _alive(snap))
        return {"kind": KIND_GENERIC, "id": g["id"], "grant": g}
    if msg_svc.enforced() and msg_svc.owns_grant(grant_id):
        names = (names_alive()[1] if names_alive else {}) or _names(snap)
        g = msg_svc.revoke(grant_id, actor, names)
        return {"kind": KIND_MSG, "id": g["id"], "grant": g}
    if grant_id[:3] in ("pg-", "pm-"):
        peer = grant_id[3:]
        r = host_grant_svc.change(actor or P.human("-"), peer, "deny-control" if grant_id.startswith("pg-") else "deny-agent")
        return {"kind": KIND_PEER, "id": grant_id, "grant": {"id": grant_id, "peer": peer, "state": r.get("state")}}
    if grant_id.startswith("pp-"):
        from . import pairs
        r = pairs.revoke_by_grant(grant_id, actor)
        return {"kind": KIND_PAIR, "id": grant_id, "grant": {"id": grant_id, "pair": r["pair"]}}
    if grant_id[:3] in ("dc-", "dr-", "dw-", "di-"):
        did = grant_id[3:].split("-", 1)[0] if grant_id.startswith("di-") else grant_id[3:]
        delegation.close(did, reason="closed_by_user", actor=actor)
        return {"kind": "deleg", "id": grant_id, "grant": {"id": grant_id, "delegation": did}}
    legacy = agent_share.revoke(grant_id[3:] if grant_id.startswith("ag-") else grant_id, _share_scope(scopes), snap, actor)
    return {"kind": KIND_SHARE, "id": f"ag-{legacy['id']}", "grant": legacy}


# --- 비상 정지 -----------------------------------------------------------------------------------------


def panic(snap, actor: Optional[P.Principal], names_alive=None) -> dict:
    """사람이 아닌 주체에게 준 권한과 보류 요청을 전부 거둔다. 사람의 기본 권한·공유 정책·기기 로그인은 건드리지 않는다.
    종류별로 **기존 회수 경로**를 그대로 쓴다(각자 감사). 호스트(peer)는 view 로 내리고(등록은 유지), 위임은 닫는다."""
    import agent_share
    import delegation
    from . import host_grant_svc, msg_svc
    done = {"share": 0, "msg": 0, "peer": 0, "deleg": 0, "generic": 0, "requests": 0}
    from . import pairs as _pairs
    if _pairs.enabled():
        done["pair"] = 0
    failed: list[dict] = []
    now = time.time()

    def attempt(kind: str, fn) -> None:
        try:
            fn()
            done[kind] += 1
        except ShareError as e:
            failed.append({"kind": kind, "error": e.code})
        except Exception as e:  # noqa: BLE001 — 하나가 실패해도 나머지는 계속 거둔다
            failed.append({"kind": kind, "error": type(e).__name__})

    for g in agent_share.list_state(snap)["grants"]:
        attempt("share", lambda g=g: agent_share.revoke(g["id"], "all", snap, actor))
    st = store.snapshot(now)
    for g in list(st.grants):
        k = kind_of_grant(g)
        if k == KIND_MSG and msg_svc.enforced():
            attempt("msg", lambda g=g: msg_svc.revoke(g["id"], actor, _names(snap)))
        elif k == KIND_GENERIC:
            attempt("generic", lambda g=g: revoke_generic(g["id"], None, actor, None))
    import host_store
    for hg in host_store.list_grants():
        if hg.get("level") == "control":
            attempt("peer", lambda hg=hg: host_grant_svc.change(actor or P.human("-"), hg["id"], "deny-control"))
        if hg.get("agentMessages"):
            attempt("peer", lambda hg=hg: host_grant_svc.change(actor or P.human("-"), hg["id"], "deny-agent"))
    try:
        for it in delegation.list_all():
            if it.get("state") in delegation.OPEN_STATES:
                attempt("deleg", lambda it=it: delegation.close(it["id"], reason="closed_by_user", actor=actor))
    except Exception as e:  # noqa: BLE001
        failed.append({"kind": "deleg", "error": type(e).__name__})
    # 보류 요청 — 결정이 아니라 비운다(쿨다운 없음). 공유 요청은 대상 세션 단위의 옛 정리 경로를 쓴다.
    targets = {f"{r['target']['sid']}:{r['target']['created']}" for r in agent_share.list_state(snap)["requests"]}
    for tk in targets:
        t = (snap or {}).get(tk)
        if t is not None:
            attempt("requests", lambda t=t: agent_share.drop_target(t, snap))
    cleared = _clear_requests(actor, snap)
    done["requests"] += cleared
    from . import pairs
    if pairs.enabled():
        attempt("pair", lambda: pairs.end_all(actor, "panic"))
    _audit_strict({"principal": (actor or P.human("-")).id, "action": "authz.panic", "resource": "authz:*", "scopes": [],
                   "decision": "allow", "result": "ok", "detail": {"counts": done, "failed": len(failed)}})
    return {"ok": not failed, "revoked": done, "failed": failed}


def _clear_requests(actor, snap) -> int:
    """msg·peer·generic 의 보류 요청을 비운다(결정 기록 없이)."""
    n = 0
    now = time.time()
    with store.txn() as docs:
        keep, gone = [], 0
        for a in docs["approvals"]["items"]:
            o = str(a.get("origin", ""))
            if a.get("state") in ("pending", "denied") and o.startswith((PREFIX_REQ, "authz:msg:req:", "authz:peer:req:")):
                gone += 1
                continue
            keep.append(a)
        docs["approvals"]["items"] = keep
        n = gone
    return n
