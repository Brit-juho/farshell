"""세션 목록의 webScreens / otherClients — 「다른 화면 N」 배지의 근거.

webScreens = 이 서버가 PTY를 쥔 웹 화면 수(session_store 기준, 추가 tmux 호출 없음),
otherClients = tmux attached 중 웹이 아닌 것(맥 터미널 등), 음수면 0.
"""

import asyncio

import pytest

import routes.tmux as tmux_route
import tmux_runner
from deps import pty_mgr, session_store


@pytest.fixture
def fake_tmux(monkeypatch):
    calls = []

    async def fake_run_text_async(args, timeout=None):
        calls.append(list(args))
        if args[0] == "list-sessions":
            # name, windows, attached, @fsh_wt, @fsh_origin
            return "a\t1\t3\t\tlocal\nb\t1\t1\t\tlocal\nc\t1\t0\t\tlocal\nd\t1\t1\t\tlocal\n"
        return "a\tzsh\t/x\t%1\nb\tzsh\t/y\t%2\nc\tzsh\t/z\t%3\nd\tzsh\t/w\t%4\n"

    monkeypatch.setattr(tmux_runner, "run_text_async", fake_run_text_async)
    return calls


def _web(monkeypatch, sid, tmux_name, screen, alive=True):
    session_store.add(sid, name=sid, screen_id=screen)
    session_store.update_tmux_name(sid, tmux_name)
    if alive:
        monkeypatch.setitem(pty_mgr._sessions, sid, object())


@pytest.fixture(autouse=True)
def _clean():
    before = {i.session_id for i in session_store.list_all()}
    yield
    for info in session_store.list_all():
        if info.session_id not in before:
            session_store.remove(info.session_id)


def test_web_screens_and_other_clients(fake_tmux, monkeypatch):
    # a: 웹 2(탭 둘) + 맥 터미널 1 / b: 웹 0, attach 1(맥) / c: 아무도 / d: 웹 1, attached 1
    _web(monkeypatch, "ts-a1", "a", "scr1")
    _web(monkeypatch, "ts-a2", "a", "scr2")
    _web(monkeypatch, "ts-d1", "d", "scr1")
    out = {s["name"]: s for s in asyncio.run(tmux_route.collect_tmux_sessions())}
    assert (out["a"]["attached"], out["a"]["webScreens"], out["a"]["otherClients"]) == (3, 2, 1)
    assert (out["b"]["webScreens"], out["b"]["otherClients"]) == (0, 1)
    assert (out["c"]["webScreens"], out["c"]["otherClients"]) == (0, 0)
    assert (out["d"]["webScreens"], out["d"]["otherClients"]) == (1, 0)
    assert len(fake_tmux) == 2, "추가 tmux 호출이 없다(list-sessions + list-panes)"


def test_dead_pty_is_not_a_web_screen(fake_tmux, monkeypatch):
    _web(monkeypatch, "ts-a1", "a", "scr1", alive=False)
    out = {s["name"]: s for s in asyncio.run(tmux_route.collect_tmux_sessions())}
    assert out["a"]["webScreens"] == 0 and out["a"]["otherClients"] == 3


def test_other_clients_never_negative(fake_tmux, monkeypatch):
    # attached(1)보다 웹 화면이 많아도(attach 직전·직후의 일시적 어긋남) 0으로 자른다.
    _web(monkeypatch, "ts-b1", "b", "scr1")
    _web(monkeypatch, "ts-b2", "b", "scr2")
    out = {s["name"]: s for s in asyncio.run(tmux_route.collect_tmux_sessions())}
    assert out["b"]["webScreens"] == 2 and out["b"]["otherClients"] == 0


def test_metadata_uses_active_pane_in_active_window(monkeypatch):
    async def fake_run(args, timeout=None):
        if args[0] == "list-sessions":
            return "a\t2\t0\told-tag\tlocal\n"
        return ("a\tzsh\t/old\t%1\t0\t1\n"
                "a\tclaude\t/other\t%2\t1\t0\n"
                "a\tcodex\t/current\t%3\t1\t1\n")
    monkeypatch.setattr(tmux_runner, "run_text_async", fake_run)
    out = asyncio.run(tmux_route.collect_tmux_sessions())[0]
    assert (out["cwd"], out["command"], out["pane_id"]) == ("/current", "codex", "%3")
    assert out["context"]["cwd"] == "/current"
    assert out["wt_id"] == "old-tag", "생성 소속은 보존하지만 현재 위치로 사용하지 않는다"
