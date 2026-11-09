"""파일 삭제 = OS 휴지통으로 옮기기(결정 D2). **지우는(unlink) 길은 없다.**

- macOS: `NSFileManager.trashItemAtURL`(별도 프로세스 `fs_trash_macos.py`). Finder와 같은 길이라 휴지통 `.DS_Store`에
  원래 위치가 남아 Finder 「되돌리기」로 돌아간다. 다른 볼륨이면 그 볼륨의 `.Trashes/<uid>`로 간다.
- Linux·WSL: freedesktop.org 휴지통 규격 1.0. 같은 장치면 `$XDG_DATA_HOME/Trash`(기본 `~/.local/share/Trash`),
  다른 장치면 그 마운트 꼭대기의 `.Trash/<uid>`(끈적 비트가 있는 진짜 폴더일 때만) 또는 `.Trash-<uid>`.
  `info/<이름>.trashinfo`(`Path=`, `DeletionDate=`)를 `O_EXCL`로 먼저 만들어 이름을 잡고, 파일은 판정한 부모 dir_fd
  기준 `rename`으로 `files/<이름>`에 옮긴다 — 경로를 다시 해석하지 않는다.
- 그 밖의 OS, 휴지통을 만들 수 없는 곳(권한, 읽기 전용, 다른 장치로의 이동): `TrashUnavailable` → 409
  `trash_unavailable`. 대신 지우지 않는다.

한계(문서화): macOS 도우미는 경로 문자열을 받는다. 그래서 부르기 직전에 경로로 본 (dev, ino)가 dir_fd로 본 것과 같은지
확인하고, 옮긴 뒤 휴지통 쪽 항목의 (dev, ino)가 같은지 다시 확인한다. 그 사이(수 밀리초)에 상위 폴더를 바꿔치기하면
다른 파일이 휴지통으로 갈 수 있다 — 지워지지는 않고 휴지통에서 되돌릴 수 있으며, 서버는 `TrashMismatch`로 알린다.
"""
from __future__ import annotations

import errno
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from i18n import tr
from resilience import proc

HELPER = Path(__file__).resolve().parent / "fs_trash_macos.py"
HELPER_TIMEOUT = 15.0

# 시험이 macOS에서 freedesktop 구현을 돌릴 수 있게 둔 선택(None이면 플랫폼으로 고른다).
IMPL: Optional[str] = None


