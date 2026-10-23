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
# routes/ports.py의 `_run_vt`는 VT_DIR로 **실제 bin/fsh**를 찾아 실행한다(터널 열기·
# 내리기). 개발자 셸에는 보통 VT_DIR이 있어서, 그대로 두면 lifespan의 만료 노출
# 정리 루프나 expose 테스트가 사용자의 진짜 cloudflared를 건드릴 수 있다. 필요한
# 테스트는 monkeypatch로 직접 넣는다.
os.environ.pop("VT_DIR", None)
os.environ.setdefault("VT_NETWORK_MODE", "all")
# 화면 언어는 서버 전역 한 값이다(server/i18n.py). 한국어 문구를 단언하는 테스트가
# 개발자 셸의 LANG(en_US 등)에 따라 달라지지 않게 고정한다. 영어를 보는 테스트는
# monkeypatch로 VT_LANG을 바꾼다.
os.environ["VT_LANG"] = "ko"
# unix socket 서버(authz/sock.py)는 시험에서 기본으로 끈다 — lifespan 을 도는 TestClient 가 임시 HOME 아래에
# 소켓을 열면 경로 길이(AF_UNIX 100바이트)와 병렬 실행에 의존하게 된다. 소켓을 시험하는 파일이 직접 켠다.
os.environ["VT_AUTHZ_SOCKET"] = "0"
os.environ.pop("VT_AUTHZ_SOCKET_PATH", None)

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

# ⚠ HOME·Claude·tmux 도 격리한다 (2026-10-06 점검).
#
# VT_CONFIG만 막아서는 부족했다. HOME 을 임시 폴더로 바꿔 스위트를 돌려 보니 라우트
# 테스트가 `~/.vt/`(tickets·files·queue·e2e_identity 등)와 `~/.config/vt/workspace.json`
# 을 **실제 사용자 홈에 만들고 있었다**(실행하는 사람의 레일·레이아웃 상태 파일이다).
# 그래서 import 전에 HOME 을 전용 임시 디렉터리로 고정하고, Claude 설정 경로와 tmux
# 소켓도 테스트 전용으로 둔다 — 어느 코드가 새더라도 실제 `~/.claude`·`~/.vt.env`·
# `tmux -L fsh` 에는 닿지 않는다. 필요한 테스트는 monkeypatch 로 더 좁게 지정한다.
_TEST_HOME = tempfile.mkdtemp(prefix="vt-tests-home-")
os.environ["HOME"] = _TEST_HOME
import atexit, shutil  # noqa: E402
atexit.register(shutil.rmtree, _TEST_HOME, ignore_errors=True)
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(_TEST_HOME, ".claude")
os.environ["VT_TMUX_SOCKET"] = "fsh-tests-" + str(os.getpid())
for _k in ("VT_WORKSPACE_PATH", "VT_RUN_DIR", "VT_STATE_DIR"):
    os.environ.pop(_k, None)


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
def _reset_preauth_state():
    """사전 인증 실패 한도(authz/preauth.py)와 거부 기록의 분당 카운터는 모듈 전역(메모리)이다. 앞 테스트의 실패·잠금이
    다음 테스트로 새면 안 된다."""
    from authz import denylog, inbox, preauth
    preauth.reset()
    denylog.reset()
    inbox.reset_rate()
    yield
    preauth.reset()
    denylog.reset()
    inbox.reset_rate()


@pytest.fixture(autouse=True)
def _reset_agent_owners():
    """agent_status의 세션 소유자·은퇴 목록은 모듈 전역이다. 테스트들은 `_state`만 비우는데,
    앞 테스트가 은퇴시킨 session_id("s" 등)를 다음 테스트가 다시 쓰면 이벤트가 무시된다."""
    import agent_status
    agent_status._owners.clear()
    agent_status._retired.clear()
    agent_status._last_uid_of.clear()
    yield
    agent_status._owners.clear()
    agent_status._retired.clear()
    agent_status._last_uid_of.clear()


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



@pytest.fixture
def process_table():
    """실제 프로세스 표를 읽어야 하는 테스트의 환경 전제조건.

    제한된 실행 환경에서 ps 자체가 EPERM이면 RSS/부모 PID/고아 판정을
    검증할 수 없다. 일반 환경에서는 실제 프로세스를 그대로 검증한다.
    """
    import subprocess
    try:
        result = subprocess.run(["ps", "-p", str(os.getpid()), "-o", "pid=,command="],
                                capture_output=True, text=True)
    except PermissionError:
        pytest.skip("실행 환경이 실제 프로세스 표(ps) 조회를 허용하지 않습니다")
    if result.returncode != 0 or str(os.getpid()) not in result.stdout:
        pytest.skip("실행 환경이 실제 프로세스 표(ps) 조회를 허용하지 않습니다")


