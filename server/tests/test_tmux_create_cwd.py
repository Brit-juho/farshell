"""N6(60 §4) 스니펫 「새 섹션」의 전제 — POST /api/tmux/create가 cwd를 받으면
그 디렉토리로 tmux 세션을 연다(`new-session -c <cwd>`). 실제 tmux 프로세스를
띄우지 않고, tmux_runner.run에 전달되는 인자와 400 분기만 검증한다(라우트
레이어의 책임 — 실제 cwd 상속 자체는 tmux 자신의 `-c` 계약이라 여기서 다시
검증할 필요가 없다).
"""

import asyncio
import os

import pytest

import routes.tmux as tmux_route
import tmux_runner


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture(autouse=True)
def fake_attach(monkeypatch):
    """new-session 이후의 attach 파이프라인(실 PTY 스폰)은 이 테스트의 관심사가
    아니다 — 최소 스텁으로 대체한다."""
    async def _fake_attach_tmux(name, cols, rows):
        return {"id": "sess-1", "name": name, "tmux_session": name}
    monkeypatch.setattr(tmux_route, "_attach_tmux", _fake_attach_tmux)


def test_create_with_valid_cwd_passes_dash_c(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, timeout=None):
        calls.append(list(args))
        return (0, b"", b"")
    monkeypatch.setattr(tmux_runner, "run", fake_run)

    body = {"name": "wt-test", "cwd": str(tmp_path)}
    resp = asyncio.run(tmux_route.create_tmux_session(_FakeRequest(body)))
    assert resp["tmux_session"] == "wt-test"
    assert calls, "tmux_runner.run이 호출되지 않았다"
    args = calls[0]
    assert "-c" in args
    assert args[args.index("-c") + 1] == str(tmp_path)


def test_create_without_cwd_uses_default_start_dir(monkeypatch):
    calls = []

    def fake_run(args, timeout=None):
        calls.append(list(args))
        return (0, b"", b"")
    monkeypatch.setattr(tmux_runner, "run", fake_run)
    monkeypatch.setattr(tmux_route.platform_utils, "default_start_dir", lambda: "/tmp/default-start")

    body = {"name": "wt-plain"}
    asyncio.run(tmux_route.create_tmux_session(_FakeRequest(body)))
    args = calls[0]
    assert args[args.index("-c") + 1] == "/tmp/default-start"


def test_create_rejects_relative_cwd(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: calls.append(args) or (0, b"", b""))

    body = {"name": "wt-bad", "cwd": "relative/path"}
    resp = asyncio.run(tmux_route.create_tmux_session(_FakeRequest(body)))
    assert resp.status_code == 400
    assert not calls, "검증에 실패했는데 tmux new-session이 호출됐다"


def test_create_rejects_nonexistent_cwd(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: calls.append(args) or (0, b"", b""))

    missing = str(tmp_path / "does-not-exist")
    body = {"name": "wt-bad2", "cwd": missing}
    resp = asyncio.run(tmux_route.create_tmux_session(_FakeRequest(body)))
    assert resp.status_code == 400
    assert not calls