class TrashUnavailable(Exception):
    """휴지통으로 옮길 수 없다. 파일은 그대로다."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class TrashMismatch(Exception):
    """옮긴 항목이 판정한 파일과 다르다(경합). 휴지통에서 되돌릴 수 있다."""


class TrashChanged(Exception):
    """옮기기 직전에 경로가 판정한 파일을 가리키지 않는다(바꿔치기)."""


def _impl() -> str:
    if IMPL:
        return IMPL
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "freedesktop"
    return "none"


def trash(dir_fd: int, name: str, path: Path, st: os.stat_result) -> dict:
    """`dir_fd` 안의 `name`(= 판정한 `path`, `st`는 dir_fd 기준 lstat)을 휴지통으로 옮긴다.

    돌려주는 것: `{"trash": "macos"|"freedesktop", "name": <휴지통 안 이름>}`."""
    kind = _impl()
    if kind == "macos":
        return _trash_macos(path, st)
    if kind == "freedesktop":
        return _trash_freedesktop(dir_fd, name, path, st)
    raise TrashUnavailable(tr("이 운영체제에서는 휴지통을 쓸 수 없어 삭제하지 않았습니다"))


def _same(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


# --- macOS --------------------------------------------------------------------------


def _trash_macos(path: Path, st: os.stat_result) -> dict:
    try:
        cur = os.lstat(str(path))
    except OSError as e:
        raise TrashChanged(str(e)) from None
    if not _same(cur, st) or not stat.S_ISREG(cur.st_mode):
        raise TrashChanged("path no longer names the checked file")
    try:
        cp = proc.run([sys.executable, "-I", str(HELPER), str(path)], timeout=HELPER_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise TrashUnavailable(tr("휴지통 이동이 응답하지 않아 멈췄습니다")) from None
    out = _parse_helper(cp.stdout)
    if out is None:
        # 도우미가 죽었다. 옮겨졌는지 모른다 — 원래 자리에 그대로면 「못 옮김」, 없으면 결과 불명으로 알린다.
        if _still_there(path, st):
            raise TrashUnavailable(tr("휴지통 도우미가 실패해 삭제하지 않았습니다"))
        raise TrashMismatch("helper failed after the file moved")
    if not out.get("ok"):
        reason = str(out.get("reason") or "")
        raise TrashUnavailable(tr("휴지통으로 옮기지 못해 삭제하지 않았습니다: {reason}", reason=reason))
    dest = str(out.get("path") or "")
    try:
        moved = os.lstat(dest)
    except OSError:
        raise TrashMismatch("trash item not found") from None
    if not _same(moved, st):
        raise TrashMismatch("trash item is a different file")
    return {"trash": "macos", "name": Path(dest).name}


def _parse_helper(raw: Optional[bytes]) -> Optional[dict]:
    try:
        line = (raw or b"").decode("utf-8", "replace").strip().splitlines()[-1]
        out = json.loads(line)
    except (IndexError, ValueError):
        return None
    return out if isinstance(out, dict) else None


def _still_there(path: Path, st: os.stat_result) -> bool:
    try:
        return _same(os.lstat(str(path)), st)
    except OSError:
        return False


# --- freedesktop (Linux, WSL) --------------------------------------------------------

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _home_trash() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    data = Path(base) if base and os.path.isabs(base) else Path.home() / ".local" / "share"
    return data / "Trash"


def _mkdir_private(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass


def _open_dir(path: Path) -> int:
    fd = os.open(str(path), _DIR_FLAGS)
    st = os.fstat(fd)
    if st.st_uid != os.geteuid():
        os.close(fd)
        raise TrashUnavailable(tr("휴지통 폴더의 주인이 다릅니다: {path}", path=str(path)))
    return fd


def _topdir(path: Path, dev: int) -> Path:
    """`path`가 있는 마운트의 꼭대기 — 장치 번호가 바뀌기 직전 폴더."""
    cur = path.parent
    while cur.parent != cur:
        try:
            if os.stat(cur.parent).st_dev != dev:
                break
        except OSError:
            break
        cur = cur.parent
    return cur


def _pick_trash(path: Path, dev: int) -> tuple[Path, str]:
    """(휴지통 폴더, `.trashinfo`에 쓸 Path 값)."""
    home = _home_trash()
    try:
        home.parent.mkdir(parents=True, exist_ok=True)
        _mkdir_private(home)
        home_dev = os.stat(home).st_dev
    except OSError:
        home_dev = None
    if home_dev == dev:
        return home, quote(str(path), safe="/")
    top = _topdir(path, dev)
    rel = quote(str(path.relative_to(top)), safe="/")
    uid = os.geteuid()
    shared = top / ".Trash"
    try:
        sst = os.lstat(shared)
        if stat.S_ISDIR(sst.st_mode) and not stat.S_ISLNK(sst.st_mode) and sst.st_mode & stat.S_ISVTX:
            mine = shared / str(uid)
            _mkdir_private(mine)
            return mine, rel
    except OSError:
        pass
    own = top / f".Trash-{uid}"
    try:
        _mkdir_private(own)
    except OSError as e:
        raise TrashUnavailable(tr("이 위치의 휴지통을 만들 수 없어 삭제하지 않았습니다: {error}", error=e.strerror or e)) from None
    return own, rel


def _candidates(name: str):
    stem, dot, ext = name.rpartition(".")
    if not stem:                       # `.bashrc`처럼 점으로 시작하는 이름, 또는 확장자 없음
        stem, dot, ext = name, "", ""
    yield name
    for i in range(2, 1000):
        yield f"{stem}.{i}{dot}{ext}" if dot else f"{name}.{i}"


def _trash_freedesktop(dir_fd: int, name: str, path: Path, st: os.stat_result) -> dict:
    trash_dir, info_path = _pick_trash(path, st.st_dev)
    try:
        tfd = _open_dir(trash_dir)
    except OSError as e:
        raise TrashUnavailable(tr("이 위치의 휴지통을 만들 수 없어 삭제하지 않았습니다: {error}", error=e.strerror or e)) from None
    files_fd = info_fd = -1
    try:
        for sub in ("files", "info"):
            try:
                os.mkdir(sub, 0o700, dir_fd=tfd)
            except FileExistsError:
                pass
        files_fd = os.open("files", _DIR_FLAGS, dir_fd=tfd)
        info_fd = os.open("info", _DIR_FLAGS, dir_fd=tfd)
        if os.fstat(files_fd).st_dev != st.st_dev:
            raise TrashUnavailable(tr("휴지통이 다른 장치에 있어 옮길 수 없습니다. 삭제하지 않았습니다"))
        when = time.strftime("%Y-%m-%dT%H:%M:%S")
        body = f"[Trash Info]\nPath={info_path}\nDeletionDate={when}\n".encode("utf-8")
        for cand in _candidates(name):
            info_name = cand + ".trashinfo"
            try:
                fd = os.open(info_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=info_fd)
            except FileExistsError:
                continue
            try:
                os.write(fd, body)
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.lstat(cand, dir_fd=files_fd)
                os.unlink(info_name, dir_fd=info_fd)      # 우리가 방금 만든 .trashinfo만 지운다(대상 파일이 아니다)
                continue
            except FileNotFoundError:
                pass
            try:
                os.rename(name, cand, src_dir_fd=dir_fd, dst_dir_fd=files_fd)
            except OSError as e:
                with _quiet():
                    os.unlink(info_name, dir_fd=info_fd)
                if e.errno == errno.EXDEV:
                    raise TrashUnavailable(tr("휴지통이 다른 장치에 있어 옮길 수 없습니다. 삭제하지 않았습니다")) from None
                raise TrashUnavailable(tr("휴지통으로 옮기지 못해 삭제하지 않았습니다: {reason}", reason=e.strerror or e)) from None
            with _quiet():
                os.fsync(files_fd)
            moved = os.lstat(cand, dir_fd=files_fd)
            if not _same(moved, st):
                raise TrashMismatch("trash item is a different file")
            return {"trash": "freedesktop", "name": cand}
        raise TrashUnavailable(tr("휴지통에 같은 이름이 너무 많습니다. 삭제하지 않았습니다"))
    finally:
        for fd in (files_fd, info_fd, tfd):
            if fd >= 0:
                os.close(fd)


class _quiet:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return exc[0] is not None and issubclass(exc[0], OSError)
