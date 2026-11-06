"""호스트 전환 중계 — `relay_policy` · `/api/peer/relay/*`(B) · `/api/hosts/{id}/relay/*`(A).

B 쪽은 인증이 **켜진** 서버로 본다: 중계가 TokenAuthMiddleware를 우회하는 만큼
peer 서명이 그 자리를 대신하는지가 요점이다. A 쪽은 A와 B를 **같은 앱**으로 두고
(자기 자신을 peer로 등록) httpx를 ASGI로 붙여 네트워크 없이 끝까지 돌린다.
"""

from __future__ import annotations

import hashlib
import importlib
import time

import httpx
import pytest
from starlette.testclient import TestClient

import relay_policy


# --- 허용 목록 --------------------------------------------------------------------


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/tmux/sessions"),
    ("POST", "/api/tmux/create"),
    ("GET", "/api/workspace"),
    ("PUT", "/api/workspace"),
    ("GET", "/api/ports"),
    ("GET", "/api/files"),
    ("GET", "/api/files/abc/download"),
    ("POST", "/api/upload"),
    ("GET", "/api/fs/raw"),
    ("GET", "/api/git/status"),
    ("POST", "/api/queue"),
    ("GET", "/api/capabilities"),
])
def test_policy_allows_host_scoped_apis(method, path):
    assert relay_policy.http_allowed(method, path)


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/auth"),
    ("GET", "/api/auth/status"),
    ("GET", "/api/devices"),
    ("GET", "/api/hosts"),
    ("POST", "/api/hosts/x/relay/api/tmux/sessions"),   # 전이 중계
    ("GET", "/api/peer/ping"),
    ("POST", "/api/files/abc/share"),                    # 공유 링크(승격)
    ("POST", "/api/files/abc/send"),                     # 다른 peer로(전이)
    ("POST", "/api/ports/3000/expose"),
    ("POST", "/api/git/accounts"),
    ("PUT", "/api/git/binding"),
    ("POST", "/api/mcp/toggle"),
    ("POST", "/api/push/subscribe"),
    ("POST", "/api/clipboard/push"),
    ("POST", "/api/agent/event"),
    ("POST", "/api/tmux/open-on-mac"),
    ("POST", "/api/capabilities"),                       # 읽기 전용은 GET만
    ("GET", "/api/files/../auth"),
    ("GET", "/api//files"),
    ("GET", "/api/unknown"),
])
def test_policy_refuses_everything_else(method, path):
    assert not relay_policy.http_allowed(method, path)


def test_policy_ws():
    assert relay_policy.ws_allowed("/ws/abc-123")
    assert relay_policy.ws_allowed("/ws-workspace")
    assert relay_policy.ws_allowed("/ws-agent")
    assert relay_policy.ws_allowed("/ws-fs-watch")
    assert not relay_policy.ws_allowed("/ws/remote/m5/dev")
    assert not relay_policy.ws_allowed("/ws/hosts/m5/relay/ws/x")
    assert not relay_policy.ws_allowed("/ws-notify")
    assert not relay_policy.ws_allowed("/ws/..")


def test_strip_token_removes_only_the_token():
    assert relay_policy.strip_token("token=SECRET&path=%2Fa") == "path=%2Fa"
    assert relay_policy.strip_token("token=SECRET") == ""
    assert relay_policy.strip_token("") == ""


# --- B: /api/peer/relay -------------------------------------------------------------


