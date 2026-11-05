"""위험도별 스레드 풀(격벽, bulkhead).

왜 필요한가: `asyncio.to_thread`는 루프의 기본 실행기 하나를 쓴다(크기 `min(32, 코어 + 4)`). 거기서
작업 몇 개가 영원히 멈추면(NFS `open()`, 잠금 무기한 대기) 나머지 수백 곳의 `to_thread`가 전부 줄을
서서 서버 전체가 응답하지 않는다. 스레드는 강제로 멈출 수 없고 `wait_for` 타임아웃도 **호출자만**
풀어 줄 뿐 스레드는 계속 묶여 있다(공식 문서: 이미 실행 중인 future 는 취소되지 않는다).

그래서
- 멈출 수 있는 일은 종류별 풀로 보낸다: `fs`(사용자 경로), `git`, `proc`(tmux·ps 등 짧은 명령),
  `net`(다른 맥·외부 HTTP), `slow`(STT·`fsh` 하위 명령처럼 원래 오래 걸리는 일).
  한 종류가 막혀도 다른 종류와 기본 실행기는 살아 있다.
- 풀마다 입장 상한(작업자 수 + 대기 칸)을 둔다. 꽉 차면 기다리지 않고 `Busy`(→ 503)를 낸다.
  상한이 없으면 막힌 풀 앞에 요청이 끝없이 쌓인다(`ThreadPoolExecutor` 내부 큐는 무제한이다).
- 기한(`timeout`)을 넘기면 호출자에게 `OpTimeout`을 던지고, 그 작업은 "버려짐"으로 센다.
  스레드가 끝나면 자리와 카운트가 돌아온다. 버려진 작업이 쌓이면 `health`가 그 풀을 degraded 로 본다.
- 기본 실행기도 `InstrumentedExecutor`로 바꿔 끼워, 이름 없는 `to_thread` 447곳도 장부에 남긴다.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

from . import registry

logger = logging.getLogger(__name__)

# 이름 → (작업자 수, 대기 칸). 크기는 VT_POOL_<NAME>_SIZE 로 바꿀 수 있다(대기 칸은 작업자의 4배).
DEFAULT_SIZES = {
    "fs": 4,
    "git": 6,
    "proc": 6,
    "net": 8,
    "slow": 3,
}
QUEUE_FACTOR = 4


class Busy(RuntimeError):
    """풀 입장 상한 초과 — 지금은 받을 수 없다(503)."""

    def __init__(self, pool: str):
        super().__init__(f"pool {pool} is saturated")
        self.pool = pool


class OpTimeout(TimeoutError):
    """기한 초과 — 호출자는 떠났고, 스레드는 끝날 때까지 계속 돈다."""

    def __init__(self, pool: str, op: str, key: str, timeout: float):
        super().__init__(f"{pool}:{op} timed out after {timeout:g}s ({key})")
        self.pool = pool
        self.op = op
        self.key = key
        self.timeout = timeout


def _env_size(name: str, default: int) -> int:
    raw = os.environ.get(f"VT_POOL_{name.upper()}_SIZE", "").strip()
    if raw:
        try:
            n = int(raw)
            if 1 <= n <= 64:
                return n
        except ValueError:
            pass
        logger.warning(f"VT_POOL_{name.upper()}_SIZE={raw!r} 무시 — 1~64 정수만 받는다")
    return default


def _describe(fn: Callable) -> str:
    """`to_thread`가 넘기는 `partial(ctx.run, func, ...)`를 벗겨 실제 함수 이름을 찾는다."""
    seen = 0
    while seen < 5:
        seen += 1
        if isinstance(fn, functools.partial):
            inner = fn.func
            if getattr(inner, "__name__", "") == "run" and fn.args and callable(fn.args[0]):
                fn = fn.args[0]
                continue
            fn = inner
            continue
        break
    mod = getattr(fn, "__module__", "") or ""
    name = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None) or type(fn).__name__
    return f"{mod}.{name}" if mod and mod != "builtins" else str(name)


class Pool:
    def __init__(self, name: str, size: int, queue: Optional[int] = None):
        self.name = name
        self.size = size
        self.capacity = size + (size * QUEUE_FACTOR if queue is None else queue)
        self._executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix=f"fsh-{name}")
        self._lock = threading.Lock()
        self.inflight = 0          # 입장해서 아직 끝나지 않은 작업(대기 + 실행)
        self.abandoned = 0         # 기한 초과로 호출자가 떠났는데 아직 안 끝난 작업
        self.rejected = 0          # 누적 Busy
        self.timeouts = 0          # 누적 OpTimeout
        self.completed = 0

    # --- 입장 ---------------------------------------------------------------------------
    def _admit(self) -> None:
        with self._lock:
            if self.inflight >= self.capacity:
                self.rejected += 1
                raise Busy(self.name)
            self.inflight += 1

    def _leave(self, rec: registry.Op) -> None:
        with self._lock:
            self.inflight -= 1
            self.completed += 1
            if rec.abandoned:
                self.abandoned -= 1
        registry.end(rec)

    def _wrap(self, rec: registry.Op, fn: Callable, args, kwargs) -> Callable[[], Any]:
        ctx = contextvars.copy_context()

        def call():
            registry.started(rec)
            try:
                return ctx.run(fn, *args, **kwargs)
            finally:
                self._leave(rec)
        return call

    def _submit(self, rec: registry.Op, fn: Callable, args, kwargs) -> Future:
        try:
            cfut = self._executor.submit(self._wrap(rec, fn, args, kwargs))
        except BaseException:
            self._leave(rec)
            raise
        # 실행 전에 취소되면(요청이 끊겨 asyncio 쪽 future 가 취소됨) call() 이 안 돌아 자리가 안 돌아온다.
        cfut.add_done_callback(lambda f: self._leave(rec) if f.cancelled() else None)
        return cfut

    # --- 비동기 호출 ----------------------------------------------------------------------
    async def run(self, fn: Callable, *args, op: Optional[str] = None, key: str = "",
                  timeout: Optional[float] = None, **kwargs) -> Any:
        self._admit()
        rec = registry.begin(self.name, op or _describe(fn), key)
        cfut = self._submit(rec, fn, args, kwargs)
        afut = asyncio.wrap_future(cfut)
        if not timeout:
            return await afut
        try:
            return await asyncio.wait_for(asyncio.shield(afut), timeout)
        except asyncio.TimeoutError:
            self._abandon(rec, cfut)
            raise OpTimeout(self.name, rec.op, rec.key, timeout) from None

    # --- 동기 호출(이미 스레드 안에 있는 코드용) -----------------------------------------
    def call(self, fn: Callable, *args, op: Optional[str] = None, key: str = "",
             timeout: Optional[float] = None, **kwargs) -> Any:
        """스레드 안에서 부른다. 기다리는 동안 호출 스레드도 묶이지만 기한이 있다."""
        self._admit()
        rec = registry.begin(self.name, op or _describe(fn), key)
        cfut = self._submit(rec, fn, args, kwargs)
        try:
            return cfut.result(timeout=timeout)
        except TimeoutError:
            self._abandon(rec, cfut)
            raise OpTimeout(self.name, rec.op, rec.key, timeout or 0.0) from None

    def _abandon(self, rec: registry.Op, cfut: Future) -> None:
        with self._lock:
            self.timeouts += 1
            if not cfut.done() and not rec.abandoned:
                rec.abandoned = True
                self.abandoned += 1
        # 나중에 끝나며 생긴 예외를 아무도 안 읽으면 asyncio 가 경고를 남긴다 — 여기서 읽어 둔다.
        cfut.add_done_callback(_swallow)
        logger.warning(f"[pool:{self.name}] 기한 초과로 호출자를 풀어 줌 — {rec.op} {rec.key}")

    # --- 상태 ---------------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            inflight, abandoned = self.inflight, self.abandoned
            rejected, timeouts, completed = self.rejected, self.timeouts, self.completed
        oldest = registry.oldest_running(self.name)
        running = sum(1 for r in registry.snapshot() if r.pool == self.name and r.started is not None)
        return {
            "name": self.name,
            "size": self.size,
            "capacity": self.capacity,
            "inflight": inflight,
            "running": running,
            "queued": max(0, inflight - running),
            "abandoned": abandoned,
            "rejected": rejected,
            "timeouts": timeouts,
            "completed": completed,
            "oldest": oldest.public() if oldest else None,
            "degraded": self.degraded(abandoned, running),
        }

    def degraded(self, abandoned: Optional[int] = None, running: Optional[int] = None) -> bool:
        """작업자 절반 이상이 버려진 작업에 묶였거나, 전부 오래(60초+) 돌고 있으면 degraded."""
        if abandoned is None:
            abandoned = self.abandoned
        if abandoned >= max(1, (self.size + 1) // 2):
            return True
        if running is None:
            running = sum(1 for r in registry.snapshot() if r.pool == self.name and r.started is not None)
        if running >= self.size:
            oldest = registry.oldest_running(self.name)
            return bool(oldest and oldest.age() > 60)
        return False

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def _swallow(fut: Future) -> None:
    if not fut.cancelled():
        fut.exception()


class InstrumentedExecutor(ThreadPoolExecutor):
    """기본 실행기 대체 — 동작은 같고, 작업마다 장부에 남긴다(op 이름은 실제 함수에서)."""

    pool_name = "default"

    def __init__(self, max_workers: int):
        super().__init__(max_workers=max_workers, thread_name_prefix="fsh-default")
        self.size = max_workers

    def submit(self, fn, /, *args, **kwargs):
        rec = registry.begin(self.pool_name, _describe(fn))

        def call():
            registry.started(rec)
            try:
                return fn(*args, **kwargs)
            finally:
                registry.end(rec)
        try:
            fut = super().submit(call)
        except BaseException:
            registry.end(rec)
            raise
        fut.add_done_callback(lambda f: registry.end(rec) if f.cancelled() else None)
        return fut

    def snapshot(self) -> dict:
        recs = [r for r in registry.snapshot() if r.pool == self.pool_name]
        running = [r for r in recs if r.started is not None]
        oldest = min(running, key=lambda r: r.started, default=None)
        return {
            "name": self.pool_name,
            "size": self.size,
            "running": len(running),
            "queued": len(recs) - len(running),
            "oldest": oldest.public() if oldest else None,
            "degraded": len(running) >= self.size and bool(oldest and oldest.age() > 60),
        }


# --- 전역 레지스트리 -------------------------------------------------------------------------
_pools: dict[str, Pool] = {}
_pools_lock = threading.Lock()
_default: Optional[InstrumentedExecutor] = None


def get(name: str) -> Pool:
    p = _pools.get(name)
    if p is not None:
        return p
    with _pools_lock:
        p = _pools.get(name)
        if p is None:
            if name not in DEFAULT_SIZES:
                raise KeyError(f"unknown pool {name!r}")
            p = Pool(name, _env_size(name, DEFAULT_SIZES[name]))
            _pools[name] = p
        return p


async def run(pool: str, fn: Callable, *args, op: Optional[str] = None, key: str = "",
              timeout: Optional[float] = None, **kwargs) -> Any:
    return await get(pool).run(fn, *args, op=op, key=key, timeout=timeout, **kwargs)


def call(pool: str, fn: Callable, *args, op: Optional[str] = None, key: str = "",
         timeout: Optional[float] = None, **kwargs) -> Any:
    return get(pool).call(fn, *args, op=op, key=key, timeout=timeout, **kwargs)


def install_default_executor(loop: asyncio.AbstractEventLoop) -> InstrumentedExecutor:
    """기본 실행기를 계측판으로 바꾼다. 크기는 표준과 같은 공식(min(32, 코어 + 4))."""
    global _default
    cpus = (os.process_cpu_count() if hasattr(os, "process_cpu_count") else os.cpu_count()) or 1
    size = _env_size("default", min(32, cpus + 4))
    ex = InstrumentedExecutor(size)
    loop.set_default_executor(ex)
    _default = ex
    return ex


def default_executor() -> Optional[InstrumentedExecutor]:
    return _default


def snapshot() -> list[dict]:
    out = []
    if _default is not None:
        out.append(_default.snapshot())
    for name in DEFAULT_SIZES:
        p = _pools.get(name)
        out.append(p.snapshot() if p else {"name": name, "size": _env_size(name, DEFAULT_SIZES[name]),
                                             "inflight": 0, "running": 0, "queued": 0, "abandoned": 0,
                                             "rejected": 0, "timeouts": 0, "completed": 0,
                                             "oldest": None, "degraded": False, "idle": True})
    return out


async def probe(pool: str, timeout: float = 1.5) -> bool:
    """그 풀에서 빈 작업이 기한 안에 도는가. 입장 상한은 건너뛴다(포화 여부 자체를 재는 것이므로)."""
    loop = asyncio.get_running_loop()
    if pool == "default":
        ex = _default
        if ex is None:
            fut = loop.run_in_executor(None, _noop)
        else:
            fut = asyncio.wrap_future(ThreadPoolExecutor.submit(ex, _noop))
    else:
        p = _pools.get(pool)
        if p is None:
            return True          # 아직 한 번도 안 쓴 풀 — 막힐 작업이 없다
        fut = asyncio.wrap_future(p._executor.submit(_noop))
    try:
        await asyncio.wait_for(asyncio.shield(fut), timeout)
        return True
    except asyncio.TimeoutError:
        return False


def _noop() -> float:
    return time.monotonic()


def _reset_for_tests() -> None:
    global _default
    with _pools_lock:
        for p in _pools.values():
            p.shutdown()
        _pools.clear()
    _default = None
