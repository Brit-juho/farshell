"""peer 프로토콜 v2 — `VT_AUTHZ_ENFORCE=peer` 를 켠 서버의 인증·관문 판정(라우트 × 호출자). 인가(grant)의 의미는 `test_peer_v2.py`(서버 둘).

켜기 전의 동작(전부 404)은 matrix.json 의 `peer_v2.*` 가 고정한다. 켠 뒤:
  - 서명이 없거나(사람 쿠키·기계 토큰·무인증) 틀린 호출자(peer_forged): 401 `peer_denied` — 쿠키·토큰은 peer 서명을 대신하지 못한다.
  - 서명이 맞는 peer(view·control·control+agent 등급은 v2 에서 의미가 없다 — 인가는 grant 다):
      * 대리 주체가 필요한 라우트는 obo 없이 400 `obo_required`, obo 가 있으면 관문을 지나 핸들러에 닿는다.
      * `authz/grants`·`revoke`·`rotate` 는 호스트 단위로 닿는다.
  - 본문 해시(`X-Peer-Body`)가 없으면 401.
"""

from __future__ import annotations

import hashlib
import json
import time

import pytest
from starlette.testclient import TestClient

import host_store
from authz_cases import V2_ROUTES, PEERS
from authz_harness import PEER_IDS

OBO_FREE = ("/api/peer/v2/authz/grants", "/api/peer/v2/revoke", "/api/peer/v2/rotate", "/api/peer/v2/rotate/confirm")


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "peer,deleg,pair")


def _client():
    import main
    return TestClient(main.app, raise_server_exceptions=False)


def _call(world, who: str, method: str, route: str, body, *, obo=None, with_hash=True, secret=None):
    path = route.replace("{request_id}", "gq-none").replace("{pair_id}", "pr-none")
    data = json.dumps(body).encode() if body is not None else b""
    world.set_mode("password")
    h = world.headers_for(who, method, path, data)
    if who in PEER_IDS:
        pid = PEER_IDS[who]
        sec = secret or ("x" * 43 if who == "peer_forged" else world.secrets[pid])
        ts, nonce = int(time.time()), f"n-{time.time_ns()}"
        digest = hashlib.sha256(data).hexdigest()
        h = {"X-Peer-Id": pid, "X-Peer-Ts": str(ts), "X-Peer-Nonce": nonce,
             "X-Peer-Sig": host_store.sign_request(sec, method, path, ts, nonce, digest, obo or "")}
        if with_hash:
            h["X-Peer-Body"] = digest
        if obo:
            h["X-Peer-Obo"] = obo
    if data:
        h["Content-Type"] = "application/json"
    r = _client().request(method, path, content=data or None, headers=h)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


ROUTE_PARAMS = [(m, r, b) for (m, r, b) in V2_ROUTES]


@pytest.mark.parametrize("who", ("none", "machine", "session"))
@pytest.mark.parametrize("method,route,body", ROUTE_PARAMS, ids=lambda x: str(x)[:40])
def test_cookie_token_and_anonymous_callers_are_not_peers(world, who, method, route, body):
    st, out = _call(world, who, method, route, body)
    assert st == 401 and out["error"] == "peer_denied", (st, out)


@pytest.mark.parametrize("method,route,body", ROUTE_PARAMS, ids=lambda x: str(x)[:40])
def test_a_wrong_secret_is_refused(world, method, route, body):
    st, out = _call(world, "peer_forged", method, route, body, obo="agent:pc/$2:1002")
    assert st == 401 and out["error"] == "peer_denied", (st, out)


@pytest.mark.parametrize("who", [p for p in PEERS if p != "peer_forged"])
@pytest.mark.parametrize("method,route,body", ROUTE_PARAMS, ids=lambda x: str(x)[:40])
def test_valid_peers_need_an_obo_where_the_route_is_about_an_agent(world, who, method, route, body):
    pid = PEER_IDS[who]
    st, out = _call(world, who, method, route, body)
    if route in OBO_FREE:
        assert st not in (400, 401), (st, out)
        return
    assert st == 400 and out["error"] == "obo_required", (st, out)
    st, out = _call(world, who, method, route, body, obo=f"agent:{pid}/$2:1002")
    assert st != 401 and not (st == 400 and out.get("error") == "obo_required"), (st, out)        # 관문을 지나 핸들러에 닿았다


def test_the_body_hash_is_mandatory_on_v2(world):
    st, out = _call(world, "peer_control", "GET", "/api/peer/v2/authz/grants", None, with_hash=False)
    assert st == 401 and "본문 해시" in out["reason"]


def test_an_obo_for_another_hosts_agent_is_refused(world):
    st, out = _call(world, "peer_control", "GET", "/api/peer/v2/session/shared", None, obo="agent:pv/$2:1002")
    assert st == 401


def test_grants_listing_is_scoped_to_the_callers_own_agents(world):
    st, out = _call(world, "peer_control", "GET", "/api/peer/v2/authz/grants", None)
    assert st == 200 and out["registered"] is True and out["grants"] == [] and out["obo"] is None


def test_rotate_hands_out_a_pending_secret_that_the_next_signed_request_promotes(world):
    st, out = _call(world, "peer_control", "POST", "/api/peer/v2/rotate", {})
    assert st == 200 and len(out["secret"]) >= 32
    new = out["secret"]
    g = host_store.find_grant("pc")
    assert g["secret"] == world.secrets["pc"] and g["next"]["secret"] == new
    st, out = _call(world, "peer_control", "POST", "/api/peer/v2/rotate/confirm", {}, secret=new)
    assert st == 200 and out["ok"]
    g = host_store.find_grant("pc")
    assert g["secret"] == new and "next" not in g and g["rotatedAt"]
    st, _ = _call(world, "peer_control", "GET", "/api/peer/v2/authz/grants", None)      # 옛 secret
    assert st == 401
