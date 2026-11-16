"""session_state — 「세션 없음」과 「확인 못 함」을 가른다.

has_session은 타임아웃(124)·tmux 없음(127)까지 False라서, 부하나 재시작 직후의
attach가 멀쩡한 세션을 404(없음)로 답했고 복원 중인 화면이 그 칸을 비워 저장했다.
"""

import asyncio

import pytest

import routes.tmux as tmux_route
import tmux_runner


def _run_returning(*results):
    seq = list(results)
    calls = []

    def fake(args, timeout=None, input=None):
        calls.append(list(args))
        return seq.pop(0) if len(seq) > 1 else seq[0]
    fake.calls = calls
    return fake


def test_exists(monkeypatch):
    monkeypatch.setattr(tmux_runner, "run", _run_returning((0, b"", b"")))
    assert tmux_runner.session_state("a") == tmux_runner.SESSION_EXISTS


@pytest.mark.parametrize("err", [b"can't find session: a", b"no server running on /tmp/x",
                                 b"error connecting to /tmp/x (No such file or directory)"])
def test_missing_only_when_tmux_says_so(monkeypatch, err):
    fake = _run_returning((1, b"", err))
    monkeypatch.setattr(tmux_runner, "run", fake)
    assert tmux_runner.session_state("a") == tmux_runner.SESSION_MISSING
    assert len(fake.calls) == 1, "확정이면 재질문하지 않는다"


@pytest.mark.parametrize("rc,err", [(124, b"timeout"), (127, b"tmux not found"), (1, b"weird failure")])
def test_unknown_on_timeout_missing_binary_or_unrecognised_error(monkeypatch, caplog, rc, err):
    fake = _run_returning((rc, b"", err))
    monkeypatch.setattr(tmux_runner, "run", fake)
    assert tmux_runner.session_state("a") == tmux_runner.SESSION_UNKNOWN
    assert len(fake.calls) == 2, "한 번 더 물어 본다"
    assert "has-session unknown" in caplog.text


def test_retry_recovers_from_a_transient_timeout(monkeypatch):
    monkeypatch.setattr(tmux_runner, "run", _run_returning((124, b"", b"timeout"), (0, b"", b"")))
    assert tmux_runner.session_state("a") == tmux_runner.SESSION_EXISTS


def _attach(monkeypatch, state):
    async def fake_state(name):
        return state
    monkeypatch.setattr(tmux_runner, "session_state_async", fake_state)
    return asyncio.run(tmux_route._attach_tmux("a", 80, 24))


def test_attach_missing_is_404(monkeypatch):
    assert _attach(monkeypatch, tmux_runner.SESSION_MISSING).status_code == 404


def test_attach_unknown_is_503_not_404(monkeypatch):
    res = _attach(monkeypatch, tmux_runner.SESSION_UNKNOWN)
    assert res.status_code == 503


def _screen_session(runtime_id, catalog_session_id=None):
    sid = "w-" + (runtime_id or "none")
    tmux_route.session_store.add(sid, name="dev", screen_id="a" * 32)
    tmux_route.session_store.update_tmux_name(sid, "dev")
    info = tmux_route.session_store.get(sid)
    info.runtime_id, info.catalog_session_id = runtime_id, catalog_session_id
    return sid


def test_a_screen_session_without_a_runtime_is_bound_not_refused(monkeypatch):
    """옛 복원이 실행 회차 없이 만든 이 화면의 웹 세션 — 목록에서 카탈로그 짝으로 열면 거절(409)하지 않고 묶어서 쓴다.
    예전에는 서버를 재시작할 때까지 영영 runtime_changed였다(새로고침해도 같은 웹 세션이 남는다, 2026-11-16 M5)."""
    sid = _screen_session(None)
    monkeypatch.setitem(tmux_route.pty_mgr._sessions, sid, object())   # sessions는 사본을 돌려주는 속성
    try:
        res = asyncio.run(tmux_route.attach_screen("dev", 80, 24, None, "a" * 32,
                                                   {"session_id": "c" * 32, "runtime_id": "r" * 32, "tmux_id": "$1", "host": "h" * 32}))
        assert isinstance(res, dict) and res["id"] == sid and res["runtime_id"] == "r" * 32
        info = tmux_route.session_store.get(sid)
        assert (info.catalog_session_id, info.runtime_id) == ("c" * 32, "r" * 32)
    finally:
        tmux_route.session_store.remove(sid)


def test_a_screen_session_of_an_old_runtime_is_replaced(monkeypatch):
    """같은 이름의 다른 실행 회차에 붙어 있던 옛 웹 세션은 걷어내고 새로 붙인다(거절하면 이 화면에서 다시 열 길이 없다)."""
    sid = _screen_session("o" * 32, "c" * 32)
    destroyed = []
    monkeypatch.setattr(tmux_route.pty_mgr, "destroy_session", lambda s: destroyed.append(s))

    async def fake_attach(*args, **kwargs):
        return {"id": "fresh"}
    monkeypatch.setattr(tmux_route, "_attach_tmux", fake_attach)
    res = asyncio.run(tmux_route.attach_screen("dev", 80, 24, None, "a" * 32,
                                               {"session_id": "c" * 32, "runtime_id": "r" * 32, "tmux_id": "$1", "host": "h" * 32}))
    assert res == {"id": "fresh"} and destroyed == [sid]
    assert tmux_route.session_store.get(sid) is None
