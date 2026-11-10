"""사전 인증 실패 한도 — 가짜 시계로 10회/60초 → 5분 잠금, 교환 엔드포인트 전역 분당 300, 소켓 면제, 오탐 방지.

실제 앱(`main.app`) 위에서 TestClient 로 돈다. 시계는 `preauth.LIMITER.clock` 를 바꿔 끼운다.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from authz import denylog, preauth


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(preauth.LIMITER, "clock", c)
    return c


@pytest.fixture
def app_auth(tmp_path, monkeypatch):
    import auth
    state = tmp_path / "vt"
    monkeypatch.setattr(auth, "STATE_DIR", state)
    monkeypatch.setattr(auth, "DEVICES_PATH", state / "devices.json")
    monkeypatch.setattr(auth, "TOTP_PATH", state / "totp.json")
    monkeypatch.setattr(auth, "TICKETS_PATH", state / "tickets.json")
    monkeypatch.setenv("VT_STATE_DIR", str(state))
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", "preauth-test-signing-key")
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", auth.hash_password("pw-" + "preauth"))
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "-".join(["machine", "tok", "preauth"]))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    return auth


def client():
    import main
    return TestClient(main.app, raise_server_exceptions=False)


# --- Limiter 단위 ------------------------------------------------------------------------------------


def test_ten_failures_in_sixty_seconds_lock_for_five_minutes(clock):
    lim = preauth.LIMITER
    for i in range(9):
        assert lim.note_failure("1.2.3.4") == 0
        clock.t += 5
    assert lim.lock_remaining("1.2.3.4") == 0
    assert lim.note_failure("1.2.3.4") == 300                        # 열 번째 — 잠긴다
    assert lim.lock_remaining("1.2.3.4") == 300
    clock.t += 100
    assert lim.lock_remaining("1.2.3.4") == 200
    assert lim.note_failure("1.2.3.4") == 200                        # 잠긴 중의 실패는 잠금을 늘리지 않는다
    clock.t += 200
    assert lim.lock_remaining("1.2.3.4") == 0                        # 5분 뒤 풀린다
    assert lim.note_failure("1.2.3.4") == 0                          # 실패 이력도 비워졌다
    assert lim.lock_remaining("5.6.7.8") == 0                        # 다른 출처는 무관


def test_failures_outside_the_window_do_not_add_up(clock):
    lim = preauth.LIMITER
    for i in range(30):
        assert lim.note_failure("a") == 0
        clock.t += 7                                                  # 60초 창 안에 9회뿐
    assert lim.lock_remaining("a") == 0


def test_exchange_cap_is_global_per_minute(clock):
    lim = preauth.LIMITER
    assert all(lim.exchange_allowed() for _ in range(300))
    assert lim.exchange_allowed() is False
    assert 1 <= lim.exchange_retry_after() <= 60
    clock.t += 61
    assert lim.exchange_allowed() is True


def test_source_key_follows_the_proxy_trust_switch(monkeypatch):
    h = {"cf-connecting-ip": "9.9.9.9"}
    monkeypatch.delenv("VT_TRUST_PROXY", raising=False)
    assert preauth.source_key("127.0.0.1", h) == "via:9.9.9.9"        # 터널 헤더를 단 루프백 = 그 원격 사용자
    assert preauth.source_key("127.0.0.1", {}) == "127.0.0.1"         # 헤더 없는 루프백은 한 키
    assert preauth.source_key("8.8.8.8", h) == "8.8.8.8"              # 직결 원격이 헤더를 위조해도 자기 주소로 센다
    monkeypatch.setenv("VT_TRUST_PROXY", "1")
    assert preauth.source_key("127.0.0.1", h) == "9.9.9.9"


# --- 앱 위에서 -----------------------------------------------------------------------------------------


def bad_login(c, i=0):
    return c.post("/api/auth", json={"token": f"wrong-{i}"})


def test_wrong_logins_lock_the_source_with_retry_after(clock, app_auth, monkeypatch):
    import auth.lockout as lo                                         # 기존 비밀번호 잠금보다 이 한도가 먼저 닿게
    monkeypatch.setattr(lo._password_lockout, "_max_fails", 1000)
    c = client()
    codes = [bad_login(c, i).status_code for i in range(10)]
    assert codes == [401] * 10
    r = bad_login(c, 10)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited" and r.json()["reason"] == "source_locked"
    assert r.headers["Retry-After"] == str(r.json()["retry_after"]) and 1 <= r.json()["retry_after"] <= 300
    # 잠긴 출처는 어느 인증 경로에서도 막힌다 — 맞는 자격이어도(추측 속도를 묶는 것이 목적)
    ok = c.post("/api/auth", json={"token": "pw-" + "preauth"})
    assert ok.status_code == 429
    assert c.get("/api/capabilities", headers={"Authorization": "Bearer " + "-".join(["machine", "tok", "preauth"])}).status_code == 429
    # 로그인 화면과 정적 파일은 열려 있다
    assert c.get("/api/auth/status").status_code == 200
    clock.t += 301
    assert c.post("/api/auth", json={"token": "pw-" + "preauth"}).status_code == 200


def test_invalid_cookie_and_token_failures_count_but_stale_signed_cookies_do_not(clock, app_auth):
    c = client()
    for i in range(9):
        assert c.get("/api/capabilities", headers={"Authorization": f"Bearer guess-{i}"}).status_code == 401
    # 서명은 맞는데 만료된 쿠키 — 낡은 탭이지 추측이 아니다. 몇 번을 보내도 세지 않는다.
    import time
    stale = app_auth.make_session("", ttl=-10)
    assert app_auth.session_signed(stale) and not app_auth.verify_session(stale)
    for _ in range(30):
        assert c.get("/api/capabilities", headers={"Cookie": f"vt_session={stale}"}).status_code == 401
    assert preauth.LIMITER.lock_remaining("testclient") == 0
    assert c.get("/api/capabilities", headers={"Authorization": "Bearer guess-last"}).status_code == 401    # 10번째 — 잠금
    assert c.get("/api/capabilities", headers={"Authorization": "Bearer guess-next"}).status_code == 429
    # 자격을 아예 안 낸 요청(401)은 추측이 아니다
    preauth.reset()
    for _ in range(30):
        assert c.get("/api/capabilities").status_code == 401
    assert c.get("/api/capabilities").status_code == 401


def test_forged_cookie_counts(clock, app_auth):
    c = client()
    for i in range(10):
        c.get("/api/capabilities", headers={"Cookie": f"vt_session=v2.9999999999.-.deadbeef{i}"})
    assert c.get("/api/capabilities").status_code == 429


def _cookie_del(r):
    return [h for h in r.headers.get_list("set-cookie") if h.startswith("vt_session=") and "Max-Age=0" in h]


def test_same_wrong_cookie_is_counted_once_per_window(clock, app_auth):
    c = client()
    hdr = {"Cookie": "vt_session=v2.9999999999.-.deadbeef"}
    for _ in range(20):
        assert c.get("/api/capabilities", headers=hdr).status_code == 401
    assert preauth.LIMITER.lock_remaining("testclient") == 0
    assert c.post("/api/auth", json={"token": "pw-" + "preauth"}).status_code == 200


def test_ten_distinct_wrong_cookies_still_lock(clock, app_auth):
    c = client()
    for i in range(10):
        c.get("/api/capabilities", headers={"Cookie": f"vt_session=bad-{i}"})
    assert c.get("/api/capabilities").status_code == 429


def test_same_wrong_value_counts_again_after_the_window(clock):
    lim = preauth.LIMITER
    assert lim.note_failure_once("9.9.9.9", "v") == (True, 0)
    clock.t += 59
    assert lim.note_failure_once("9.9.9.9", "v") == (False, 0)      # 창 안 — 세지 않는다
    clock.t += 2                                                     # 처음 센 뒤 61초
    assert lim.note_failure_once("9.9.9.9", "v") == (True, 0)       # 다시 센다
    assert lim.note_failure_once("9.9.9.9", "w") == (True, 0)
    assert len(lim._failures["9.9.9.9"]) == 2      # 처음 센 것은 창 밖으로 빠졌다


def test_unsigned_cookie_401_deletes_the_cookie(clock, app_auth):
    c = client()
    r = c.get("/api/capabilities", headers={"Cookie": "vt_session=foreign-cookie"})
    assert r.status_code == 401
    dels = _cookie_del(r)
    assert len(dels) == 1 and "Path=/" in dels[0]


def test_bearer_or_query_wrong_value_does_not_delete_cookie(clock, app_auth):
    c = client()
    r = c.get("/api/capabilities", headers={"Authorization": "Bearer wrong-bearer"})
    assert r.status_code == 401 and not r.headers.get_list("set-cookie")
    r = c.get("/api/capabilities?token=wrong-query")
    assert r.status_code == 401 and not r.headers.get_list("set-cookie")


def test_expired_signed_cookie_is_not_deleted(clock, app_auth):
    c = client()
    stale = app_auth.make_session("", ttl=-10)
    r = c.get("/api/capabilities", headers={"Cookie": f"vt_session={stale}"})
    assert r.status_code == 401 and not r.headers.get_list("set-cookie")


def test_peer_signature_failures_count_and_lock_the_source(clock, app_auth):
    c = client()
    h = {"X-Peer-Id": "ghost", "X-Peer-Ts": "1", "X-Peer-Nonce": "n", "X-Peer-Sig": "00"}
    codes = [c.get("/api/peer/ping", headers={**h, "X-Peer-Nonce": f"n{i}"}).status_code for i in range(10)]
    assert codes == [401] * 10
    r = c.get("/api/peer/ping", headers=h)
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"


def test_pairing_code_failures_count(clock, app_auth):
    c = client()
    for i in range(10):
        assert c.post("/api/peer/pair", json={"ticket": f"bad{i}", "id": "m9"}).status_code == 401
    assert c.post("/api/peer/pair", json={"ticket": "bad", "id": "m9"}).status_code == 429


def test_exchange_cap_applies_across_sources(clock, app_auth, monkeypatch):
    monkeypatch.setattr(preauth.LIMITER, "exchange_per_min", 5)
    c = client()
    for i in range(5):
        assert c.post("/api/auth/logout").status_code != 429          # 교환 경로가 아니다
    codes = [c.post("/api/peer/pair", json={"ticket": "x", "id": "m9"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    other = client()
    assert other.post("/api/auth", json={"token": "pw-" + "preauth"}, headers={"CF-Connecting-IP": "9.9.9.9"}).status_code == 429   # 전역 상한은 출처와 무관


def test_origin_check_is_not_skipped_for_loopback(clock, app_auth):
    c = client()
    r = c.post("/api/auth", json={"token": "x"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["reason"] == "cross_origin"
    loop = TestClient(__import__("main").app, base_url="http://127.0.0.1:7777", raise_server_exceptions=False)
    r = loop.post("/api/auth", json={"token": "x"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["reason"] == "cross_origin"


def test_failures_and_lockouts_go_to_the_deny_log_not_the_chain(clock, app_auth):
    from authz import audit
    c = client()
    for i in range(11):
        c.get("/api/capabilities", headers={"Authorization": f"Bearer guess-{i}"})
    rows = denylog.read_recent(50)
    acts = {r["action"] for r in rows}
    assert {"auth.failure", "auth.lockout", "preauth.locked"} <= acts
    assert all(r["decision"] == "deny" and r["source"] == "denials" for r in rows)
    assert "guess-" not in json.dumps(rows)                           # 자격 값은 기록하지 않는다
    assert not audit.path().exists()


def test_the_unix_socket_is_never_limited(clock):
    """소켓 요청(`fsh_sock`)은 한도를 지나지 않는다 — 잠긴 출처 키와 무관하게 앱까지 닿는다."""
    import asyncio
    import main
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope.get("fsh_sock"))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = main.PreauthMiddleware(inner)
    for _ in range(20):
        preauth.LIMITER.note_failure("127.0.0.1")
    assert preauth.LIMITER.lock_remaining("127.0.0.1") > 0

    async def run(scope):
        sent = []

        async def send(m):
            sent.append(m)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await mw(scope, receive, send)
        return next(m for m in sent if m["type"] == "http.response.start")["status"]

    base = {"type": "http", "method": "GET", "path": "/api/queue", "headers": [], "client": ("127.0.0.1", 5000)}
    assert asyncio.run(run({**base, "fsh_sock": True})) == 204
    assert asyncio.run(run(dict(base))) == 429
