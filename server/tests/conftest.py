"""pytest 부트스트랩 — server/ 를 import 경로에 추가하고 인증/네트워크 env를 초기화."""

import os
import sys
import tempfile

# server/ 디렉토리를 sys.path에 추가 (main, network_access 등 직접 import)
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

# auth 모듈은 import 시점에 인증 env를 읽는다. 테스트가 셸 env에 오염되지 않도록
# import 전에 인증 관련 값을 모두 비우고, 네트워크 모드도 명시적으로 all(기본)로 둔다.
for _k in ("VT_AUTH_TOKEN", "VT_TOKEN", "VT_AUTH_PASSWORD_HASH", "VT_PASSWORD_HASH",
           "VT_AUTH_SESSION_KEY", "VT_SECRET_KEY"):
    os.environ.pop(_k, None)
os.environ.pop("VT_ACCESS_SPEC", None)
os.environ.pop("VT_TRUST_PROXY", None)
os.environ.setdefault("VT_NETWORK_MODE", "all")

# ⚠ 이 스위트는 **개발자의 실제 ~/.vt.env를 절대 읽지 않는다.**
#
# 2026-09-17에 경계값(VT_BROWSE_ROOTS·VT_AUTH_* 등)은 환경변수보다 설정 파일이
# 이기도록 바꾸면서, main 을 import 하는 것만으로 실제 ~/.vt.env 가 적용되게
# 됐다 — 위에서 비운 인증 값이 곧바로 되살아나 195건이 깨졌다. 값이 되살아나는
# 것보다 나쁜 건, 그 상태로 통과하는 테스트가 **사용자의 실제 상태 파일을
# 건드릴 수 있다**는 점이다(test_auth_isolation.py가 그 성질을 지킨다).
#
# VT_CONFIG 는 "어느 파일을 읽을지" 고르는 키라서 의도적으로 경계값이 아니다.
# 그래서 여기서 빈 파일로 고정하면 스위트 전체가 사용자 설정과 분리된다.
# setdefault 가 아니라 **무조건 덮어쓴다** — 개발자 셸에 VT_CONFIG 가 이미
# 있으면(흔하다) setdefault 는 아무것도 막지 못한다.
_EMPTY_CFG = os.path.join(tempfile.gettempdir(), "vt-tests-empty.env")
with open(_EMPTY_CFG, "w", encoding="utf-8") as _f:
    _f.write("# server/tests 전용 빈 설정 — conftest.py가 만든다\n")
os.environ["VT_CONFIG"] = _EMPTY_CFG


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_codex_plugin_cache():
    """codex 플러그인 목록은 모듈 전역 캐시다(ADR-48). 한 테스트가 채운 결과가 다음
    테스트의 `run_json` 스텁을 가리면 안 된다."""
    import codex_cli
    codex_cli.reset_plugin_cache()
    yield
    codex_cli.reset_plugin_cache()


@pytest.fixture(autouse=True)
def _no_real_tmux_hooks(request, monkeypatch):
    """tmux 훅(tmux_hooks.py)을 실제 tmux 서버에 걸거나 지우지 않는다.

    `TestClient(main.app)`는 lifespan을 돌리고, lifespan은 훅을 설치·제거한다.
    테스트에는 tmux 소켓 격리가 없어 그대로 두면 **개발자의 실제 tmux(-L fsh)에
    걸린 서버 훅을 테스트 비밀값으로 덮어쓰고 끝날 때 지운다.** 훅 자체를
    검사하는 테스트는 `real_tmux_hooks` 마커로 이 차단을 끈다(가짜 tmux_runner로).
    """
    if request.node.get_closest_marker("real_tmux_hooks"):
        yield
        return
    import tmux_hooks
    monkeypatch.setattr(tmux_hooks, "install", lambda: True)
    monkeypatch.setattr(tmux_hooks, "uninstall", lambda: None)
    yield

