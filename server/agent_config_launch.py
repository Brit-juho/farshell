"""Private one-use exec payload; values never appear in tmux or ps arguments."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from i18n import tr


def launch(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or not stat.S_ISREG(info.st_mode):
            raise ValueError("invalid launch payload")
        data = json.load(stream)
    path.unlink()
    os.chdir(data["cwd"])
    os.execve(data["argv"][0], data["argv"], data["env"])


if __name__ == "__main__":
    try:
        launch(Path(sys.argv[1]))
    except Exception:
        print(tr("CLI 재시작에 실패했습니다 — 터미널에서 직접 이어가세요"), file=sys.stderr)
        sys.exit(1)
