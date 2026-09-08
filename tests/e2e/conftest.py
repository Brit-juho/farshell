"""E1 실브라우저 스모크 전용 설정.

## 왜 server/tests/ 밖에 있나

playwright의 동기 API를 같은 pytest 프로세스에서 쓰면 **다른 테스트가 깨진다.**
실측: `pytest server/tests`에 이 파일이 섞여 있으면 test_pty_manager 4종,
test_notify_fallback 5종 등 15건이 RuntimeError로 실패했고, 이 파일만 빼면 0건이었다.
playwright가 프로세스에 세우는 greenlet 기반 루프가 asyncio를 쓰는 기존 테스트와
충돌한다.

그래서 디렉토리를 분리하고 pytest.ini의 testpaths(server/tests)에서 빼둔다 —
`pytest`를 그냥 치면 이 파일은 수집되지 않는다. CI의 e2e 잡과 로컬에서
경로를 명시할 때만 돈다:

    pytest tests/e2e -v
"""

import pytest


# 실패한 테스트에서만 스크린샷을 남기기 위해 "이 테스트가 실패했는가"를
# fixture teardown 시점에 알 수 있게 한다.
#
# 이 훅은 **conftest.py에 있어야만** 수집된다. 테스트 모듈 안에 두면 pytest가
# 조용히 무시해서, 에러 없이 스크린샷만 안 찍힌다(실제로 한 번 놓쳤다).
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call":
        item.stash_failed = rep.failed
