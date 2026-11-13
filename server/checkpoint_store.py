"""체크포인트 「이 저장소에서 켜기」 상태 — 이 Mac 전용 작은 파일 `<state>/checkpoints.json`.

켜기 상태는 「내 저장소의 `.git`에 작업 상태 사본을 남길지」를 정하는 보안성 설정이다. 그래서
- 세션 카탈로그 DB(`session_catalog`, 다른 Mac과 복제된다)에 두지 않는다 — 다른 Mac이 대신 켤 수 없어야 한다.
- 단위는 **저장소**다: 키는 git 공통 디렉터리의 실경로(`checkpoints.repo_key`). linked worktree는 객체·ref를 같이 쓰므로
  worktree 하나만 따로 켜고 끌 수 없다.
- 켠 것만 적는다(끄면 항목이 사라진다). 기본은 모두 꺼짐. 파일은 0600, 쓰기는 임시 파일 + `os.replace`, 잠금은 락 전용 파일.
- 경로는 상태 폴더(`~/.vt`, fsguard가 이미 막는 곳)에만 있다. 이 모듈의 값은 화면·로그에 내보내지 않는다.

훅 이벤트마다(도구 호출 때마다) `any_enabled()`를 부르므로 파일 변경(mtime·크기)을 보고 읽은 값을 재사용한다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

import store_lock
import vt_paths

logger = logging.getLogger(__name__)

VERSION = 1
MAX_REPOS = 200

_cache_lock = threading.Lock()
_cache: tuple[tuple[int, int] | None, frozenset[str]] = (None, frozenset())


def _path() -> Path:
    return vt_paths.state_dir() / "checkpoints.json"


def _lock_path() -> Path:
    return vt_paths.state_dir() / "checkpoints.lock"


def _read_file() -> list[str]:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        logger.warning("체크포인트 설정 읽기 실패(%s) — 모두 꺼진 것으로 본다", type(e).__name__)
        return []
    raw = data.get("repos") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        key = item.get("key") if isinstance(item, dict) else None
        if isinstance(key, str) and key.startswith("/") and "\0" not in key and key not in out and item.get("enabled") is True:
            out.append(key)
        if len(out) >= MAX_REPOS:
            break
    return out


def enabled_keys() -> frozenset[str]:
    """켜진 저장소 키 전부. 파일이 안 바뀌었으면 읽은 값을 그대로 쓴다."""
    global _cache
    try:
        st = os.stat(_path())
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    with _cache_lock:
        if _cache[0] == stamp and stamp is not None:
            return _cache[1]
        keys = frozenset(_read_file()) if stamp is not None else frozenset()
        _cache = (stamp, keys)
        return keys


def any_enabled() -> bool:
    return bool(enabled_keys())


def is_enabled(key: str) -> bool:
    return key in enabled_keys()


def set_enabled(key: str, enabled: bool) -> dict:
    """한 저장소를 켜거나 끈다. 결과 `{ok, enabled, count}`. 켜는 수가 `MAX_REPOS`를 넘으면 `{ok: False, error: "too_many"}`."""
    if not isinstance(key, str) or not key.startswith("/") or "\0" in key:
        return {"ok": False, "error": "bad_key"}
    d = vt_paths.state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    with store_lock.exclusive(_lock_path()):
        keys = _read_file()
        # 사라진 저장소(경로가 없어짐)는 쓸 때 같이 정리한다.
        keys = [k for k in keys if os.path.isdir(k) or k == key]
        if enabled:
            if key not in keys:
                if len(keys) >= MAX_REPOS:
                    return {"ok": False, "error": "too_many"}
                keys.append(key)
        else:
            keys = [k for k in keys if k != key]
        _write(keys)
    global _cache
    with _cache_lock:
        _cache = (None, frozenset())          # 다음 조회에서 다시 읽는다
    return {"ok": True, "enabled": bool(enabled), "count": len(keys)}


def _write(keys: list[str]) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "repos": [{"key": k, "enabled": True} for k in keys]}, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))
