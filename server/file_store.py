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
import hashlib
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


def find_by_origin(origin: str) -> dict | None:
    """A2 — 다른 호스트가 보낸 파일의 출처(`<보낸 호스트 id>:<그쪽 파일 id>`)로 찾는다.

    같은 파일을 두 번 보내도 디스크에 두 벌 쌓이지 않게 하는 유일한 키다.
    내용 해시를 쓰지 않는 이유는 `routes/peer.py`의 엔드포인트 주석에 있다.
    """
    if not origin:
        return None
    with _locked():
        for x in _read_unlocked():
            if x.get("origin") == origin:
                return x
    return None


def add_from_upload(tmp_path: Path, name: str, size: int,
                     session: str | None = None, worktree: str | None = None,
                     origin: str = "") -> dict:
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
            # A2 — 다른 호스트에서 건너온 파일이면 그 출처. 로컬 업로드는 빈 값이다.
            "origin": origin,
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

        # 만료된 공유부터 배열에서 걷어낸다 — "공유 중"의 정의는 **유효한** 공유가
        # 있다는 뜻이지, 예전에 발급됐다가 만료된 공유가 영구 보호막이 되면 안 된다.
        for x in items:
            live = [s for s in x.get("shares", []) if s.get("exp", 0) > now]
            if len(live) != len(x.get("shares", [])):
                x["shares"] = live

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


# --- 공유 링크 (N21, 50-files-share.md §3) ------------------------------------
#
# 토큰 자체의 서명·만료 검증은 routes/share.py(auth._sign 재사용)가 맡는다. 여기서는
# "취소되면 서명이 유효해도 404가 나와야 한다"는 요구를 위해 shares[] 를 파일 소유권
# 있는 이 모듈에서만 건드리게 한다 — 취소 = 배열에서 제거, 그게 전부다.

VALID_SHARE_MODES = {"device", "pin"}
MAX_PIN_ATTEMPTS = 5


def hash_pin(pin: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{pin}".encode("utf-8")).hexdigest()


def add_share(file_id: str, mode: str, ttl: int, once: bool, pin: str | None = None) -> dict | None:
    """새 공유 발급. 파일이 없으면 None(호출자가 404)."""
    if mode not in VALID_SHARE_MODES:
        raise ValueError(f"bad mode: {mode}")
    with _locked():
        items = _read_unlocked()
        item = next((x for x in items if x["id"] == file_id), None)
        if item is None:
            return None
        share: dict = {
            "shareId": secrets.token_urlsafe(6),
            "mode": mode,
            "exp": time.time() + ttl,
            "once": bool(once),
            "attempts": 0,
            "views": 0,
            "lastAccess": None,
        }
        if mode == "pin":
            salt = secrets.token_hex(8)
            share["pinSalt"] = salt
            share["pinHash"] = hash_pin(pin or "", salt)
        item.setdefault("shares", []).append(share)
        _write_unlocked(items)
    return share


def get_item_and_share(file_id: str, share_id: str) -> tuple[dict | None, dict | None]:
    """취소된 공유는 배열에 아예 없으므로 여기서 이미 None — 토큰 서명이 유효해도
    호출자는 이 결과만 보고 404를 내면 된다."""
    item = get_item(file_id)
    if item is None:
        return None, None
    share = next((s for s in item.get("shares", []) if s.get("shareId") == share_id), None)
    return item, share


def remove_share(file_id: str, share_id: str) -> bool:
    with _locked():
        items = _read_unlocked()
        item = next((x for x in items if x["id"] == file_id), None)
        if item is None:
            return False
        before = len(item.get("shares", []))
        item["shares"] = [s for s in item.get("shares", []) if s.get("shareId") != share_id]
        if len(item["shares"]) == before:
            return False
        _write_unlocked(items)
    return True


def update_share(file_id: str, share_id: str, **fields) -> dict | None:
    """attempts/views/lastAccess 등 공유 레코드 필드 갱신. 없으면 None."""
    with _locked():
        items = _read_unlocked()
        item = next((x for x in items if x["id"] == file_id), None)
        if item is None:
            return None
        share = next((s for s in item.get("shares", []) if s.get("shareId") == share_id), None)
        if share is None:
            return None
        share.update(fields)
        _write_unlocked(items)
        return share


def build_share_token(file_id: str, share: dict) -> str:
    """공유 레코드 → URL 토큰(`v1.<exp>.<fileId>.<shareId>.<hmac>`).

    routes/share.py(HTTP 발급)와 `fsh files share`(CLI, 서버 없이 동작) 양쪽이
    정확히 같은 포맷을 만들어야 서로가 발급한 링크를 서로 검증할 수 있다 —
    그래서 이 한 함수로 합쳐뒀다. auth는 여기서 함수 안에서만 import한다:
    file_store는 원래 순수 저장소 모듈이라 fastapi/starlette 의존이 없는데,
    모듈 최상단에서 import auth를 하면 그 무게가 그대로 옮아온다.
    """
    import auth
    exp = int(share["exp"])
    payload = f"v1.{exp}.{file_id}.{share['shareId']}"
    return f"{payload}.{auth.sign_payload(payload)}"


# --- CLI (N23, 50-files-share.md §6) ------------------------------------------
#
# `fsh files ...`가 서버 없이(queue_store.py/worktree.py와 같은 방식) 직접
# `file_store.py <sub> ...`를 실행한다. 공개 URL의 스킴/호스트(터널이냐 로컬이냐)는
# bin/fsh가 이미 알고 있으므로(`_main_tunnel_url`), 여기서는 `/s/<token>` 경로만
# 찍고 base URL 조립은 bash 쪽(cmd_files)에 맡긴다.

_CLI_TTL_ALIASES = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}


