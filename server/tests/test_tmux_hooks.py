"""tmux 훅이 세션 목록 변화를 서버에 알린다(2026-09-30, tmux_hooks.py).

실제 tmux는 부르지 않는다 — tmux_runner.run을 가짜로 바꿔 무엇을 부르는지만 본다.
"""

import pytest
from starlette.testclient import TestClient

import auth
import main
import tmux_hooks
import tmux_runner
from routes import system as system_route


@pytest.fixture
def calls(monkeypatch):
    box = {"calls": [], "show": b""}

    def fake_run(args, timeout=2.0, input=None):
        box["calls"].append(list(args))
        if args[0] == "show-hooks":
            return 0, box["show"], b""
        return 0, b"", b""
    monkeypatch.setattr(tmux_runner, "run", fake_run)
    return box


def test_hook_command_carries_secret_and_port():
    cmd = tmux_hooks.hook_command("7791")
    assert cmd.startswith('run-shell -b "') and cmd.endswith('"')
    assert "http://127.0.0.1:7791/api/tmux/kick" in cmd
    assert f"{tmux_hooks.HEADER}: {tmux_hooks._SECRET}" in cmd
    assert "-m 2" in cmd, "서버가 내려가 있어도 훅이 쌓이지 않게 짧게 포기한다"


@pytest.mark.real_tmux_hooks
def test_install_uses_our_slot_for_every_event(calls):
    assert tmux_hooks.install() is True
    slots = [c[2] for c in calls["calls"] if c[0] == "set-hook"]
    assert slots == [f"{e}[{tmux_hooks.HOOK_INDEX}]" for e in tmux_hooks.EVENTS]
    assert all(c[1] == "-g" for c in calls["calls"]), "사용자 훅([0])이 아닌 우리 칸만"


@pytest.mark.real_tmux_hooks
def test_uninstall_leaves_another_servers_hook(calls):
    calls["show"] = b'session-created[77] run-shell -b "curl ... other-secret ..."'
    tmux_hooks.uninstall()
    assert not [c for c in calls["calls"] if c[0] == "set-hook"], \
        "다른 서버가 덮어쓴 칸은 지우지 않는다"

    calls["calls"].clear()
    calls["show"] = f"... {tmux_hooks._SECRET} ...".encode()
    tmux_hooks.uninstall()
    removed = [c for c in calls["calls"] if c[0] == "set-hook"]
    assert len(removed) == len(tmux_hooks.EVENTS) and all(c[1] == "-gu" for c in removed)


def test_route_path_matches_hook_url():
    # 라우트는 문서 검사(check_docs.py) 때문에 경로를 문자 그대로 적는다 — 훅이
    # 부르는 주소와 어긋나면 훅이 조용히 404만 받는다.
    paths = {r.path for r in system_route.router.routes}
    assert tmux_hooks.KICK_PATH in paths


def test_verify():
    assert tmux_hooks.verify(tmux_hooks._SECRET)
    assert not tmux_hooks.verify("wrong")
    assert not tmux_hooks.verify(None)
    assert not tmux_hooks.verify("")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    with TestClient(main.app) as c:
        yield c


def test_kick_endpoint_checks_secret_and_nudges_watcher(client, monkeypatch):
    kicked = []
    monkeypatch.setattr(system_route, "kick_sessions", lambda: kicked.append(1))
    assert client.post("/api/tmux/kick").status_code == 403
    assert client.post("/api/tmux/kick", headers={tmux_hooks.HEADER: "wrong"}).status_code == 403
    assert kicked == []
    r = client.post("/api/tmux/kick", headers={tmux_hooks.HEADER: tmux_hooks._SECRET})
    assert r.status_code == 204 and kicked == [1]


def test_kick_passes_login_gate_but_still_needs_secret(client, monkeypatch):
    # 비밀번호만 쓰는 서버(기계 토큰 없음)에서도 훅이 닿아야 한다 — 일반 인증은
    # 빼고 비밀값만 본다. 인증이 켜진 상태에서 비밀값이 없으면 여전히 403이다.
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "scrypt$x")
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "")
    monkeypatch.setattr(system_route, "kick_sessions", lambda: None)
    assert client.get("/api/tmux/sessions").status_code == 401, "전제: 인증이 켜져 있다"
    assert client.post("/api/tmux/kick").status_code == 403
    r = client.post("/api/tmux/kick", headers={tmux_hooks.HEADER: tmux_hooks._SECRET})
    assert r.status_code == 204


def test_agent_event_nudges_session_watcher(client, monkeypatch):
    # 에이전트가 시작·끝나는 순간 pane의 실행 중 명령이 바뀐다 — tmux엔 그 훅이 없다.
    kicked = []
    monkeypatch.setattr(system_route, "kick_sessions", lambda: kicked.append(1))
    client.post("/api/agent/event", json={"event": "stop", "payload": {}})
    assert kicked == [1]
