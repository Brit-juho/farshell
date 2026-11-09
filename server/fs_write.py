"""파일 쓰기 — 저장·새 파일·이름 바꾸기·휴지통 삭제(files-overhaul 단계 8). 판정은 `fsguard.resolve_for_write` 한 곳.

공개 터널로 쓰기가 열리는 첫 경로다. 라우트(`routes/fs_write.py`)가 사람 세션 + 승격을 요구하고, 여기서는:

- **경로**: `resolve_for_write`로 판정한 resolve된 경로를 `open_resolved_parent`로 한 조각씩 `O_NOFOLLOW` 열어 얻은
  부모 dir_fd 기준으로만 만들고·바꾸고·옮긴다. 판정 뒤 부모나 마지막 조각을 심링크로 바꿔도 경계 밖에 닿지 않는다.
- **직렬화**: (부모 dev, 부모 ino, 이름) 키의 프로세스 안 잠금 아래에서 rev 비교부터 rename까지 한 번에 한다.
  같은 서버를 거치는 동시 저장 두 건 중 하나는 반드시 409 `conflict`다. 외부 편집기와의 경합은 rename 직전 재검사와
  rename 사이의 아주 짧은 구간에 남는다(문서화한 한계).
- **원자적 저장**: 같은 폴더에 `.fsh-<uuid>.tmp`를 `O_CREAT|O_EXCL|O_NOFOLLOW`로 만들고, 원래 mode를 `fchmod`로
  옮기고, 쓰고 `fsync`, rev를 다시 확인하고, `rename`, 부모 `fsync`. 실패하면 임시 파일을 지운다.
- **인코딩·줄 끝(D9)**: 지금 디스크 내용을 요청한 인코딩으로 엄격하게 풀 수 있고 줄 끝이 한 가지일 때만 저장한다.
  BOM과 줄 끝은 디스크에 있던 것을 지킨다.
- **멱등**: `save_id`별 결과를 10분 기억한다. 시간 초과(503)로 다시 보낸 같은 저장은 첫 결과를 받고 두 번 쓰지 않는다.

모두 blocking — 라우트가 `fsops.call`(fs 풀, 기한, 차단기) 안에서 부른다.
"""
from __future__ import annotations

import codecs
import errno
import hashlib
import os
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import fs_read
import fs_trash
import fsguard
from i18n import tr

# 편집 저장 상한(계획 8.4) — 디스크의 지금 파일과 새 내용 둘 다.
MAX_SAVE_BYTES = 2 * 1024 * 1024
# 요청 본문 상한. JSON 문자열 이스케이프(`\uXXXX`는 6바이트)를 감안해 넉넉히, 그래도 무한히 읽지는 않는다.
MAX_BODY_BYTES = MAX_SAVE_BYTES * 6 + 64 * 1024
# 같은 이름 잠금을 기다리는 최대 시간. 넘으면 503 — fs 풀 스레드를 오래 잡지 않는다.
LOCK_WAIT = 5.0
# save_id 결과를 기억하는 시간·개수.
SAVE_ID_TTL = 600.0
SAVE_ID_MAX = 2000
# 같은 save_id의 첫 요청이 아직 쓰는 중일 때 재시도가 기다리는 시간.
SAVE_ID_WAIT = 8.0

EOLS = {"lf": "\n", "crlf": "\r\n", "cr": "\r"}
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_IMMUTABLE = getattr(stat, "UF_IMMUTABLE", 0) | getattr(stat, "SF_IMMUTABLE", 0) | \
    getattr(stat, "UF_APPEND", 0) | getattr(stat, "SF_APPEND", 0)


class WriteError(Exception):
    """라우트가 그대로 응답으로 바꾸는 실패. `body`는 JSON 본문."""

    def __init__(self, status: int, error: str, reason: str = "", **extra):
        super().__init__(error)
        self.status = status
        self.body = {"error": error, **({"reason": reason} if reason else {}), **extra}


# --- 이름 잠금 ------------------------------------------------------------------------


