"""tests/test_worktree.py 전용 부트스트랩 — server/를 import 경로에 추가.

server/tests/conftest.py와 같은 이유(server 모듈을 `import worktree`처럼 최상위로
불러오는 관례)이지만, 이 파일은 pytest.ini의 testpaths(server/tests) 밖에 있어
`pytest tests/test_worktree.py`로 경로를 직접 줄 때만 수집된다(수용 기준 원문).
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER_DIR = os.path.join(_REPO_ROOT, "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)
