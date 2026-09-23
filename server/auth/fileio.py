"""상태 파일 I/O — 0600 보장 + 원자적 교체. **설정을 읽지 않는다.**

경로를 인자로만 받으므로 이 모듈에는 격리 함정이 없다(호출부가 auth.X를
호출 시점에 읽어 넘긴다).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json_secure(path: Path, data) -> None:
    """0600으로 원자적 저장. 디렉토리도 0700으로 맞춘다.

    **임시파일 이름이 유니크해야 한다.** 예전엔 `<이름>.tmp`로 고정이었는데, 같은
    경로에 동시 쓰기 둘이 들어오면 둘이 같은 파일을 `O_TRUNC`로 열고 번갈아 써서
    먼저 끝난 쪽이 **반쯤 섞인 내용**을 제자리에 놓았다. `os.replace`는 원자적이지만
    옮기는 내용물이 이미 오염돼 있으면 소용이 없다. 여기 쓰는 쪽은 기기 목록과
    TOTP라 깨지면 **로그인이 막힌다.**

    락은 걸지 않는다. 유니크 이름 + `os.replace`면 파일이 깨지는 경로는 닫힌다.
    남는 것은 lost update인데, 기기 등록·TOTP 설정은 `workspace.json`과 달리
    동시 수정이 정상 경로가 아니다. 그 전제가 깨지는 증거가 나오면
    `store_lock.exclusive`를 얹는다 — 구조가 같아 나중 비용이 작다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix="." + path.name + "-", suffix=".tmp",
    )
    try:
        # fdopen을 먼저 해서 fd 소유권을 파일 객체에 넘긴다 — 안에서 무엇이 터지든
        # `with`가 닫는다. fchmod를 밖에서 하면 그게 실패했을 때 fd가 샌다.
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # mkstemp가 이미 0600으로 만들지만 umask와 무관하게 못 박는다.
            os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, str(path))
    except BaseException:
        # 실패한 쓰기가 남긴 조각이 다음 쓰기의 이름과 부딪히지 않게 치운다.
        # 유니크 이름과 짝을 이루는 부분이라 한쪽만 하면 반만 고친 것이 된다.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