class _Locks:
    """(부모 dev, 부모 ino, 이름) → 잠금. 쓰는 사람이 없으면 지운다(무한히 쌓이지 않게)."""

    def __init__(self):
        self._mu = threading.Lock()
        self._items: dict[tuple, list] = {}      # key → [Lock, refcount]

    @contextmanager
    def hold(self, *keys: tuple):
        keys = sorted(set(keys))                 # 이름 둘(이름 바꾸기)은 늘 같은 순서로 — 교착 방지
        taken: list[tuple] = []
        try:
            for key in keys:
                with self._mu:
                    item = self._items.setdefault(key, [threading.Lock(), 0])
                    item[1] += 1
                if not item[0].acquire(timeout=LOCK_WAIT):
                    self._release_ref(key)
                    raise WriteError(503, "busy", tr("같은 파일에 다른 쓰기가 진행 중입니다 — 잠시 뒤 다시 시도하세요"))
                taken.append(key)
            yield
        finally:
            for key in reversed(taken):
                self._items[key][0].release()
                self._release_ref(key)

    def _release_ref(self, key: tuple) -> None:
        with self._mu:
            item = self._items.get(key)
            if item is not None:
                item[1] -= 1
                if item[1] <= 0:
                    del self._items[key]


LOCKS = _Locks()


def _lock_key(dir_fd: int, name: str) -> tuple:
    st = os.fstat(dir_fd)
    return (st.st_dev, st.st_ino, name.casefold())


# --- save_id 기억 ----------------------------------------------------------------------


class _Saves:
    """save_id → 결과. 진행 중이면 `Event`를 기다린다. 다른 내용·다른 기기의 같은 save_id는 거부한다."""

    def __init__(self):
        self._mu = threading.Lock()
        self._items: dict[str, dict] = {}

    def _prune(self, now: float) -> None:
        dead = [k for k, v in self._items.items() if v["done"].is_set() and now - v["at"] > SAVE_ID_TTL]
        for k in dead:
            del self._items[k]
        if len(self._items) > SAVE_ID_MAX:
            done = sorted((v["at"], k) for k, v in self._items.items() if v["done"].is_set())
            for _, k in done[:len(self._items) - SAVE_ID_MAX]:
                del self._items[k]

    def lookup(self, save_id: str, owner: str, fingerprint: str) -> Optional[tuple[int, dict]]:
        """끝난 결과가 있으면 (상태, 본문). 없거나 진행 중이면 None. 다른 요청이 쓴 id면 409."""
        with self._mu:
            self._prune(time.time())
            item = self._items.get(save_id)
        if item is None:
            return None
        if item["owner"] != owner or item["fp"] != fingerprint:
            raise WriteError(409, "save_id_reused", tr("다른 저장에 쓰인 save_id입니다"))
        if item["done"].is_set():
            return item["result"]
        return None

    def begin(self, save_id: str, owner: str, fingerprint: str) -> Optional[tuple[int, dict]]:
        """처음이면 자리를 잡고 None. 이미 있으면 그 결과(진행 중이면 잠시 기다린 뒤)."""
        with self._mu:
            self._prune(time.time())
            item = self._items.get(save_id)
            if item is None:
                self._items[save_id] = {"owner": owner, "fp": fingerprint, "done": threading.Event(),
                                        "result": None, "at": time.time()}
                return None
        if item["owner"] != owner or item["fp"] != fingerprint:
            raise WriteError(409, "save_id_reused", tr("다른 저장에 쓰인 save_id입니다"))
        if not item["done"].wait(SAVE_ID_WAIT):
            raise WriteError(503, "in_progress", tr("같은 저장이 아직 진행 중입니다 — 잠시 뒤 다시 시도하세요"))
        return item["result"]

    def finish(self, save_id: str, result: tuple[int, dict]) -> None:
        with self._mu:
            item = self._items.get(save_id)
            if item is None:
                return
            item["result"] = result
            item["at"] = time.time()
            item["done"].set()

    def forget(self, save_id: str) -> None:
        """쓰지 않고 끝난 시도(잠금 대기 초과) — 자리를 비워 같은 id로 다시 시도할 수 있게. 기다리던 재시도도 깨운다."""
        with self._mu:
            item = self._items.pop(save_id, None)
        if item is not None:
            item["result"] = (503, {"error": "busy", "reason": tr("같은 파일에 다른 쓰기가 진행 중입니다 — 잠시 뒤 다시 시도하세요")})
            item["done"].set()

    def clear(self) -> None:
        with self._mu:
            self._items.clear()


