"""liveness / readiness 판정.

- live : 이벤트 루프가 이 요청에 답했다는 사실 하나(포트가 열려 있다는 것보다 한 단계 깊다).
- ready: 실제로 일을 받을 수 있는가 — 풀마다 빈 작업을 넣어 기한 안에 도는지 재고(기본 실행기 포함),
  버려진 작업·오래 쥔 잠금·루프 지연을 본다. 사고 때 이 판정이 있었다면 "default 풀 응답 없음,
  잠금 worktree.discover 412s 보유"로 바로 드러났다.
판정 자체는 스레드를 쓰지 않는다(빈 작업 기다림에도 기한이 있다) — 풀이 막혀도 이 응답은 온다.
"""
from __future__ import annotations

import asyncio
import os
import time

from . import breaker, locks, mounts, pools, proc, quarantine, registry, scan, watch

PROBE_TIMEOUT = 1.5
LOCK_STUCK_SEC = 60.0
STARTED = time.time()


async def ready() -> dict:
    names = ["default", *pools.DEFAULT_SIZES]
    results = await asyncio.gather(*(pools.probe(n, PROBE_TIMEOUT) for n in names))
    probe = dict(zip(names, results))
    pool_snaps = {p["name"]: p for p in pools.snapshot()}
    problems: list[str] = []
    for name in names:
        snap = pool_snaps.get(name, {})
        if not probe[name]:
            problems.append(f"pool:{name}:unresponsive")
        elif snap.get("degraded"):
            problems.append(f"pool:{name}:degraded")
    lock_snaps = locks.snapshot()
    for lk in lock_snaps:
        h = lk.get("holder") or {}
        if (h.get("held_for") or 0) > LOCK_STUCK_SEC and lk.get("waiters"):
            problems.append(f"lock:{lk['name']}:stuck")
    w = watch.state()
    if w["loop_lag"] > watch.LOOP_STALL_SEC:
        problems.append("loop:stalled")
    # 기본 실행기와 핵심 풀이 응답하지 않으면 "일을 못 받는다" — 재시작 대상이다.
    fatal = any(p.startswith(("pool:default:unresponsive", "loop:stalled")) for p in problems) or \
        sum(1 for p in problems if p.endswith(":unresponsive")) >= 2
    now = time.monotonic()
    slow = sorted((r for r in registry.snapshot() if r.started is not None and now - r.started > 5),
                  key=lambda r: r.started)[:20]
    return {
        "ok": not fatal,
        "status": "unready" if fatal else ("degraded" if problems else "ok"),
        "problems": problems,
        "pid": os.getpid(),
        "uptime": round(time.time() - STARTED),
        "probe": probe,
        "pools": list(pool_snaps.values()),
        "slow_ops": [r.public(now) for r in slow],
        "locks": [lk for lk in lock_snaps if lk["held"] or lk["waiters"] or lk["timeouts"]],
        "watch": w,
        "abandoned_procs": proc.abandoned(),
        "mounts": mounts.snapshot(),
        "blocked_paths": breaker.snapshot(),
        "quarantine": quarantine.entries(),
        "scans": scan.last(),
    }


def live() -> dict:
    return {"ok": True, "pid": os.getpid(), "uptime": round(time.time() - STARTED)}
