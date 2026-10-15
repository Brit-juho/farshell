"""shadow 판정 — 기존 판정 직후 같은 질문을 엔진에 던지고, 다르면 기록만 한다(1단계).

    shadow_check(old_allow, site, principal, scope, resource, ctx=None, legacy=None)

- **절대 예외를 던지지 않는다**(F8). 엔진·저장소·기록 어디서 실패해도 경고 로그 한 줄(속도 제한)과
  shadow.jsonl 의 `error` 줄만 남기고 None 을 돌려준다. 호출부는 반환값을 쓰지 않는다 —
  응답은 언제나 기존 판정이 정한다.
- 기록: `~/.vt/authz/shadow.jsonl`(0600). 불일치·오류만. 내용(입력·화면·본문)은 싣지 않는다 —
  주체 id·scope·리소스·두 판정·이유뿐이다(R11.2 와 같은 규율).
- 크기·속도 제한: 같은 (지점, 주체, scope, 리소스, 판정 쌍)은 60초에 한 번만 쓰고 나머지는 세기만 한다.
  전체로는 분당 120줄, 파일은 5MB 에서 `.1` 한 세대로 돈다.

판정 입력(snapshot)의 출처 — `VT_AUTHZ_SHADOW_SOURCE`:
  live(기본)  저장소의 비-legacy grant + 판정 지점이 쥔 옛 레코드를 그 자리에서 번역(migrate.live_grants).
              1단계에서 옛 코드는 옛 저장소에만 쓰므로 이렇게 해야 이전 뒤 생긴 grant 가 잡음이 안 된다.
  store       이전된 저장소만(`fsh migrate-auth --verify` 재생용). 공유 등급은 낮은 쪽.
끄기: `VT_AUTHZ_SHADOW=0`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional, Union

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
LINES_PER_MIN = 120
DEDUP_SEC = 60.0
LOG_EVERY_SEC = 60.0


def enabled() -> bool:
    return os.environ.get("VT_AUTHZ_SHADOW", "1").strip() not in ("0", "false", "off", "no")


def source() -> str:
    s = os.environ.get("VT_AUTHZ_SHADOW_SOURCE", "live").strip().lower()
    return s if s in ("live", "store") else "live"


def path() -> Path:
    from . import store
    return store.root() / "shadow.jsonl"


class _Stats:
    """프로세스 안 카운터 + 기록 속도 제한. 재시작하면 초기화(진단용)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.checks = 0
        self.mismatches = 0
        self.errors = 0
        self.suppressed = 0
        self.by_site: dict[str, dict] = {}
        self._recent: dict[tuple, float] = {}
        self._lines: deque = deque()
        self._last_log = 0.0

    def admit(self, key: tuple, now: float) -> bool:
        with self.lock:
            while self._lines and now - self._lines[0] >= 60.0:
                self._lines.popleft()
            last = self._recent.get(key)
            if (last is not None and now - last < DEDUP_SEC) or len(self._lines) >= LINES_PER_MIN:
                self.suppressed += 1
                return False
            if len(self._recent) > 4096:
                self._recent = {k: t for k, t in self._recent.items() if now - t < DEDUP_SEC}
            self._recent[key] = now
            self._lines.append(now)
            return True

    def site(self, name: str, field: str) -> None:
        with self.lock:
            d = self.by_site.setdefault(name, {"checks": 0, "mismatches": 0, "errors": 0})
            d[field] += 1

    def should_log(self, now: float) -> bool:
        with self.lock:
            if now - self._last_log >= LOG_EVERY_SEC:
                self._last_log = now
                return True
            return False

    def as_dict(self) -> dict:
        with self.lock:
            return {"checks": self.checks, "mismatches": self.mismatches, "errors": self.errors,
                    "suppressed": self.suppressed, "bySite": {k: dict(v) for k, v in self.by_site.items()}}


STATS = _Stats()