SAVES = _Saves()


def fingerprint(path: str, content: str, encoding: str, eol: Optional[str], expected_rev: Optional[str]) -> str:
    h = hashlib.sha256()
    for part in (path, encoding, eol or "", expected_rev or ""):
        h.update(part.encode("utf-8", "surrogatepass"))
        h.update(b"\0")
    h.update(content.encode("utf-8", "surrogatepass"))
    return h.hexdigest()


# --- 공통 --------------------------------------------------------------------------------


@contextmanager
def _parent(p: Path):
    try:
        dir_fd, name = fsguard.open_resolved_parent(p)
    except fsguard.NotRegularFile:
        raise WriteError(400, "bad_path", tr("쓸 수 없는 경로입니다")) from None
    except FileNotFoundError:
        raise WriteError(404, "not_found", tr("폴더가 없습니다")) from None
    except OSError as e:
        raise _os_error(e, tr("폴더를 열 수 없습니다 (바뀌었거나 심링크로 바뀜)")) from None
    try:
        yield dir_fd, name
    finally:
        os.close(dir_fd)


def _os_error(e: OSError, fallback: str) -> WriteError:
    if e.errno == errno.ENOSPC or e.errno == getattr(errno, "EDQUOT", -1):
        return WriteError(507, "no_space", tr("디스크가 가득 차 저장하지 못했습니다"))
    if e.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
        return WriteError(403, "not_writable", tr("이 위치에 쓸 수 없습니다 (읽기 전용이거나 권한 없음)"))
    if e.errno in (errno.ELOOP, errno.ENOTDIR):
        return WriteError(409, "changed", tr("경로가 바뀌었습니다 (심링크로 바뀜)"))
    if e.errno == errno.EXDEV:
        return WriteError(409, "cross_device", tr("다른 디스크로는 옮길 수 없습니다"))
    if e.errno == errno.ENAMETOOLONG:
        return WriteError(400, "bad_name", tr("이름이 너무 깁니다"))
    return WriteError(500, "write_failed", fallback)


def _lstat_at(dir_fd: int, name: str) -> Optional[os.stat_result]:
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _require_regular(st: Optional[os.stat_result], *, what: str = "file") -> os.stat_result:
    if st is None:
        raise WriteError(404, "not_found", tr("파일이 없습니다"))
    if stat.S_ISLNK(st.st_mode):
        raise WriteError(400, "is_symlink", tr("심링크는 이 작업을 할 수 없습니다"))
    if stat.S_ISDIR(st.st_mode):
        raise WriteError(400, "is_directory", tr("폴더는 아직 이 작업을 할 수 없습니다 (파일만)"))
    if not stat.S_ISREG(st.st_mode):
        raise WriteError(400, "not_a_file", tr("일반 파일만 쓸 수 있습니다"))
    return st


def _require_mine_and_writable(st: os.stat_result) -> None:
    """바꿔 쓰기(rename)는 폴더 쓰기 권한만 있으면 된다 — 읽기 전용 파일·남의 파일도 갈아 끼워진다. 그래서 직접 막는다."""
    if st.st_uid != os.geteuid():
        raise WriteError(403, "not_writable", tr("다른 사용자의 파일이라 쓰지 않았습니다"))
    if not st.st_mode & stat.S_IWUSR:
        raise WriteError(403, "not_writable", tr("읽기 전용 파일입니다"))
    if getattr(st, "st_flags", 0) & _IMMUTABLE:
        raise WriteError(403, "not_writable", tr("잠긴 파일입니다"))


def check_name(name) -> str:
    """새 이름 규칙(Paseo와 같다): 비어 있지 않고, `/`·`\\`·NUL·제어 문자 없음, `.`·`..` 아님, 255바이트 이하."""
    if not isinstance(name, str) or not name or not name.strip():
        raise WriteError(400, "bad_name", tr("이름이 비었습니다"))
    if "/" in name or "\\" in name:
        raise WriteError(400, "bad_name", tr("이름에 「/」나 「\\」를 쓸 수 없습니다"))
    if name in (".", ".."):
        raise WriteError(400, "bad_name", tr("「.」과 「..」은 이름으로 쓸 수 없습니다"))
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
        raise WriteError(400, "bad_name", tr("이름에 제어 문자가 있습니다"))
    if len(name.encode("utf-8", "surrogatepass")) > 255:
        raise WriteError(400, "bad_name", tr("이름이 너무 깁니다"))
    return name