@pytest.fixture
def b_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_WORKSPACE_PATH", str(tmp_path / "workspace.json"))
    import auth as _auth
    monkeypatch.setattr(_auth, "STATE_DIR", tmp_path)
    monkeypatch.setattr(_auth, "DEVICES_PATH", tmp_path / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", tmp_path / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", tmp_path / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "relay-test-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password("pw"))
    import host_store
    importlib.reload(host_store)
    import main
    importlib.reload(main)
    with TestClient(main.app) as c:
        grant, secret = host_store.add_grant("laptop", "노트북")
        host_store.set_grant_level("laptop", host_store.LEVEL_CONTROL)
        yield c, host_store, secret


def _signed(hs, secret, method, path_q, body=b"", peer_id="laptop", body_hash=None):
    ts = int(time.time())
    nonce = f"n-{time.time_ns()}"
    digest = hashlib.sha256(body).hexdigest() if body_hash is None else body_hash
    return {
        "X-Peer-Id": peer_id, "X-Peer-Ts": str(ts), "X-Peer-Nonce": nonce,
        "X-Peer-Sig": hs.sign_request(secret, method, path_q, ts, nonce, digest),
        "X-Peer-Body": digest,
    }


def test_relay_runs_the_real_api_behind_a_valid_signature(b_env):
    client, hs, secret = b_env
    # 일반 경로로는 막혀 있는 API가
    assert client.get("/api/workspace").status_code == 401
    # 중계로는 열린다
    path = "/api/peer/relay/api/workspace"
    r = client.get(path, headers=_signed(hs, secret, "GET", path))
    assert r.status_code == 200, r.text
    assert r.headers.get("x-peer-relay") == "1"


def test_relay_without_signature_is_refused(b_env):
    client, _, _ = b_env
    r = client.get("/api/peer/relay/api/workspace")
    assert r.status_code == 401
    assert r.headers.get("x-peer-relay") == "1"


def test_relay_signature_covers_the_query(b_env):
    """쿼리를 서명 밖에 두면 서명 하나로 아무 파일이나 읽는다(`/api/fs/raw?path=`)."""
    client, hs, secret = b_env
    path = "/api/peer/relay/api/fs/raw"
    headers = _signed(hs, secret, "GET", path + "?path=%2Fa")
    r = client.get(path + "?path=%2Fetc%2Fpasswd", headers=headers)
    assert r.status_code == 401


def test_relay_requires_body_hash(b_env):
    client, hs, secret = b_env
    path = "/api/peer/relay/api/queue"
    headers = _signed(hs, secret, "POST", path, b'{"a":1}')
    r = client.post(path, content=b'{"a":2}', headers=headers)   # 본문 바꿔치기
    assert r.status_code == 401
    headers = _signed(hs, secret, "GET", "/api/peer/relay/api/workspace")
    headers.pop("X-Peer-Body")
    r = client.get("/api/peer/relay/api/workspace", headers=headers)
    assert r.status_code == 401


def test_relay_requires_control_level(b_env):
    client, hs, secret = b_env
    hs.set_grant_level("laptop", hs.LEVEL_VIEW)
    path = "/api/peer/relay/api/workspace"
    r = client.get(path, headers=_signed(hs, secret, "GET", path))
    assert r.status_code == 403
    assert r.json()["error"] == "level_required"


@pytest.mark.parametrize("target", [
    "/api/auth/status", "/api/devices", "/api/hosts", "/api/peer/ping",
])
def test_relay_refuses_paths_outside_the_allowlist(b_env, target):
    client, hs, secret = b_env
    path = "/api/peer/relay" + target
    r = client.get(path, headers=_signed(hs, secret, "GET", path))
    assert r.status_code == 403
    assert r.json()["error"] == "relay_forbidden"


def test_relay_mutations_are_audited(b_env):
    client, hs, secret = b_env
    path = "/api/peer/relay/api/workspace"
    body = b'{"tabs":[]}'
    headers = {**_signed(hs, secret, "PUT", path, body), "Content-Type": "application/json"}
    assert client.put(path, content=body, headers=headers).status_code == 200
    log = hs.read_audit("laptop", 20)
    assert any(e["action"] == "relay" and e["detail"] == "PUT /api/workspace" for e in log)


def test_relay_ws_rejects_unsigned_and_forbidden(b_env):
    client, hs, secret = b_env
    with client.websocket_connect("/api/peer/relay-ws/ws-workspace") as ws:
        msg = ws.receive()
        assert msg["type"] == "websocket.close" and msg["code"] == 4401
    path = "/api/peer/relay-ws/ws-notify"
    ts = int(time.time())
    nonce = f"n-{time.time_ns()}"
    headers = {"X-Peer-Id": "laptop", "X-Peer-Ts": str(ts), "X-Peer-Nonce": nonce,
               "X-Peer-Sig": hs.sign_request(secret, "GET", path, ts, nonce)}
    with client.websocket_connect(path, headers=headers) as ws:
        msg = ws.receive()
        assert msg["type"] == "websocket.close" and msg["code"] == 4403


# --- A: /api/hosts/{id}/relay (A와 B가 같은 앱) --------------------------------------


@pytest.fixture
def a_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VT_WORKSPACE_PATH", str(tmp_path / "workspace.json"))
    import host_store
    importlib.reload(host_store)
    import main
    importlib.reload(main)
    import routes.host_relay as hr
    me = host_store.get_self()
    _, secret = host_store.add_grant(me["id"], "self")
    host_store.set_grant_level(me["id"], host_store.LEVEL_CONTROL)
    host_store.add_peer("m5", "http://testserver", secret, label="M5")
    monkeypatch.setattr(hr, "_client", httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://testserver"))
    with TestClient(main.app) as c:
        yield c, host_store


def test_host_relay_round_trip(a_env):
    client, _ = a_env
    r = client.put("/api/hosts/m5/relay/api/workspace", json={"marker": "m5-side"})
    assert r.status_code == 200, r.text
    r = client.get("/api/hosts/m5/relay/api/workspace")
    assert r.status_code == 200
    assert r.json().get("marker") == "m5-side"


def test_host_relay_unknown_host_is_404(a_env):
    client, _ = a_env
    assert client.get("/api/hosts/nope/relay/api/workspace").status_code == 404


def test_host_relay_refuses_forbidden_path_before_calling_out(a_env):
    client, _ = a_env
    r = client.get("/api/hosts/m5/relay/api/hosts")
    assert r.status_code == 403
    assert r.json()["error"] == "relay_forbidden"


def test_host_relay_reports_outdated_peer(a_env, monkeypatch):
    """상대에 중계 라우트가 없으면(구버전) `x-peer-relay` 없는 404가 온다 → 501."""
    client, _ = a_env
    import routes.host_relay as hr

    async def handler(request):
        return httpx.Response(404, json={"detail": "Not Found"})

    monkeypatch.setattr(hr, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = client.get("/api/hosts/m5/relay/api/workspace")
    assert r.status_code == 501
    assert r.json()["error"] == "peer_outdated"


def test_host_relay_never_forwards_token_or_cookie(a_env, monkeypatch):
    client, _ = a_env
    import routes.host_relay as hr
    seen = {}

    async def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={}, headers={"x-peer-relay": "1"})

    monkeypatch.setattr(hr, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    client.get("/api/hosts/m5/relay/api/fs/tree?token=MYSECRET&path=x",
               headers={"Cookie": "vt_session=abc", "Authorization": "Bearer MYSECRET"})
    assert "MYSECRET" not in seen["url"]
    assert "path=x" in seen["url"]
    assert "cookie" not in seen["headers"]
    assert "MYSECRET" not in str(seen["headers"])


@pytest.mark.parametrize("enforced", [False, True])
def test_native_transcript_relay_reads_verified_child(b_env, tmp_path, monkeypatch, enforced):
    """Exercise signed relay + actual inner transcript route, not a dispatch stub."""
    import json
    import agent_status as A
    c, hs, secret = b_env
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "peer" if enforced else "")
    home = tmp_path / "cli"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    parent = home / "projects" / "sample" / "relay-parent.jsonl"
    parent.parent.mkdir(parents=True)
    parent.write_text("")
    child = parent.parent / "relay-parent" / "subagents" / "agent-child.jsonl"
    child.parent.mkdir(parents=True)
    child.write_text(json.dumps({"type": "assistant", "message": {"content": "relayed record"}}) + "\n")
    A.on_event("session_start", {"session_id": "relay-parent", "source": "startup"}, session="native-relay", agent="claude")
    A.on_event("subagent_start", {"session_id": "relay-parent", "agent_id": "child", "transcript_path": str(parent)}, session="native-relay", agent="claude")
    path = "/api/peer/relay/api/agents/native-relay/subagents/child/transcript"
    try:
        response = c.get(path, headers=_signed(hs, secret, "GET", path))
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["items"][0]["text"] == "relayed record"
        hs.set_grant_level("laptop", hs.LEVEL_VIEW)
        response = c.get(path, headers=_signed(hs, secret, "GET", path))
        assert response.status_code == 403
    finally:
        A._retire("relay-parent")


# --- 보안 헤더가 중계를 지나도 남는다 ---------------------------------------------------
# 라우트가 붙인 응답별 정책(파일 미리보기의 sandbox CSP 등)이 중계에서 빠지면, 바깥 서버의
# 기본 헤더(frame-ancestors 'none'만)로 바뀌어 같은 바이트가 로컬보다 약한 정책으로 열린다.

_SANDBOX_CSP = "sandbox; default-src 'none'"


def _install_header_probe(monkeypatch, app):
    """허용 목록 안 경로(`/api/fs/raw`)를 응답 헤더 탐침으로 바꿔 끼운다(라우터 맨 앞)."""
    from starlette.responses import Response
    from starlette.routing import Route

    seen = {}

    async def probe(request):
        seen["if-range"] = request.headers.get("if-range")
        seen["range"] = request.headers.get("range")
        resp = Response(b"bytes", media_type="image/png")
        # 같은 이름의 CSP 두 개 — 둘 다 강제되는 정책 목록이다.
        resp.raw_headers.append((b"content-security-policy", _SANDBOX_CSP.encode()))
        resp.raw_headers.append((b"content-security-policy", b"img-src 'self'"))
        resp.raw_headers.append((b"x-content-type-options", b"nosniff"))
        return resp

    monkeypatch.setattr(app.router, "routes", [Route("/api/fs/raw", probe), *app.router.routes])
    return seen


def test_relay_passes_route_security_headers_and_if_range(b_env, monkeypatch):
    import main
    client, hs, secret = b_env
    seen = _install_header_probe(monkeypatch, main.app)
    path = "/api/peer/relay/api/fs/raw"
    r = client.get(path + "?path=%2Fx.png", headers={
        **_signed(hs, secret, "GET", path + "?path=%2Fx.png"),
        "Range": "bytes=0-1", "If-Range": '"rev-1"',
    })
    assert r.status_code == 200, r.text
    csp = r.headers.get("content-security-policy", "")
    assert _SANDBOX_CSP in csp and "img-src 'self'" in csp
    assert "frame-ancestors" not in csp           # 바깥 기본값으로 바뀌지 않았다
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert seen == {"if-range": '"rev-1"', "range": "bytes=0-1"}


def test_host_relay_passes_security_headers_and_if_range(a_env, monkeypatch):
    client, _ = a_env
    import routes.host_relay as hr
    seen = {}

    async def handler(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, content=b"bytes", headers={
            "x-peer-relay": "1", "content-type": "image/png",
            "content-security-policy": _SANDBOX_CSP, "x-content-type-options": "nosniff",
        })

    monkeypatch.setattr(hr, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = client.get("/api/hosts/m5/relay/api/fs/raw?path=x.png",
                   headers={"Range": "bytes=0-1", "If-Range": '"rev-1"'})
    assert r.status_code == 200
    assert r.headers.get("content-security-policy") == _SANDBOX_CSP
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert seen["headers"].get("if-range") == '"rev-1"'
    assert seen["headers"].get("range") == "bytes=0-1"


def test_host_relay_round_trip_keeps_route_csp(a_env, monkeypatch):
    """A → B 끝까지(같은 앱) — B 라우트의 CSP 가 A 응답에 그대로 남는다."""
    import main
    client, _ = a_env
    _install_header_probe(monkeypatch, main.app)
    r = client.get("/api/hosts/m5/relay/api/fs/raw?path=x.png")
    assert r.status_code == 200, r.text
    csp = r.headers.get("content-security-policy", "")
    assert _SANDBOX_CSP in csp and "frame-ancestors" not in csp


# --- 파일 읽기 2판이 중계를 지나도 같은 계약이다(D7: 읽기 기능은 원격에서도) ---------------------


def test_host_relay_fs_file_keeps_etag_304_range_and_409(a_env, tmp_path, monkeypatch):
    """A → B(같은 앱) 끝까지: ETag·`If-None-Match` → 304, 구간 필드, `expected_rev` 불일치 → 409."""
    client, _ = a_env
    root = (tmp_path / "browse").resolve()
    root.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    (root / "a.txt").write_bytes(b"one\ntwo\n")
    url = "/api/hosts/m5/relay/api/fs/file"
    params = {"path": str(root / "a.txt"), "offset": "0", "length": "5"}
    r = client.get(url, params=params)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["content"] == "one\n" and d["next_offset"] == 4 and d["rev"]
    etag = r.headers["etag"]
    assert r.headers["cache-control"] == "private, no-cache"
    r = client.get(url, params=params, headers={"If-None-Match": etag})
    assert r.status_code == 304 and r.content == b""
    assert r.headers["etag"] == etag
    r = client.get(url, params={"path": str(root / "a.txt"), "offset": "4", "expected_rev": "0:0:0:0"})
    assert r.status_code == 409
    assert r.json()["error"] == "changed" and r.json()["rev"] == d["rev"]
