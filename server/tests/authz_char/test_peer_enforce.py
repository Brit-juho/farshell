"""`VT_AUTHZ_ENFORCE=peer`(3d): 다른 맥(peer)의 `/api/peer/*` 라우트와 열린 소켓을 엔진이 판정한다.

같은 사례를 켠 채로 돌려 기록(matrix.json)과 맞추는 시험은 `test_char_peer_enforced.py`. 여기는 켜졌을 때만 성립하는 성질:

  - 선언 단일 표: 모든 `/api/peer/*` 라우트가 `peer_svc.NEEDS` 에 (scope, 조건)을 적었다 — 빠지면 실패.
  - 등급 × 라우트 parity 행렬(보기·제어·제어+agentMessages)과 응답 본문이 옛 등급 검사와 같다.
  - `peer_ws`: 입력 프레임마다 재확인, grant 회수 후 5초 안에 소켓이 닫힌다(기본 주기 2초).
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from authz_harness import MACHINE, Case, FakeSess


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "peer")
    from authz import peer_svc
    peer_svc._denied_at.clear()          # 거부 감사의 60초 중복 억제는 프로세스 전역이다 — 시험끼리 섞이지 않게


def client():
    import main
    return TestClient(main.app, raise_server_exceptions=False)


def peer_get(world, who: str, path: str, method: str = "GET", body: bytes = b""):
    world.set_mode("password")
    h = world.headers_for(who, method, path, body)
    r = client().request(method, path, content=body or None, headers=h)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


# --- 선언 단일 표 --------------------------------------------------------------------------------


def _peer_routes():
    """`/api/peer/*` 를 가진 라우터들을 직접 훑는다(main 이 실제로 포함했는지도 본다)."""
    import importlib

    import main
    from fastapi import APIRouter
    from fastapi.routing import APIRoute, APIWebSocketRoute
    included = {id(getattr(r, "original_router", None)) for r in main.app.router.routes}
    out = []
    for name in ("routes.peer", "routes.peer_relay"):
        mod = importlib.import_module(name)
        for router in {id(v): v for v in vars(mod).values() if isinstance(v, APIRouter)}.values():
            assert id(router) in included, f"{name} 의 라우터가 main 에 포함되지 않음"
            out += [r for r in router.routes if isinstance(r, (APIRoute, APIWebSocketRoute)) and r.path.startswith("/api/peer")]
    return out


def test_every_peer_route_is_declared():
    """라우트를 새로 만들고 (scope, 조건)을 표에 안 적으면 여기서 실패한다."""
    from authz import peer_svc
    routes = _peer_routes()
    assert len(routes) >= 15, [r.path for r in routes]            # 못 찾아 공허하게 통과하는 일을 막는다
    missing = []
    for r in routes:
        action = getattr(r.endpoint, "peer_action", None)
        if action is None:
            if r.path not in peer_svc.ROUTE_ACTIONS:
                missing.append(r.path)
                continue
            action = peer_svc.ROUTE_ACTIONS[r.path]
            if action is None:
                continue                                          # 페어링: 서명 대신 1회용 티켓이 인증
        if action not in peer_svc.NEEDS:
            missing.append(f"{r.path} ({action})")
    assert missing == [], f"peer_svc.NEEDS 에 (scope, 조건)이 없는 라우트: {missing}"


def test_the_table_uses_only_known_scopes_and_matches_the_decorator_levels():
    from authz import peer_svc
    from authz import scopes as S
    for name, n in peer_svc.NEEDS.items():
        assert S.known(n.scope), (name, n)
    for r in _peer_routes():
        action = getattr(r.endpoint, "peer_action", None)
        if action is None:
            continue
        assert peer_svc.level_of(peer_svc.NEEDS[action]) == r.endpoint.peer_level, (r.path, action)


def test_an_undeclared_action_falls_back_to_its_level_not_to_allow():
    from authz import peer_svc
    assert peer_svc.need_for("nope", "view").scope == "session.view"
    assert peer_svc.need_for("nope", "control").scope == "session.input"
    assert peer_svc.need_for("nope", "weird").scope == "level.weird"


# --- parity: 등급 × 라우트 ------------------------------------------------------------------------

ROUTES = [
    ("GET", "/api/peer/sessions", None), ("GET", "/api/peer/clients?session=target", None),
    ("GET", "/api/peer/search?q=foo", None), ("GET", "/api/peer/ping", None),
    ("POST", "/api/peer/clients/detach", {"session": "target", "ttys": []}),
    ("POST", "/api/peer/clients/solo", {"session": "target"}),
    ("POST", "/api/peer/input", {"session": "target", "data": "ls"}),
    ("POST", "/api/peer/sessions/create", {"name": "newone"}),
    ("POST", "/api/peer/sessions/kill", {"name": "other"}),
    ("POST", "/api/peer/sessions/rename", {"name": "other", "new": "other2"}),
    ("POST", "/api/peer/agent/message", {"to": "target", "text": "hi", "from": "dev", "id": "m1"}),
    ("POST", "/api/peer/file", b"file-bytes"),
]


def _run(world, who, method, path, body):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
    world.set_mode("password")
    h = world.headers_for(who, method, path, data)
    if path == "/api/peer/file":
        h["X-Peer-File-Name"] = "a.txt"
    elif data:
        h["Content-Type"] = "application/json"
    r = client().request(method, path, content=data or None, headers=h)
    try:
        j = r.json()
    except ValueError:
        j = None
    return r.status_code, j


@pytest.mark.parametrize("method,path,body", ROUTES, ids=lambda x: str(x)[:40])
def test_the_level_by_route_matrix_matches_the_old_gate(world, method, path, body):
    """보기·제어·제어+agentMessages × 모든 라우트: 켠 상태의 상태 코드가 옛 등급 규칙(제어 라우트는 control 만, agent/message 는 스위치까지)과 같다.
    (기록 전체와의 비교는 `test_char_peer_enforced.py`)"""
    control_only = path.split("?")[0] not in ("/api/peer/sessions", "/api/peer/clients", "/api/peer/search", "/api/peer/ping")
    for who, level in (("peer_view", "view"), ("peer_control", "control"), ("peer_control_agent", "control")):
        st, j = _run(world, who, method, path, body)
        if control_only and level == "view":
            assert (st, j["error"]) == (403, "level_required"), (who, st, j)
        elif path == "/api/peer/agent/message" and who == "peer_control":
            assert (st, j["error"]) == (403, "agent_messages_off")
        else:
            assert st != 403 or (j or {}).get("error") not in ("level_required", "peer_forbidden"), (who, st, j)


def test_a_view_peer_gets_the_exact_old_level_required_body(world):
    st, body = peer_get(world, "peer_view", "/api/peer/sessions/kill", "POST", b"{}")
    assert st == 403 and body["error"] == "level_required"
    assert "fsh host allow-control" in body["reason"]


def test_agent_message_checks_the_control_gate_then_the_switch(world):
    body = json.dumps({"to": "target", "text": "hi", "from": "dev", "id": "m1"}).encode()
    st, j = _run(world, "peer_view", "POST", "/api/peer/agent/message", {"to": "target", "text": "hi", "from": "dev", "id": "m1"})
    assert (st, j["error"]) == (403, "level_required")
    st, j = _run(world, "peer_control", "POST", "/api/peer/agent/message", {"to": "target", "text": "hi", "from": "dev", "id": "m2"})
    assert (st, j["error"]) == (403, "agent_messages_off")
    st, j = _run(world, "peer_control_agent", "POST", "/api/peer/agent/message", {"to": "target", "text": "hi", "from": "dev", "id": "m3"})
    assert st == 200 and j["ok"] is True
    assert body


def test_a_deny_rule_in_the_store_blocks_a_route_even_for_control(world):
    """저장소의 deny 규칙은 hosts.json 에서 번역한 grant 보다 우선한다(옛 등급 검사에는 없던 길)."""
    from authz import store
    with store.txn() as docs:
        docs["grants"]["items"].append({"id": "d-input", "effect": "deny", "subject": "peer:pc", "resource": "*",
                                        "scopes": ["session.input"], "sconds": {"session.input": {"modes": ["raw"]}},
                                        "state": "active", "schema": 2})
    st, j = _run(world, "peer_control", "POST", "/api/peer/input", {"session": "target", "data": "ls"})
    assert (st, j["error"]) == (403, "level_required")
    st, j = _run(world, "peer_control", "GET", "/api/peer/sessions", None)
    assert st == 200


def test_a_corrupt_store_fails_closed_with_503_for_a_peer(world):
    from authz import store
    store.doc_path("grants").parent.mkdir(parents=True, exist_ok=True)
    store.doc_path("grants").write_text("{not json", encoding="utf-8")
    st, j = _run(world, "peer_view", "GET", "/api/peer/sessions", None)
    assert (st, j["error"]) == (503, "authz_unavailable")


# --- peer_ws -------------------------------------------------------------------------------------


class _Pty:
    """attach PTY 흉내 — 쓰기를 기록하고 출력은 흘리지 않는다."""

    def __init__(self):
        self.sessions: dict = {}
        self.writes: list = []

    def create_session(self, sid, **_):
        self.sessions[sid] = object()

    def subscribe(self, sid, cb):
        pass

    def unsubscribe(self, sid, cb):
        pass

    def destroy_session(self, sid):
        self.sessions.pop(sid, None)

    def write(self, sid, data):
        self.writes.append(data)

    def resize(self, *a):
        pass


@pytest.fixture
def ws_world(world, monkeypatch):
    import deps
    pty = _Pty()
    monkeypatch.setattr(deps, "pty_mgr", pty)
    world.set_mode("password")
    return world, pty


def _open(world, who="peer_control"):
    path = "/api/peer/ws/target"
    h = world.headers_for(who, "GET", path, b"")
    return client().websocket_connect(path, headers=h)


def _wait(cond, secs=2.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _closer(ws, after):
    t = threading.Timer(after, lambda: ws.close())
    t.daemon = True
    t.start()
    return t


def test_ws_input_is_rechecked_per_frame_and_the_socket_closes_after_a_downgrade(ws_world):
    import host_store
    world, pty = ws_world
    with _open(world) as ws:
        ws.send_bytes(b"a")
        assert _wait(lambda: pty.writes == [b"a"])
        host_store.set_grant_level("pc", host_store.LEVEL_VIEW)          # 회수 — 상대는 아직 연결돼 있다
        ws.send_bytes(b"b")                                              # 이 프레임은 연결 시점이 아니라 지금 권한으로 판정된다
        ws.send_text(json.dumps({"type": "input", "data": "c"}))
        time.sleep(0.4)
        assert pty.writes == [b"a"], pty.writes
        timer = _closer(ws, 7.0)
        t0 = time.time()
        with pytest.raises(WebSocketDisconnect) as e:
            while True:
                ws.receive_bytes()
        timer.cancel()
        assert e.value.code == 4401, e.value.code
        assert time.time() - t0 < 5.0, "회수 후 5초 안에 닫혀야 한다"


def test_ws_closes_within_five_seconds_when_the_peer_is_removed(ws_world):
    import host_store
    world, pty = ws_world
    with _open(world, "peer_view") as ws:
        time.sleep(0.2)
        host_store.remove_grant("pv")
        timer = _closer(ws, 7.0)
        t0 = time.time()
        with pytest.raises(WebSocketDisconnect) as e:
            while True:
                ws.receive_bytes()
        timer.cancel()
        assert e.value.code == 4401
        assert time.time() - t0 < 5.0


def test_ws_poll_interval_is_short_enough_for_the_five_second_promise():
    from authz import peer_svc
    assert 0 < peer_svc.WATCH_INTERVAL <= 2.0


def test_a_view_socket_never_writes_and_a_control_socket_does(ws_world):
    world, pty = ws_world
    with _open(world, "peer_view") as ws:
        ws.send_bytes(b"x")
        time.sleep(0.3)
    assert pty.writes == []
    with _open(world, "peer_control") as ws:
        ws.send_text(json.dumps({"type": "input", "data": "ok"}))
        assert _wait(lambda: pty.writes == [b"ok"])


def test_grant_watch_uses_the_cache_until_a_file_changes(world, monkeypatch):
    import host_store
    from authz import peer_svc
    w = peer_svc.GrantWatch("pc", "target")
    w.refresh(True)
    assert w.alive and w.view_ok and w.input_ok
    calls = []
    real = host_store.find_grant
    monkeypatch.setattr(host_store, "find_grant", lambda i: calls.append(i) or real(i))
    for _ in range(50):
        assert w.input_allowed()
    assert calls == []                                   # 파일이 안 바뀌었으면 입력 프레임마다 읽지 않는다
    host_store.set_grant_level("pc", host_store.LEVEL_VIEW)
    assert w.input_allowed() is False and calls == ["pc"]