def _resolve(raw: str) -> Path:
    try:
        return fsguard.resolve_for_write(raw)
    except fsguard.FsDenied as e:
        raise WriteError(403, "denied", e.reason) from None


def _lexical_is_symlink(raw: str) -> bool:
    try:
        return stat.S_ISLNK(os.lstat(os.path.expanduser(raw)).st_mode)
    except OSError:
        return False


# --- 인코딩·줄 끝 -------------------------------------------------------------------------


def _eol_kind(text: str) -> Optional[str]:
    """한 가지면 그 이름, 줄 끝이 없으면 None, 섞였으면 `mixed`."""
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    kinds = [k for k, n in (("crlf", crlf), ("cr", cr), ("lf", lf)) if n]
    if not kinds:
        return None
    return kinds[0] if len(kinds) == 1 else "mixed"


def _current_text(raw: bytes, encoding: str) -> tuple[bool, Optional[str]]:
    """디스크의 지금 내용이 이 인코딩으로 편집 가능한가(D9) → (BOM 있음, 줄 끝 종류). 아니면 422 `not_editable`."""
    codec = fs_read.ENCODINGS[encoding]
    bom_enc, bom_len = fs_read.bom_encoding(raw)
    if bom_enc and bom_enc != encoding:
        raise WriteError(422, "not_editable", tr("파일의 BOM이 고른 인코딩과 다릅니다"), detail="encoding_mismatch")
    body = raw[bom_len:]
    if encoding not in ("utf-16le", "utf-16be") and b"\x00" in body:
        raise WriteError(422, "not_editable", tr("바이너리 파일은 편집할 수 없습니다"), detail="binary")
    try:
        text = codecs.decode(body, codec, "strict")
    except UnicodeDecodeError:
        raise WriteError(422, "not_editable", tr("이 인코딩으로 풀 수 없는 바이트가 있어 저장하지 않았습니다"),
                         detail="decode_errors") from None
    kind = _eol_kind(text)
    if kind == "mixed":
        raise WriteError(422, "not_editable", tr("줄 끝이 섞인 파일이라 저장하지 않았습니다"), detail="mixed_eol")
    return bool(bom_enc), kind


def encode_content(content: str, encoding: str, eol: str, bom: bool) -> bytes:
    """편집기 글자 → 디스크 바이트. 줄 끝을 한 가지로 맞추고, 못 바꾸는 글자는 422 `unencodable`(첫 위치)."""
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    codec = fs_read.ENCODINGS[encoding]
    try:
        text.encode(codec, "strict")
    except UnicodeEncodeError as e:
        start = e.start
        line = text.count("\n", 0, start) + 1
        col = start - (text.rfind("\n", 0, start) + 1) + 1
        ch = text[start]
        raise WriteError(422, "unencodable", tr("{encoding}로 바꿀 수 없는 글자가 있습니다", encoding=encoding),
                         line=line, col=col, char=ch if ch.isprintable() else f"U+{ord(ch):04X}") from None
    if eol != "lf":
        text = text.replace("\n", EOLS[eol])
    data = text.encode(codec, "strict")
    if bom:
        data = {"utf-8": codecs.BOM_UTF8, "utf-16le": codecs.BOM_UTF16_LE,
                "utf-16be": codecs.BOM_UTF16_BE}.get(encoding, b"") + data
    return data


def _read_all(fd: int, size: int) -> bytes:
    data = fs_read._pread_full(fd, size + 1, 0)
    if len(data) > size:
        raise WriteError(409, "conflict", tr("저장 전에 파일이 바뀌었습니다"), rev=fs_read.rev_of(os.fstat(fd)))
    return data


# --- 저장 ------------------------------------------------------------------------------


