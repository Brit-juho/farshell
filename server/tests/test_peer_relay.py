"""호스트 전환 중계 — `relay_policy` · `/api/peer/relay/*`(B) · `/api/hosts/{id}/relay/*`(A).

B 쪽은 인증이 **켜진** 서버로 본다: 중계가 TokenAuthMiddleware를 우회하는 만큼
peer 서명이 그 자리를 대신하는지가 요점이다. A 쪽은 A와 B를 **같은 앱**으로 두고
(자기 자신을 peer로 등록) httpx를 ASGI로 붙여 네트워크 없이 끝까지 돌린다.
"""

from __future__ import annotations

import hashlib
import json
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
    ("GET", "/api/fs/grep"),
    ("GET", "/api/fs/grep/progress"),                   # 내용 검색 진행(같은 sid의 것만 보인다)
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
    ("POST", "/api/fs/grep"),
    ("POST", "/api/fs/grep/progress"),
    ("GET", "/api/files/../auth"),
    ("GET", "/api//files"),
    ("GET", "/api/unknown"),
    ("GET", "/api/fs/download"),                         # 경로 기반 다운로드는 로컬 전용(D7)
    ("PUT", "/api/fs/file"),                             # 쓰기는 로컬 전용(D7) — 읽기 GET만 중계
    ("POST", "/api/fs/new"),
    ("POST", "/api/fs/rename"),
    ("POST", "/api/fs/delete"),
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
    "/api/fs/download",                                  # B도 스스로 거절한다(경로 기반 다운로드는 로컬 전용, D7)
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


