"""기계 토큰으로 인증된 요청의 가벼운 사용 기록 — `fsh doctor` 의 「최근 24h legacy 사용 N」 (plan-2.6 2단계).

`~/.vt/authz/legacy_token_use.jsonl`(0600). 줄 하나 = (분, 경로, UA) 묶음 하나의 횟수:
`{"ts": <분 시작 epoch>, "path": "/api/x", "ua": "curl/8.7", "n": 3}`.
**내용은 싣지 않는다** — 쿼리 문자열(`?token=` 포함)·헤더 값·본문은 어디에도 남지 않는다. 경로는 `?` 앞까지만,
UA 는 80자까지만. 크기는 1MB 에서 `.1` 한 세대로 돈다(약 2MB 상한). 기록은 절대 예외를 던지지 않는다.

같은 분·경로·UA 는 메모리에서 합산해 분이 바뀔 때 한 줄로 쓴다 — 요청마다 디스크를 두드리지 않는다.
현재 분의 몇 건은 다른 프로세스(doctor)에게 최대 1분 늦게 보인다.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

MAX_BYTES = 1024 * 1024
UA_MAX = 80
PATH_MAX = 200
_lock = threading.Lock()
_pending: dict[tuple[int, str, str], int] = {}


def path() -> Path:
    import vt_paths
    return vt_paths.state_dir() / "authz" / "legacy_token_use.jsonl"


def _flush_locked(before_minute: int) -> None:
    lines = []
    for key in [k for k in _pending if k[0] < before_minute]:
        n = _pending.pop(key)
        lines.append(json.dumps({"ts": key[0] * 60, "path": key[1], "ua": key[2], "n": n}, ensure_ascii=False))
    if not lines:
        return
    p = path()
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        if p.stat().st_size > MAX_BYTES:
            os.replace(p, p.with_name(p.name + ".1"))
    except FileNotFoundError:
        pass
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, ("\n".join(lines) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def record(req_path: str, user_agent: str = "", *, now: float | None = None) -> None:
    try:
        minute = int((now if now is not None else time.time()) // 60)
        key = (minute, (req_path or "").split("?", 1)[0][:PATH_MAX], (user_agent or "")[:UA_MAX])
        with _lock:
            _pending[key] = _pending.get(key, 0) + 1
            if len(_pending) > 512 or any(k[0] < minute for k in _pending):
                _flush_locked(minute)
    except Exception:  # noqa: BLE001
        pass


def flush() -> None:
    try:
        with _lock:
            _flush_locked(1 << 60)
    except Exception:  # noqa: BLE001
        pass


def count_24h(*, now: float | None = None) -> int:
    """파일에 기록된 최근 24시간 합. 읽지 못하면 0."""
    cutoff = (now if now is not None else time.time()) - 86400
    total = 0
    try:
        p = path()
    except Exception:  # noqa: BLE001
        return 0
    for f in (p.with_name(p.name + ".1"), p):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        if r["ts"] >= cutoff:
                            total += int(r.get("n", 1))
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            continue
    return total
