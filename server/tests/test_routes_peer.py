"""N7/N39 1단계 — `/api/peer/*` 라우터 회귀.

host_store.py의 서명/레지스트리 로직은 test_hosts.py에서 단위 테스트하므로,
여기서는 **HTTP 경계에서만 확인 가능한 것**을 본다:
  - TokenAuthMiddleware를 우회하되 미인증으로 뚫리지는 않는다
  - peer 토큰으로 다른 API(`/api/files` 등)에 못 넘어간다 ← 네임스페이스 분리의 핵심
  - 취소된 상대는 서명이 유효해도 즉시 거부
"""

from __future__ import annotations

import importlib
import time

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    """인증이 켜진 서버 — peer 경로가 그걸 우회하는지 봐야 하므로 일부러 보호 상태로."""
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_WORKSPACE_PATH", str(tmp_path / "workspace.json"))
    import auth as _auth
    monkeypatch.setattr(_auth, "STATE_DIR", tmp_path)
    monkeypatch.setattr(_auth, "DEVICES_PATH", tmp_path / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", tmp_path / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", tmp_path / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "peer-test-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password("pw"))

    import host_store
    importlib.reload(host_store)
    import routes.peer as peer_mod
    importlib.reload(peer_mod)
    import main
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c, host_store, peer_mod


def _sign_headers(hs, peer_id, secret, method, path, ts=None, nonce=None):
    ts = int(time.time()) if ts is None else ts
    nonce = nonce or f"n-{time.time_ns()}"
    return {
        "X-Peer-Id": peer_id,
        "X-Peer-Ts": str(ts),
        "X-Peer-Nonce": nonce,
        "X-Peer-Sig": hs.sign_request(secret, method, path, ts, nonce),
    }


def _pair(client, hs, peer_id="laptop"):
    ticket = hs.issue_pair_ticket()
    r = client.post("/api/peer/pair", json={"ticket": ticket, "id": peer_id, "label": "노트북"})
    assert r.status_code == 200, r.text
    return r.json()["secret"]


# --- 페어링 ------------------------------------------------------------------------


def test_pair_works_without_login_session(env):
    """서버는 인증 보호 상태지만 페어링은 티켓만으로 통과해야 한다 —
    아직 공유 secret이 없는 시점이라 다른 인증 수단이 있을 수 없다."""
    client, hs, _ = env
    assert client.get("/api/capabilities").status_code == 401  # 일반 API는 막혀 있다
    secret = _pair(client, hs)
    assert secret and len(secret) > 20


def test_pair_rejects_bad_ticket(env):
    client, hs, _ = env
    r = client.post("/api/peer/pair", json={"ticket": "nope", "id": "laptop"})
    assert r.status_code == 401
    assert r.json()["error"] == "ticket_invalid"


def test_pair_rejects_reserved_id_before_consuming_ticket(env):
    """`local`로 등록 시도는 400이고, **티켓은 소모되지 않아야** 한다 —
    잘못된 id 한 번으로 사용자의 티켓을 태워버리면 안 된다."""
    client, hs, _ = env
    ticket = hs.issue_pair_ticket()
    r = client.post("/api/peer/pair", json={"ticket": ticket, "id": "local"})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_id"
    # 같은 티켓이 아직 살아 있다
    r2 = client.post("/api/peer/pair", json={"ticket": ticket, "id": "laptop"})
    assert r2.status_code == 200


def test_pair_response_exposes_only_public_fields(env):
    client, hs, _ = env
    ticket = hs.issue_pair_ticket()
    body = client.post("/api/peer/pair", json={"ticket": ticket, "id": "laptop"}).json()
    assert set(body) == {"ok", "secret", "id", "label", "version", "serverTime", "level"}
    assert body["level"] == "view"  # 기본은 읽기 전용


def test_pair_is_single_use(env):
    client, hs, _ = env
    ticket = hs.issue_pair_ticket()
    assert client.post("/api/peer/pair", json={"ticket": ticket, "id": "a"}).status_code == 200
    assert client.post("/api/peer/pair", json={"ticket": ticket, "id": "b"}).status_code == 401


# --- ping 인증 ---------------------------------------------------------------------


def test_ping_without_signature_is_401(env):
    client, _, _ = env
    r = client.get("/api/peer/ping")
    assert r.status_code == 401
    assert r.json()["error"] == "peer_denied"


def test_ping_with_valid_signature_succeeds(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    r = client.get("/api/peer/ping",
                   headers=_sign_headers(hs, "laptop", secret, "GET", "/api/peer/ping"))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["level"] == "view"
    assert body["serverTime"] > 0  # 상대가 시계 오차를 계산할 수 있어야 한다


def test_ping_with_wrong_secret_is_401(env):
    client, hs, _ = env
    _pair(client, hs)
    r = client.get("/api/peer/ping",
                   headers=_sign_headers(hs, "laptop", "wrong-secret", "GET", "/api/peer/ping"))
    assert r.status_code == 401


def test_ping_with_stale_timestamp_is_401(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    old = int(time.time()) - hs.SIGNATURE_WINDOW_SEC - 30
    r = client.get("/api/peer/ping",
                   headers=_sign_headers(hs, "laptop", secret, "GET", "/api/peer/ping", ts=old))
    assert r.status_code == 401


def test_replayed_nonce_is_rejected(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    h = _sign_headers(hs, "laptop", secret, "GET", "/api/peer/ping", nonce="fixed")
    assert client.get("/api/peer/ping", headers=h).status_code == 200
    assert client.get("/api/peer/ping", headers=h).status_code == 401


def test_signature_for_other_path_is_rejected(env):
    """다른 경로용 서명을 ping에 돌려쓸 수 없다."""
    client, hs, _ = env
    secret = _pair(client, hs)
    h = _sign_headers(hs, "laptop", secret, "GET", "/api/peer/other")
    assert client.get("/api/peer/ping", headers=h).status_code == 401


def test_revoked_peer_is_rejected_even_with_valid_signature(env):
    """취소는 서명 검증보다 앞선다 — 상대가 옛 secret을 갖고 있어도 즉시 막힌다."""
    client, hs, _ = env
    secret = _pair(client, hs)
    assert hs.remove_grant("laptop") is True
    r = client.get("/api/peer/ping",
                   headers=_sign_headers(hs, "laptop", secret, "GET", "/api/peer/ping"))
    assert r.status_code == 401
    assert "등록되지" in r.json()["reason"]


def test_revoke_all_kills_peer_access(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.revoke_all()
    r = client.get("/api/peer/ping",
                   headers=_sign_headers(hs, "laptop", secret, "GET", "/api/peer/ping"))
    assert r.status_code == 401


# --- 네임스페이스 격리 (이 설계의 핵심) ------------------------------------------------


def test_peer_credentials_cannot_reach_other_apis(env):
    """peer 서명 헤더로 일반 API를 부르면 여전히 401이어야 한다.
    토큰 하나가 새도 파일·git·포트 같은 건 손 못 댄다는 보장."""
    client, hs, _ = env
    secret = _pair(client, hs)
    for path in ("/api/files", "/api/capabilities", "/api/sessions", "/api/ports"):
        r = client.get(path, headers=_sign_headers(hs, "laptop", secret, "GET", path))
        assert r.status_code == 401, f"{path}가 peer 자격증명으로 뚫렸다"


def test_peer_bypass_is_prefix_scoped(env):
    """`/api/peer`로 시작하는 **다른** 경로가 실수로 열려 있지 않은지."""
    client, _, _ = env
    assert client.get("/api/peering-secret").status_code == 401


# --- 감사 로그 -------------------------------------------------------------------------


def test_failures_are_audited(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    client.get("/api/peer/ping",
               headers=_sign_headers(hs, "laptop", "wrong", "GET", "/api/peer/ping"))
    rows = hs.read_audit("laptop")
    assert any(r["action"] == "auth" and not r["ok"] for r in rows)


def test_successful_ping_is_audited(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    client.get("/api/peer/ping",
               headers=_sign_headers(hs, "laptop", secret, "GET", "/api/peer/ping"))
    assert any(r["action"] == "ping" and r["ok"] for r in hs.read_audit("laptop"))


# --- Origin 가드 (B6 — 우연히 동작하는 것을 고정) --------------------------------------


def test_server_to_server_request_has_no_origin_and_passes(env):
    """서버-서버 요청은 Origin 헤더가 없어 OriginGuardMiddleware를 통과한다.
    지금은 우연히 그렇게 동작하는 것이라, 나중에 'Origin 없으면 거부'로 강화할 때
    멀티호스트가 조용히 죽지 않도록 여기서 고정한다."""
    client, hs, _ = env
    secret = _pair(client, hs)
    r = client.get("/api/peer/ping",
                   headers=_sign_headers(hs, "laptop", secret, "GET", "/api/peer/ping"))
    assert r.status_code == 200


def test_cross_origin_peer_request_is_blocked(env):
    """반대로 브라우저에서 온 것처럼 Origin이 붙으면 OriginGuard가 막아야 한다 —
    악성 웹페이지가 로컬 서버의 peer 엔드포인트를 두드리는 경로 차단."""
    client, hs, _ = env
    secret = _pair(client, hs)
    h = _sign_headers(hs, "laptop", secret, "GET", "/api/peer/ping")
    h["Origin"] = "https://evil.example.com"
    r = client.get("/api/peer/ping", headers=h)
    assert r.status_code == 403


# --- /api/peer/sessions (2단계) --------------------------------------------------


def _fake_tmux(monkeypatch, sessions=("dev",)):
    import tmux_runner
    lines = "\n".join(f"{n}\t1\t0" for n in sessions)
    monkeypatch.setattr(tmux_runner, "run_text", lambda *a, **kw: lines)
    monkeypatch.setattr(tmux_runner, "get_all_panes", lambda: [])


def test_sessions_requires_signature(env, monkeypatch):
    client, _, _ = env
    _fake_tmux(monkeypatch)
    assert client.get("/api/peer/sessions").status_code == 401


def test_sessions_returns_local_tmux_sessions(env, monkeypatch):
    client, hs, _ = env
    secret = _pair(client, hs)
    _fake_tmux(monkeypatch, ("dev", "build"))
    r = client.get("/api/peer/sessions",
                   headers=_sign_headers(hs, "laptop", secret, "GET", "/api/peer/sessions"))
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body["sessions"]] == ["dev", "build"]
    assert body["id"] and body["label"]


def test_sessions_reports_local_agent_status_only(env, monkeypatch):
    """**전이 금지(hop 0)** — 내가 등록한 다른 peer의 상태가 섞이면 A↔B 상호
    페어링에서 무한 재귀가 된다. 로컬 엔트리만 실려야 한다."""
    client, hs, _ = env
    secret = _pair(client, hs)
    _fake_tmux(monkeypatch, ("dev",))
    import agent_status
    agent_status._state.clear()
    agent_status.report("s-local", agent_status.WORKING, session="dev")
    agent_status.report("s-other", agent_status.WAITING, session="dev", host="third-mac")
    r = client.get("/api/peer/sessions",
                   headers=_sign_headers(hs, "laptop", secret, "GET", "/api/peer/sessions"))
    assert r.json()["sessions"][0]["status"] == agent_status.WORKING
    agent_status._state.clear()


def test_sessions_is_audited(env, monkeypatch):
    client, hs, _ = env
    secret = _pair(client, hs)
    _fake_tmux(monkeypatch)
    client.get("/api/peer/sessions",
               headers=_sign_headers(hs, "laptop", secret, "GET", "/api/peer/sessions"))
    assert any(r["action"] == "sessions" and r["ok"] for r in hs.read_audit("laptop"))


# --- 3단계: 입력(control) · WS ------------------------------------------------------


def test_input_is_refused_for_view_level_with_actionable_reason(env):
    """기본 등급은 view다 — 입력은 막히되, **어떻게 켜는지**까지 말해야 한다."""
    client, hs, _ = env
    secret = _pair(client, hs)
    r = client.post(
        "/api/peer/input",
        json={"session": "dev", "data": "ls"},
        headers=_sign_headers(hs, "laptop", secret, "POST", "/api/peer/input"),
    )
    assert r.status_code == 403
    assert r.json()["error"] == "level_required"
    assert "allow-control" in r.json()["reason"]


def test_input_types_into_pane_at_control_level(env, monkeypatch):
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)

    import tmux_target
    calls = []
    monkeypatch.setattr(tmux_target, "session_pane", lambda name: "%7" if name == "dev" else None)
    monkeypatch.setattr(tmux_target, "type_to_tmux", lambda pane, text: calls.append((pane, text)) or True)

    r = client.post(
        "/api/peer/input",
        json={"session": "dev", "data": "git status"},
        headers=_sign_headers(hs, "laptop", secret, "POST", "/api/peer/input"),
    )
    assert r.status_code == 200
    assert calls == [("%7", "git status")]

    # 없는 세션은 404 — 입력이 엉뚱한 pane으로 새지 않는다.
    r = client.post(
        "/api/peer/input",
        json={"session": "ghost", "data": "x"},
        headers=_sign_headers(hs, "laptop", secret, "POST", "/api/peer/input"),
    )
    assert r.status_code == 404


def test_view_get_signature_cannot_be_replayed_on_control_post(env):
    """서명이 method+path에 묶여 있다는 계약 — 3단계에서 실제로 중요해진다."""
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)
    headers = _sign_headers(hs, "laptop", secret, "GET", "/api/peer/sessions")
    r = client.post("/api/peer/input", json={"session": "dev", "data": "x"}, headers=headers)
    assert r.status_code == 401


def test_input_requires_session_and_data(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)
    r = client.post(
        "/api/peer/input", json={"session": "dev"},
        headers=_sign_headers(hs, "laptop", secret, "POST", "/api/peer/input"),
    )
    assert r.status_code == 400


def test_peer_ws_rejects_unsigned_connection(env):
    """서명 없이 붙으면 PTY를 만들기 전에 끊긴다."""
    from starlette.websockets import WebSocketDisconnect as WSD
    client, hs, _ = env
    with pytest.raises(WSD) as e:
        with client.websocket_connect("/api/peer/ws/dev") as ws:
            ws.receive_bytes()
    assert e.value.code == 4401


def test_peer_ws_rejects_unknown_tmux_session(env, monkeypatch):
    """세션이 없으면 4404 — 유령 PTY를 만들지 않는다(routes/tmux.py가 겪은 그 버그)."""
    from starlette.websockets import WebSocketDisconnect as WSD
    client, hs, _ = env
    secret = _pair(client, hs)
    import tmux_runner
    monkeypatch.setattr(tmux_runner, "has_session", lambda name: False)
    headers = _sign_headers(hs, "laptop", secret, "GET", "/api/peer/ws/ghost")
    with pytest.raises(WSD) as e:
        with client.websocket_connect("/api/peer/ws/ghost", headers=headers) as ws:
            ws.receive_bytes()
    assert e.value.code == 4404


def test_peer_proxy_ws_requires_known_host(env):
    """A 쪽 프록시: 등록되지 않은 호스트로는 아예 못 나간다."""
    from starlette.websockets import WebSocketDisconnect as WSD
    client, hs, _ = env
    with pytest.raises(WSD) as e:
        with client.websocket_connect("/ws/remote/nope/dev") as ws:
            ws.receive_bytes()
    # 인증(4001) 또는 호스트 없음(4004) — 둘 다 "연결되지 않는다"는 같은 계약이다.
    assert e.value.code in (4001, 4004)


# --- A2: 파일 전송 (본문 해시 서명 + control 등급) ----------------------------------


def _file_headers(hs, peer_id, secret, data, name="a.txt", session="", src_id="f1"):
    import hashlib
    digest = hashlib.sha256(data).hexdigest()
    ts = int(time.time())
    nonce = f"n-{time.time_ns()}"
    h = {
        "X-Peer-Id": peer_id,
        "X-Peer-Ts": str(ts),
        "X-Peer-Nonce": nonce,
        "X-Peer-Sig": hs.sign_request(secret, "POST", "/api/peer/file", ts, nonce, digest),
        "X-Peer-Body": digest,
        "X-Peer-File-Name": name,
        "X-Peer-File-Id": src_id,
        "Content-Type": "application/octet-stream",
    }
    if session:
        h["X-Peer-File-Session"] = session
    return h


def test_file_transfer_requires_control_level(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    data = b"hello"
    r = client.post("/api/peer/file", content=data, headers=_file_headers(hs, "laptop", secret, data))
    assert r.status_code == 403
    assert r.json()["error"] == "level_required"


def test_file_transfer_stores_bytes_and_dedupes_by_origin(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)
    import file_store
    data = b"payload-bytes"

    r = client.post("/api/peer/file", content=data,
                    headers=_file_headers(hs, "laptop", secret, data, name="build.tar.gz"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reused"] is False
    stored = file_store.real_path_for(body["id"])
    assert stored.read_bytes() == data

    # 같은 파일을 다시 보내면 디스크에 두 벌 쌓이지 않는다.
    r2 = client.post("/api/peer/file", content=data,
                     headers=_file_headers(hs, "laptop", secret, data, name="build.tar.gz"))
    assert r2.json()["reused"] is True
    assert r2.json()["id"] == body["id"]
    assert len(file_store.list_items()) == 1


def test_file_transfer_rejects_a_tampered_body(env):
    """서명은 그대로 두고 바이트만 바꾼 요청 — 본문 해시를 서명에 넣은 이유다."""
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)
    data = b"original"
    headers = _file_headers(hs, "laptop", secret, data)
    r = client.post("/api/peer/file", content=b"tampered", headers=headers)
    assert r.status_code == 401
    assert "본문" in r.json()["reason"]


def test_file_transfer_over_size_cap_is_413(env, monkeypatch):
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)
    import file_store
    monkeypatch.setattr(file_store, "MAX_UPLOAD_BYTES", 4)
    data = b"too-long-body"
    r = client.post("/api/peer/file", content=data, headers=_file_headers(hs, "laptop", secret, data))
    assert r.status_code == 413


def test_file_transfer_types_the_path_into_the_named_session(env, monkeypatch):
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)
    import tmux_target
    typed = []
    monkeypatch.setattr(tmux_target, "session_pane", lambda n: "%2" if n == "dev" else None)
    # Enter 없이 타이핑돼야 한다(로컬 파일 삽입과 같은 계약).
    monkeypatch.setattr(tmux_target, "type_to_tmux", lambda p, t: typed.append((p, t)) or True)
    monkeypatch.setattr(tmux_target, "send_to_tmux",
                        lambda p, t: pytest.fail("파일 삽입은 Enter를 누르면 안 된다"))

    data = b"x"
    r = client.post("/api/peer/file", content=data,
                    headers=_file_headers(hs, "laptop", secret, data, session="dev"))
    assert r.json()["typed"] is True
    assert typed and typed[0][0] == "%2" and typed[0][1] == r.json()["path"]


# --- 2.1.3: 「연결된 화면」(view 목록 / control 끊기) · 스크롤백 검색 -----------------
#
# 2.1.2에서 이 둘은 원격에서 **꺼져 있었다**. `/api/tmux/clients`는 요청을 받은
# 맥의 tmux를 보므로 원격 탭에서 부르면 남의 세션 목록을 보여주고, 무엇보다
# 「이 화면만 남기기」가 자기 자신을 끊었다. 그래서 여기 테스트의 무게는
# **"자기 화면을 어떻게 판정하는가"**에 있다.


def _fake_client_rows(monkeypatch, rows):
    import routes.tmux as tmux_mod
    monkeypatch.setattr(tmux_mod, "_client_rows", lambda s: [dict(r) for r in rows])


def _fake_tty(monkeypatch, mapping):
    """web/PTY 세션 id → tty. 원격 화면은 `peer-<peer id>-<screen>`로 들어온다."""
    import routes.tmux as tmux_mod
    monkeypatch.setattr(tmux_mod, "_tty_of_web_session", lambda sid: mapping.get(sid))


ROWS = [
    {"tty": "/dev/ttys001", "name": "iterm", "width": 80, "height": 24, "activity": "0"},
    {"tty": "/dev/ttys002", "name": "remote", "width": 100, "height": 30, "activity": "0"},
]


def test_clients_list_is_view_level_and_marks_my_screen(env, monkeypatch):
    client, hs, _ = env
    secret = _pair(client, hs)          # 기본 등급은 view
    _fake_client_rows(monkeypatch, ROWS)
    _fake_tty(monkeypatch, {"peer-laptop-abc": "/dev/ttys002"})
    path = "/api/peer/clients"
    r = client.get(f"{path}?session=dev&screen=abc",
                   headers=_sign_headers(hs, "laptop", secret, "GET", path))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["me_tty"] == "/dev/ttys002"
    assert [c["is_me"] for c in body["clients"]] == [False, True]
    assert body["clients"][1]["label"] == "이 화면"


def test_clients_screen_token_cannot_claim_another_peers_screen(env, monkeypatch):
    """접두사가 강제되므로 남의 화면 id를 주장해도 자기 것이 되지 않는다 —
    로컬 경로의 "tty를 클라이언트가 고르게 하지 않는다"와 같은 성질."""
    client, hs, _ = env
    secret = _pair(client, hs)
    _fake_client_rows(monkeypatch, ROWS)
    _fake_tty(monkeypatch, {"peer-other-abc": "/dev/ttys002"})
    path = "/api/peer/clients"
    r = client.get(f"{path}?session=dev&screen=abc",
                   headers=_sign_headers(hs, "laptop", secret, "GET", path))
    assert r.json()["me_tty"] is None
    assert all(c["is_me"] is False for c in r.json()["clients"])


def test_clients_detach_requires_control_level(env, monkeypatch):
    client, hs, _ = env
    secret = _pair(client, hs)
    _fake_client_rows(monkeypatch, ROWS)
    path = "/api/peer/clients/detach"
    r = client.post(path, json={"tty": "/dev/ttys001", "screen": "abc"},
                    headers=_sign_headers(hs, "laptop", secret, "POST", path))
    assert r.status_code == 403
    assert "allow-control" in r.json()["reason"]


def test_clients_detach_refuses_my_own_screen(env, monkeypatch):
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)
    _fake_tty(monkeypatch, {"peer-laptop-abc": "/dev/ttys002"})
    import tmux_runner
    monkeypatch.setattr(tmux_runner, "run",
                        lambda *a, **k: pytest.fail("자기 화면을 끊으면 안 된다"))
    path = "/api/peer/clients/detach"
    r = client.post(path, json={"tty": "/dev/ttys002", "screen": "abc"},
                    headers=_sign_headers(hs, "laptop", secret, "POST", path))
    assert r.status_code == 400
    assert r.json()["error"] == "cannot detach self"


def test_clients_solo_detaches_everyone_but_me(env, monkeypatch):
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)
    _fake_client_rows(monkeypatch, ROWS)
    _fake_tty(monkeypatch, {"peer-laptop-abc": "/dev/ttys002"})
    calls = []
    import tmux_runner
    monkeypatch.setattr(tmux_runner, "run", lambda args, timeout=2.0: calls.append(args) or (0, b"", b""))
    path = "/api/peer/clients/solo"
    r = client.post(path, json={"session": "dev", "screen": "abc"},
                    headers=_sign_headers(hs, "laptop", secret, "POST", path))
    assert r.json() == {"ok": True, "kept": "/dev/ttys002", "detached": ["/dev/ttys001"]}
    assert ["detach-client", "-t", "/dev/ttys001"] in calls


def test_clients_solo_detaches_nothing_when_my_screen_is_unknown(env, monkeypatch):
    """전부 끊고 나면 되돌릴 방법이 없다 — 로컬 경로와 같은 규칙."""
    client, hs, _ = env
    secret = _pair(client, hs)
    hs.set_grant_level("laptop", hs.LEVEL_CONTROL)
    _fake_client_rows(monkeypatch, ROWS)
    _fake_tty(monkeypatch, {})
    import tmux_runner
    monkeypatch.setattr(tmux_runner, "run", lambda *a, **k: pytest.fail("아무것도 끊으면 안 된다"))
    path = "/api/peer/clients/solo"
    r = client.post(path, json={"session": "dev", "screen": "abc"},
                    headers=_sign_headers(hs, "laptop", secret, "POST", path))
    assert r.status_code == 400
    assert r.json()["error"] == "unknown client"


def test_clients_rejects_option_like_session_name(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    path = "/api/peer/clients"
    r = client.get(f"{path}?session=-t%20other",
                   headers=_sign_headers(hs, "laptop", secret, "GET", path))
    assert r.status_code == 400


def test_search_is_view_level_and_strips_local_session_ids(env, monkeypatch):
    """session_id는 이 호스트 안에서만 뜻이 있는 값이다 — 그대로 넘기면 상대가
    그걸로 자기 로컬 세션을 열려다 엉뚱한 세션을 연다."""
    client, hs, _ = env
    secret = _pair(client, hs)

    async def fake_search(q, sessions="all"):
        return {"results": [{"session_id": "uuid-1", "session_name": "dev", "source": "log",
                             "line_no": 3, "line": f"hit {q}",
                             "context_before": [], "context_after": []}],
                "truncated": False}

    import routes.search as search_mod
    monkeypatch.setattr(search_mod, "search_scrollback", fake_search)
    path = "/api/peer/search"
    r = client.get(f"{path}?q=boom", headers=_sign_headers(hs, "laptop", secret, "GET", path))
    assert r.status_code == 200, r.text
    row = r.json()["results"][0]
    assert "session_id" not in row
    assert row["session_name"] == "dev" and row["line"] == "hit boom"


def test_search_with_empty_query_returns_nothing(env):
    client, hs, _ = env
    secret = _pair(client, hs)
    path = "/api/peer/search"
    r = client.get(f"{path}?q=%20", headers=_sign_headers(hs, "laptop", secret, "GET", path))
    assert r.json() == {"results": [], "truncated": False}
