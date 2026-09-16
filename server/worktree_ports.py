"""워크트리 포트 베이스 맵 — `~/.vt/worktrees.json`.

워크트리마다 개발 서버 포트가 겹치지 않도록 5200·5300·… 식으로 베이스를
하나씩 떼어 준다. 그 배정을 기억해야 하므로(`.env` 치환은 만들 때 한 번만
일어난다) 이 파일 하나가 필요하다.

worktree.py에서 떼어낸 이유: **이 모듈은 자기 완결적이다.** 경로를 모듈
전역에 굳히지 않고 `VT_STATE_DIR`를 호출 시점에 읽으므로, 테스트가 환경변수만
바꾸면 격리된다 — worktree.py의 다른 부분(git 실행·tmux·캐시)과 공유하는
상태가 없다.

파일 규율은 이 저장소의 다른 저장 파일과 동일하다 — 0700 디렉토리 + 0600
파일 + flock + 임시파일 rename(snippet_store.py·queue_store.py와 같은 규칙).
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

# 워크트리마다 100씩 띄운다 — 한 워크트리가 프런트·API·스토리북처럼 여러
# 포트를 쓰는 걸 전제한 간격이다.
DEFAULT_PORT_BASE = 5200
PORT_STEP = 100

# --- 상태 저장 (~/.vt/worktrees.json) ---------------------------------------


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _state_path() -> Path:
    return _state_dir() / "worktrees.json"


def _lock_path() -> Path:
    return _state_dir() / "worktrees.lock"


@contextmanager
def _locked():
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    fd = os.open(str(_lock_path()), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _load_ports_map() -> dict:
    p = _state_path()
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"워크트리 상태 파일 읽기 실패({e}) — 빈 상태로 시작")
        return {}
    return data if isinstance(data, dict) else {}


def _save_ports_map(data: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


def _update_ports_map(wt_id: str, port_base: int) -> None:
    with _locked():
        m = _load_ports_map()
        m[wt_id] = {"ports": {"base": port_base}}
        _save_ports_map(m)


def _remove_ports_entry(wt_id: str) -> None:
    with _locked():
        m = _load_ports_map()
        if m.pop(wt_id, None) is not None:
            _save_ports_map(m)


def next_port_base() -> int:
    m = _load_ports_map()
    used = [
        v.get("ports", {}).get("base")
        for v in m.values()
        if isinstance(v, dict) and isinstance(v.get("ports", {}).get("base"), int)
    ]
    if not used:
        return DEFAULT_PORT_BASE
    return max(used) + PORT_STEP