def _write(rec: dict) -> None:
    from . import store
    p = path()
    store._ensure_dir(p.parent)
    line = (json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n").encode()
    try:
        if p.stat().st_size + len(line) > MAX_BYTES:
            os.replace(p, p.with_name(p.name + ".1"))
    except FileNotFoundError:
        pass
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as f:
        f.write(line)


def _snapshot(legacy: Optional[dict], use_store: bool, now: float):
    from . import migrate, store

    src = source()
    if not use_store:
        return store.Snapshot(now=now, source="empty")
    snap = store.snapshot(now)
    if src == "store":
        return snap
    snap.grants = [g for g in snap.grants if not str(g.get("origin", "")).startswith("legacy:")]
    snap.grants += migrate.live_grants(**(legacy or {}))
    snap.source = "live"
    return snap


def shadow_check(old_allow, site: str, principal: Union[object, Callable[[], object]], scope: str, resource: str,
                 ctx: Optional[dict] = None, legacy: Optional[dict] = None, *, use_store: bool = True):
    """기존 판정 `old_allow`와 엔진 판정을 비교한다. 엔진 Decision 또는 None. **예외를 던지지 않는다.**

    principal 은 Principal 또는 그것을 만드는 함수(지연 — shadow 가 꺼져 있으면 만들지도 않는다).
    legacy 는 판정 지점이 이미 쥔 옛 레코드 `{agent_grants: [...], peer_grants: [...], delegations: [...]}`.
    """
    try:
        if not enabled():
            return None
        from . import engine

        now = time.time()
        p = principal() if callable(principal) else principal
        c = dict(ctx or {})
        if source() == "store":
            c["prefer_live"] = False
        snap = _snapshot(legacy, use_store, now)
        dec = engine.decide(p, scope, resource, c, snap)
        old = bool(old_allow)
        with STATS.lock:
            STATS.checks += 1
        STATS.site(site, "checks")
        if dec.allow != old:
            with STATS.lock:
                STATS.mismatches += 1
            STATS.site(site, "mismatches")
            key = (site, p.short(), scope, resource, old, dec.allow)
            if STATS.admit(key, now):
                _write({"ts": round(now, 3), "kind": "mismatch", "site": site, "principal": p.short(),
                        "principalKind": p.kind, "scope": scope, "resource": resource, "old": old,
                        "new": dec.allow, "reason": dec.reason, "rule": dec.rule, "grantId": dec.grant_id,
                        "source": snap.source})
        return dec
    except Exception as e:  # noqa: BLE001 — F8: shadow 는 어떤 경우에도 요청을 깨지 않는다
        _error(site, e)
        return None


def _error(site: str, e: BaseException) -> None:
    try:
        now = time.time()
        with STATS.lock:
            STATS.errors += 1
        STATS.site(site, "errors")
        if STATS.should_log(now):
            logger.warning(f"[authz shadow] {site}: {type(e).__name__}: {e}")
        if STATS.admit((site, "error", type(e).__name__), now):
            _write({"ts": round(now, 3), "kind": "error", "site": site, "error": type(e).__name__,
                    "detail": str(e)[:200]})
    except Exception:  # noqa: BLE001
        pass


def guard(fn: Callable[[], object]) -> None:
    """판정 지점의 한 줄 호출을 감싼다 — 인자(주체·리소스)를 만들다 터져도 응답에 영향이 없게.
    `shadow.guard(lambda: shadow_check(...))`."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        _error("guard", e)


def tail(limit: int = 100) -> list[dict]:
    """최근 기록(새 것 먼저). 디버그 엔드포인트용."""
    limit = max(1, min(int(limit), 1000))
    out: list[dict] = []
    for p in (path(), path().with_name(path().name + ".1")):
        try:
            with open(p, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 512 * 1024))
                lines = f.read().splitlines()
        except OSError:
            continue
        for raw in reversed(lines):
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
            if len(out) >= limit:
                return out
    return out


def summary(records: list[dict]) -> dict:
    by: dict[str, int] = {}
    for r in records:
        if r.get("kind") == "mismatch":
            k = f"{r.get('site')} {r.get('scope')} old={r.get('old')} new={r.get('new')} ({r.get('reason')})"
            by[k] = by.get(k, 0) + 1
    return by
