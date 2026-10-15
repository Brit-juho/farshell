"""기계 토큰으로 인증된 요청의 가벼운 사용 기록 — `fsh doctor` 의 「최근 24h legacy 사용 N」.

`~/.vt/authz/legacy_token_use.jsonl`(0600). 줄 하나 = (분, 경로, UA) 묶음 하나의 횟수:
`{"ts": <분 시작 epoch>, "path": "/api/x", "ua": "curl/8.7", "n": 3}`.
**내용은 싣지 않는다** — 쿼리 문자열(`?token=` 포함)·헤더 값·본문은 어디에도 남지 않는다. 경로는 `?` 앞까지만,
UA 는 80자까지만. 크기는 1MB 에서 `.1` 한 세대로 돈다(약 2MB 상한). 기록은 절대 예외를 던지지 않는다.

`legacy` 강제 플래그를 켜면 허용표 밖이라 403 `legacy_token_scope` 로 거절한 요청도 같은 모양으로 `legacy_token_denied.jsonl`
에 센다(거절된 호출이 어느 외부 스크립트인지 다음 릴리스 전에 보려는 것이다 — 마찬가지로 내용 없음).

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
_pending_denied: dict[tuple[int, str, str], int] = {}


def path() -> Path:
    import vt_paths
    return vt_paths.state_dir() / "authz" / "legacy_token_use.jsonl"


def denied_path() -> Path:
    return path().with_name("legacy_token_denied.jsonl")


def _flush_locked(before_minute: int) -> None:
    _flush_one(_pending, path, before_minute)
    _flush_one(_pending_denied, denied_path, before_minute)


def _flush_one(pending: dict, path_fn, before_minute: int) -> None:
    lines = []
    for key in [k for k in pending if k[0] < before_minute]:
        n = pending.pop(key)
        lines.append(json.dumps({"ts": key[0] * 60, "path": key[1], "ua": key[2], "n": n}, ensure_ascii=False))
    if not lines:
        return
    p = path_fn()
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


def record(req_path: str, user_agent: str = "", *, now: float | None = None, denied: bool = False) -> None:
    try:
        minute = int((now if now is not None else time.time()) // 60)
        key = (minute, (req_path or "").split("?", 1)[0][:PATH_MAX], (user_agent or "")[:UA_MAX])
        pending = _pending_denied if denied else _pending
        with _lock:
            pending[key] = pending.get(key, 0) + 1
            if len(pending) > 512 or any(k[0] < minute for k in pending):
                _flush_locked(minute)
    except Exception:  # noqa: BLE001
        pass


def flush() -> None:
    try:
        with _lock:
            _flush_locked(1 << 60)
    except Exception:  # noqa: BLE001
        pass


def _rows_24h(p: Path, now: float | None):
    cutoff = (now if now is not None else time.time()) - 86400
    for f in (p.with_name(p.name + ".1"), p):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                        if r["ts"] >= cutoff:
                            yield r
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            continue


def count_24h(*, now: float | None = None) -> int:
    """파일에 기록된 최근 24시간 합. 읽지 못하면 0."""
    try:
        return sum(int(r.get("n", 1)) for r in _rows_24h(path(), now))
    except Exception:  # noqa: BLE001
        return 0


def denied_24h(*, now: float | None = None) -> int:
    """최근 24시간 동안 `legacy_token_scope` 로 거절한 수. 읽지 못하면 0."""
    try:
        return sum(int(r.get("n", 1)) for r in _rows_24h(denied_path(), now))
    except Exception:  # noqa: BLE001
        return 0


def denied_top_24h(limit: int = 8, *, now: float | None = None) -> list[dict]:
    """거절된 (경로, UA) 별 최근 24h 횟수, 많은 순. 어느 외부 스크립트를 옮겨야 하는지 보이게 한다."""
    acc: dict[tuple[str, str], int] = {}
    try:
        for r in _rows_24h(denied_path(), now):
            k = (str(r.get("path", "")), str(r.get("ua", "")))
            acc[k] = acc.get(k, 0) + int(r.get("n", 1))
    except Exception:  # noqa: BLE001
        return []
    top = sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [{"path": k[0], "ua": k[1], "n": n} for k, n in top]
