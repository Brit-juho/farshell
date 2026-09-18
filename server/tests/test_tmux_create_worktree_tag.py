"""2.1 D2 그루드워크 — POST /api/tmux/create가 `worktreeId`를 받으면 새 세션에
`@fsh_wt`를 심는다. 지금 어느 프런트도 이 필드를 보내지 않지만(2단계 이후에
쓰인다), 엔드포인트가 받아둘 수 있어야 나중에 프런트만 바꿔도 된다.
"""

import asyncio

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
    async def _fake_attach_tmux(name, cols, rows):
        return {"id": "sess-1", "name": name, "tmux_session": name}
    monkeypatch.setattr(tmux_route, "_attach_tmux", _fake_attach_tmux)


def test_create_with_worktree_id_tags_the_session(monkeypatch):
    run_calls = []
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: run_calls.append(list(args)) or (0, b"", b""))
    tagged = []
    monkeypatch.setattr(tmux_runner, "set_option", lambda s, k, v: tagged.append((s, k, v)) or True)

    body = {"name": "wt-test", "worktreeId": "abcdef123456"}
    asyncio.run(tmux_route.create_tmux_session(_FakeRequest(body)))

    assert tagged == [("wt-test", "@fsh_wt", "abcdef123456")]


def test_create_without_worktree_id_does_not_tag(monkeypatch):
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: (0, b"", b""))
    monkeypatch.setattr(tmux_runner, "set_option", lambda *a: (_ for _ in ()).throw(
        AssertionError("worktreeId 없이 태그를 심으면 안 된다")))

    body = {"name": "wt-plain"}
    asyncio.run(tmux_route.create_tmux_session(_FakeRequest(body)))


def test_create_rejects_malformed_worktree_id(monkeypatch):
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: (0, b"", b""))
    monkeypatch.setattr(tmux_runner, "set_option", lambda *a: (_ for _ in ()).throw(
        AssertionError("형식이 안 맞는 worktreeId는 무시해야 한다")))

    body = {"name": "wt-bad-id", "worktreeId": "not-a-real-id; rm -rf"}
    asyncio.run(tmux_route.create_tmux_session(_FakeRequest(body)))
