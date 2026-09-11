"""파일 저장소 (N19, 50-files-share.md §1) — 업로드/공유 파일의 단일 진실원.

`/tmp/vt-uploads/`(경로 문자열 기반, `/api/download?path=`)를 대체한다. 예전 방식은
API가 절대경로를 그대로 받아 fsguard 없이 `is_relative_to` 하나로만 막고 있었다 —
여기서는 아예 **API가 id만 받게** 만들어 그 클래스의 실수 자체를 없앤다.

저장 규칙은 snippet_store.py/queue_store.py와 동일: `~/.vt/`(VT_STATE_DIR로 override
가능) 아래 0700 디렉토리 + 0600 메타 파일 + flock + atomic replace.

- 실 파일: `~/.vt/files/<id>__<safe_name>` (0600)
- 메타: `~/.vt/files.json` — [{id, name, size, mime, created, session, worktree, host, shares, pin}]
- `id`는 `secrets.token_urlsafe(6)` — 공유 URL에 들어가도 안전한 짧은 랜덤 토큰.
"""

from __future__ import annotations

import fcntl
import json
import logging
import mimetypes
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = int(os.environ.get("VT_MAX_UPLOAD_MB", "200")) * 1024 * 1024
MAX_TOTAL_BYTES = int(float(os.environ.get("VT_FILES_MAX_GB", "2")) * 1024 * 1024 * 1024)
TTL_SECONDS = int(os.environ.get("VT_FILES_TTL_DAYS", "30")) * 86400
CLEANUP_INTERVAL_SECONDS = 6 * 3600

# 마이그레이션 대상 — 이전 버전이 쓰던 경로. VT_RUN_DIR 도입(2026-09) 이후
# 기본값은 /tmp/vt-uploads지만, 두 인스턴스 분리를 쓰던 사람은 여기도 갈린다.
LEGACY_UPLOAD_DIR = Path(os.environ.get("VT_RUN_DIR", "/tmp")) / "vt-uploads"


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def files_dir() -> Path:
    return _state_dir() / "files"


def _meta_path() -> Path:
    return _state_dir() / "files.json"


def _lock_path() -> Path:
    return _state_dir() / "files.lock"


def _chmod_quiet(p: Path, mode: int) -> None:
    try:
        os.chmod(p, mode)
    except OSError:
        pass


@contextmanager
def _locked():
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    _chmod_quiet(d, 0o700)
    fd_dir = files_dir()
    fd_dir.mkdir(parents=True, exist_ok=True)
    _chmod_quiet(fd_dir, 0o700)
    lp = _lock_path()
    fd = os.open(str(lp), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_unlocked() -> list[dict]:
    p = _meta_path()
    if not p.is_file():
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"files.json 읽기 실패({e}) — 빈 목록으로 시작")
        return []
    if not isinstance(data, list):
        return []
    items = [x for x in data if isinstance(x, dict) and x.get("id")]
    for x in items:
        x.setdefault("shares", [])
        x.setdefault("host", "local")
        x.setdefault("pin", False)
    return items


def _write_unlocked(items: list[dict]) -> None:
    p = _meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _chmod_quiet(p.parent, 0o700)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(p))


def _safe_name(name: str) -> str:
    """경로 구분자·제어문자를 뺀 파일명만 — 실 저장 경로는 id가 결정하므로
    이건 표시용/확장자 보존용일 뿐 접근 판정에는 전혀 관여하지 않는다."""
    base = Path((name or "").strip()).name  # ".." 나 "/" 섞인 입력에서 마지막 세그먼트만
    cleaned = "".join(c for c in base if c.isprintable() and c not in ("\x00",))
    cleaned = cleaned.strip().lstrip(".")  # 숨김파일/공백 시작 방지
    return cleaned or "file"


def _real_path(item: dict) -> Path:
    return files_dir() / f"{item['id']}__{item['name']}"


# --- 공개 API ----------------------------------------------------------------


def list_items() -> list[dict]:
    with _locked():
        return _read_unlocked()


def get_item(file_id: str) -> dict | None:
    with _locked():
        for x in _read_unlocked():
            if x["id"] == file_id:
                return x
    return None


def real_path_for(file_id: str) -> Path | None:
    """id → 실 파일 경로. 존재하지 않으면 None — 호출자는 이 하나만 보고 404를 낸다."""
    item = get_item(file_id)
    if item is None:
        return None
    fp = _real_path(item)
    return fp if fp.is_file() else None


