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
