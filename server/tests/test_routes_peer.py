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
