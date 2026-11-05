"""응답 없는 경로 차단기.

어떤 경로에서 파일 작업이 기한을 넘기면 그 경로(와 그 아래)를 `OPEN_SEC` 동안 막는다. 막힌 동안에는
그 경로로 가는 작업이 스레드를 하나 더 잡아먹지 않고 즉시 실패한다. 시간이 지나면 한 번 시도를
허용하고(half-open), 성공하면 닫힌다. 같은 NFS 를 반복해서 찔러 스레드를 하나씩 잃는 일을 막는다.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from . import mounts

OPEN_SEC = 300.0


class PathBlocked(OSError):
    def __init__(self, prefix: str, retry_in: float, reason: str):
        super().__init__(f"{prefix} 가 응답하지 않아 {retry_in:.0f}초 동안 막아 둠 ({reason})")
        self.prefix = prefix
        self.retry_in = retry_in
        self.reason = reason


_lock = threading.Lock()
_open: dict[str, dict] = {}     # prefix → {until, reason, trips, probing}


def _norm(path: str) -> str:
    return os.path.abspath(os.path.expanduser(str(path)))


def key_for(path: str) -> str:
    """막을 범위 — 원격 마운트면 마운트 지점 전체, 아니면 그 경로 자체."""
    p = _norm(path)
    m = mounts.mount_for(p)
    if m is not None and m.risky and m.path != "/":
        return m.path
    return p


def _within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def check(path: str) -> None:
    """막힌 경로면 `PathBlocked`. 열린 시간이 지났으면 이번 한 번은 통과시킨다(half-open)."""
    p = _norm(path)
    now = time.monotonic()
    with _lock:
        for prefix, st in _open.items():
            if not _within(p, prefix):
                continue
            if now < st["until"]:
                raise PathBlocked(prefix, st["until"] - now, st["reason"])
            if st.get("probing"):
                raise PathBlocked(prefix, OPEN_SEC / 10, st["reason"])
            st["probing"] = True
            return


def trip(path: str, reason: str) -> str:
    prefix = key_for(path)
    with _lock:
        st = _open.get(prefix) or {"trips": 0}
        st.update(until=time.monotonic() + OPEN_SEC, reason=reason, probing=False)
        st["trips"] += 1
        _open[prefix] = st
    return prefix


def success(path: str) -> None:
    p = _norm(path)
    with _lock:
        for prefix in [k for k in _open if _within(p, k)]:
            if _open[prefix].get("probing"):
                _open.pop(prefix, None)


def snapshot() -> list[dict]:
    now = time.monotonic()
    with _lock:
        return [{"path": k, "reason": v["reason"], "trips": v["trips"],
                 "retry_in": max(0, round(v["until"] - now))} for k, v in _open.items()]


def blocked_prefixes() -> list[str]:
    now = time.monotonic()
    with _lock:
        return [k for k, v in _open.items() if now < v["until"]]


def _reset_for_tests() -> None:
    with _lock:
        _open.clear()