# `VT_AUTHZ_ENFORCE=msg`(3c)에서는 보낸 세션을 본문의 pane/tmux 주장이 아니라 인증된 호출자로 정한다. 아래 시험은
# "본문 주장으로 보낸 세션을 정한다"는 옛 계약 자체를 단언하므로 그 플래그를 끈 채 돈다(켠 쪽 성질은
# test_authz_msg_enforce.py 가 같은 내용을 인증된 호출자로 확인한다).
_LEGACY_BODY_CLAIM_SENDER = (
    "test_agent_msg.py::test_local_send_resolves_sender_from_pane_and_queues",
    "test_agent_msg.py::test_local_send_refuses_missing_session_and_self",
    "test_char_machine_token.py::test_h2_sender_session_comes_from_the_request_body",
)


def _legacy_body_claim(nodeid: str) -> bool:
    """H2 의 「현재 동작 고정」 사례(`spoof_other` 포함)도 같은 이유로 꺼진 채 돈다 — 켠 쪽은 EXPECTED_CHANGE 로 따로 본다."""
    return nodeid.endswith(_LEGACY_BODY_CLAIM_SENDER) or (
        "test_char_machine_token.py::test_matrix[" in nodeid and "api_agent_message" in nodeid and "spoof_other" in nodeid)


@pytest.fixture(autouse=True)
def _legacy_body_claim_without_msg_flag(request, monkeypatch):
    if _legacy_body_claim(request.node.nodeid):
        toks = [t for t in os.environ.get("VT_AUTHZ_ENFORCE", "").split(",") if t.strip() and t.strip() != "msg"]
        monkeypatch.setenv("VT_AUTHZ_ENFORCE", ",".join(toks))
    yield


# `VT_AUTHZ_ENFORCE=legacy`(3e)에서는 공용 기계 토큰이 소켓 허용표 안의 (메서드, 경로)만 쓰고, `?token=`·쿠키 토큰은 받지 않으며, 비밀번호 없이
# 토큰만 있는 서버에서는 서명 쿠키가 사람이 아니고 승인 요청이 만들어지지 않는다. 아래 시험은 **켜지 않은 상태의 계약**
# (토큰이 API 전체를 연다 / 서명 쿠키가 사람이다 / 승인 요청이 만들어진다)을 단언하므로 그 플래그를 끈 채 돈다. 켠 쪽 성질은
# test_authz_legacy_enforce.py 와 authz_char/test_char_legacy_enforced.py 가 따로 본다.
_LEGACY_OPEN_CONTRACT = (
    "test_agent_share.py::test_machine_token_cannot_share_approve_or_revoke",
    "test_authz_legacy_use.py::test_middleware_counts_machine_token_requests_only",
    "test_authz_shadow.py::test_shadow_exceptions_are_swallowed_and_responses_unchanged",
)


def _legacy_open_contract(nodeid: str) -> bool:
    if "authz_char/" in nodeid:
        return "test_char_legacy_enforced.py" not in nodeid        # 0단계 golden(matrix.json)은 꺼진 상태의 기록이다
    return nodeid.split("/")[-1].startswith(_LEGACY_OPEN_CONTRACT)


@pytest.fixture(autouse=True)
def _legacy_off_for_open_contract(request, monkeypatch):
    if _legacy_open_contract(request.node.nodeid):
        toks = [t for t in os.environ.get("VT_AUTHZ_ENFORCE", "").split(",") if t.strip() and t.strip() != "legacy"]
        monkeypatch.setenv("VT_AUTHZ_ENFORCE", ",".join(toks))
    yield


@pytest.fixture(autouse=True)
def _isolate_catalog_background_workers(monkeypatch):
    """Route mocks must not record unrelated server-owned discovery commands.

    Collector/archive behavior is tested directly with disposable state; the
    complete daemon is exercised by isolated HTTP/tmux integration checks.
    """
    import asyncio
    from session_history import worker
    async def dormant():
        await asyncio.Event().wait()
    async def initialized():
        return None
    monkeypatch.setattr(worker,'initialize',initialized)
    for name in ('run','replicate','discover','index_history'):
        monkeypatch.setattr(worker,name,dormant)
