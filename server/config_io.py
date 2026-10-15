"""남의 설정 파일을 쓰는 단일 구현.

FarShell은 Claude Code·Codex의 설정 파일(`settings.json`, `.claude.json`,
`config.toml`, `hooks.json`, MCP 설정)을 고친다. 이 파일들은 우리 것이 아니라서
쓰기 한 번에 지켜야 할 계약이 많은데, 예전엔 네 군데가 제각각 구현해 서로 달랐다. 한곳으로 모은다.

계약:

1. **심볼릭 링크를 따라간다.** `os.replace(tmp, 링크)`는 링크를 일반 파일로 바꾸고
   원본을 낡게 남긴다. dotfiles 저장소나 계정별 설정 폴더가 공유 파일을 링크로
   걸어 두므로, 링크 끝의 실제 파일을 쓴다.
2. **권한을 물려받는다.** `mkstemp`는 0600을 주므로 0644였던 파일의 권한이 조용히
   바뀐다. 남의 파일 권한을 바꾸는 것도 남의 설정을 건드리는 것이다.
3. **하드링크는 쓰지 않는다.** `rename`은 이름 하나만 새 파일로 만들어 나머지를
   낡게 남긴다. 제자리 쓰기는 비원자적이라 반쯤 쓴 설정이 남을 수 있다. 거부하고
   이유를 올린다.
4. **디스크에 닿은 뒤 교체한다.** `fsync` 없이 rename하면 전원이 나갈 때 길이만
   남고 내용이 0인 파일이 될 수 있다.
5. **락 이름은 실제 파일 기준이다.** 같은 파일을 링크 경로와 실제 경로(또는 계정
   폴더 A와 B)로 열어도 같은 락을 잡는다. 경로 문자열로 이름을 정하면 서로 다른
   락을 잡아 직렬화가 깨진다.
6. **오래된 임시 파일을 치운다.** `SIGKILL`로 죽으면 `except`가 못 돌아 임시 파일이
   남는다.

락은 협조적이다 — Claude Code는 이 락을 지키지 않는다. 그래서 호출부는 락 안에서
`stamp`를 읽고 쓰기 직전에 다시 비교해야 한다. 창을 줄일 뿐 없애지는 못한다.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import store_lock
import vt_paths
from i18n import tr

# 이보다 오래된 임시 파일만 치운다. 다른 프로세스가 쓰는 중인 것은 건드리지 않는다.
STALE_TEMP_SEC = 600.0
_TEMP_SUFFIX = ".vt-tmp"


class ConfigWriteRefused(Exception):
    """쓰기를 시작하지 않았다 — 파일은 그대로다. 메시지는 사용자에게 보여도 된다."""


def resolve_target(path) -> Path:
    """쓰기가 실제로 바꿀 파일. 심볼릭 링크 끝의 파일이고, 아직 없으면 그 경로다.

    끊어진 링크는 링크가 가리키는 경로를 돌려준다(그 자리에 새로 만든다).
    링크가 순환하면 해석이 끝나지 않으므로 거부한다.
    """
    resolved = Path(os.path.realpath(path))
    if resolved.is_symlink():
        raise ConfigWriteRefused(tr("{path}: 심볼릭 링크가 순환합니다", path=path))
    return resolved


def stamp(path) -> Optional[tuple]:
    """파일이 바뀌었는지 비교하는 표식. 없으면 None. 링크는 실제 파일 기준."""
    try:
        st = resolve_target(path).stat()
    except (FileNotFoundError, ConfigWriteRefused):
        return None
    return (st.st_mtime_ns, st.st_size)


def ensure_unchanged(path, expected: Optional[tuple]) -> None:
    """읽은 뒤 파일이 바뀌었으면 `ConfigWriteRefused`. 쓰기 직전에 부른다.

    Claude Code는 우리 락을 지키지 않으므로 락만으로는 읽기→쓰기 사이의 변경을 못 막는다.
    """
    if stamp(path) != expected:
        raise ConfigWriteRefused(tr("설정 파일이 바뀌었습니다. 다시 시도하세요"))


def _lock_dir() -> Path:
    # 락 파일은 사용자의 설정 디렉토리를 더럽히지 않도록 우리 상태 디렉토리에 둔다.
    d = vt_paths.state_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


@contextmanager
def locked(path, deadline: float = 10.0):
    """대상 파일 하나에 대한 잠금(실제 파일 기준).

    기한이 다른 저장소보다 길다(10초) — 상대(Claude Code 자신)가 협조하지 않을 수
    있는 파일이라 정직한 경쟁에 더 여유를 준다.
    """
    key = hashlib.sha1(str(resolve_target(path)).encode()).hexdigest()[:16]
    with store_lock.exclusive(_lock_dir() / f"{key}.lock", deadline=deadline):
        yield


def atomic_write(path, text: str, *, default_mode: int = 0o600) -> None:
    """`text`를 임시 파일로 쓰고 rename한다. 계약은 모듈 주석 참고.

    `ConfigWriteRefused`면 아무것도 바뀌지 않았다. 그 밖의 `OSError`도 파일은 그대로다.
    """
    target = resolve_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        st = target.stat()
    except FileNotFoundError:
        st = None
    if st is not None:
        if not stat.S_ISREG(st.st_mode):
            raise ConfigWriteRefused(tr("{path}: 일반 파일이 아닙니다", path=target))
        if st.st_nlink > 1:
            raise ConfigWriteRefused(
                tr("{path}: 다른 이름이 같은 파일을 가리키는 하드링크라 안전하게 쓸 수 없습니다 — 건드리지 않았습니다",
                   path=target))
        mode = stat.S_IMODE(st.st_mode)
    else:
        mode = default_mode

    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=_TEMP_SUFFIX)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if st is not None:
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except OSError:
                pass  # 권한이 없으면 그냥 둔다 — 소유자를 못 바꾸는 쪽이 정상이다
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _remove_stale_temps(target)


def _remove_stale_temps(target: Path) -> None:
    now = time.time()
    try:
        for p in target.parent.glob(f".{target.name}.*{_TEMP_SUFFIX}"):
            try:
                if now - p.lstat().st_mtime > STALE_TEMP_SEC:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass
