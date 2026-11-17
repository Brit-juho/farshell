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
    async def _fake_attach_tmux(name, cols, rows, request=None):
        return {"id": "sess-1", "name": name, "tmux_session": name}
    monkeypatch.setattr(tmux_route, "_attach_tmux", _fake_attach_tmux)
    monkeypatch.setattr(tmux_route, "_alias_after_create", lambda name: None)


def _new_session(calls):
    return next(a for a in calls if a[0] == "new-session")


def test_create_with_valid_cwd_passes_dash_c(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, timeout=None):
        calls.append(list(args))
        return (0, b"", b"")
    monkeypatch.setattr(tmux_runner, "run", fake_run)

    body = {"name": "wt-test", "cwd": str(tmp_path)}
    resp = asyncio.run(tmux_route.create_tmux_session(_FakeRequest(body)))
    import session_names
    assert session_names.is_id(resp["tmux_session"])
    args = _new_session(calls)
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
    args = _new_session(calls)
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


# ── 세션 ID와 별칭 요청(2026-11 설계) — tmux 이름은 랜덤 ID, 사람이 친 이름·폴더 이름은 별칭 요청 ─────────────

def _fake_tmux(monkeypatch, existing):
    """has-session은 `existing`에 있는 이름만 0, new-session은 이름을 기록하고 existing에 더한다."""
    created = []

    def fake_run(args, timeout=None):
        if args[0] == "has-session":
            return (0 if args[args.index("-t") + 1].removeprefix("=") in existing else 1, b"", b"")
        if args[0] == "new-session":
            name = args[args.index("-s") + 1]
            created.append(name)
            existing.add(name)
        return (0, b"", b"")
    monkeypatch.setattr(tmux_runner, "run", fake_run)
    return created


def _requests(created):
    import session_names
    assert all(session_names.is_id(n) for n in created), created
    return [session_names.take_alias_request(n) for n in created]


def test_every_create_gets_a_distinct_random_id_and_keeps_the_name_as_alias_request(monkeypatch):
    created = _fake_tmux(monkeypatch, set())
    for _ in range(3):
        asyncio.run(tmux_route.create_tmux_session(_FakeRequest({"name": "deploy", "unique_name": True})))
    assert len(set(created)) == 3
    assert _requests(created) == ["deploy"] * 3       # 번호(`deploy-1`)는 기록이 붙인다(test_catalog_v2_behaviour)


def test_korean_and_punctuation_names_are_kept_as_aliases(tmp_path, monkeypatch):
    created = _fake_tmux(monkeypatch, set())
    asyncio.run(tmux_route.create_tmux_session(_FakeRequest({"name": "입력만", "unique_name": True})))
    asyncio.run(tmux_route.create_tmux_session(_FakeRequest({"name": "my build: v1.2", "unique_name": True})))
    assert _requests(created) == ["입력만", "my build: v1.2"]


def test_default_alias_comes_from_requested_cwd(tmp_path, monkeypatch):
    proj = tmp_path / "Proj"
    proj.mkdir()
    created = _fake_tmux(monkeypatch, set())
    asyncio.run(tmux_route.create_tmux_session(_FakeRequest({"cwd": str(proj)})))
    assert _requests(created) == ["Proj"]


def test_concurrent_creates_get_distinct_ids(monkeypatch):
    created = _fake_tmux(monkeypatch, set())

    async def go():
        await asyncio.gather(*[
            tmux_route.create_tmux_session(_FakeRequest({"name": "same", "unique_name": True}))
            for _ in range(4)
        ])
    asyncio.run(go())
    assert len(set(created)) == 4


def test_an_id_taken_meanwhile_is_retried_with_a_new_id(monkeypatch):
    created, seen = [], []

    def fake_run(args, timeout=None):
        if args[0] == "new-session":
            name = args[args.index("-s") + 1]
            seen.append(name)
            if len(seen) == 1:
                return (1, b"", b"duplicate session: " + name.encode())
            created.append(name)
        return (1, b"", b"") if args[0] == "has-session" else (0, b"", b"")
    monkeypatch.setattr(tmux_runner, "run", fake_run)
    resp = asyncio.run(tmux_route.create_tmux_session(_FakeRequest({"name": "x", "unique_name": True})))
    assert len(seen) == 2 and resp["tmux_session"] == created[0] == seen[1]
