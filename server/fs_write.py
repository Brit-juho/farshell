"""파일 쓰기 — 저장·새 파일·이름 바꾸기·휴지통 삭제(files-overhaul 단계 8). 판정은 `fsguard.resolve_for_write` 한 곳.

공개 터널로 쓰기가 열리는 첫 경로다. 라우트(`routes/fs_write.py`)가 사람 세션 + 승격을 요구하고, 여기서는:

- **경로**: `resolve_for_write`로 판정한 resolve된 경로를 `open_resolved_parent`로 한 조각씩 `O_NOFOLLOW` 열어 얻은
  부모 dir_fd 기준으로만 만들고·바꾸고·옮긴다. 판정 뒤 부모나 마지막 조각을 심링크로 바꿔도 경계 밖에 닿지 않는다.
- **직렬화**: (부모 dev, 부모 ino, 이름) 키의 프로세스 안 잠금 아래에서 rev 비교부터 rename까지 한 번에 한다.
  같은 서버를 거치는 동시 저장 두 건 중 하나는 반드시 409 `conflict`다. 외부 편집기와의 경합은 rename 직전 재검사와
  rename 사이의 아주 짧은 구간에 남는다(문서화한 한계).
- **원자적 저장**: 같은 폴더에 `.fsh-<uuid>.tmp`를 `O_CREAT|O_EXCL|O_NOFOLLOW`(0600)로 만들고, 쓰고, 원래 mode를
  `fchmod`로 옮기고 `fsync`, rev를 다시 확인하고, `rename`, 부모 `fsync`. 실패하면 임시 파일을 지운다(아직 그 inode일
  때만). 서버가 도중에 죽어 남은 0600 임시 파일은 다음 저장이 그 폴더에서 치운다(`sweep_stale_temps`).
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
import re
import stat
import sys
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
    """macOS는 대소문자·정규화(NFC/NFD)를 가리지 않아 `Café`와 `cafe\\u0301`이 같은 항목이다 — 같은 잠금을 쓴다."""
    st = os.fstat(dir_fd)
    return (st.st_dev, st.st_ino, fsguard.write_name_key(name))


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


def _same(a: Optional[os.stat_result], b: Optional[os.stat_result]) -> bool:
    return a is not None and b is not None and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _unlink_if_same(dir_fd: int, name: str, mine: os.stat_result) -> bool:
    """정리용 unlink — `name`이 아직 **우리가 만든 그 inode**일 때만 지운다. 그 사이 다른 항목으로 바뀌었으면 두고
    False. (lstat과 unlink 사이의 틈은 남는다 — 같은 사용자의 프로세스가 그 순간 이름을 바꿔치기해야 한다.)"""
    try:
        if not _same(_lstat_at(dir_fd, name), mine):
            return False
        os.unlink(name, dir_fd=dir_fd)
        return True
    except OSError:
        return False


def _refuse_denied_alias(dir_fd: int, name: str) -> None:
    """이름 정규화(`resolve_for_write`)를 지나도, 같은 폴더의 `.git`·훅 설정과 **같은 inode**인 이름은 쓰지 않는다."""
    reason = fsguard.write_denied_alias_at(dir_fd, name)
    if reason:
        raise WriteError(403, "denied", reason)


def _require_regular(st: Optional[os.stat_result], *, what: str = "file") -> os.stat_result:
    if st is None:
        raise WriteError(404, "not_found", tr("파일이 없습니다"))
    if stat.S_ISLNK(st.st_mode):
        raise WriteError(400, "is_symlink", tr("심링크는 이 작업을 할 수 없습니다"))
    if stat.S_ISDIR(st.st_mode):
        raise WriteError(400, "is_directory", tr("폴더입니다 — 폴더 작업(kind: dir)으로 요청해야 합니다"))
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
        sweep_stale_temps(dir_fd)
        with LOCKS.hold(_lock_key(dir_fd, name)):
            _refuse_denied_alias(dir_fd, name)
            if expected_rev is None:
                return _create_with(dir_fd, name, p, content, encoding, eol or "lf")
            return _replace(dir_fd, name, p, content, encoding, eol, expected_rev)


# --- 남은 임시 파일 치우기 ------------------------------------------------------------------
# 저장 도중 서버가 죽으면 `.fsh-<uuid>.tmp`가 남는다. 다음 저장 때 **그 폴더에서만**, 아래 조건을 모두 갖춘 것만
# 지운다: 정확히 우리 이름 모양, 일반 파일, 내 것, 하드 링크 없음, mode 0600(쓰는 동안의 mode — 원래 mode는 rename
# 직전에 옮긴다), STALE_TEMP_AGE보다 오래됨. dir_fd 기준 `O_NOFOLLOW`로 열어 확인한 inode만 지운다. 한 폴더는
# STALE_SWEEP_EVERY마다 한 번, 한 번에 STALE_SWEEP_SCAN개까지만 본다(큰 폴더에서 저장이 느려지지 않게).

_TEMP_NAME = re.compile(r"\.fsh-[0-9a-f]{32}\.tmp")
STALE_TEMP_AGE = 15 * 60.0
STALE_SWEEP_EVERY = 10 * 60.0
STALE_SWEEP_SCAN = 10000
_swept: dict[tuple, float] = {}
_swept_mu = threading.Lock()


def sweep_stale_temps(dir_fd: int, *, now: Optional[float] = None) -> int:
    """`dir_fd` 폴더에 남은 오래된 우리 임시 파일을 지우고 지운 개수를 돌려준다. 실패는 조용히 넘긴다(저장을 막지 않는다)."""
    now = time.time() if now is None else now
    try:
        dst = os.fstat(dir_fd)
    except OSError:
        return 0
    key = (dst.st_dev, dst.st_ino)
    with _swept_mu:
        if now - _swept.get(key, float("-inf")) < STALE_SWEEP_EVERY:
            return 0
        _swept[key] = now
        if len(_swept) > 4096:
            for k, _ in sorted(_swept.items(), key=lambda kv: kv[1])[:2048]:
                del _swept[k]
    removed = 0
    try:
        with os.scandir(dir_fd) as it:
            for i, entry in enumerate(it):
                if i >= STALE_SWEEP_SCAN:
                    break
                if _TEMP_NAME.fullmatch(entry.name) and _remove_stale_temp(dir_fd, entry.name, now):
                    removed += 1
    except OSError:
        pass
    return removed


def _remove_stale_temp(dir_fd: int, name: str, now: float) -> bool:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC, dir_fd=dir_fd)
    except OSError:
        return False
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1 \
            or stat.S_IMODE(st.st_mode) != 0o600 or now - st.st_mtime < STALE_TEMP_AGE:
        return False
    return _unlink_if_same(dir_fd, name, st)


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
        mine: Optional[os.stat_result] = os.fstat(tfd)
    except OSError:
        mine = None                            # 확인할 수 없으면 지우지 않는다(0600으로 남아 다음 저장이 치운다)
    try:
        try:
            if mine is None:
                raise OSError(errno.EIO, "fstat")
            view = memoryview(data)
            while view:
                n = os.write(tfd, view)
                view = view[n:]
            # 원래 mode는 다 쓴 뒤에 옮긴다 — 쓰는 도중 죽어 남은 임시 파일은 0600이라 다음 저장이 알아보고 치운다.
            os.fchmod(tfd, stat.S_IMODE(st.st_mode))
            if os.fstat(tfd).st_gid != st.st_gid:
                try:
                    os.fchown(tfd, -1, st.st_gid)
                except OSError:
                    pass                       # 그룹을 못 맞추면 폴더 기본값으로 남는다(문서화한 한계)
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
        if not renamed and mine is not None:
            _unlink_if_same(dir_fd, tmp, mine)     # 이름으로 지우되, 아직 우리가 만든 그 inode일 때만
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
    mine: Optional[os.stat_result] = None
    try:
        mine = os.fstat(fd)
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
        if not ok and mine is not None:
            _unlink_if_same(dir_fd, name, mine)    # 방금 O_EXCL로 만든 그 파일일 때만 — 바뀌었으면 두고 간다
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
            _refuse_denied_alias(dir_fd, leaf)
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
            _refuse_denied_alias(sfd, sname)
            _refuse_denied_alias(dfd, dname)
            st = _require_regular(_lstat_at(sfd, sname))
            if expected_rev is not None and fs_read.rev_of(st) != expected_rev:
                raise WriteError(409, "conflict", tr("파일이 바뀌었습니다"), rev=fs_read.rev_of(st))
            _require_mine_and_writable(st)
            target = _lstat_at(dfd, dname)
            # 대소문자만 바꾸기: 같은 폴더(dev+ino — 다른 장치의 폴더가 같은 ino일 수 있다)의 같은 파일.
            case_only = _same(target, st) and fsguard.write_name_key(sname) == fsguard.write_name_key(dname) \
                and _same(os.fstat(sfd), os.fstat(dfd))
            if target is not None and not case_only:
                if not overwrite:
                    raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"))
                _require_regular(target)
                if target.st_nlink > 1:
                    raise WriteError(409, "hardlinked", tr("하드 링크가 걸린 파일이라 바꿔 쓰지 않았습니다"))
                _require_mine_and_writable(target)
                _require_unchanged(sfd, sname, st)
                _require_unchanged(dfd, dname, target)      # 확인한 대상이 그대로일 때만(심링크로 바뀌었으면 거부)
                _rename_at(sfd, sname, dfd, dname)
            elif case_only:
                _require_unchanged(sfd, sname, st)
                _rename_at(sfd, sname, dfd, dname)          # 대소문자만 바꾸기(같은 파일)
            else:
                _require_unchanged(sfd, sname, st)
                _rename_noreplace(sfd, sname, dfd, dname, st)
            for fd in {sfd, dfd}:
                try:
                    os.fsync(fd)
                except OSError:
                    pass
            moved = _lstat_at(dfd, dname)
    return {"ok": True, "path": str(dst), "rev": fs_read.rev_of(moved) if moved is not None else None}


def _require_unchanged(dir_fd: int, name: str, checked: os.stat_result) -> None:
    """rename 바로 앞에서 dir_fd 기준 lstat을 다시 본다 — 확인한 그 일반 파일(dev, ino)이 아니면(심링크로 바뀜,
    다른 파일로 바뀜, 사라짐) 409 `changed`. 남는 틈은 이 lstat과 rename 사이뿐이고, rename은 심링크를 따라가지 않는다."""
    now = _lstat_at(dir_fd, name)
    if now is None or not stat.S_ISREG(now.st_mode) or not _same(now, checked):
        raise WriteError(409, "changed", tr("경로가 바뀌었습니다 (심링크로 바뀜)"))


def _rename_at(sfd: int, sname: str, dfd: int, dname: str) -> None:
    try:
        os.rename(sname, dname, src_dir_fd=sfd, dst_dir_fd=dfd)
    except OSError as e:
        raise _os_error(e, tr("이름을 바꾸지 못했습니다")) from None


def _rename_noreplace(sfd: int, sname: str, dfd: int, dname: str, st: os.stat_result) -> None:
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
    # 원래 이름을 지운다 — 새 이름이 같은 inode를 가리키므로 내용은 그대로다. 그 사이 원래 이름이 다른 항목으로
    # 바뀌었으면 지우지 않고 방금 건 링크만 되돌린다.
    if not _same(_lstat_at(sfd, sname), st):
        _undo_link(dfd, dname, st)
        raise WriteError(409, "changed", tr("경로가 바뀌었습니다 (심링크로 바뀜)"))
    try:
        os.unlink(sname, dir_fd=sfd)
    except OSError as e:
        _undo_link(dfd, dname, st)
        raise _os_error(e, tr("이름을 바꾸지 못했습니다")) from None


def _undo_link(dfd: int, dname: str, st: os.stat_result) -> None:
    """방금 `link`로 건 새 이름을 되돌린다 — 그 이름이 아직 우리 inode이고 **다른 이름도 남아 있을 때만**
    (`st_nlink > 1`). 마지막 이름이면 지우지 않는다(내용을 잃느니 새 이름에 남겨 둔다)."""
    now = _lstat_at(dfd, dname)
    if _same(now, st) and now.st_nlink > 1:
        _unlink_if_same(dfd, dname, now)


# --- 삭제 = 휴지통 ------------------------------------------------------------------------


def delete(raw_path: str, *, expected_rev: Optional[str] = None) -> dict:
    """파일 하나를 OS 휴지통으로 옮긴다(`fs_trash`). 지우지 않는다. 폴더·심링크는 거부(1차 범위)."""
    if _lexical_is_symlink(raw_path):
        raise WriteError(400, "is_symlink", tr("심링크는 이 작업을 할 수 없습니다"))
    p = _resolve(raw_path)
    with _parent(p) as (dir_fd, name):
        with LOCKS.hold(_lock_key(dir_fd, name)):
            _refuse_denied_alias(dir_fd, name)
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


# --- 폴더: 새 폴더 · 이름 바꾸기 · 휴지통(결정 D2 후속) ---------------------------------------------
# 파일과 같은 규칙(승격 · 감사 · 이름 잠금 · dir_fd 기준)에 폴더만의 규칙을 더한다:
# - 판정은 `fsguard.resolve_dir_for_write` — 루트 자신·루트를 품는 폴더, 숨은 상태 폴더 이름도 거부.
# - 심링크 폴더는 거부한다(경로가 링크면 링크를 따라간 **대상**이 옮겨지거나 휴지통에 간다).
# - 자기 안으로 옮기기는 resolve된 경로로 먼저 거부하고, 커널도 EINVAL로 막는다.
# - 이름 바꾸기는 **절대 덮어쓰지 않는다** — `rename(2)`은 빈 폴더를 말없이 갈아 끼우므로 `RENAME_EXCL`
#   (macOS `renameatx_np`, Linux `renameat2` `RENAME_NOREPLACE`)로 원자적으로 거절한다. 없는 시스템은 잠금 안에서
#   없음을 확인하고 옮긴다(그 사이 외부 프로세스가 빈 폴더를 만드는 아주 짧은 틈만 남는다).
# - 휴지통은 내용이 있어도 된다(지우는 게 아니라 옮기기). 마운트 지점은 옮기지 않는다.
# - 요청은 `kind: "dir"`를 밝혀야 폴더로 다룬다 — 파일 삭제 요청이 바꿔치기로 폴더 통째 삭제가 되지 않게.


def _resolve_dir(raw: str) -> Path:
    try:
        return fsguard.resolve_dir_for_write(raw)
    except fsguard.FsDenied as e:
        raise WriteError(403, "denied", e.reason) from None


def _require_dir(st: Optional[os.stat_result], dir_fd: int) -> os.stat_result:
    if st is None:
        raise WriteError(404, "not_found", tr("폴더가 없습니다"))
    if stat.S_ISLNK(st.st_mode):
        raise WriteError(400, "is_symlink", tr("심링크는 이 작업을 할 수 없습니다"))
    if not stat.S_ISDIR(st.st_mode):
        raise WriteError(400, "not_a_directory", tr("폴더가 아닙니다"))
    if st.st_dev != os.fstat(dir_fd).st_dev:
        raise WriteError(400, "mount_point", tr("다른 디스크가 연결된 폴더(마운트 지점)는 옮길 수 없습니다"))
    return st


def _require_dir_unchanged(dir_fd: int, name: str, checked: os.stat_result) -> None:
    now = _lstat_at(dir_fd, name)
    if now is None or not stat.S_ISDIR(now.st_mode) or not _same(now, checked):
        raise WriteError(409, "changed", tr("경로가 바뀌었습니다 (심링크로 바뀜)"))


def mkdir(raw_dir: str, name) -> dict:
    """빈 새 폴더. 같은 이름(파일·폴더·심링크)이 있으면 409 `exists`."""
    name = check_name(name)
    d = _resolve(raw_dir)
    p = _resolve_dir(os.path.join(os.path.expanduser(raw_dir), name))
    if p.parent != d or p.name != name:
        raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"))
    with _parent(p) as (dir_fd, leaf):
        with LOCKS.hold(_lock_key(dir_fd, leaf)):
            _refuse_denied_alias(dir_fd, leaf)
            try:
                os.mkdir(leaf, 0o777, dir_fd=dir_fd)      # 마지막 조각이 심링크여도 따라가지 않는다(EEXIST)
            except FileExistsError:
                raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)")) from None
            except OSError as e:
                raise _os_error(e, tr("폴더를 만들 수 없습니다")) from None
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
    return {"ok": True, "path": str(p), "kind": "dir"}


def rename_dir(raw_from: str, raw_to: str) -> dict:
    """폴더 하나의 이름 바꾸기·옮기기. 대상이 있으면 늘 409 `exists`(폴더는 덮어쓰지 않는다)."""
    if _lexical_is_symlink(raw_from):
        raise WriteError(400, "is_symlink", tr("심링크는 이 작업을 할 수 없습니다"))
    if _lexical_is_symlink(raw_to):
        raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"))
    src = _resolve_dir(raw_from)
    dst = _resolve_dir(raw_to)
    check_name(dst.name)
    if src == dst:
        raise WriteError(400, "same_path", tr("같은 이름입니다"))
    if dst.is_relative_to(src):
        raise WriteError(400, "into_itself", tr("폴더를 자기 안으로 옮길 수 없습니다"))
    with _parent(src) as (sfd, sname), _parent(dst) as (dfd, dname):
        with LOCKS.hold(_lock_key(sfd, sname), _lock_key(dfd, dname)):
            _refuse_denied_alias(sfd, sname)
            _refuse_denied_alias(dfd, dname)
            st = _require_dir(_lstat_at(sfd, sname), sfd)
            _require_mine_and_writable(st)
            target = _lstat_at(dfd, dname)
            case_only = _same(target, st) and fsguard.write_name_key(sname) == fsguard.write_name_key(dname) \
                and _same(os.fstat(sfd), os.fstat(dfd))
            if target is not None and not case_only:
                raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"))
            _require_dir_unchanged(sfd, sname, st)
            if case_only:
                _rename_at(sfd, sname, dfd, dname)
            else:
                _rename_dir_noreplace(sfd, sname, dfd, dname)
            for fd in {sfd, dfd}:
                try:
                    os.fsync(fd)
                except OSError:
                    pass
    return {"ok": True, "path": str(dst), "kind": "dir"}


# macOS `RENAME_EXCL`(0x4), Linux `RENAME_NOREPLACE`(0x1).
_RENAME_EXCL_FLAG = 0x4 if sys.platform == "darwin" else 0x1
_renamex: Optional[tuple] = None
_renamex_checked = False


def _renamex_fn() -> Optional[tuple]:
    """`(renameatx_np 또는 renameat2, ctypes)`. 이 C 라이브러리에 없으면 None."""
    global _renamex, _renamex_checked
    if _renamex_checked:
        return _renamex
    _renamex_checked = True
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        fn = getattr(libc, "renameatx_np" if sys.platform == "darwin" else "renameat2")
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        _renamex = (fn, ctypes)
    except (OSError, AttributeError):
        _renamex = None
    return _renamex


def _rename_dir_noreplace(sfd: int, sname: str, dfd: int, dname: str) -> None:
    """덮어쓰지 않는 폴더 이름 바꾸기. 원자적 `RENAME_EXCL`이 안 되면 잠금 안에서 없음을 확인하고 `rename`."""
    got = _renamex_fn()
    if got is not None:
        fn, ctypes = got
        rc = fn(sfd, os.fsencode(sname), dfd, os.fsencode(dname), _RENAME_EXCL_FLAG)
        if rc == 0:
            return
        err = ctypes.get_errno()
        if err not in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise _dir_os_error(OSError(err, os.strerror(err)))
        # 플래그를 모르는 파일시스템(또는 EINVAL — 자기 안으로 옮기기면 아래 rename이 같은 EINVAL을 낸다)
    if _lstat_at(dfd, dname) is not None:
        raise WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"))
    try:
        os.rename(sname, dname, src_dir_fd=sfd, dst_dir_fd=dfd)
    except OSError as e:
        raise _dir_os_error(e) from None


def _dir_os_error(e: OSError) -> WriteError:
    if e.errno == errno.EINVAL:
        return WriteError(400, "into_itself", tr("폴더를 자기 안으로 옮길 수 없습니다"))
    if e.errno in (errno.EEXIST, errno.ENOTEMPTY):
        return WriteError(409, "exists", tr("같은 이름이 이미 있습니다 (덮어쓰지 않음)"))
    if e.errno == errno.EBUSY:
        return WriteError(409, "busy", tr("사용 중인 폴더라 옮기지 못했습니다"))
    return _os_error(e, tr("이름을 바꾸지 못했습니다"))


def delete_dir(raw_path: str) -> dict:
    """폴더 하나를 OS 휴지통으로 옮긴다(내용이 있어도 된다 — 지우지 않고 옮긴다). 휴지통을 못 쓰면 409, 지우지 않는다."""
    if _lexical_is_symlink(raw_path):
        raise WriteError(400, "is_symlink", tr("심링크는 이 작업을 할 수 없습니다"))
    p = _resolve_dir(raw_path)
    with _parent(p) as (dir_fd, name):
        with LOCKS.hold(_lock_key(dir_fd, name)):
            _refuse_denied_alias(dir_fd, name)
            st = _require_dir(_lstat_at(dir_fd, name), dir_fd)
            _require_mine_and_writable(st)
            try:
                out = fs_trash.trash(dir_fd, name, p, st)
            except fs_trash.TrashUnavailable as e:
                raise WriteError(409, "trash_unavailable", e.reason) from None
            except fs_trash.TrashChanged:
                raise WriteError(409, "changed", tr("경로가 바뀌었습니다 (심링크로 바뀜)")) from None
            except fs_trash.TrashMismatch:
                raise WriteError(500, "trash_mismatch",
                                 tr("휴지통으로 옮긴 항목이 확인한 폴더와 다릅니다 — 휴지통을 확인하세요")) from None
    return {"ok": True, "path": str(p), "kind": "dir", "trash": out["trash"], "trash_name": out["name"]}
