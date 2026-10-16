"""브라우저 broker — 에이전트가 **격리된 브라우저 탭**을 열고 조작하는 길. 마스터 스위치 `VT_AUTHZ_ENFORCE` 의 `browser`(기본 꺼짐).

모든 조작은 같은 순서를 지난다(어느 단계든 거부하면 어댑터에 닿지 않는다 — fail closed):

    판정(엔진) → 소유(R9.2) → 호스트 임대·세대(N-28)·탭 상태(F7) → 한도 → 입력 검증 → URL 정책(R9.3) → 감사 → 어댑터 → 행동 뒤 URL 재검사(N-27) → 값 가림(R9.5)

  - **판정**: `browser`(ops = open·navigate·read·input·close) · `browser.js`(eval) · `browser.sensitive`(ops = cookies·storage·network·download·attach). 리소스는
    `browser:local/new`(열기·목록)과 `browser:local/tab/<id>`(탭). 엔진 예외·저장소 손상은 거부(503 `authz_unavailable`).
  - **소유(R9.2)**: 탭은 연 주체의 것이다. 소유자가 아닌 주체는 그 탭을 **이름으로 가리킨 grant**(`browser:local/tab/<id>` 또는 `browser:tab:<id>`)나 같은 pair 의 grant 가 있어야
    조작한다. 사람·터미널(fsh)은 관리자라서 모든 탭을 보고 닫을 수 있다(조작은 못 한다). broker 가 열지 않은 탭(사용자 프로필 탭)에 붙는 `attach` 는 이 빌드에서
    구현하지 않는다 — 판정을 통과해도 501 `unsupported_profile`.
  - **URL(R9.3)**: `policy.py`. 허용 목록(`url_allowlist`)은 이 주체의 browser grant 들에서 모은다. 어댑터가 네트워크 계층에서 redirect hop·서브프레임·하위 리소스를 막고,
    broker 는 이동·행동(클릭·입력·eval) 뒤에 모든 프레임 URL 을 다시 검사해 위반이면 탭을 `about:blank` 로 돌린다(N-27).
  - **요소 참조**: `snapshot` 이 `e1`… 번호를 주고, `click`·`type` 은 그 번호만 받는다(어댑터 고유 selector 는 밖으로 나가지 않는다).
  - **값 가림(R9.5)**: 쿠키·storage 값은 `values=true` + grant 조건 `values`(엔진). network 의 민감 헤더·64KB 초과 body 는 언제나 가림. 값은 감사에 남지 않는다.
  - **한도**: 탭당 60/분, `browser.js` 10/분, 이동 30/분. 탭은 주체당 4개·전체 12개, 놀고 있는 탭은 60분 뒤 닫는다.
  - **F7**: 어댑터가 내려가면(`AdapterDown`) 그 어댑터의 열린 탭이 `orphaned`(소유 유지)가 되고 조작은 거부된다. 닫기만 된다.
  - **회수(N-15)**: grant·pair·주체가 회수되면 saga 의 `browser_tabs` 단계가 탭을 닫지 않고 **소유권만** 푼다. 사람에게 넘긴 탭(`handoff`)은 구분된다.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from typing import Callable, Optional

from i18n import tr

from authz import audit, engine, flags, store
from authz import principals as P
from authz import scopes as S
from authz.errors import ShareError

from . import adapters as A
from . import config, policy, tabs
from .adapters.base import AdapterDown, AdapterError

logger = logging.getLogger(__name__)

MAX_TABS_PER_OWNER = 4
MAX_TABS_TOTAL = 12
IDLE_SECONDS = 3600.0
RES_NEW = "browser:local/new"
SNAPSHOT_TEXT_MAX = 20000

# 조작 이름 -> (scope, 조건 op). `None` 이면 조건 없음.
OPSPEC: dict[str, tuple[str, Optional[str]]] = {
    "navigate": ("browser", "navigate"),
    "snapshot": ("browser", "read"),
    "screenshot": ("browser", "read"),
    "click": ("browser", "input"),
    "type": ("browser", "input"),
    "eval": ("browser.js", None),
    "cookies": ("browser.sensitive", "cookies"),
    "storage": ("browser.sensitive", "storage"),
    "network": ("browser.sensitive", "network"),
}
ACTIONS = tuple(OPSPEC)
RELAY_FORBIDDEN = frozenset({"eval", "cookies", "storage", "network"})     # 값·스크립트는 peer 링크를 넘지 않는다(R9.5, R8.7)
POST_CHECK = frozenset({"navigate", "click", "type", "eval"})
READ_AUDIT_EVERY = 600.0


class BrowserError(ShareError):
    pass


def _err(code: str, reason: str, status: int, **extra) -> BrowserError:
    return BrowserError(code, reason, status, **extra)


# --- 스위치 · 시작 -------------------------------------------------------------------------------


def enabled() -> bool:
    return flags.enforced("browser")


_limits = policy.TabLimits()
_rt: dict[str, dict] = {}                      # 탭 id -> {refs, allowlist, lock}  (메모리 — 재시작하면 탭이 orphaned 이므로 복원하지 않는다)
_rt_guard = threading.Lock()
_started = False
_read_audited: dict[tuple, float] = {}


def exposed_ports() -> set:
    """FarShell 이 노출한 localhost 포트(터널 레지스트리) — 공개하면 위험한 포트(원격 디버깅 등)는 뺀다. 못 읽으면 빈 집합(= 더 좁게). 시험이 이 함수를 바꿔 끼운다."""
    try:
        import tunnel_registry
        import port_share_store
        return {p for p in tunnel_registry.exposed_ports() if port_share_store.danger_reason(p) is None}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"노출 포트를 읽지 못했다: {e}")
        return set()


def startup() -> None:
    """서버 시작 때 — 호스트 임대를 잡고(세대 +1, 옛 탭 orphaned) 회수 saga 에 탭 단계를 끼운다. 실패해도 서버는 뜬다(조작은 임대가 없어 거부)."""
    global _started
    try:
        tabs.acquire_lease()
        register_saga_step()
        _started = True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"브라우저 broker 시작 실패 — 조작은 거부됩니다: {type(e).__name__}: {e}")


def ensure_started() -> None:
    if tabs.current_lease() is None:
        startup()
    if tabs.current_lease() is None:
        raise _err("no_lease", tr("브라우저 호스트 임대를 잡지 못했습니다"), 503)


def shutdown() -> None:
    global _started
    from authz import saga
    saga.unregister("browser_tabs")
    A.shutdown_all()
    tabs.drop_lease()
    _started = False
    with _rt_guard:
        _rt.clear()


def reset_for_tests() -> None:
    global _started
    from authz import saga
    saga.unregister("browser_tabs")
    _limits.__init__()                      # type: ignore[misc]
    with _rt_guard:
        _rt.clear()
    _read_audited.clear()
    _started = False


# --- 판정 ---------------------------------------------------------------------------------------


def _fresh() -> store.Snapshot:
    docs = {n: store._read(n) for n in ("grants", "approvals", "windows", "shares")}
    return store.Snapshot(grants=docs["grants"][0]["items"], approvals=docs["approvals"][0]["items"], windows=docs["windows"][0]["items"],
                          shares=docs["shares"][0]["items"], corrupt=frozenset(n for n, (_d, bad) in docs.items() if bad),
                          now=time.time(), source="store")


def _unavailable(e: Optional[BaseException] = None) -> BrowserError:
    if e is not None:
        logger.warning(f"브라우저 권한 엔진을 쓸 수 없다 — 거부: {type(e).__name__}: {e}")
    return _err("authz_unavailable", tr("권한 엔진을 쓸 수 없어 요청을 거부했습니다"), 503)


def _decide(p: P.Principal, scope: str, resources: tuple, ctx: dict, snap: store.Snapshot) -> tuple[engine.Decision, str]:
    last: Optional[engine.Decision] = None
    best: Optional[engine.Decision] = None            # 거부 사유는 «grant 없음» 보다 구체적인 첫 것(예: cond_values)을 보인다
    for res in resources:
        try:
            last = engine.decide(p, scope, res, ctx, snap)
        except Exception as e:  # noqa: BLE001 — 엔진 예외 = 닫힘
            raise _unavailable(e) from e
        if last.reason == "store_corrupt":
            raise _unavailable()
        if last.allow:
            return last, res
        if best is None and last.reason != "no_grant":
            best = last
    return best or last or engine.Decision(False, "no_grant"), resources[0]


def _grant_of(snap: store.Snapshot, gid: Optional[str]) -> Optional[dict]:
    return next((g for g in snap.grants if gid and g.get("id") == gid), None)


def _audit(p: P.Principal, action: str, resource: str, scopes: list, decision: str, result: str, reason: str = "", *,
           grant_id: Optional[str] = None, must: bool = False, **detail) -> None:
    """감사 한 줄(내용 없음 — `audit.scrub` 가 값·텍스트·URL query 를 버린다). `must` 면 실패 시 BrowserError(503) — js·민감 조작·설정 변경은 기록 없이 실행하지 않는다."""
    rec = {"principal": p.short(), "action": action, "resource": resource, "scopes": scopes, "decision": decision, "result": result,
           "reason": reason, "grant_id": grant_id, "detail": {k: v for k, v in detail.items() if v is not None}}
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        if must:
            logger.warning(f"브라우저 감사 기록 실패 — 거부: {type(e).__name__}: {e}")
            raise _err("audit_unavailable", tr("감사 기록을 쓸 수 없어 요청을 거부했습니다"), 503) from e
        logger.debug(f"브라우저 감사 기록 실패: {e}")


def is_manager(p: P.Principal) -> bool:
    """사람(로그인 세션)과 터미널의 fsh — 어댑터를 켜고 모든 탭을 보고 닫는다. 에이전트·기계 토큰·peer 는 아니다."""
    return p.kind == P.HUMAN or P.is_cli(p)


def _forbidden(scope: str, resource: str, op: Optional[str], why: str) -> BrowserError:
    need: dict = {"resource": resource, "scopes": [scope]}
    if op:
        need["ops"] = [op]
    return _err("forbidden", tr("이 브라우저 조작을 할 권한이 없습니다 — {scope} 권한이 필요합니다(request_access 로 요청)", scope=scope), 403, need=need, why=why)


def _allowlist_for(p: P.Principal, resource: str, snap: store.Snapshot) -> list:
    """이 주체의 browser grant 들이 허용한 URL 목록의 합(`conds.url_allowlist`). 열기·이동 op 를 가진 grant 만 센다."""
    out: list = []
    for g in snap.grants:
        if g.get("effect") == "deny" or g.get("state", "active") != "active":
            continue
        if g.get("pair_id") and not flags.enforced("pair"):
            continue
        if not engine._subject_matches(str(g.get("subject", "")), p) or not S.resource_matches(str(g.get("resource", "")), resource):
            continue
        if not engine._time_ok(g, snap.now):
            continue
        entry = S.caps_of(g).get("browser")
        if entry is None or not ({"open", "navigate"} & set(entry.get("ops", ()))):
            continue
        out += [str(x) for x in ((g.get("conds") or {}).get("url_allowlist") or []) if isinstance(x, str)]
    return list(dict.fromkeys(out))


# --- 탭 런타임 ----------------------------------------------------------------------------------


def _runtime(tab_id: str) -> dict:
    with _rt_guard:
        return _rt.setdefault(tab_id, {"refs": {}, "allowlist": [], "lock": threading.RLock()})


def _guard_for(tab_id: str) -> Callable[[str, str], Optional[str]]:
    """어댑터가 요청을 가로챌 때 부르는 판정 — 최상위 문서·redirect hop·서브프레임은 R9.3 전체, 하위 리소스는 위험 대역만. 막으면 사유를 돌려준다."""
    def guard(url: str, kind: str) -> Optional[str]:
        rt = _runtime(tab_id)
        if kind == "subresource":
            d = policy.check_subresource(url, allowlist=rt["allowlist"], exposed_ports=exposed_ports())
        else:
            d = policy.check_url(url, allowlist=rt["allowlist"], exposed_ports=exposed_ports(), top_level=(kind == "document"))
        return None if d.allow else d.reason
    return guard


def _adapter_for(name: str):
    if not A.known(name):
        raise _err("unknown_adapter", tr("모르는 어댑터입니다: {name}", name=name), 404)
    return A.get(name)


def _require_adapter(name: str):
    if not config.is_enabled(name):
        raise _err("adapter_disabled", tr("이 어댑터는 꺼져 있습니다: {name}", name=name), 409)
    a = _adapter_for(name)
    d = A.describe(name)
    if not d.get("available"):
        raise _err("adapter_unavailable", tr("이 어댑터를 쓸 수 없습니다: {reason}", reason=d.get("reason") or ""), 409, adapterReason=d.get("reason"))
    return a


def _live_adapter(name: str):
    """이미 열린 탭을 조작할 때의 어댑터 — 꺼져 있으면 409, 내려가 있으면 그 어댑터의 열린 탭을 orphaned 로 돌리고 503(F7)."""
    if not config.is_enabled(name):
        raise _err("adapter_disabled", tr("이 어댑터는 꺼져 있습니다: {name}", name=name), 409)
    ad = _adapter_for(name)
    try:
        ok = ad.alive()
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        _orphan(name, "adapter_down")
        raise _err("adapter_down", tr("브라우저 어댑터에 닿지 못합니다 — 탭은 orphaned 로 표시했습니다"), 503, adapterReason="adapter_down")
    return ad


def _orphan(adapter: str, reason: str) -> None:
    try:
        n = tabs.mark_orphaned(adapter, reason)
        if n:
            logger.warning(f"브라우저 어댑터 {adapter} 이(가) 내려가 탭 {len(n)}개를 orphaned 로 표시했습니다({reason})")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"탭 orphaned 표시 실패: {e}")


def _call(adapter_name: str, fn: Callable, *a, **kw):
    """어댑터 호출 — 내려갔으면 탭을 orphaned 로 돌리고 503, 그 밖의 실패는 502 로 번역한다."""
    try:
        return fn(*a, **kw)
    except AdapterDown as e:
        _orphan(adapter_name, e.reason)
        raise _err("adapter_down", tr("브라우저 어댑터에 닿지 못합니다 — 탭은 orphaned 로 표시했습니다"), 503, adapterReason=e.reason) from e
    except AdapterError as e:
        raise _err(e.code, tr("브라우저 조작에 실패했습니다: {reason}", reason=e.reason[:200]), 502) from e


# --- 어댑터 · 설정 ------------------------------------------------------------------------------


def adapters_view() -> dict:
    cfg = config.load()
    rows = []
    for n in A.names():
        d = A.describe(n)
        rows.append({**d, "enabled": bool(cfg["adapters"].get(n)) and bool(d.get("available"))})
    return {"enabled": True, "consented": cfg["consent"] is not None, "configError": cfg["error"], "adapters": rows,
            "consentText": None if cfg["consent"] is not None else config.consent_text()}


def list_adapters(p: P.Principal) -> dict:
    ensure_started()
    if not is_manager(p):
        snap = _fresh()
        d, _r = _decide(p, "browser", (RES_NEW,), {"op": "read"}, snap)
        if not d.allow:
            _audit(p, "browser.adapters", RES_NEW, ["browser"], "deny", "denied", d.reason)
            raise _forbidden("browser", RES_NEW, "read", d.reason)
    return adapters_view()


def set_adapter(p: P.Principal, name: str, body: dict) -> dict:
    """어댑터를 켜거나 끈다(사람·터미널만). 처음 켤 때는 `consent=true` 가 있어야 한다. `profile=user` 인 어댑터는 켤 수 없다(미구현)."""
    if not is_manager(p):
        _audit(p, "browser.enable", f"browser:adapter/{name}", ["browser"], "deny", "denied", "manager_required")
        raise _err("forbidden", tr("어댑터는 사람(또는 터미널의 fsh)만 켜고 끌 수 있습니다"), 403)
    if not A.known(name):
        raise _err("unknown_adapter", tr("모르는 어댑터입니다: {name}", name=name), 404)
    want = body.get("enabled", True)
    if not isinstance(want, bool):
        raise _err("bad_request", tr("enabled 는 true/false 입니다"), 400)
    d = A.describe(name)
    if want:
        if d.get("reason") == "profile_user_unsupported":
            raise _err("unsupported_profile", tr("사용자 프로필(profile=user) 어댑터는 아직 지원하지 않습니다"), 501)
        if not d.get("available"):
            raise _err("adapter_unavailable", tr("이 어댑터를 쓸 수 없습니다: {reason}", reason=d.get("reason") or ""), 409, adapterReason=d.get("reason"))
        if config.load()["consent"] is None and body.get("consent") is not True:
            raise _err("consent_required", config.consent_text(), 409, consentText=config.consent_text())
    _audit(p, "browser.enable", f"browser:adapter/{name}", ["browser"], "allow", "ok", "on" if want else "off", must=True)
    try:
        config.set_enabled(name, want, consent=body.get("consent") is True)
    except ValueError as e:
        raise _err("consent_required", config.consent_text(), 409, consentText=config.consent_text()) from e
    except OSError as e:
        raise _err("config_unwritable", tr("브라우저 설정을 쓸 수 없습니다: {e}", e=str(e)[:120]), 503) from e
    if not want:
        _orphan(name, "adapter_disabled")
        try:
            A.get(name).shutdown()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"어댑터 종료 실패: {e}")
    return adapters_view()


# --- 열기 · 목록 · 닫기 ---------------------------------------------------------------------------


def _pick_adapter(requested: Optional[str]) -> str:
    cfg = config.load()["adapters"]
    if requested:
        return requested
    for n in A.names():
        if cfg.get(n) and A.describe(n).get("available"):
            return n
    raise _err("no_adapter_enabled", tr("켜진 브라우저 어댑터가 없습니다 — 사람이 설정에서(또는 fsh browser enable) 먼저 켜야 합니다"), 409)


def _check_url(raw, rt: dict, *, top_level: bool = True) -> policy.Decision:
    d = policy.check_url(raw, allowlist=rt["allowlist"], exposed_ports=exposed_ports(), top_level=top_level)
    return d


def _url_denied(d: policy.Decision, p: P.Principal, action: str, resource: str, raw) -> BrowserError:
    _audit(p, action, resource, ["browser"], "deny", "url_blocked", d.reason, url=policy.audit_url(raw))
    return _err("url_blocked", tr("이 주소는 열 수 없습니다: {reason}", reason=d.reason), 403, urlReason=d.reason)


def _reap(now: Optional[float] = None) -> None:
    """놀고 있는 탭(열림·풀림)을 닫는다 — 사람이 이어받은 탭·orphaned 는 건드리지 않는다."""
    t = time.time() if now is None else now
    for rec in tabs.list_all():
        if rec.get("state") in (tabs.OPEN, tabs.RELEASED) and not rec.get("humanTaken") and t - float(rec.get("lastUsed") or 0) > IDLE_SECONDS:
            try:
                _close_record(rec, quiet=True)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"idle 탭 닫기 실패: {e}")


def open_tab(p: P.Principal, body: dict, *, via_relay: bool = False) -> dict:
    ensure_started()
    if body.get("attach") not in (None, "", False):
        return _attach(p, body)
    if body.get("profile") not in (None, "", "isolated"):
        raise _err("unsupported_profile", tr("사용자 프로필(profile=user)은 아직 지원하지 않습니다 — 격리된 탭만 열 수 있습니다"), 501)
    url = body.get("url")
    if url is not None and not isinstance(url, str):
        raise _err("bad_request", tr("url 은 문자열입니다"), 400)
    snap = _fresh()
    d, _r = _decide(p, "browser", (RES_NEW,), {"op": "open"}, snap)
    if not d.allow:
        _audit(p, "browser.open", RES_NEW, ["browser"], "deny", "denied", d.reason)
        raise _forbidden("browser", RES_NEW, "open", d.reason)
    want_nav = bool(url) and url != policy.BLANK
    if want_nav:
        dn, _r = _decide(p, "browser", (RES_NEW,), {"op": "navigate"}, snap)
        if not dn.allow:
            _audit(p, "browser.open", RES_NEW, ["browser"], "deny", "denied", dn.reason)
            raise _forbidden("browser", RES_NEW, "navigate", dn.reason)
    rt_probe = {"allowlist": _allowlist_for(p, RES_NEW, snap)}
    if want_nav:
        pd = _check_url(url, rt_probe)
        if not pd.allow:
            raise _url_denied(pd, p, "browser.open", RES_NEW, url)
    _reap()
    mine = sum(1 for t in tabs.list_all() if t.get("owner") == p.id)
    if mine >= MAX_TABS_PER_OWNER or len(tabs.list_all()) >= MAX_TABS_TOTAL:
        raise _err("too_many_tabs", tr("열 수 있는 탭 수를 넘었습니다 — 쓰지 않는 탭을 닫으세요"), 429)
    name = _pick_adapter(body.get("adapter") if isinstance(body.get("adapter"), str) else None)
    ad = _require_adapter(name)
    tab_id = tabs.new_id()
    rt = _runtime(tab_id)
    rt["allowlist"] = rt_probe["allowlist"]
    try:
        info = _call(name, ad.open, url=None, guard=_guard_for(tab_id))
    except BaseException:
        with _rt_guard:
            _rt.pop(tab_id, None)
        raise
    g = _grant_of(snap, d.grant_id) or {}
    rec = tabs.create(adapter=name, ref=info["ref"], owner=p.id, owner_name=str(p.attrs.get("name") or ""), pair=g.get("pair_id"), tab_id=tab_id,
                      grant=d.grant_id)
    _audit(p, "browser.open", f"browser:local/tab/{tab_id}", ["browser"], "allow", "ok", grant_id=d.grant_id, adapter=name)
    out = tabs.view(rec)
    if want_nav:
        try:
            out["navigate"] = op(p, tab_id, "navigate", {"url": url}, via_relay=via_relay)
        except BrowserError:
            _close_record(rec)                # 첫 이동이 막히면 빈 탭을 남기지 않는다
            raise
    return out


def _attach(p: P.Principal, body: dict) -> dict:
    """broker 가 열지 않은 탭(사용자 브라우저)에 붙기 — `browser.sensitive`(ops∋attach) 판정을 거치고, 통과해도 구현하지 않았다(profile=user, 후속 과제)."""
    snap = _fresh()
    res = RES_NEW
    d, _r = _decide(p, "browser.sensitive", (res,), {"op": "attach"}, snap)
    if not d.allow:
        _audit(p, "browser.attach", res, ["browser.sensitive"], "deny", "denied", d.reason)
        raise _forbidden("browser.sensitive", res, "attach", d.reason)
    _audit(p, "browser.attach", res, ["browser.sensitive"], "allow", "unsupported", "profile_user_unsupported", grant_id=d.grant_id, must=True)
    raise _err("unsupported_profile", tr("사용자 프로필 탭에 붙는 기능은 아직 지원하지 않습니다"), 501)


def reconcile() -> None:
    """어댑터가 아는 탭과 장부를 맞춘다 — 어댑터가 응답하지 않으면 그 어댑터의 열린 탭을 orphaned 로, 사라진 탭도 orphaned 로(F7)."""
    by: dict[str, set] = {}
    for t in tabs.list_all():
        if t.get("state") == tabs.OPEN:
            by.setdefault(t["adapter"], set()).add(t["ref"])
    for name, refs in by.items():
        try:
            live = {x["ref"] for x in A.get(name).list()}
        except AdapterDown as e:
            _orphan(name, e.reason)
            continue
        except Exception as e:  # noqa: BLE001
            _orphan(name, f"list_failed:{type(e).__name__}")
            continue
        gone = refs - live
        if gone:
            tabs.mark_orphaned(name, "tab_gone", refs=live)


def list_tabs(p: P.Principal) -> dict:
    ensure_started()
    if not is_manager(p):
        snap = _fresh()
        d, _r = _decide(p, "browser", (RES_NEW,), {"op": "read"}, snap)
        if not d.allow:
            _audit(p, "browser.tabs", RES_NEW, ["browser"], "deny", "denied", d.reason)
            raise _forbidden("browser", RES_NEW, "read", d.reason)
    reconcile()
    rows = tabs.list_all()
    if not is_manager(p):
        rows = [t for t in rows if t.get("owner") == p.id]
    return {"tabs": [tabs.view(t) for t in rows]}


def _close_record(rec: dict, *, quiet: bool = False) -> None:
    name = rec["adapter"]
    try:
        A.get(name).close(rec["ref"])
    except AdapterDown as e:
        _orphan(name, e.reason)
    except Exception as e:  # noqa: BLE001 — 어댑터가 모르는 탭이어도 장부는 지운다
        if not quiet:
            logger.debug(f"탭 닫기 실패(장부는 지움): {type(e).__name__}")
    tabs.delete(rec["id"])
    _limits.forget(rec["id"])
    with _rt_guard:
        _rt.pop(rec["id"], None)


def _owner_ok(p: P.Principal, rec: dict, d: engine.Decision, resource_alt: tuple, snap: store.Snapshot) -> bool:
    """소유(R9.2): 소유자이거나, 이 탭을 이름으로 가리킨 grant·같은 pair 의 grant 로 허용됐을 때."""
    if rec.get("owner") and rec["owner"] == p.id:
        return True
    g = _grant_of(snap, d.grant_id) or {}
    if str(g.get("resource", "")) in resource_alt:
        return True
    return bool(rec.get("pair") and g.get("pair_id") == rec.get("pair"))


def close_tab(p: P.Principal, tab_id: str) -> dict:
    ensure_started()
    rec = tabs.get(tab_id)
    if rec is None:
        raise _err("not_found", tr("그런 탭이 없습니다"), 404)
    res = (f"browser:local/tab/{tab_id}", f"browser:tab:{tab_id}")
    snap = _fresh()
    if is_manager(p):
        pass                                # 사람·터미널은 어느 탭이든 닫는다(관리)
    else:
        d, _r = _decide(p, "browser", res, {"op": "close"}, snap)
        if not d.allow:
            _audit(p, "browser.close", res[0], ["browser"], "deny", "denied", d.reason)
            raise _forbidden("browser", res[0], "close", d.reason)
        if not _owner_ok(p, rec, d, res, snap):
            _audit(p, "browser.close", res[0], ["browser"], "deny", "denied", "not_owner")
            raise _forbidden("browser", res[0], "close", "not_owner")
    _audit(p, "browser.close", res[0], ["browser"], "allow", "ok")
    _close_record(rec)
    return {"closed": tab_id}


def handoff(p: P.Principal, tab_id: str) -> dict:
    """소유자가 탭을 사람에게 넘긴다 — 소유권이 풀리고 `humanTaken` 으로 구분된다. 탭은 닫히지 않는다."""
    ensure_started()
    rec = tabs.get(tab_id)
    res = f"browser:local/tab/{tab_id}"
    if rec is None or not rec.get("owner") or rec.get("owner") != p.id:
        _audit(p, "browser.handoff", res, ["browser"], "deny", "denied", "not_owner")
        raise _err("forbidden", tr("탭의 소유자만 넘길 수 있습니다"), 403, why="not_owner")
    snap = _fresh()
    d, _r = _decide(p, "browser", (res, f"browser:tab:{tab_id}"), {"op": "read"}, snap)
    if not d.allow:
        _audit(p, "browser.handoff", res, ["browser"], "deny", "denied", d.reason)
        raise _forbidden("browser", res, "read", d.reason)
    out = tabs.handoff(tab_id)
    _audit(p, "browser.handoff", res, ["browser"], "allow", "ok", grant_id=d.grant_id)
    return tabs.view(out or rec)


# --- 조작 ---------------------------------------------------------------------------------------


def _text_arg(body: dict, key: str, limit: int, *, required: bool = True) -> str:
    v = body.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str) or (required and not v):
        raise _err("bad_request", tr("{key} 는 문자열입니다", key=key), 400)
    if len(v) > limit:
        raise _err("too_large", tr("{key} 가 너무 깁니다(최대 {n}자)", key=key, n=limit), 413)
    return v


def _resolve_ref(rt: dict, body: dict) -> object:
    ref = body.get("ref")
    if not isinstance(ref, str) or not ref:
        raise _err("bad_request", tr("ref 는 snapshot 이 준 요소 번호(예: e3)입니다"), 400)
    native = rt["refs"].get(ref)
    if native is None:
        raise _err("unknown_ref", tr("알 수 없는 요소 번호입니다 — snapshot 을 다시 찍으세요"), 400)
    return native


def op(p: P.Principal, tab_id: str, name: str, body: Optional[dict] = None, *, via_relay: bool = False):
    """탭 조작 한 번. 파이프라인은 모듈 머리말. 반환은 JSON 으로 내보낼 dict 이거나(스크린샷은 `{png: base64}`)."""
    body = body if isinstance(body, dict) else {}
    if name not in OPSPEC:
        raise _err("unknown_op", tr("모르는 조작입니다: {name}", name=name), 404)
    ensure_started()
    scope, cop = OPSPEC[name]
    rec = tabs.get(tab_id)
    if rec is None:
        raise _err("not_found", tr("그런 탭이 없습니다"), 404)
    res = (f"browser:local/tab/{tab_id}", f"browser:tab:{tab_id}")
    values = bool(body.get("values")) if name in ("cookies", "storage") else False
    if via_relay and name in RELAY_FORBIDDEN:
        _audit(p, f"browser.{name}", res[0], [scope], "deny", "denied", "relay_forbidden")
        raise _err("relay_forbidden", tr("이 조작은 다른 맥을 거쳐 할 수 없습니다"), 403)
    snap = _fresh()
    ctx: dict = {}
    if cop:
        ctx["op"] = cop
    if values:
        ctx["values"] = True
    d, _r = _decide(p, scope, res, ctx, snap)
    if not d.allow:
        _audit(p, f"browser.{name}", res[0], [scope], "deny", "denied", d.reason)
        raise _forbidden(scope, res[0], cop, d.reason)
    if not _owner_ok(p, rec, d, res, snap):
        _audit(p, f"browser.{name}", res[0], [scope], "deny", "denied", "not_owner")
        raise _forbidden(scope, res[0], cop, "not_owner")
    problem = tabs.lease_problem(rec)
    if problem or rec.get("state") == tabs.ORPHANED:             # F7·N-28: 어댑터·호스트가 사라졌거나 임대·세대가 다르면 조작은 닫힌다(소유는 그대로)
        why = problem or rec.get("orphanReason") or "orphaned"
        _audit(p, f"browser.{name}", res[0], [scope], "deny", "fail_closed", f"tab_{why}")
        raise _err("tab_orphaned", tr("이 탭은 조작할 수 없습니다({why}) — 닫고 새로 여세요", why=why), 503, tabState=rec.get("state"))
    if rec.get("state") != tabs.OPEN:                            # 소유권이 풀린 탭(회수·handoff)은 아무도 조작하지 못한다
        _audit(p, f"browser.{name}", res[0], [scope], "deny", "fail_closed", f"tab_{rec.get('state')}")
        raise _err("tab_not_open", tr("이 탭은 소유권이 풀려 조작할 수 없습니다"), 409, tabState=rec.get("state"))
    kind = "js" if scope == "browser.js" else ("navigate" if name == "navigate" else "other")
    over = _limits.hit(tab_id, kind)
    if over:
        _audit(p, f"browser.{name}", res[0], [scope], "deny", "rate_limited", over)
        raise _err("rate_limited", tr("이 탭의 요청이 너무 많습니다({kind}) — 잠시 뒤에 다시 하세요", kind=over), 429, limit=over)

    rt = _runtime(tab_id)
    with rt["lock"]:
        rt["allowlist"] = _allowlist_for(p, res[0], snap)         # 호출자의 grant 가 허용한 URL — 회수·축소가 곧바로 어댑터의 가로채기에도 반영된다
        # 입력 검증 + URL 사전 검사
        arg: dict = {}
        if name == "navigate":
            arg["url"] = _text_arg(body, "url", policy.MAX_URL)
            pd = _check_url(arg["url"], rt)
            if not pd.allow:
                raise _url_denied(pd, p, "browser.navigate", res[0], arg["url"])
        elif name == "type":
            arg["text"] = _text_arg(body, "text", policy.MAX_TYPE_TEXT)
            arg["submit"] = bool(body.get("submit"))
            arg["native"] = _resolve_ref(rt, body)
        elif name == "click":
            arg["native"] = _resolve_ref(rt, body)
        elif name == "eval":
            arg["script"] = _text_arg(body, "script", policy.MAX_SCRIPT)
        adapter_name = rec["adapter"]
        ad = _live_adapter(adapter_name)
        sizes = {"chars": len(arg.get("text", "") or arg.get("script", ""))} if ("text" in arg or "script" in arg) else None
        must = scope in ("browser.js", "browser.sensitive")
        if must or name in ("navigate", "click", "type"):
            _audit(p, f"browser.{name}", res[0], [scope], "allow", "ok", grant_id=d.grant_id, must=must, sizes=sizes, withValues=values or None,
                   url=policy.audit_url(arg["url"]) if "url" in arg else None)
        else:
            _read_audit(p, name, res[0], scope, d.grant_id)

        out = _run(p, rec, ad, name, arg, rt, values)
        if name in POST_CHECK:
            _post_check(p, rec, ad, rt, res[0])
        _limits_touch(tab_id)
        return out


def _read_audit(p: P.Principal, name: str, resource: str, scope: str, grant_id) -> None:
    """읽기(snapshot·screenshot)는 (주체, 탭)당 10분에 한 번만 감사한다(R11.3)."""
    key = (p.id, resource)
    now = time.monotonic()
    if now - _read_audited.get(key, -1e9) < READ_AUDIT_EVERY:
        return
    if len(_read_audited) > 1024:
        _read_audited.clear()
    _read_audited[key] = now
    _audit(p, f"browser.{name}", resource, [scope], "allow", "ok", grant_id=grant_id)


def _limits_touch(tab_id: str) -> None:
    try:
        tabs.touch(tab_id)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"탭 lastUsed 갱신 실패: {e}")


def _run(p: P.Principal, rec: dict, ad, name: str, arg: dict, rt: dict, values: bool):
    ref, adn = rec["ref"], rec["adapter"]
    if name == "navigate":
        r = _call(adn, ad.navigate, ref, arg["url"])
        rt["refs"].clear()
        if r.get("blocked"):
            _audit(p, "browser.navigate", f"browser:local/tab/{rec['id']}", ["browser"], "deny", "url_blocked", str(r["blocked"]), url=policy.audit_url(arg["url"]))
            _safe_blank(adn, ad, ref)
            raise _err("url_blocked", tr("이동 중 막힌 주소가 있었습니다: {reason}", reason=r["blocked"]), 403, urlReason=r["blocked"])
        return {"url": r.get("url"), "title": r.get("title")}
    if name == "snapshot":
        r = _call(adn, ad.snapshot, ref)
        rt["refs"] = {e["ref"]: e["native"] for e in r.get("elements", []) if e.get("native") is not None}
        els = [{k: v for k, v in e.items() if k != "native"} for e in r.get("elements", [])]
        return {"url": r.get("url"), "title": r.get("title"), "text": str(r.get("text", ""))[:SNAPSHOT_TEXT_MAX], "elements": els}
    if name == "screenshot":
        png = _call(adn, ad.screenshot, ref)
        return {"png": base64.b64encode(png).decode("ascii"), "bytes": len(png)}
    if name == "click":
        _call(adn, ad.click, ref, arg["native"])
        return {"ok": True}
    if name == "type":
        _call(adn, ad.type, ref, arg["native"], arg["text"], arg["submit"])
        return {"ok": True}
    if name == "eval":
        v = _call(adn, ad.eval, ref, arg["script"])
        v, truncated = policy.cap_result(v)
        return {"result": v, "truncated": truncated}
    if name == "cookies":
        return {"cookies": policy.redact_cookies(_call(adn, ad.cookies, ref, values), values), "values": values}
    if name == "storage":
        return {"storage": policy.redact_storage(_call(adn, ad.storage, ref, values), values), "values": values}
    if name == "network":
        return {"requests": policy.redact_network(_call(adn, ad.network, ref))}
    raise _err("unknown_op", name, 404)


def _safe_blank(adn: str, ad, ref: str) -> None:
    try:
        ad.blank(ref)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"탭을 about:blank 로 돌리지 못했다({adn}): {type(e).__name__}")


def _post_check(p: P.Principal, rec: dict, ad, rt: dict, resource: str) -> None:
    """행동 뒤(N-27): 가라앉기를 기다린 뒤 최상위·서브프레임 URL 을 전부 다시 본다. 위반이면 탭을 `about:blank` 로 돌리고 막는다."""
    adn, ref = rec["adapter"], rec["ref"]
    _call(adn, ad.settle, ref)
    blocked = _call(adn, ad.pop_blocked, ref)
    if blocked:                            # 행동이 일으킨 이동·프레임을 어댑터가 네트워크 계층에서 이미 막았다 — 에이전트에게 알린다
        _audit(p, "browser.post_check", resource, ["browser"], "deny", "url_blocked", str(blocked[0]))
        raise _err("url_blocked", tr("행동이 연 주소가 정책에 어긋나 막혔습니다: {reason}", reason=blocked[0]), 403, urlReason=blocked[0])
    urls = _call(adn, ad.frame_urls, ref)
    for i, u in enumerate(urls):
        d = policy.check_url(u, allowlist=rt["allowlist"], exposed_ports=exposed_ports(), top_level=(i == 0))
        if not d.allow:
            _safe_blank(adn, ad, ref)
            rt["refs"].clear()
            _audit(p, "browser.post_check", resource, ["browser"], "deny", "url_blocked", d.reason, url=policy.audit_url(u), frame=i)
            raise _err("url_blocked", tr("행동 뒤 열린 주소가 정책에 어긋나 탭을 about:blank 로 돌렸습니다: {reason}", reason=d.reason), 403, urlReason=d.reason)


# --- 회수 saga (N-15) ---------------------------------------------------------------------------


def _release_match(t: dict) -> Callable[[dict], bool]:
    gids = set(t.get("grant_ids") or ())

    def match(rec: dict) -> bool:
        owner = str(rec.get("owner") or "")
        return bool((gids and rec.get("grant") in gids)
                    or (t.get("key") and owner == f"agent:local/{t['key']}")
                    or (t.get("subject") and owner == t["subject"])
                    or (t.get("pair") and rec.get("pair") == t["pair"])
                    or (t.get("all") and owner and not owner.startswith("human:")))
    return match


def saga_release(t: dict) -> None:
    """회수 saga 단계 — 대상의 탭을 **닫지 않고** 소유권만 푼다(멱등). 풀린 뒤에도 남아 있으면 SagaIncomplete 로 saga 가 다시 돈다."""
    from authz import saga
    m = _release_match(t)
    tabs.release_where(m, "revoked")
    left = [r for r in tabs.list_all() if r.get("owner") and not r.get("humanTaken") and m(r)]
    if left:
        raise saga.SagaIncomplete(f"browser_tabs={len(left)}")


def register_saga_step() -> None:
    from authz import saga
    if "browser_tabs" not in saga.step_names():
        saga.register("browser_tabs", saga_release, before="verify")
