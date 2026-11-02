"""P2-4(ADR-41) — `vt_paths.py` 단일 해석기.

엣지 케이스 표를 그대로 테스트로 옮긴다.
관용구 수렴 테스트(§6-1)는 호출부가 실제로 `vt_paths`로 갈아탄 뒤에만
의미가 있다 — 수정 전에는 갈라져야 정상이라는 걸 먼저 확인하고 시작한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vt_paths
from vt_paths import PathOverrideError


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for k in ("VT_STATE_DIR", "VT_RUN_DIR", "VT_WORKSPACE_PATH", "VT_CONFIG",
              "VT_DETECT_DIR", "VT_START_DIR", "VT_DIR", "HOME"):
        monkeypatch.delenv(k, raising=False)
    # HOME 미설정은 실제 passwd 홈으로 복귀한다. 경로 테스트도 사용자 상태와 격리한다.
    monkeypatch.setenv("HOME", str(tmp_path))


# ---------------------------------------------------------------------------
# §4 표 — E1~E7

def test_e1_empty_string_is_demoted_to_unset(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setenv("VT_STATE_DIR", "")
    assert vt_paths.state_dir() == Path("/home/u/.vt")


def test_e2_whitespace_only_is_demoted_to_unset(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setenv("VT_STATE_DIR", "   ")
    assert vt_paths.state_dir() == Path("/home/u/.vt")


def test_e3_surrounding_whitespace_is_trimmed(monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", "  /tmp/vt  ")
    assert vt_paths.state_dir() == Path("/tmp/vt")


def test_e4_tilde_prefix_expands_against_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setenv("VT_STATE_DIR", "~/vt-test")
    assert vt_paths.state_dir() == Path("/home/u/vt-test")


def test_e5_bare_tilde_expands_to_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    monkeypatch.setenv("VT_STATE_DIR", "~")
    assert vt_paths.state_dir() == Path("/home/u")


def test_e6_relative_path_is_rejected(monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", "vt-test")
    with pytest.raises(PathOverrideError) as exc:
        vt_paths.state_dir()
    assert exc.value.variable == "VT_STATE_DIR"
    assert exc.value.value == "vt-test"


def test_e7_home_unavailable_names_the_variable(monkeypatch):
    """HOME 미설정이고 `Path.home()`도 실패하는 환경(systemd·CI 컨테이너)의
    흉내 — `Path.home`을 직접 모킹해 그 실패를 재현한다."""

    def _boom(cls=None):
        raise RuntimeError("Could not determine home directory")

    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: _boom()))
    with pytest.raises(PathOverrideError) as exc:
        vt_paths.user_home()
    assert exc.value.variable == "HOME"


# ---------------------------------------------------------------------------
# 그 외 공개 함수

def test_state_dir_default_is_dot_vt(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert vt_paths.state_dir() == Path("/home/u/.vt")


def test_run_dir_default_is_under_state_dir():
    # 2.7: 공용 /tmp 가 아니라 state_dir()/run(유닉스 소켓과 같은 폴더) — test_runtime_dir.py
    assert vt_paths.run_dir() == vt_paths.state_dir() / "run"


def test_run_dir_honors_override(monkeypatch):
    monkeypatch.setenv("VT_RUN_DIR", "/tmp/isolated-run")
    assert vt_paths.run_dir() == Path("/tmp/isolated-run")


def test_workspace_path_default(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert vt_paths.workspace_path() == Path("/home/u/.config/vt/workspace.json")


def test_config_path_default(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert vt_paths.config_path() == Path("/home/u/.vt.env")


def test_detect_dir_default_resolves_relative_to_server_dir():
    server_dir = Path(vt_paths.__file__).parent
    assert vt_paths.detect_dir() == server_dir / "detect"


def test_start_dir_default_is_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert vt_paths.start_dir() == Path("/home/u")


def test_install_dir_default_is_none():
    assert vt_paths.install_dir() is None


def test_install_dir_honors_override(monkeypatch):
    monkeypatch.setenv("VT_DIR", "/opt/farshell")
    assert vt_paths.install_dir() == Path("/opt/farshell")


def test_install_dir_rejects_relative_paths(monkeypatch):
    monkeypatch.setenv("VT_DIR", "farshell")
    with pytest.raises(PathOverrideError):
        vt_paths.install_dir()


def test_tunnel_log_is_under_run_dir(monkeypatch):
    monkeypatch.setenv("VT_RUN_DIR", "/tmp/iso")
    assert vt_paths.tunnel_log() == Path("/tmp/iso/cloudflared.log")


def test_tunnel_log_default_follows_run_dir():
    """2.7 — 기본 위치가 공용 /tmp 에서 state_dir()/run 으로 옮겨졌다(bin/fsh 와 같은 규칙, test_runtime_dir.py)."""
    assert vt_paths.tunnel_log() == vt_paths.state_dir() / "run" / "cloudflared.log"


def test_is_isolated_false_when_both_unset():
    assert vt_paths.is_isolated() is False


def test_is_isolated_true_when_state_dir_set(monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", "/tmp/iso")
    assert vt_paths.is_isolated() is True


def test_is_isolated_true_when_run_dir_set(monkeypatch):
    monkeypatch.setenv("VT_RUN_DIR", "/tmp/iso")
    assert vt_paths.is_isolated() is True


def test_is_isolated_ignores_whitespace_only_values(monkeypatch):
    """E1/E2와 같은 강등 규칙이 is_isolated 판정에도 그대로 적용돼야 한다 —
    안 그러면 빈 문자열 하나로 "격리됐다"고 잘못 판단해 레거시 마이그레이션이
    건너뛰어진다."""
    monkeypatch.setenv("VT_STATE_DIR", "   ")
    assert vt_paths.is_isolated() is False


# ---------------------------------------------------------------------------
# §3.1 — 에러 메시지 3요소(변수명·원본 값·"절대경로")

def test_error_message_contains_all_three_elements(monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", "vt-test")
    with pytest.raises(PathOverrideError) as exc:
        vt_paths.state_dir()
    msg = str(exc.value)
    assert "VT_STATE_DIR" in msg
    assert "vt-test" in msg
    assert "절대경로" in msg


# ---------------------------------------------------------------------------
# §3.1 — 호출 시점 해석(모듈 전역에 안 굳는다)

def test_resolution_happens_at_call_time_not_import_time(monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", "/tmp/first")
    assert vt_paths.state_dir() == Path("/tmp/first")
    monkeypatch.setenv("VT_STATE_DIR", "/tmp/second")
    assert vt_paths.state_dir() == Path("/tmp/second")


# ---------------------------------------------------------------------------
# §6-1 — 관용구 수렴. 세 관용구를 쓰던 대표 세 곳이 이제 전부 같은 경로를
# 답해야 한다. 수정 전에는 3·4행 값(공백/`~`)에서 갈라져야 정상이었다.

def test_auth_queue_and_mcp_write_converge_on_the_same_state_dir(monkeypatch, tmp_path):
    import mcp_write
    import queue_store

    home = tmp_path / "home"
    home.mkdir()
    for raw in (
        "",                             # 빈 문자열 — 미설정으로 강등
        "~/vt-test",                    # `~` 확장
        f"  {tmp_path / 'vt-conv'}  ",  # 공백 트림
    ):
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("VT_STATE_DIR", raw)
        # mcp_write._lock_dir()는 실제로 mkdir을 하는 소유 모듈이라, 순수
        # 해석기(queue_store._state_dir())와 같은 부모를 답하는지로 수렴을 본다.
        assert queue_store._state_dir() == mcp_write._lock_dir().parent


def test_auth_state_dir_uses_the_same_resolver(monkeypatch, tmp_path):
    """auth.STATE_DIR은 모듈 import 시점에 한 번 계산되는 값이라(테스트가
    env로 다시 흔들 수 없다) `vt_paths.state_dir()`와 **같은 함수**를 쓰는지를
    소스에서 확인한다 — 되돌아가서 다시 세 관용구로 갈라지면 여기서 잡는다."""
    import inspect

    import auth

    src = inspect.getsource(auth)
    assert "STATE_DIR = vt_paths.state_dir()" in src


# ---------------------------------------------------------------------------
# §6-4 — tunnel_log() 격리. `tunnel.py`·`tunnel_watchdog.py` 둘 다 같은 곳을
# 본다. 실제 cloudflared는 띄우지 않는다(AGENTS.md 「가짜 터널 금지」) — 로그
# 파일 경로 해석만 확인한다.

def test_tunnel_py_follows_vt_run_dir(monkeypatch):
    import tunnel

    monkeypatch.setenv("VT_RUN_DIR", "/tmp/iso-run")
    status = tunnel.get_tunnel_status(force=True)
    assert status["log_path"] == "/tmp/iso-run/cloudflared.log"


def test_tunnel_py_parse_url_from_log_defaults_to_vt_paths(monkeypatch, tmp_path):
    import tunnel

    log = tmp_path / "cloudflared.log"
    log.write_text("2026-09-23 https://abc-def.trycloudflare.com foo\n")
    monkeypatch.setenv("VT_RUN_DIR", str(tmp_path))
    assert tunnel.parse_url_from_log() == "https://abc-def.trycloudflare.com"


def test_tunnel_watchdog_uses_the_same_resolver(monkeypatch):
    """tunnel.py와 tunnel_watchdog.py가 같은 값을 봐야 한다 — 갈라지면 좀비
    감지가 엉뚱한 로그를 본다."""
    monkeypatch.setenv("VT_RUN_DIR", "/tmp/iso-run")
    import importlib

    import tunnel_watchdog
    importlib.reload(tunnel_watchdog)
    try:
        assert str(tunnel_watchdog.TUNNEL_LOG) == "/tmp/iso-run/cloudflared.log"
    finally:
        monkeypatch.undo()
        importlib.reload(tunnel_watchdog)  # 다음 테스트를 위해 기본값으로 되돌린다


# ---------------------------------------------------------------------------
# §6-6 / E12 — is_isolated()이면 레거시 업로드 편입(main.py 기동 훅)이
# 실행되지 않는다. 실제 파일 이동 로직은 test_file_store.py가 이미 덮는다 —
# 여기서 보는 건 "호출 자체를 건너뛰는가"뿐이다.

def test_legacy_migration_runs_when_not_isolated(monkeypatch):
    from unittest.mock import patch

    from starlette.testclient import TestClient

    import main

    with patch("main.file_store.migrate_legacy") as mocked:
        with TestClient(main.app):
            pass
    mocked.assert_called_once()


def test_legacy_migration_is_skipped_when_isolated(monkeypatch, tmp_path):
    from unittest.mock import patch

    from starlette.testclient import TestClient

    import main

    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    with patch("main.file_store.migrate_legacy") as mocked:
        with TestClient(main.app):
            pass
    mocked.assert_not_called()
