"""matrix.json 을 지금 서버의 판정으로 다시 만든다.

    .venv/bin/python server/tests/authz_char/gen_matrix.py [pytest 인자…]

사례 실행은 테스트와 같은 픽스처(가짜 tmux·tmp 상태)를 써야 해서 pytest 로 돌린다 —
`VT_AUTHZ_REGEN=1`이면 conftest 가 비교 대신 기록하고 끝에 파일을 쓴다. 다시 만든 뒤에는
`git diff server/tests/authz_char/matrix.json`이 곧 "판정이 바뀐 사례" 목록이다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    env = {**os.environ, "VT_AUTHZ_REGEN": "1"}
    cmd = [sys.executable, "-m", "pytest", str(HERE), "-q", "-p", "no:randomly", *sys.argv[1:]]
    return subprocess.call(cmd, env=env, cwd=str(HERE.parents[2]))


if __name__ == "__main__":
    sys.exit(main())