# --- 응답 헤더는 A가 짓는다: B를 믿지 않는다 ----------------------------------------------
# B의 바이트는 A의 출처에서 열린다. 악의적이거나 털린 B가 A의 nosniff·frame-ancestors 를 지우거나,
# CSP 보고서를 밖으로 보내게 하거나, text/html 로 A의 출처에서 스크립트를 돌리면 안 된다.

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _mock_b(monkeypatch, status=200, content=b"{}", headers=None):
    """A의 상대(B)를 마음대로 응답하는 가짜로 바꾼다. 받은 요청 헤더를 돌려준다."""
    import routes.host_relay as hr
    seen = {}

    async def handler(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(status, content=content, headers={"x-peer-relay": "1", **(headers or {})})

    monkeypatch.setattr(hr, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return seen


def _assert_a_policy(r):
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("content-security-policy") == relay_policy.RELAY_CSP
    assert r.headers.get("x-frame-options") == "DENY"


def test_host_relay_overrides_b_nosniff(a_env, monkeypatch):
    client, _ = a_env
    _mock_b(monkeypatch, headers={"content-type": "application/json", "x-content-type-options": "x"})
    r = client.get("/api/hosts/m5/relay/api/workspace")
    assert r.status_code == 200
    assert r.headers.get_list("x-content-type-options") == ["nosniff"]


def test_host_relay_drops_b_csp_frame_ancestors_and_reporting(a_env, monkeypatch):
    client, _ = a_env
    _mock_b(monkeypatch, headers={
        "content-type": "application/json",
        "content-security-policy": "frame-ancestors *; report-uri https://evil.example/r; report-to x",
        "report-to": '{"group":"x","endpoints":[{"url":"https://evil.example/r"}]}',
        "reporting-endpoints": 'x="https://evil.example/r"',
    })
    r = client.get("/api/hosts/m5/relay/api/workspace")
    assert r.status_code == 200
    assert r.headers.get_list("content-security-policy") == [relay_policy.RELAY_CSP]
    assert "evil" not in str(r.headers).lower()
    _assert_a_policy(r)


@pytest.mark.parametrize("target,ctype", [
    ("/api/workspace", "text/html"),
    ("/api/workspace", "text/html; charset=utf-8"),
    ("/api/fs/tree", "application/xhtml+xml"),
    ("/api/fs/raw", "text/xml"),
    ("/api/fs/raw", "text/html"),
    ("/api/fs/raw", "application/javascript"),
    ("/api/agents", "text/javascript"),
    ("/api/fs/file", "application/pdf"),
    ("/api/workspace", "text/html, application/json"),
])
def test_host_relay_neutralizes_active_content_types(a_env, monkeypatch, target, ctype):
    client, _ = a_env
    _mock_b(monkeypatch, content=b"<script>alert(1)</script>",
            headers={"content-type": ctype, "content-disposition": "inline"})
    r = client.get("/api/hosts/m5/relay" + target + "?path=x.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    assert "content-disposition" not in r.headers
    _assert_a_policy(r)


def test_host_relay_neutralizes_missing_content_type_with_body(a_env, monkeypatch):
    client, _ = a_env
    _mock_b(monkeypatch, content=b"<html><script>alert(1)</script>")
    r = client.get("/api/hosts/m5/relay/api/workspace")
    assert r.headers["content-type"] == "text/plain; charset=utf-8"
    _assert_a_policy(r)


@pytest.mark.parametrize("name,value", [
    ("set-cookie", "vt_session=attacker; Path=/"),
    ("location", "https://evil.example/"),
    ("refresh", "0; url=https://evil.example/"),
    ("link", "<https://evil.example/x.js>; rel=preload; as=script"),
    ("access-control-allow-origin", "*"),
    ("access-control-allow-credentials", "true"),
    ("clear-site-data", '"cookies", "storage"'),
    ("service-worker-allowed", "/"),
    ("content-encoding", "identity"),
    ("strict-transport-security", "max-age=0"),
    ("x-frame-options", "ALLOWALL"),
    ("cross-origin-resource-policy", "cross-origin"),
])
def test_host_relay_drops_dangerous_headers(a_env, monkeypatch, name, value):
    client, _ = a_env
    _mock_b(monkeypatch, status=302 if name == "location" else 200,
            headers={"content-type": "application/json", name: value})
    r = client.get("/api/hosts/m5/relay/api/workspace")
    if name == "x-frame-options":
        assert r.headers.get_list(name) == ["DENY"]
    else:
        assert name not in r.headers
    _assert_a_policy(r)


def test_host_relay_cache_control_stays_private(a_env, monkeypatch):
    client, _ = a_env
    _mock_b(monkeypatch, headers={"content-type": "application/json", "cache-control": "public, max-age=31536000"})
    assert client.get("/api/hosts/m5/relay/api/workspace").headers["cache-control"] == "private, no-cache"
    _mock_b(monkeypatch, headers={"content-type": "application/json", "cache-control": "private, no-store"})
    assert client.get("/api/hosts/m5/relay/api/workspace").headers["cache-control"] == "no-store"


def test_host_relay_download_forces_attachment(a_env, monkeypatch):
    """첨부 내려받기는 무엇이든 받되 attachment 를 강제하고, 실행 타입은 octet-stream 으로."""
    client, _ = a_env
    _mock_b(monkeypatch, content=b"<script>1</script>",
            headers={"content-type": "text/html", "content-disposition": "inline"})
    r = client.get("/api/hosts/m5/relay/api/files/abc/download")
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == "attachment"
    _assert_a_policy(r)


def test_host_relay_keeps_range_and_validators(a_env, monkeypatch):
    client, _ = a_env
    seen = _mock_b(monkeypatch, status=206, content=b"by", headers={
        "content-type": "image/png", "content-range": "bytes 0-1/5", "accept-ranges": "bytes",
        "etag": '"rev-1"', "last-modified": "Sat, 07 Nov 2026 00:00:00 GMT",
    })
    r = client.get("/api/hosts/m5/relay/api/fs/raw?path=x.png",
                   headers={"Range": "bytes=0-1", "If-Range": '"rev-1"'})
    assert r.status_code == 206
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-range"] == "bytes 0-1/5" and r.headers["etag"] == '"rev-1"'
    assert r.headers["accept-ranges"] == "bytes" and r.headers["last-modified"]
    assert seen["headers"].get("if-range") == '"rev-1"'
    assert seen["headers"].get("range") == "bytes=0-1"


def test_relay_b_no_longer_sends_security_headers(b_env, monkeypatch):
    """B(받는 쪽)는 CSP·nosniff 를 A에 넘기지 않는다 — A가 쓰지 않는 값이다. Range/If-Range 는 넘긴다."""
    import main
    from starlette.responses import Response
    from starlette.routing import Route
    client, hs, secret = b_env
    seen = {}

    async def probe(request):
        seen["if-range"] = request.headers.get("if-range")
        resp = Response(b"bytes", media_type="image/png")
        resp.raw_headers.append((b"content-security-policy", b"frame-ancestors *"))
        resp.raw_headers.append((b"set-cookie", b"a=b"))
        return resp

    monkeypatch.setattr(main.app.router, "routes", [Route("/api/fs/raw", probe), *main.app.router.routes])
    path = "/api/peer/relay/api/fs/raw"
    r = client.get(path + "?path=%2Fx.png", headers={
        **_signed(hs, secret, "GET", path + "?path=%2Fx.png"), "If-Range": '"rev-1"'})
    assert r.status_code == 200, r.text
    assert "frame-ancestors *" not in r.headers.get("content-security-policy", "")
    assert "set-cookie" not in r.headers
    assert seen["if-range"] == '"rev-1"'


# --- 정상 기능은 그대로: A → B(같은 앱) 끝까지 ------------------------------------------------


def test_host_relay_json_end_to_end_keeps_json(a_env):
    client, _ = a_env
    r = client.get("/api/hosts/m5/relay/api/workspace")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    assert isinstance(r.json(), dict)
    _assert_a_policy(r)


def test_host_relay_fs_raw_image_end_to_end(a_env, tmp_path, monkeypatch):
    client, _ = a_env
    root = (tmp_path / "img").resolve()
    root.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    (root / "a.png").write_bytes(_PNG)
    r = client.get("/api/hosts/m5/relay/api/fs/raw", params={"path": str(root / "a.png")})
    assert r.status_code == 200, r.text
    assert r.content == _PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"] == "inline"
    assert r.headers["cache-control"] == "no-store"
    _assert_a_policy(r)
    # SVG(결정 D8): 그림으로는 지나가되 A의 sandbox CSP가 붙는다 — A의 출처에서 직접 열어도 스크립트가 돌지 않는다.
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    (root / "b.svg").write_bytes(svg)
    r = client.get("/api/hosts/m5/relay/api/fs/raw", params={"path": str(root / "b.svg")})
    assert r.status_code == 200 and r.content == svg
    assert r.headers["content-type"] == "image/svg+xml"
    assert "sandbox" in r.headers["content-security-policy"]
    _assert_a_policy(r)
    # 이름만 .svg인 HTML은 B 자신이 거절한다(JSON 오류) — 그 오류도 JSON 그대로 온다.
    (root / "c.svg").write_bytes(b"just text, not svg")
    r = client.get("/api/hosts/m5/relay/api/fs/raw", params={"path": str(root / "c.svg")})
    assert r.status_code == 403 and r.headers["content-type"] == "application/json"


def test_host_relay_b_svg_cannot_escape_the_sandbox(a_env, monkeypatch):
    """털린 B가 SVG에 스크립트를 넣고 자기 CSP를 풀어 보내도, A는 B의 CSP를 버리고 sandbox를 붙인다(계획 7.3·3.4)."""
    client, _ = a_env
    _mock_b(monkeypatch, content=b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
            headers={"content-type": "image/svg+xml", "content-security-policy": "script-src *",
                     "content-disposition": "attachment"})
    r = client.get("/api/hosts/m5/relay/api/fs/raw?path=x.svg")
    assert r.status_code == 200
    assert r.headers.get_list("content-security-policy") == [relay_policy.RELAY_CSP]
    assert r.headers["content-disposition"] == "inline"
    _assert_a_policy(r)


def test_host_relay_refuses_path_download_before_calling_out(a_env, monkeypatch):
    """경로 기반 다운로드는 이 서버 파일만(결정 D7) — A가 B로 보내기 전에 거절한다."""
    client, _ = a_env
    called = []

    async def handler(request):
        called.append(request.url)
        return httpx.Response(200, content=b"x")

    import routes.host_relay as hr
    monkeypatch.setattr(hr, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = client.get("/api/hosts/m5/relay/api/fs/download", params={"path": "/etc/hosts"})
    assert r.status_code == 403 and r.json()["error"] == "relay_forbidden"
    assert called == []


@pytest.mark.parametrize("method,target", [("PUT", "/api/fs/file"), ("POST", "/api/fs/new"),
                                           ("POST", "/api/fs/rename"), ("POST", "/api/fs/delete")])
def test_host_relay_refuses_file_writes_before_calling_out(a_env, monkeypatch, method, target):
    """파일 쓰기는 이 맥의 파일만(결정 D7) — 다른 호스트로는 보내지도 않는다."""
    client, _ = a_env
    called = []

    async def handler(request):
        called.append(request.url)
        return httpx.Response(200, json={"ok": True})

    import routes.host_relay as hr
    monkeypatch.setattr(hr, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r = client.request(method, "/api/hosts/m5/relay" + target, json={"path": "/tmp/x", "content": "x"})
    assert r.status_code == 403 and r.json()["error"] == "relay_forbidden"
    assert called == []


@pytest.mark.parametrize("method,target", [("PUT", "/api/fs/file"), ("POST", "/api/fs/delete")])
def test_peer_relay_refuses_file_writes_even_when_signed(b_env, method, target):
    """B도 스스로 거절한다 — 서명이 맞아도 쓰기 라우트는 중계 허용 목록에 없다."""
    client, hs, secret = b_env
    path = "/api/peer/relay" + target
    body = json.dumps({"path": "/tmp/x", "content": "x"}).encode()
    r = client.request(method, path, content=body, headers={**_signed(hs, secret, method, path, body),
                                                              "Content-Type": "application/json"})
    assert r.status_code == 403 and r.json()["error"] == "relay_forbidden"


@pytest.mark.parametrize("name,expect_type", [
    ("notes.txt", "text/plain"),
    ("page.html", "application/octet-stream"),
])
def test_host_relay_download_end_to_end(a_env, name, expect_type):
    client, _ = a_env
    up = client.post("/api/hosts/m5/relay/api/upload", files={"file": (name, b"<b>hi</b>")})
    assert up.status_code == 200, up.text
    r = client.get(f"/api/hosts/m5/relay/api/files/{up.json()['id']}/download")
    assert r.status_code == 200, r.text
    assert r.content == b"<b>hi</b>"
    assert r.headers["content-type"] == expect_type
    assert r.headers["content-disposition"].startswith("attachment")
    assert name in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    _assert_a_policy(r)


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


def test_host_relay_fs_grep_end_to_end(a_env, tmp_path, monkeypatch):
    """내용 검색도 읽기 기능이라 원격에서 된다(D7). 경계·거부 규칙은 B(상대)가 자기 것으로 판정한다."""
    client, _ = a_env
    root = (tmp_path / "grep-root").resolve()
    root.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    (root / "a.txt").write_text("one\nneedle here\n")
    (root / ".env").write_text("needle=secret\n")
    url = "/api/hosts/m5/relay/api/fs/grep"
    r = client.get(url, params={"q": "needle", "path": str(root), "sid": "tab1"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/json"
    d = r.json()
    assert [(x["rel"], x["line"], x["col"], x["path"]) for x in d["results"]] == [("a.txt", 2, 1, str(root / "a.txt"))]
    _assert_a_policy(r)
    r = client.get(url, params={"q": "needle", "path": "/etc"})
    assert r.status_code == 403 and r.json()["error"] == "denied"
    r = client.get(url, params={"q": "(", "mode": "regex", "path": str(root)})
    assert r.status_code == 400 and r.json()["error"] == "bad_regex"