def add_from_upload(tmp_path: Path, name: str, size: int,
                     session: str | None = None, worktree: str | None = None) -> dict:
    """이미 디스크에 받아둔 파일(tmp_path)을 저장소로 편입 — 데이터 재복사 없이 rename.

    호출자(routes)가 스트리밍 업로드를 먼저 tmp 위치에 0600으로 써두고 넘긴다.
    """
    safe = _safe_name(name)
    mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
    with _locked():
        items = _read_unlocked()
        file_id = secrets.token_urlsafe(6)
        # 극히 드물지만 id 충돌 방지
        existing_ids = {x["id"] for x in items}
        while file_id in existing_ids:
            file_id = secrets.token_urlsafe(6)
        item = {
            "id": file_id,
            "name": safe,
            "size": size,
            "mime": mime,
            "created": time.time(),
            "session": session,
            "worktree": worktree,
            "host": "local",
            "shares": [],
            "pin": False,
        }
        dest = _real_path(item)
        os.replace(str(tmp_path), str(dest))
        _chmod_quiet(dest, 0o600)
        items.append(item)
        _write_unlocked(items)
    return item


def delete(file_id: str) -> bool:
    with _locked():
        items = _read_unlocked()
        rest = [x for x in items if x["id"] != file_id]
        if len(rest) == len(items):
            return False
        target = next(x for x in items if x["id"] == file_id)
        try:
            _real_path(target).unlink(missing_ok=True)
        except OSError as e:
            logger.warning(f"파일 삭제 실패({file_id}): {e}")
        _write_unlocked(rest)
    return True


def _is_protected(item: dict, now: float) -> bool:
    """정리 대상에서 뺀다 — 공유 중이거나 사용자가 고정한 파일."""
    return bool(item.get("shares")) or bool(item.get("pin"))


def cleanup() -> dict:
    """TTL 초과 삭제 → 그래도 상한 초과면 오래된 순 삭제. 공유 중/고정 파일은 제외.

    반환값은 로그/테스트용 요약. 실패한 개별 unlink는 건너뛰고 메타에서만 제거해
    디스크에 고아가 남더라도 서비스는 계속 돈다(다음 재시작 시 고아 정리로 회수).
    """
    now = time.time()
    removed_ttl = removed_cap = 0
    with _locked():
        items = _read_unlocked()

        kept = []
        for x in items:
            if not _is_protected(x, now) and (now - x.get("created", now)) > TTL_SECONDS:
                _real_path(x).unlink(missing_ok=True)
                removed_ttl += 1
            else:
                kept.append(x)

        total = sum(x.get("size", 0) for x in kept)
        if total > MAX_TOTAL_BYTES:
            # 보호 대상(공유 중/고정)은 건드리지 않고, 나머지 중 오래된 것부터 지운다.
            evictable = sorted(
                (x for x in kept if not _is_protected(x, now)),
                key=lambda x: x.get("created", 0),
            )
            evicted_ids: set[str] = set()
            for x in evictable:
                if total <= MAX_TOTAL_BYTES:
                    break
                _real_path(x).unlink(missing_ok=True)
                total -= x.get("size", 0)
                evicted_ids.add(x["id"])
                removed_cap += 1
            kept = [x for x in kept if x["id"] not in evicted_ids]

        _write_unlocked(kept)
    if removed_ttl or removed_cap:
        logger.info(f"file_store cleanup: ttl={removed_ttl} cap={removed_cap} 삭제")
    return {"removed_ttl": removed_ttl, "removed_cap": removed_cap, "kept": len(kept)}


def migrate_legacy() -> int:
    """`/tmp/vt-uploads/*`(또는 VT_RUN_DIR 기준 경로)를 저장소로 편입.

    서버 기동 시 1회 호출. 개별 파일 실패는 건너뛰고 계속 진행 — 마이그레이션
    하나가 막혔다고 기동 전체가 막히면 안 된다.
    """
    legacy = LEGACY_UPLOAD_DIR
    if not legacy.is_dir():
        return 0
    moved = 0
    for entry in sorted(legacy.iterdir()):
        if not entry.is_file():
            continue
        try:
            size = entry.stat().st_size
            add_from_upload(entry, entry.name, size)
            moved += 1
        except OSError as e:
            logger.warning(f"레거시 업로드 이전 실패({entry.name}): {e}")
    if moved:
        logger.info(f"레거시 업로드 {moved}개를 ~/.vt/files/로 이전")
    return moved


def reconcile_orphans() -> dict:
    """메타에 없는 실 파일 / 실 파일 없는 메타 항목을 정리 — 비정상 종료 이후 대비.

    기동 시 migrate_legacy 다음에 호출한다.
    """
    with _locked():
        items = _read_unlocked()
        known_files = {_real_path(x).name for x in items}
        removed_meta = 0
        kept = []
        for x in items:
            if _real_path(x).is_file():
                kept.append(x)
            else:
                removed_meta += 1
        removed_disk = 0
        fd_dir = files_dir()
        if fd_dir.is_dir():
            for entry in fd_dir.iterdir():
                if entry.is_file() and entry.name not in known_files:
                    try:
                        entry.unlink()
                        removed_disk += 1
                    except OSError:
                        pass
        if removed_meta:
            _write_unlocked(kept)
    if removed_meta or removed_disk:
        logger.info(f"file_store 고아 정리: meta={removed_meta} disk={removed_disk}")
    return {"removed_meta": removed_meta, "removed_disk": removed_disk}
