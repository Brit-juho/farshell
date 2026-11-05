"""프로세스 안 감시 — 멈추면 반드시 흔적을 남긴다.

1. `SIGUSR1` → 모든 스레드의 파이썬 스택을 `$VT_RUN_DIR/stacks.log`에 덧붙인다(`faulthandler`).
   `kill -USR1 <pid>` 한 번이면 py-spy(macOS 는 root 필요) 없이 어디서 멈췄는지 본다. 서버 워치독도
   재시작 전에 이걸 보낸다.
2. 감시 스레드(1초 주기)
   - 이벤트 루프 지연: 루프에 신호를 넣고 돌아오기까지 걸린 시간. `LOOP_STALL_SEC`을 넘으면 전체 스택을
     한 번 기록한다(루프가 멈춘 동안 무엇을 하고 있었는지).
   - 오래 도는 스레드 작업(`registry`): `SLOW_OP_SEC`을 넘긴 작업마다 그 스레드의 스택을 한 번 기록한다.
     이번 사고였다면 "worktree 탐색이 /Users/…/OrbStack/… 에서 412초째"가 로그에 남았을 것이다.
   - 오래 쥔 잠금(`locks.TimedLock`): 쥔 스레드의 스택을 한 번 기록한다.
   스택 기록은 `stacks.log`(사람이 읽는 형식)와 서버 로그(한 줄 요약) 두 곳에 남긴다.
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import signal
import sys
import threading
import time
import traceback
from typing import Optional

from . import locks, registry

logger = logging.getLogger("resilience.watch")

INTERVAL = 1.0
LOOP_STALL_SEC = float(os.environ.get("VT_WATCH_LOOP_STALL_SEC", "5"))
SLOW_OP_SEC = float(os.environ.get("VT_WATCH_SLOW_OP_SEC", "15"))
LOCK_HOLD_SEC = float(os.environ.get("VT_WATCH_LOCK_HOLD_SEC", "15"))
STACKS_MAX_BYTES = 5 * 1024 * 1024

_state = {"loop_lag": 0.0, "loop_lag_max": 0.0, "last_beat": None, "stalls": 0, "started": None}
_stacks_file = None
_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def stacks_path():
    import vt_paths
    return vt_paths.run_dir() / "stacks.log"


def _open_stacks():
    global _stacks_file
    p = stacks_path()
    try:
        if p.exists() and p.stat().st_size > STACKS_MAX_BYTES:
            os.replace(p, p.with_suffix(".log.1"))
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        _stacks_file = os.fdopen(fd, "a", buffering=1, encoding="utf-8")
    except OSError as e:
        logger.warning(f"stacks.log 를 열지 못함: {e}")
        _stacks_file = None
    return _stacks_file


def install_signal_dump() -> bool:
    """SIGUSR1 → 전 스레드 스택. 파일 디스크립터를 붙잡아 두므로 열린 채로 둔다."""
    f = _stacks_file or _open_stacks()
    if f is None or not hasattr(signal, "SIGUSR1"):
        return False
    try:
        faulthandler.register(signal.SIGUSR1, file=f, all_threads=True, chain=False)
        return True
    except (RuntimeError, ValueError, OSError) as e:
        logger.warning(f"SIGUSR1 스택 덤프 등록 실패: {e}")
        return False


def _write_block(title: str, body: str) -> None:
    f = _stacks_file
    if f is None:
        return
    try:
        f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {title} =====\n{body}\n")
        f.flush()
    except (OSError, ValueError):
        pass


def thread_stack(ident: Optional[int]) -> str:
    if ident is None:
        return "(thread unknown)"
    frame = sys._current_frames().get(ident)
    if frame is None:
        return "(thread gone)"
    return "".join(traceback.format_stack(frame))


def all_stacks() -> str:
    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():
        parts.append(f"--- thread {names.get(ident, '?')} ({ident})\n" + "".join(traceback.format_stack(frame)))
    return "\n".join(parts)


def _report_slow_ops(now: float) -> None:
    for rec in registry.snapshot():
        if rec.reported or rec.started is None or now - rec.started < SLOW_OP_SEC:
            continue
        rec.reported = True
        stack = thread_stack(rec.thread)
        last = stack.strip().splitlines()[-1].strip() if stack.strip() else ""
        logger.warning(f"[watch] 오래 도는 작업 {rec.pool}:{rec.op} {now - rec.started:.0f}s key={rec.key!r} "
                       f"thread={rec.thread_name} at {last}")
        _write_block(f"slow op {rec.pool}:{rec.op} key={rec.key!r} thread={rec.thread_name}", stack)


def _report_locks() -> None:
    for lk in locks.all_locks():
        held = lk.held_for()
        if lk.reported or held < LOCK_HOLD_SEC:
            continue
        lk.reported = True
        h = lk.holder()
        stack = thread_stack(h.get("thread_id"))
        logger.warning(f"[watch] 잠금 {lk.name} 을 {held:.0f}s 째 쥐고 있음 — {h.get('thread')} op={h.get('op')!r}, "
                       f"대기 {lk.snapshot()['waiters']}")
        _write_block(f"lock {lk.name} held {held:.0f}s by {h.get('thread')} op={h.get('op')!r}", stack)


def _run(loop: asyncio.AbstractEventLoop) -> None:
    beat = threading.Event()
    stalled_reported = False
    while not _stop.wait(INTERVAL):
        beat.clear()
        sent = time.monotonic()
        try:
            loop.call_soon_threadsafe(beat.set)
        except RuntimeError:
            return            # 루프가 닫혔다
        ok = beat.wait(LOOP_STALL_SEC)
        lag = time.monotonic() - sent
        _state["loop_lag"] = lag if ok else time.monotonic() - sent
        _state["loop_lag_max"] = max(_state["loop_lag_max"], _state["loop_lag"])
        if ok:
            _state["last_beat"] = time.time()
            stalled_reported = False
        elif not stalled_reported:
            stalled_reported = True
            _state["stalls"] += 1
            logger.error(f"[watch] 이벤트 루프가 {LOOP_STALL_SEC:g}s 넘게 응답하지 않음 — 전체 스택을 stacks.log 에 기록")
            _write_block("event loop stalled", all_stacks())
        now = time.monotonic()
        try:
            _report_slow_ops(now)
            _report_locks()
        except Exception:  # noqa: BLE001 — 감시가 죽으면 안 된다
            logger.debug("watch 보고 실패", exc_info=True)


def start(loop: asyncio.AbstractEventLoop) -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _state["started"] = time.time()
    install_signal_dump()
    _thread = threading.Thread(target=_run, args=(loop,), name="fsh-watch", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


def state() -> dict:
    return {
        "loop_lag": round(_state["loop_lag"], 3),
        "loop_lag_max": round(_state["loop_lag_max"], 3),
        "loop_stalls": _state["stalls"],
        "last_beat": _state["last_beat"],
        "running": bool(_thread and _thread.is_alive()),
        "stacks_file": str(stacks_path()) if _stacks_file else None,
    }