def _cli_fmt_size(n: int) -> str:
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.0f}{unit}"
    return f"{n}B"


def _cli_find_by_prefix(file_id_prefix: str) -> dict | None:
    matches = [x for x in list_items() if x["id"].startswith(file_id_prefix)]
    if len(matches) == 1:
        return matches[0]
    return None


def _cli(argv: list[str]) -> int:
    import sys

    cmd = argv[0] if argv else "ls"
    rest = argv[1:]

    if cmd in ("ls", "list"):
        items = sorted(list_items(), key=lambda x: x.get("created", 0), reverse=True)
        print()
        if not items:
            print("  파일이 없습니다")
        else:
            total = sum(x.get("size", 0) for x in items)
            print(f"  📁 파일 {len(items)}개 · {_cli_fmt_size(total)}")
            print()
            for x in items:
                shared = " [공유중]" if x.get("shares") else ""
                print(f"    {x['id']}  {x['name']}  {_cli_fmt_size(x.get('size', 0))}{shared}")
        print()
        return 0

    if cmd == "add":
        if not rest or rest[0].startswith("--"):
            print("  ✗ 경로가 필요합니다. 사용법: fsh files add <path> [--share 1h|24h|7d|30d] [--pin]",
                  file=sys.stderr)
            return 2
        src = Path(rest[0]).expanduser()
        if not src.is_file():
            print(f"  ✗ 파일이 없습니다: {src}", file=sys.stderr)
            return 1
        share_ttl_arg = None
        want_pin = False
        i = 1
        while i < len(rest):
            if rest[i] == "--share" and i + 1 < len(rest):
                share_ttl_arg = rest[i + 1]
                i += 2
            elif rest[i] == "--pin":
                want_pin = True
                i += 1
            else:
                i += 1
        # add는 원본을 지우지 않는다(복사) — 임시 tmp 파일을 만들어 기존
        # add_from_upload(rename 기반)에 그대로 태운다.
        import shutil
        import uuid as _uuid
        tmp = files_dir() / f".tmp-{_uuid.uuid4().hex}"
        files_dir().mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, tmp)
        item = add_from_upload(tmp, src.name, src.stat().st_size)
        print(f"  ✓ 추가됨 {item['name']} {_cli_fmt_size(item['size'])} id {item['id']}")
        if share_ttl_arg:
            ttl = _CLI_TTL_ALIASES.get(share_ttl_arg)
            if ttl is None:
                print(f"  ⚠ 알 수 없는 --share 값: {share_ttl_arg} (1h|24h|7d|30d 중 하나)", file=sys.stderr)
                return 0
            pin = None
            if want_pin:
                pin = secrets.token_hex(2)  # 4자리 숫자 대용(hex) — 화면에 보여줄 값
                print(f"  ℹ PIN: {pin}")
            share = add_share(item["id"], "pin" if want_pin else "device", ttl, False, pin=pin)
            token = build_share_token(item["id"], share)
            print(f"  ✓ 공유 경로: /s/{token}")
        return 0

    if cmd == "rm":
        if not rest:
            print("  ✗ id가 필요합니다", file=sys.stderr)
            return 2
        item = _cli_find_by_prefix(rest[0])
        if item is None:
            print(f"  ✗ '{rest[0]}' 로 특정되는 파일이 없습니다", file=sys.stderr)
            return 1
        delete(item["id"])
        print(f"  ✓ 삭제됨: {item['name']}")
        return 0

    if cmd == "share":
        if not rest:
            print("  ✗ id가 필요합니다. 사용법: fsh files share <id> [--ttl 1h|24h|7d|30d] [--pin] [--once]",
                  file=sys.stderr)
            return 2
        item = _cli_find_by_prefix(rest[0])
        if item is None:
            print(f"  ✗ '{rest[0]}' 로 특정되는 파일이 없습니다", file=sys.stderr)
            return 1
        ttl_arg, want_pin, want_once = "24h", False, False
        i = 1
        while i < len(rest):
            if rest[i] == "--ttl" and i + 1 < len(rest):
                ttl_arg = rest[i + 1]
                i += 2
            elif rest[i] == "--pin":
                want_pin = True
                i += 1
            elif rest[i] == "--once":
                want_once = True
                i += 1
            else:
                i += 1
        ttl = _CLI_TTL_ALIASES.get(ttl_arg)
        if ttl is None:
            print(f"  ✗ 알 수 없는 --ttl 값: {ttl_arg} (1h|24h|7d|30d 중 하나)", file=sys.stderr)
            return 2
        pin = None
        if want_pin:
            pin = secrets.token_hex(2)
            print(f"  ℹ PIN: {pin}")
        share = add_share(item["id"], "pin" if want_pin else "device", ttl, want_once, pin=pin)
        token = build_share_token(item["id"], share)
        print(f"  ✓ 공유 경로: /s/{token}")
        return 0

    if cmd == "unshare":
        if not rest:
            print("  ✗ id가 필요합니다", file=sys.stderr)
            return 2
        item = _cli_find_by_prefix(rest[0])
        if item is None:
            print(f"  ✗ '{rest[0]}' 로 특정되는 파일이 없습니다", file=sys.stderr)
            return 1
        shares = item.get("shares", [])
        if not shares:
            print("  ⓘ 공유 중이 아닙니다")
            return 0
        for s in list(shares):
            remove_share(item["id"], s["shareId"])
        print(f"  ✓ 공유 {len(shares)}건 취소됨")
        return 0

    if cmd == "insert":
        if not rest:
            print("  ✗ id가 필요합니다", file=sys.stderr)
            return 2
        item = _cli_find_by_prefix(rest[0])
        if item is None:
            print(f"  ✗ '{rest[0]}' 로 특정되는 파일이 없습니다", file=sys.stderr)
            return 1
        fp = real_path_for(item["id"])
        if fp is None:
            print("  ✗ 디스크에서 파일을 찾을 수 없습니다(고아 메타)", file=sys.stderr)
            return 1
        import tmux_target
        pane, mode = tmux_target.resolve_voice_target_pane()
        if not pane:
            print("  ✗ 대상 tmux pane을 찾지 못했습니다", file=sys.stderr)
            return 1
        if not tmux_target.type_to_tmux(pane, str(fp)):
            print("  ✗ 삽입 실패", file=sys.stderr)
            return 1
        print(f"  ✓ 삽입됨 → {pane} ({mode})")
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
