"""세션 '만든 곳'(origin) 기록 — tmux 사용자 옵션 `@fsh_origin`.

값은 local/peer/agent 화이트리스트뿐이고, 기록이 없으면 external이다.
읽기는 기존 list-sessions/list-panes 호출의 포맷에 합쳐져 추가 tmux 호출이 없다.
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


def test_normalize_origin():
    for v in ("local", "peer"):
        assert tmux_runner.normalize_origin(v) == v
    # 옛 `agent`(폐기)도 모르는 값 — external
    for v in ("agent", "", None, "external", "LOCAL", "evil", "local; kill-server"):
        assert tmux_runner.normalize_origin(v) == "external"


def test_set_origin_whitelist(monkeypatch):
    calls = []
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: calls.append(list(args)) or (0, b"", b""))
    assert tmux_runner.set_origin("s", "local") is True
    assert tmux_runner.set_origin("s", "peer") is True
    assert calls[0] == ["set-option", "-t", "s", "@fsh_origin", "local"]
    for bad in ("agent", "external", "", "x", "Local"):
        assert tmux_runner.set_origin("s", bad) is False
    assert len(calls) == 2, "화이트리스트 밖의 값은 tmux를 부르지 않는다"


def test_list_sessions_and_panes_read_origin_in_the_same_call(monkeypatch):
    seen = []

    def fake_run_text(args, timeout=None):
        seen.append(list(args))
        if args[0] == "list-sessions":
            return "a\t1\t0\t100\t\tlocal\nb\t1\t0\t101\t\t\nc\t1\t0\t102\t\tbogus\nd\t1\t0\t103\t\tagent\n"
        return "a\tzsh\t1\t/x\t%1\t\tpeer\nb\tzsh\t2\t/y\t%2\t\t\n"

    monkeypatch.setattr(tmux_runner, "run_text", fake_run_text)
    sessions = tmux_runner.list_sessions()
    assert [s["origin"] for s in sessions] == ["local", "external", "external", "external"]  # 옛 agent 값도 external
    panes = tmux_runner.get_all_panes()
    assert [p.origin for p in panes] == ["peer", "external"]
    assert len(seen) == 2
    assert all("#{@fsh_origin}" in a[-1] for a in seen)


def test_collect_tmux_sessions_has_origin_without_extra_tmux_calls(monkeypatch):
    calls = []

    async def fake_run_text_async(args, timeout=None):
        calls.append(list(args))
        if args[0] == "list-sessions":
            return "a\t1\t0\t\tlocal\nb\t1\t0\t\t\n"
        return "a\tzsh\t/x\t%1\nb\tzsh\t/y\t%2\n"

    monkeypatch.setattr(tmux_runner, "run_text_async", fake_run_text_async)
    out = asyncio.run(tmux_route.collect_tmux_sessions())
    assert {s["name"]: s["origin"] for s in out} == {"a": "local", "b": "external"}
    assert [c[0] for c in calls] == ["list-sessions", "list-panes"], "tmux 호출 수는 그대로"
    assert "#{@fsh_origin}" in calls[0][-1]


@pytest.fixture
def fake_attach(monkeypatch):
    async def _fake(name, cols, rows, request=None):
        return {"id": "s", "name": name}
    monkeypatch.setattr(tmux_route, "_attach_tmux", _fake)


def test_tmux_create_records_local_and_ignores_body_origin(monkeypatch, fake_attach):
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: (0, b"", b""))
    sets = []
    monkeypatch.setattr(tmux_runner, "set_option", lambda s, k, v: sets.append((s, k, v)) or True)
    asyncio.run(tmux_route.create_tmux_session(_FakeRequest({"name": "n1", "origin": "agent"})))
    assert sets == [("n1", "@fsh_origin", "local")]


def test_tmux_create_failure_records_nothing(monkeypatch, fake_attach):
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: (1, b"", b"boom"))
    monkeypatch.setattr(tmux_runner, "set_option", lambda *a: (_ for _ in ()).throw(AssertionError("기록 금지")))
    r = asyncio.run(tmux_route.create_tmux_session(_FakeRequest({"name": "n2"})))
    assert r.status_code == 500


def test_worktree_open_records_local(monkeypatch):
    import worktree
    monkeypatch.setattr(worktree, "find_by_id", lambda i, force=False: {
        "sessions": [], "repoName": "r", "branch": "b", "path": "/p"})
    monkeypatch.setattr(worktree, "invalidate_cache", lambda: None)
    monkeypatch.setattr(tmux_runner, "has_session", lambda n: False)
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=None: (0, b"", b""))
    sets = []
    monkeypatch.setattr(tmux_runner, "set_option", lambda s, k, v: sets.append((k, v)) or True)
    worktree.open_worktree("abc")
    assert ("@fsh_origin", "local") in sets