def save(raw_path: str, content: str, *, encoding: str, eol: Optional[str], expected_rev: Optional[str]) -> dict:
    """원자적 저장. `expected_rev`가 디스크 rev와 다르면 409 `conflict`(+ 지금 rev).

    `expected_rev`가 None이면 **파일이 없어야** 한다(사라진 파일을 「내 것으로 다시 만들기」) — 있으면 409."""
    if encoding not in fs_read.ENCODINGS:
        raise WriteError(400, "bad_encoding", tr("저장할 인코딩을 골라야 합니다"))
    if eol is not None and eol not in EOLS:
        raise WriteError(400, "bad_eol", tr("줄 끝 값이 잘못됐습니다"))
    p = _resolve(raw_path)
    with _parent(p) as (dir_fd, name):
        with LOCKS.hold(_lock_key(dir_fd, name)):
            if expected_rev is None:
                return _create_with(dir_fd, name, p, content, encoding, eol or "lf")
            return _replace(dir_fd, name, p, content, encoding, eol, expected_rev)


def _open_target(dir_fd: int, name: str) -> int:
    try:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError:
        raise WriteError(409, "conflict", tr("저장 전에 파일이 사라졌습니다"), rev=None) from None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise WriteError(400, "is_symlink", tr("심링크는 이 작업을 할 수 없습니다")) from None
        raise _os_error(e, tr("파일을 열 수 없습니다")) from None


def _replace(dir_fd: int, name: str, p: Path, content: str, encoding: str, eol: Optional[str],
             expected_rev: str) -> dict:
    fd = _open_target(dir_fd, name)
    try:
        st = _require_regular(os.fstat(fd))
        cur_rev = fs_read.rev_of(st)
        if cur_rev != expected_rev:
            raise WriteError(409, "conflict", tr("저장 전에 파일이 바뀌었습니다"), rev=cur_rev)
        if st.st_nlink > 1:
            raise WriteError(409, "hardlinked", tr("하드 링크가 걸린 파일이라 바꿔 쓰지 않았습니다"))
        _require_mine_and_writable(st)
        if st.st_size > MAX_SAVE_BYTES:
            raise WriteError(413, "too_large", tr("편집 저장 상한(2 MB)을 넘습니다"), limit=MAX_SAVE_BYTES)
        bom, kind = _current_text(_read_all(fd, st.st_size), encoding)
    finally:
        os.close(fd)
    use_eol = kind or eol or "lf"
    data = encode_content(content, encoding, use_eol, bom)
    if len(data) > MAX_SAVE_BYTES:
        raise WriteError(413, "too_large", tr("편집 저장 상한(2 MB)을 넘습니다"), limit=MAX_SAVE_BYTES)
    new_rev, durable = _atomic_replace(dir_fd, name, data, st, cur_rev)
    return {"ok": True, "path": str(p), "rev": new_rev, "size": len(data), "encoding": encoding,
            "eol": use_eol, "bom": bom, "durable": durable}


def _atomic_replace(dir_fd: int, name: str, data: bytes, st: os.stat_result, cur_rev: str) -> tuple[str, bool]:
    tmp = f".fsh-{uuid.uuid4().hex}.tmp"
    try:
        tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC, 0o600, dir_fd=dir_fd)
    except OSError as e:
        raise _os_error(e, tr("임시 파일을 만들 수 없습니다")) from None
    renamed = False
    try:
        try:
            os.fchmod(tfd, stat.S_IMODE(st.st_mode))
            if os.fstat(tfd).st_gid != st.st_gid:
                try:
                    os.fchown(tfd, -1, st.st_gid)
                except OSError:
                    pass                       # 그룹을 못 맞추면 폴더 기본값으로 남는다(문서화한 한계)
            view = memoryview(data)
            while view:
                n = os.write(tfd, view)
                view = view[n:]
            os.fsync(tfd)
        except OSError as e:
            raise _os_error(e, tr("임시 파일에 쓰지 못했습니다")) from None
        now = _lstat_at(dir_fd, name)
        if now is None or not stat.S_ISREG(now.st_mode) or fs_read.rev_of(now) != cur_rev:
            raise WriteError(409, "conflict", tr("저장 전에 파일이 바뀌었습니다"),
                             rev=fs_read.rev_of(now) if now is not None and stat.S_ISREG(now.st_mode) else None)
        try:
            os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except OSError as e:
            raise _os_error(e, tr("파일을 바꾸지 못했습니다")) from None
        renamed = True
        new_rev = fs_read.rev_of(os.fstat(tfd))
    finally:
        os.close(tfd)
        if not renamed:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except OSError:
                pass
    try:
        os.fsync(dir_fd)
        durable = True
    except OSError:
        durable = False
    return new_rev, durable


