"""Repository preference compatibility facade.

The private session catalog owns hidden preferences after one-time import.
The old repos.json remains unchanged as recovery evidence; legacy rail preferences
are retained as a .bak. Imports take the legacy store lock before the catalog lock.
Repository identifiers remain compatible with existing API consumers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path

import store_lock
import vt_paths

logger = logging.getLogger(__name__)

VERSION = 1
DEFAULT_HOST = "local"
# 저장소 상한(worktree.MAX_REPOS)과 같은 수 — 전부 숨기는 것도 가능해야 하고,
# 그 이상은 잘못 쓰이고 있다는 뜻이다.
MAX_REPOS = 200


def repo_id(host: str, path: str) -> str:
    """`sha1(host+path)[:12]` — worktree.py의 워크트리 id와 같은 모양이지만
    별개 네임스페이스다(입력이 다르므로 값도 다르다)."""
    return hashlib.sha1(f"{host}{path}".encode()).hexdigest()[:12]


def _state_dir() -> Path:
    # P2-4(ADR-41) — 예전엔 `Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()`.
    # `VT_STATE_DIR=""`은 폴백이 안 걸려 서버 CWD가 됐고 공백 패딩은 안 잘렸다. 단일 해석기로 교체한다.
    return vt_paths.state_dir()


def _path() -> Path:
    return _state_dir() / "repos.json"


def _lock_path() -> Path:
    return _state_dir() / "repos.lock"


def _legacy_path() -> Path:
    return _state_dir() / "rail-repos.json"


@contextmanager
def _locked():
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    with store_lock.exclusive(_lock_path()):
        yield


def _normalize(path: str) -> str:
    """경로 키 정규화 — `~` 전개 + 끝 슬래시 제거.

    `resolve()`는 **하지 않는다**(rail_repos_store와 같은 이유) — 저장소가
    지워진 뒤에도 목록의 항목을 그대로 두고 비교할 수 있어야 한다. 비교되는
    쪽(`worktree.list_worktrees()`의 `path`)은 이미 `fsguard.resolve_under_roots`를
    거친 값이라 같은 모양이다.
    """
    p = (path or "").strip()
    if not p:
        return ""
    p = str(Path(p).expanduser())
    return p.rstrip("/") or "/"


def _migrate_from_legacy_unlocked() -> dict:
    """`rail-repos.json`의 숨김 배열을 새 스키마로 1회 이관. 원본은 `.bak`으로 보존."""
    legacy = _legacy_path()
    items: dict[str, dict] = {}
    if not legacy.is_file():
        return items
    try:
        with open(legacy, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("hidden") if isinstance(data, dict) else None
    except (OSError, ValueError) as e:
        logger.warning(f"옛 rail-repos.json 읽기 실패({e}) — 이관 없이 시작")
        raw = None
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, str):
                continue
            norm = _normalize(entry)
            if not norm:
                continue
            rid = repo_id(DEFAULT_HOST, norm)
            items[rid] = {"id": rid, "host": DEFAULT_HOST, "path": norm, "hidden": True}
    try:
        legacy.replace(legacy.with_suffix(".json.bak"))
        logger.info(f"rail-repos.json → repos.json 이관 완료({len(items)}건), 원본은 .bak으로 보존")
    except OSError as e:
        logger.warning(f"rail-repos.json.bak 보존 실패({e}) — 원본을 그대로 둔다")
    return items


def _read_unlocked() -> dict[str, dict]:
    """id → 레코드. 파일이 없으면 이관을 먼저 시도한다."""
    p = _path()
    if not p.is_file():
        migrated = _migrate_from_legacy_unlocked()
        if migrated:
            _write_unlocked(migrated)
        return migrated
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"저장소 설정 읽기 실패({e}) — 빈 목록으로 시작")
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("repos")
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = _normalize(item.get("path") or "")
        if not path:
            continue
        host = str(item.get("host") or DEFAULT_HOST)
        rid = repo_id(host, path)
        if rid in out or len(out) >= MAX_REPOS:
            continue
        out[rid] = {"id": rid, "host": host, "path": path, "hidden": bool(item.get("hidden"))}
    return out


def _write_unlocked(items: dict[str, dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"version": VERSION, "repos": list(items.values())}, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


# --- 공개 API (rail_repos_store와 같은 이름 — routes/worktree.py가 그대로 갈아 끼운다) ----


def list_repos() -> list[dict]:
    """`{id, host, path, hidden}` 레코드 전부(경로 삭제 여부와 무관하게 저장된 것)."""
    from session_catalog.projects import listing
    return listing()


def list_hidden() -> list[str]:
    return [r["path"] for r in list_repos() if r["hidden"]]


def hidden_set() -> set[str]:
    return set(list_hidden())


def set_hidden(path: str, hidden: bool, *, host: str = DEFAULT_HOST) -> dict:
    """한 저장소의 숨김 여부를 바꾼다. 결과는 `{ok, hidden: [...]}`(경로 목록,
    옛 rail_repos_store 호출자와의 호환)."""
    from session_catalog.projects import set_hidden as catalog_set_hidden
    return catalog_set_hidden(path,hidden,host)


def is_hidden(entry: dict, hidden: set[str]) -> bool:
    """워크트리 항목 하나가 숨김 대상인가.

    저장소 본체(`repo`)를 숨기면 그 저장소의 **부가 워크트리까지** 같이
    숨는다 — 사용자가 숨기는 단위는 "이 저장소"이지 "이 체크아웃 하나"가 아니다.
    """
    if not hidden:
        return False
    for key in ("path", "repo"):
        value = entry.get(key)
        if isinstance(value, str) and _normalize(value) in hidden:
            return True
    return False
