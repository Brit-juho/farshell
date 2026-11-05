"""탐색 중 멈춘 디렉터리 기억.

탐색 자식 프로세스가 기한을 넘기면 마지막으로 들어가던 디렉터리를 여기에 올린다. 다음 탐색은 그
디렉터리를 건너뛴다. 사람이 고칠 때까지 같은 곳에서 매번 기한을 다 쓰지 않게 하고, 무엇이 문제였는지를
`fsh doctor`·`/api/health/ready`에 남긴다. 기간(`TTL_SEC`)이 지나면 다시 시도한다.
파일: `$VT_STATE_DIR/scan-quarantine.json` (0600).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TTL_SEC = 6 * 3600.0
MAX_ENTRIES = 100
_lock = threading.Lock()


def _path() -> Path:
    import vt_paths
    return vt_paths.state_dir() / "scan-quarantine.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".scan-quarantine.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except OSError as e:
        logger.warning(f"격리 목록 저장 실패: {e}")


def add(path: str, reason: str) -> None:
    now = time.time()
    with _lock:
        data = _active(_load(), now)
        prev = data.get(path) or {}
        data[path] = {"since": prev.get("since", now), "until": now + TTL_SEC,
                      "reason": reason, "count": int(prev.get("count", 0)) + 1}
        if len(data) > MAX_ENTRIES:
            for k in sorted(data, key=lambda k: data[k]["since"])[: len(data) - MAX_ENTRIES]:
                data.pop(k, None)
        _save(data)
    logger.warning(f"[scan] 응답 없는 디렉터리를 {TTL_SEC / 3600:.0f}시간 동안 탐색에서 뺌: {path} ({reason})")


def _active(data: dict, now: float) -> dict:
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("until", 0) > now}


def paths() -> list[str]:
    with _lock:
        return sorted(_active(_load(), time.time()))


def entries() -> list[dict]:
    now = time.time()
    with _lock:
        data = _active(_load(), now)
    return [{"path": k, "reason": v.get("reason", ""), "count": v.get("count", 1),
             "until_in": round(v["until"] - now)} for k, v in sorted(data.items())]


def clear() -> int:
    with _lock:
        n = len(_active(_load(), time.time()))
        _save({})
    return n