def _create_with(dir_fd: int, name: str, p: Path, content: str, encoding: str, eol: str) -> dict:
    data = encode_content(content, encoding, eol, False)
    if len(data) > MAX_SAVE_BYTES:
        raise WriteError(413, "too_large", tr("편집 저장 상한(2 MB)을 넘습니다"), limit=MAX_SAVE_BYTES)
    rev = _create_exclusive(dir_fd, name, data)
    return {"ok": True, "path": str(p), "rev": rev, "size": len(data), "encoding": encoding, "eol": eol,
            "bom": False, "durable": True, "created": True}


def _create_exclusive(dir_fd: int, name: str, data: bytes) -> str:
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOEXEC, 0o666, dir_fd=dir_fd)
    except FileExistsError:
        st = _lstat_at(dir_fd, name)
        raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"),
                         rev=fs_read.rev_of(st) if st is not None and stat.S_ISREG(st.st_mode) else None) from None
    except OSError as e:
        raise _os_error(e, tr("파일을 만들 수 없습니다")) from None
    ok = False
    try:
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
        rev = fs_read.rev_of(os.fstat(fd))
        ok = True
    except OSError as e:
        raise _os_error(e, tr("파일에 쓰지 못했습니다")) from None
    finally:
        os.close(fd)
        if not ok:
            try:
                os.unlink(name, dir_fd=dir_fd)     # 방금 O_EXCL로 만든 빈 파일만
            except OSError:
                pass
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    return rev


# --- 새 파일 --------------------------------------------------------------------------


def create(raw_dir: str, name) -> dict:
    """빈 새 파일. 같은 이름이 있으면 409 `exists`(덮어쓰지 않음)."""
    name = check_name(name)
    d = _resolve(raw_dir)
    p = _resolve(os.path.join(os.path.expanduser(raw_dir), name))
    if p.parent != d or p.name != name:
        # 이름이 이미 심링크로 있으면 resolve가 따라가 다른 곳을 가리킨다 — 덮어쓰지 않는다.
        raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"))
    with _parent(p) as (dir_fd, leaf):
        with LOCKS.hold(_lock_key(dir_fd, leaf)):
            rev = _create_exclusive(dir_fd, leaf, b"")
    return {"ok": True, "path": str(p), "rev": rev}


# --- 이름 바꾸기 ------------------------------------------------------------------------


def rename(raw_from: str, raw_to: str, *, overwrite: bool = False, expected_rev: Optional[str] = None) -> dict:
    """파일 하나의 이름 바꾸기·옮기기(쓰기 경계 안에서만, 같은 디스크 안에서만).

    폴더와 심링크는 거부한다(1차 범위). 대상이 있으면 409 `exists` — `overwrite`가 참이고 대상이 일반 파일일 때만 바꿔 쓴다.
    `expected_rev`를 주면 원본이 그 판일 때만."""
    if _lexical_is_symlink(raw_from):
        raise WriteError(400, "is_symlink", tr("심링크는 이 작업을 할 수 없습니다"))
    if _lexical_is_symlink(raw_to):
        # resolve가 링크를 따라가 그 대상을 덮어쓰게 된다 — 링크 자리에는 옮기지 않는다.
        raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"))
    src = _resolve(raw_from)
    dst = _resolve(raw_to)
    check_name(dst.name)
    if src == dst:
        raise WriteError(400, "same_path", tr("같은 이름입니다"))
    with _parent(src) as (sfd, sname), _parent(dst) as (dfd, dname):
        with LOCKS.hold(_lock_key(sfd, sname), _lock_key(dfd, dname)):
            st = _require_regular(_lstat_at(sfd, sname))
            if expected_rev is not None and fs_read.rev_of(st) != expected_rev:
                raise WriteError(409, "conflict", tr("파일이 바뀌었습니다"), rev=fs_read.rev_of(st))
            _require_mine_and_writable(st)
            target = _lstat_at(dfd, dname)
            case_only = target is not None and (target.st_dev, target.st_ino) == (st.st_dev, st.st_ino) \
                and sname.casefold() == dname.casefold() and os.fstat(sfd).st_ino == os.fstat(dfd).st_ino
            if target is not None and not case_only:
                if not overwrite:
                    raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"))
                _require_regular(target)
                if target.st_nlink > 1:
                    raise WriteError(409, "hardlinked", tr("하드 링크가 걸린 파일이라 바꿔 쓰지 않았습니다"))
                _require_mine_and_writable(target)
                _rename_at(sfd, sname, dfd, dname)
            elif case_only:
                _rename_at(sfd, sname, dfd, dname)          # 대소문자만 바꾸기(같은 파일)
            else:
                _rename_noreplace(sfd, sname, dfd, dname)
            for fd in {sfd, dfd}:
                try:
                    os.fsync(fd)
                except OSError:
                    pass
            moved = _lstat_at(dfd, dname)
    return {"ok": True, "path": str(dst), "rev": fs_read.rev_of(moved) if moved is not None else None}


