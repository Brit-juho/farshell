"""상태 파일 I/O — 0600 보장 + 원자적 교체. **설정을 읽지 않는다.**

경로를 인자로만 받으므로 이 모듈에는 격리 함정이 없다(호출부가 auth.X를
호출 시점에 읽어 넘긴다).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json_secure(path: Path, data) -> None:
    """0600으로 원자적 저장. 디렉토리도 0700으로 맞춘다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(path))
