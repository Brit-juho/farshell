"""원격·가상 파일시스템 판별 — 그 파일시스템에는 닿지 않고 마운트 표만 읽는다.

macOS 는 `getfsstat(MNT_NOWAIT)`를 ctypes 로 부른다. man page: MNT_NOWAIT 은 커널에 남아 있는 정보를
돌려주며 "응답하지 않는 파일시스템 때문에 지연되지 않는다". 경로마다 `statfs()`를 부르면 그 FS 에 직접
묻게 되므로(응답 없는 NFS 면 그대로 멈춘다) 쓰지 않는다. Linux 는 `/proc/self/mountinfo` 문자열만 읽는다.

판정은 **경로 문자열 접두사**로만 한다(`os.path.abspath` — 시스템 콜 없음). 그래서 판정 자체가 멈추지
않는다. 한계: 심볼릭 링크로 원격 마운트를 가리키는 경로는 문자열로는 못 잡는다 — 그 경우는 `fs` 풀의
기한과 `breaker`가 막는다.

`VT_SCAN_INCLUDE_MOUNTS`: `1`/`all`이면 원격 마운트도 자동 탐색에 넣는다. 콜론으로 구분한 경로를 주면
그 경로(와 그 아래)만 넣는다. 사용자가 직접 연 경로(파일 탭)는 이 설정과 무관하게 열린다 — 기한과
차단기만 적용된다.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_SEC = 30.0

NETWORK_FSTYPES = {
    "nfs", "nfs4", "smbfs", "cifs", "smb3", "afpfs", "webdav", "davfs", "ftp", "sshfs",
    "9p", "virtiofs", "ceph", "glusterfs", "lustre", "afs", "ncpfs", "autofs",
}
# fuse 계열은 접두사로 판정한다(fuse, fuse.sshfs, fuseblk 는 로컬 디스크일 수 있어 제외).
FUSE_PREFIXES = ("macfuse", "osxfuse", "fuse.", "fuse")
LOCAL_FUSE = {"fuseblk"}

# 마운트가 아닌데 열면 멈출 수 있는 곳(macOS File Provider — 원격 원본을 그 자리에서 받아 온다).
_FILE_PROVIDER_REL = ("Library/CloudStorage", "Library/Mobile Documents")


@dataclass(frozen=True)
class Mount:
    path: str
    fstype: str
    source: str
    local: bool

    @property
    def risky(self) -> bool:
        t = self.fstype.lower()
        if t in LOCAL_FUSE:
            return False
        if t in NETWORK_FSTYPES or t.startswith(FUSE_PREFIXES):
            return True
        return not self.local


# --- macOS getfsstat --------------------------------------------------------------------------
MNT_NOWAIT = 2
MNT_LOCAL = 0x00001000


class _Statfs64(ctypes.Structure):
    # <sys/mount.h> struct statfs (64비트 inode 판, getfsstat64 와 같은 배치)
    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


def _darwin_mounts() -> list[Mount]:
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.dylib", use_errno=True)
    fn = getattr(libc, "getfsstat64", None) or libc.getfsstat
    fn.restype = ctypes.c_int
    fn.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    n = fn(None, 0, MNT_NOWAIT)
    if n <= 0:
        raise OSError(ctypes.get_errno(), "getfsstat count failed")
    n += 8                                   # 그 사이에 늘었을 수 있다
    buf = (_Statfs64 * n)()
    got = fn(ctypes.byref(buf), ctypes.sizeof(buf), MNT_NOWAIT)
    if got < 0:
        raise OSError(ctypes.get_errno(), "getfsstat failed")
    out = []
    for i in range(got):
        s = buf[i]
        out.append(Mount(
            path=s.f_mntonname.decode("utf-8", "replace"),
            fstype=s.f_fstypename.decode("utf-8", "replace"),
            source=s.f_mntfromname.decode("utf-8", "replace"),
            local=bool(s.f_flags & MNT_LOCAL),
        ))
    return out


def _unescape(field: str) -> str:
    # mountinfo 는 공백·탭·개행·역슬래시를 \040 같은 8진수로 쓴다.
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and i + 3 < len(field) and field[i + 1:i + 4].isdigit():
            out.append(chr(int(field[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def _linux_mounts(text: Optional[str] = None) -> list[Mount]:
    if text is None:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            text = f.read()
    out = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        lf, rf = left.split(), right.split()
        if len(lf) < 5 or len(rf) < 2:
            continue
        fstype = rf[0]
        out.append(Mount(path=_unescape(lf[4]), fstype=fstype, source=_unescape(rf[1]),
                         local=not (fstype in NETWORK_FSTYPES or fstype.startswith(FUSE_PREFIXES))))
    return out


# --- 캐시 ---------------------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache: tuple[float, list[Mount]] = (0.0, [])
_override: Optional[list[Mount]] = None     # 테스트용


def mounts() -> list[Mount]:
    global _cache
    if _override is not None:
        return list(_override)
    now = time.monotonic()
    with _cache_lock:
        at, data = _cache
        if data and now - at < CACHE_SEC:
            return list(data)
    try:
        if sys.platform == "darwin":
            data = _darwin_mounts()
        elif sys.platform.startswith("linux"):
            data = _linux_mounts()
        else:
            data = []
    except Exception as e:  # noqa: BLE001 — 판별 실패가 서버를 멈추게 하면 안 된다
        logger.debug(f"마운트 표 읽기 실패: {e}")
        with _cache_lock:
            return list(_cache[1])
    with _cache_lock:
        _cache = (now, data)
    return list(data)


def _norm(path: str) -> str:
    return os.path.abspath(os.path.expanduser(str(path)))


def _within(path: str, prefix: str) -> bool:
    if prefix == "/":
        return True
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def _home() -> str:
    try:
        import vt_paths
        return str(vt_paths.user_home())
    except Exception:  # noqa: BLE001
        return os.path.expanduser("~")


def file_provider_roots() -> list[str]:
    home = _home()
    return [os.path.join(home, rel) for rel in _FILE_PROVIDER_REL] if sys.platform == "darwin" else []


def mount_for(path: str) -> Optional[Mount]:
    """그 경로가 속한(가장 긴 접두사) 마운트."""
    p = _norm(path)
    best = None
    for m in mounts():
        if _within(p, m.path) and (best is None or len(m.path) > len(best.path)):
            best = m
    return best


def risky_reason(path: str) -> Optional[str]:
    """원격·가상 FS 나 File Provider 아래면 그 이유, 아니면 None. 시스템 콜을 하지 않는다."""
    p = _norm(path)
    for root in file_provider_roots():
        if _within(p, root):
            return f"file-provider:{root}"
    m = mount_for(p)
    if m is not None and m.risky and m.path != "/":
        return f"{m.fstype}:{m.path}"
    return None


def risky_mount_points() -> list[str]:
    pts = [m.path for m in mounts() if m.risky and m.path != "/"]
    return sorted(set(pts + file_provider_roots()))


def _includes() -> tuple[bool, list[str]]:
    raw = os.environ.get("VT_SCAN_INCLUDE_MOUNTS", "").strip()
    if raw.lower() in ("1", "all", "true", "yes"):
        return True, []
    return False, [_norm(x) for x in raw.split(":") if x.strip()]


def skip_for_scan(path: str) -> Optional[str]:
    """자동 탐색(저장소 찾기·검색·세션 cwd 조회)에서 건너뛸 경로면 그 이유."""
    reason = risky_reason(path)
    if reason is None:
        return None
    include_all, includes = _includes()
    if include_all:
        return None
    p = _norm(path)
    if any(_within(p, inc) for inc in includes):
        return None
    return reason


def scan_skip_prefixes() -> list[str]:
    """탐색 자식 프로세스에 넘길 '들어가지 말 곳' 목록."""
    include_all, includes = _includes()
    if include_all:
        return []
    return [p for p in risky_mount_points() if not any(_within(p, inc) for inc in includes)]


def snapshot() -> dict:
    return {
        "risky": [{"path": m.path, "fstype": m.fstype} for m in mounts() if m.risky and m.path != "/"],
        "file_provider": file_provider_roots(),
        "include": os.environ.get("VT_SCAN_INCLUDE_MOUNTS", ""),
    }


def _set_for_tests(data: Optional[list[Mount]]) -> None:
    global _override, _cache
    _override = data
    _cache = (0.0, [])