def _rename_at(sfd: int, sname: str, dfd: int, dname: str) -> None:
    try:
        os.rename(sname, dname, src_dir_fd=sfd, dst_dir_fd=dfd)
    except OSError as e:
        raise _os_error(e, tr("이름을 바꾸지 못했습니다")) from None


def _rename_noreplace(sfd: int, sname: str, dfd: int, dname: str) -> None:
    """덮어쓰지 않는 이름 바꾸기. `link`는 대상이 있으면 원자적으로 실패한다(EEXIST) — 그 뒤 원래 이름만 지운다.
    하드 링크를 못 거는 파일시스템이면 잠금 안에서 없음을 확인하고 `rename`한다(외부 프로세스와의 아주 짧은 경합만 남는다)."""
    try:
        os.link(sname, dname, src_dir_fd=sfd, dst_dir_fd=dfd, follow_symlinks=False)
    except FileExistsError:
        raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)")) from None
    except OSError as e:
        if e.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK, errno.ENOSYS):
            raise _os_error(e, tr("이름을 바꾸지 못했습니다")) from None
        if _lstat_at(dfd, dname) is not None:
            raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)")) from None
        _rename_at(sfd, sname, dfd, dname)
        return
    try:
        os.unlink(sname, dir_fd=sfd)                  # 새 이름이 같은 inode를 가리키므로 내용은 그대로다
    except OSError as e:
        try:
            os.unlink(dname, dir_fd=dfd)              # 되돌린다 — 방금 건 링크만
        except OSError:
            pass
        raise _os_error(e, tr("이름을 바꾸지 못했습니다")) from None


# --- 삭제 = 휴지통 ------------------------------------------------------------------------


def delete(raw_path: str, *, expected_rev: Optional[str] = None) -> dict:
    """파일 하나를 OS 휴지통으로 옮긴다(`fs_trash`). 지우지 않는다. 폴더·심링크는 거부(1차 범위)."""
    if _lexical_is_symlink(raw_path):
        raise WriteError(400, "is_symlink", tr("심링크는 이 작업을 할 수 없습니다"))
    p = _resolve(raw_path)
    with _parent(p) as (dir_fd, name):
        with LOCKS.hold(_lock_key(dir_fd, name)):
            st = _require_regular(_lstat_at(dir_fd, name))
            if expected_rev is not None and fs_read.rev_of(st) != expected_rev:
                raise WriteError(409, "conflict", tr("파일이 바뀌었습니다"), rev=fs_read.rev_of(st))
            _require_mine_and_writable(st)
            try:
                out = fs_trash.trash(dir_fd, name, p, st)
            except fs_trash.TrashUnavailable as e:
                raise WriteError(409, "trash_unavailable", e.reason) from None
            except fs_trash.TrashChanged:
                raise WriteError(409, "changed", tr("경로가 바뀌었습니다 (심링크로 바뀜)")) from None
            except fs_trash.TrashMismatch:
                raise WriteError(500, "trash_mismatch",
                                 tr("휴지통으로 옮긴 항목이 확인한 파일과 다릅니다 — 휴지통을 확인하세요")) from None
    return {"ok": True, "path": str(p), "trash": out["trash"], "trash_name": out["name"]}
