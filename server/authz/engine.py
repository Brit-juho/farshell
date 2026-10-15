"""판정 — `decide(principal, scope, resource, ctx, snap) -> Decision` (10-policy.md A4).

    allow = authn(p) ∧ ¬killswitch(s, r) ∧
            (∃ g ∈ defaults(p) ∪ grants : subj ∋ p ∧ res ∋ r ∧ s ∈ closure(g.scopes) ∧
             not_before ≤ now < expires ∧ state = active ∧ conds(g, ctx) ∧ liveness(g)) ∧
            s ∈ ceiling(r) ∧ ¬∃ deny ∧ rate_ok

**순수 함수다.** 파일·tmux·시계를 읽지 않는다 — 필요한 사실은 전부 `snap`(저장소 snapshot,
`now` 포함)과 `ctx`(판정 지점이 아는 것: 공유 모드, 살아 있는 세션 키, 위임 카운터 …)로 받는다.
같은 입력이면 같은 답이라 shadow 비교·`--verify` 재생·테스트가 모두 이 성질에 기댄다.

ctx 키(전부 선택):
  share        세션 리소스의 실시간 공유 표시 `{nick, mode}` 또는 None(미공유)
  share_known  share 를 판정 지점이 실제로 읽었는가(False 면 저장소 값만 본다)
  prefer_live  공유 표시가 저장소와 다를 때 실시간 값을 쓴다(shadow 기본) / False 면 낮은 쪽
  alive        살아 있는 세션 키 집합. None 이면 tmux 를 못 읽은 것 → liveness 통과(F3·위험 4)
  deleg        `{enabled, is_child, open_children, recent_spawns}` — deleg.spawn 조건
  safe_mode    VT_SAFE_MODE 가 켜져 있는가 — deleg.spawn killswitch(3b: 판정 지점이 켠 때만 넣는다)
  op           판정 지점이 하려는 동작 이름(`close` …) — grant 의 `conds.ops` 와 비교
  fs           `{purpose, in_roots}` — `conds.fs_roots`(fsguard 루트 안인가; 3b 위임 cwd, 5단계에서 lease 로 대체)
  counts       `{scope: 이미 센 횟수}` — rate 비교(limits.exceeded)
  limits       `{scope: 한도}` — counts 와 비교할 한도를 표(limits.LIMITS) 대신 지정(선택)
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Optional

from . import ceilings, defaults, limits
from . import principals as P
from . import scopes as S
from .store import Snapshot

# F1 — 저장소가 손상되면 이 주체 종류는 fail closed(human·svc 기본은 동작).
FAIL_CLOSED_KINDS = frozenset({P.AGENT, P.PEER, P.PAIR, P.CLI, P.HOOK})


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    grant_id: Optional[str] = None
    rule: Optional[str] = None

    def as_dict(self) -> dict:
        return {"allow": self.allow, "reason": self.reason, "grant_id": self.grant_id, "rule": self.rule}


def _deny(reason: str, rule: Optional[str] = None) -> Decision:
    return Decision(False, reason, None, rule)


def _subject_matches(sel: str, p: P.Principal) -> bool:
    if not sel:
        return False
    return any(sel == i or fnmatch.fnmatchcase(i, sel) for i in p.subject_ids())


def _session_key(resource: str) -> Optional[str]:
    if resource.startswith("session:local/"):
        return resource[len("session:local/"):]
    return None


def _live(g: dict, ctx: dict) -> bool:
    """liveness — grant 에 묶인 세션 키가 살아 있는가. tmux 를 못 읽었으면(alive=None) 산 것으로 본다."""
    alive = ctx.get("alive")
    if alive is None:
        return True
    for k in (g.get("subject_session"), g.get("resource_session")):
        if k and k not in alive:
            return False
    return True


def _conds_ok(g: dict, ctx: dict) -> tuple[bool, str]:
    conds = g.get("conds") or {}
    if conds.get("deleg_quota"):
        d = ctx.get("deleg")
        if not isinstance(d, dict):
            return False, "deleg_ctx_missing"
        if d.get("is_child"):
            return False, "depth_limit"
        if int(d.get("open_children", 0)) >= limits.DELEG_MAX_OPEN:
            return False, "child_limit"
        if limits.exceeded("deleg.spawn", int(d.get("recent_spawns", 0)), limits.DELEG_SPAWN_PER_MIN):
            return False, "rate_limited"
    if "ops" in conds and ctx.get("op") not in conds["ops"]:
        return False, "cond_op"
    if conds.get("fs_roots"):
        f = ctx.get("fs")
        if not isinstance(f, dict) or f.get("purpose") not in conds["fs_roots"]:
            return False, "fs_ctx_missing"
        if not f.get("in_roots"):
            return False, "outside_roots"
    if "host" in conds and conds["host"] != ctx.get("host", P.LOCAL_HOST):
        return False, "cond_host"
    if "max_lines" in conds and int(ctx.get("lines", 0)) > int(conds["max_lines"]):
        return False, "cond_max_lines"
    if "path_glob" in conds:
        path = ctx.get("path")
        if not path or not any(fnmatch.fnmatchcase(path, gl) for gl in conds["path_glob"]):
            return False, "cond_path_glob"
    if "requires_human_present" in conds and conds["requires_human_present"] and not ctx.get("human_present"):
        return False, "cond_human_present"
    if "max_uses" in conds and int(g.get("uses", 0)) >= int(conds["max_uses"]):
        return False, "cond_max_uses"
    return True, ""


def _killswitch(scope: str, ctx: dict) -> Optional[str]:
    if scope == "deleg.spawn":
        d = ctx.get("deleg") or {}
        if d.get("enabled") is False:
            return "spawn_disabled"
        if ctx.get("safe_mode"):
            return "safe_mode"
    if ctx.get("agents_off") and scope.split(".")[0] in ("session", "msg", "deleg"):
        return "agents_off"
    return None


def _time_ok(g: dict, now: float) -> bool:
    nb, ex = g.get("not_before"), g.get("expires")
    if nb is not None and now < float(nb):
        return False
    if ex is not None and now >= float(ex):
        return False
    return True


def decide(p: P.Principal, scope: str, resource: str, ctx: Optional[dict] = None,
           snap: Optional[Snapshot] = None) -> Decision:
    ctx = ctx or {}
    snap = snap or Snapshot(source="empty")
    if not S.known(scope):
        return _deny("unknown_scope")
    if not S.resource_ok(resource):
        return _deny("unknown_resource")
    if not p.authenticated:
        return _deny("ambiguous_principal" if p.attrs.get("ambiguous") else "unauthenticated")
    ks = _killswitch(scope, ctx)
    if ks:
        return _deny(ks, "killswitch")

    store_bad = bool(snap.corrupt) and p.kind in FAIL_CLOSED_KINDS
    candidates = defaults.rules_for(p) + ([] if store_bad else list(snap.grants))
    hit: Optional[dict] = None
    cond_fail = ""
    for g in candidates:
        if g.get("effect") == "deny" or g.get("state", "active") != "active":
            continue
        if not _subject_matches(str(g.get("subject", "")), p):
            continue
        if not S.resource_matches(str(g.get("resource", "")), resource):
            continue
        if scope not in S.closure(g.get("scopes") or ()):
            continue
        if not _time_ok(g, snap.now):
            continue
        if not _live(g, ctx):
            continue
        ok, why = _conds_ok(g, ctx)
        if not ok:
            cond_fail = cond_fail or why
            continue
        hit = g
        break
    if hit is None:
        if store_bad:
            return _deny("store_corrupt", "F1")
        return _deny(cond_fail or "no_grant", "conds" if cond_fail else None)

    if ceilings.applies(p, scope, resource):
        key = _session_key(resource)
        stored = (snap.shares or {}).get(key) if key else None
        if ctx.get("share_known", "share" in ctx):
            level = ceilings.resolve_share(ctx.get("share"), stored, prefer_live=ctx.get("prefer_live", True))
        else:
            level = ceilings.level_of(stored)
        if not ceilings.allows(scope, level):
            return Decision(False, "ceiling", hit.get("id"), "ceiling")

    for d in snap.deny_rules():
        if _subject_matches(str(d.get("subject", "")), p) and S.resource_matches(str(d.get("resource", "")), resource) \
                and scope in S.closure(d.get("scopes") or ()) and _time_ok(d, snap.now):
            return Decision(False, "denied", d.get("id"), "deny")

    counts = ctx.get("counts") or {}
    if scope in counts:
        # `limits` 는 판정 지점이 덮어쓰는 한도(옛 모듈의 조정 가능한 상수를 따라가려는 것). 없으면 표의 값.
        lim = (ctx.get("limits") or {}).get(scope)
        if (lim is not None or scope in limits.LIMITS) and limits.exceeded(scope, int(counts[scope]), lim):
            return Decision(False, "rate_limited", hit.get("id"), "rate")
    return Decision(True, "ok", hit.get("id"), "default" if str(hit.get("id", "")).startswith("default:") else "grant")


# --- 자동 승인 window (R4.5) ---------------------------------------------------------------

WINDOW_DEFAULT_MIN = 30
WINDOW_MAX_MIN_DEFAULT = 240


def window_max_seconds(env: Optional[dict] = None) -> int:
    import os
    raw = (env if env is not None else os.environ).get("VT_AUTHZ_WINDOW_MAX_MIN", "")
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = WINDOW_MAX_MIN_DEFAULT
    return max(1, min(v, WINDOW_MAX_MIN_DEFAULT)) * 60


def window_covers(w: dict, p: P.Principal, resource: str, scopes, ttl: float, now: float) -> tuple[bool, str]:
    """window 가 이 요청을 자동 승인할 수 있는가. (가능, 이유)."""
    want = set(scopes)
    if want & S.NEVER_WINDOW:
        return False, "window_forbidden_scope"       # 무엇과도 상관없이 window 로는 절대 불가
    if w.get("state", "active") != "active":
        return False, "window_inactive"
    if now >= float(w.get("expires", 0)):
        return False, "window_expired"
    if not _subject_matches(str(w.get("subject", "")), p):
        return False, "window_subject"
    if not any(S.resource_matches(r, resource) for r in (w.get("resources") or ())):
        return False, "window_resource"
    covers = list(w.get("covers") or ())
    if not want <= S.closure(covers):
        return False, "window_scope"
    # R4.3 scope 는 covers 에 **이름으로** 적혀야 한다(와일드카드 불인정).
    named = {c for c in covers if not any(ch in c for ch in "*?[")}
    need = S.requires_elevated(p.kind, want, resource)
    if not {n for n in need if n in S.CATALOG} <= named:
        return False, "window_elevated_scope"
    if float(ttl) > float(w.get("max_ttl_per_grant", 0)):
        return False, "window_ttl"
    return True, "window"


def validate_window(w: dict, *, elevated: bool, now: float, env: Optional[dict] = None) -> list[str]:
    """window 생성 요청 검증(R4.5) — 오류 목록."""
    errs = []
    if not elevated:
        errs.append("elevated_required")
    exp = float(w.get("expires", 0))
    if exp <= now:
        errs.append("expires_past")
    if exp - now > window_max_seconds(env):
        errs.append("too_long")
    if set(w.get("covers") or ()) & S.NEVER_WINDOW or any(c in ("*", "authz.*") for c in w.get("covers") or ()):
        errs.append("forbidden_scope")
    return errs
