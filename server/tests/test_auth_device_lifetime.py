"""기기 수명(임시 기기) + 기기 메타 기록 (2026-10).

지키는 것:
- 만료 판정은 한 곳(`devices._alive`)이고, 쿠키 검증과 세션 검증이 같은 판정을 본다.
  예전엔 세션 쪽이 존재 여부만 봐서 만료된 기기의 세션이 최대 24시간 더 살았다.
- 수명을 늘리는 경로가 없다: 허용값 밖의 `remember`는 400, 등록된 기기는 `remember`를
  무시, register_device도 DEVICE_TTL로 자른다.
- 세션은 기기보다 오래 살지 못한다(min(SESSION_TTL, 기기 남은 수명)).
- 만료 기록은 7일 보관 후 저장 시 정리되고, 그 기기의 설정 파일도 같이 지워진다.
- UA·IP·경로는 표시용으로만 기록된다.
"""

import importlib
import json
import time

import pytest
from starlette.testclient import TestClient

import auth as auth_mod
import main
from auth import client_info


@pytest.fixture
def auth(tmp_path, monkeypatch):
    importlib.reload(auth_mod)
    auth_mod.lockout.reset_all()
    state_dir = tmp_path / "vt"
    monkeypatch.setattr(auth_mod, "STATE_DIR", state_dir)
    monkeypatch.setattr(auth_mod, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(auth_mod, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(auth_mod, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(auth_mod, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(auth_mod, "VT_AUTH_PASSWORD_HASH", auth_mod.hash_password("correct-horse"))
    monkeypatch.setattr(auth_mod, "VT_AUTH_TOKEN", "")
    yield auth_mod


@pytest.fixture
def client(auth):
    with TestClient(main.app) as c:
        yield c


@pytest.fixture
def clock(monkeypatch):
    """time.time을 고정·이동할 수 있게 한다. `clock.now`를 바꾸면 모든 모듈에 반영된다."""
    class _Clock:
        now = 1_800_000_000.0

        def advance(self, sec):
            self.now += sec

    c = _Clock()
    monkeypatch.setattr(time, "time", lambda: c.now)
    return c


def _write_devices(auth, devices):
    auth.DEVICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    auth.DEVICES_PATH.write_text(json.dumps({"version": 1, "devices": devices}))


def _raw(auth):
    return json.loads(auth.DEVICES_PATH.read_text())["devices"]


def _cookie_max_age(resp, name):
    for h in resp.headers.get_list("set-cookie"):
        if h.startswith(name + "="):
            for part in h.split(";"):
                part = part.strip()
                if part.lower().startswith("max-age="):
                    return int(part.split("=", 1)[1])
    return None


def _login(client, **body):
    return client.post("/api/auth", json={"token": "correct-horse", **body})


# --- 수명 판정 --------------------------------------------------------------------


def test_legacy_record_without_expires_at_uses_added_at_plus_device_ttl(auth, clock):
    now = int(clock.now)
    _write_devices(auth, [
        {"id": "aaaaaaaaaaaaaaaa", "hash": "h1", "label": "Mac",
         "added_at": now - auth.DEVICE_TTL + 60, "last_seen": now},
        {"id": "bbbbbbbbbbbbbbbb", "hash": "h2", "label": "Mac",
         "added_at": now - auth.DEVICE_TTL - 60, "last_seen": now},
    ])
    rows = {d["id"]: d for d in auth.list_devices()}
    assert rows["aaaaaaaaaaaaaaaa"]["expired"] is False
    assert rows["aaaaaaaaaaaaaaaa"]["expires_at"] == now + 60
    assert rows["bbbbbbbbbbbbbbbb"]["expired"] is True


def test_register_default_is_persistent_90_days(auth, clock):
    _, did = auth.register_device("Mac")
    rec = auth._find_device(did)
    assert rec["expires_at"] == int(clock.now) + auth.DEVICE_TTL
    assert rec["kind"] == "persistent"


def test_register_temporary_and_clamps_longer_than_device_ttl(auth, clock):
    _, short = auth.register_device("Mac", ttl=3600)
    _, longer = auth.register_device("Mac", ttl=auth.DEVICE_TTL * 10)
    assert auth._find_device(short)["kind"] == "temporary"
    assert auth._find_device(short)["expires_at"] == int(clock.now) + 3600
    assert auth._find_device(longer)["expires_at"] == int(clock.now) + auth.DEVICE_TTL, \
        "DEVICE_TTL보다 긴 수명은 만들 수 없다"


@pytest.mark.parametrize("bad", [0, -1])
def test_register_rejects_non_positive_ttl(auth, bad):
    with pytest.raises(ValueError):
        auth.register_device("Mac", ttl=bad)


def test_verify_device_rejects_after_expiry(auth, clock):
    secret, _ = auth.register_device("Mac", ttl=3600)
    assert auth.verify_device(secret) is not None
    clock.advance(3601)
    assert auth.verify_device(secret) is None


def test_session_dies_with_its_expired_device(auth, clock):
    """회귀: 세션 exp는 남아 있어도 기기가 만료되면 세션도 무효여야 한다."""
    _, did = auth.register_device("Mac", ttl=3600)
    session = auth.make_session(did)  # 24h짜리를 일부러 발급
    assert auth.session_device(session) == did
    clock.advance(3601)
    assert auth.session_device(session) is None
    assert auth.check_request(session) is False


def test_elevation_dies_with_its_expired_device(auth, clock):
    _, did = auth.register_device("Mac", ttl=3600)
    session = auth.make_session(did, elev_exp=int(clock.now) + 7200)
    assert auth.session_elevated_until(session) > 0
    clock.advance(3601)
    assert auth.session_elevated_until(session) == 0


# --- 정리 -------------------------------------------------------------------------


def test_expired_device_stays_listed_during_grace_then_pruned_on_save(auth, clock):
    _, did = auth.register_device("Mac", ttl=3600)
    settings = auth.STATE_DIR / "device-settings" / f"{did}.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text("{}")

    clock.advance(3600 + auth.DEVICE_EXPIRED_GRACE - 10)
    rows = auth.list_devices()
    assert [d["id"] for d in rows] == [did] and rows[0]["expired"] is True

    clock.advance(20)
    assert auth.list_devices() == [], "보관 기간이 지나면 쓰기 전이라도 목록에서 빠진다"
    assert any(d["id"] == did for d in _raw(auth)), "아직 쓰기가 없었으니 파일에는 남아 있다"

    auth.register_device("iPhone")  # 아무 쓰기나 한 번
    assert all(d["id"] != did for d in _raw(auth))
    assert not settings.exists(), "정리된 기기의 설정 파일도 같이 지운다"


def test_prune_devices_removes_all_expired_now(auth, clock):
    _, keep = auth.register_device("Mac")
    _, gone = auth.register_device("Mac", ttl=3600)
    clock.advance(3601)
    removed = auth.prune_devices()
    assert [d["id"] for d in removed] == [gone]
    assert [d["id"] for d in _raw(auth)] == [keep]
    assert "hash" not in removed[0]


def test_drop_device_settings_ignores_unsafe_ids(auth):
    victim = auth.STATE_DIR / "victim.json"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("{}")
    auth.devices._drop_device_settings(["../victim", "", None])
    assert victim.exists()


# --- 메타 기록 ---------------------------------------------------------------------


def test_register_records_meta_and_verify_updates_ip_daily(auth, clock):
    meta = {"ua": "UA", "client": {"os": "Android 15", "browser": "Chrome 140"},
            "ip": "203.0.113.5", "via": "tunnel"}
    secret, did = auth.register_device("Android", method="password", meta=meta)
    rec = auth._find_device(did)
    assert rec["ip_first"] == rec["ip_last"] == "203.0.113.5"
    assert rec["via"] == "tunnel" and rec["method"] == "password"
    assert rec["client"] == {"os": "Android 15", "browser": "Chrome 140"}

    clock.advance(3600)
    auth.verify_device(secret, meta={"ip": "198.51.100.7", "via": "lan"})
    assert auth._find_device(did)["ip_last"] == "203.0.113.5", "하루 안에는 갱신하지 않는다"

    clock.advance(86400)
    auth.verify_device(secret, meta={"ip": "198.51.100.7", "via": "lan"})
    rec = auth._find_device(did)
    assert rec["ip_last"] == "198.51.100.7" and rec["via"] == "lan"
    assert rec["ip_first"] == "203.0.113.5"


def test_public_outputs_never_include_hash(auth):
    secret, did = auth.register_device("Mac")
    assert all("hash" not in d for d in auth.list_devices())
    assert "hash" not in auth.rename_device(did[:6], "집 맥")
    assert all("hash" not in d for d in auth.revoke_device(did[:6]))


# --- 로그인 -----------------------------------------------------------------------


def test_login_new_device_remember_1h_caps_cookie_and_session(client, auth):
    r = _login(client, remember="1h")
    assert r.status_code == 200
    assert _cookie_max_age(r, "vt_device") == 3600
    assert _cookie_max_age(r, "vt_session") == 3600, "세션은 기기보다 오래 살지 않는다"
    rec = auth._find_device(r.json()["device_id"])
    assert rec["kind"] == "temporary" and rec["method"] == "password"


def test_login_default_is_90_days(client, auth):
    r = _login(client)
    assert _cookie_max_age(r, "vt_device") == auth.DEVICE_TTL
    assert _cookie_max_age(r, "vt_session") == auth.SESSION_TTL
    assert auth._find_device(r.json()["device_id"])["kind"] == "persistent"


@pytest.mark.parametrize("bad", ["10y", "90D", 3600, ["1h"], {"x": 1}, True])
def test_login_rejects_unknown_remember(client, auth, bad):
    r = _login(client, remember=bad)
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_remember"
    assert not auth.DEVICES_PATH.exists(), "거절된 요청은 기기를 만들지 않는다"


def test_known_device_ignores_remember(client, auth):
    first = _login(client)
    did = first.json()["device_id"]
    before = auth._find_device(did)["expires_at"]
    again = _login(client, remember="1h")  # TestClient가 vt_device 쿠키를 그대로 보낸다
    assert again.status_code == 200
    assert again.json()["device_id"] == did
    assert auth._find_device(did)["expires_at"] == before
    assert _cookie_max_age(again, "vt_device") is None, "등록된 기기에는 쿠키를 새로 굽지 않는다"


def test_known_temp_device_session_capped_to_remaining(client, auth, clock):
    _login(client, remember="1h")
    clock.advance(3000)
    r = _login(client)
    assert r.status_code == 200
    assert _cookie_max_age(r, "vt_session") == 600


def test_expired_device_cookie_registers_as_new(client, auth, clock):
    first = _login(client, remember="1h").json()["device_id"]
    clock.advance(3601)
    assert client.get("/api/auth/status").json()["device_known"] is False
    second = _login(client).json()["device_id"]
    assert second != first


def test_login_with_otp_records_method(client, auth, monkeypatch):
    monkeypatch.setattr(auth, "totp_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_totp", lambda code: code == "123456")
    r = _login(client, otp="123456")
    assert r.status_code == 200
    assert auth._find_device(r.json()["device_id"])["method"] == "password+otp"


def test_ticket_temp_registers_one_day_device(client, auth):
    raw = auth.issue_ticket(label="mobile", device_ttl=auth.TICKET_TEMP_DEVICE_TTL)
    r = client.post("/api/auth", json={"ticket": raw})
    assert r.status_code == 200
    assert _cookie_max_age(r, "vt_device") == 86400
    rec = auth._find_device(r.json()["device_id"])
    assert rec["kind"] == "temporary" and rec["method"] == "ticket"


def test_ticket_ignores_body_remember(client, auth):
    raw = auth.issue_ticket(label="mobile")
    r = client.post("/api/auth", json={"ticket": raw, "remember": "1h"})
    assert _cookie_max_age(r, "vt_device") == auth.DEVICE_TTL


@pytest.mark.parametrize("bogus", ["86400", True, -5, 0, None])
def test_ticket_bogus_device_ttl_falls_back_to_default(client, auth, bogus):
    raw = auth.issue_ticket(label="mobile", device_ttl=bogus)
    r = client.post("/api/auth", json={"ticket": raw})
    assert r.status_code == 200
    assert _cookie_max_age(r, "vt_device") == auth.DEVICE_TTL


def test_cli_ticket_new_temp_binds_device_ttl(auth, capsys):
    assert auth._cli(["ticket-new", "mobile", "temp"]) == 0
    assert auth._load_tickets()[-1]["device_ttl"] == auth.TICKET_TEMP_DEVICE_TTL
    assert auth._cli(["ticket-new", "mobile"]) == 0
    assert "device_ttl" not in auth._load_tickets()[-1]


def test_cli_list_pretty_shows_temp_and_expired(auth, clock, capsys):
    auth.register_device("Mac", ttl=3600, meta={"ip": "198.51.100.7", "via": "lan",
                                                 "client": {"os": "macOS", "browser": "Safari 18"}})
    auth.register_device("Windows")
    clock.advance(3601)
    assert auth._cli(["device-list-pretty"]) == 0
    out = capsys.readouterr().out
    assert "유효 1개" in out and "만료 1개" in out
    assert "[임시]" in out and "만료됨" in out
    assert "macOS · Safari 18 · 198.51.100.7 (LAN)" in out
    assert "fsh device prune" in out


# --- /api/devices -----------------------------------------------------------------


def test_devices_route_exposes_lifetime_and_meta_but_not_ua(client, auth):
    _login(client, remember="24h")
    row = client.get("/api/devices").json()["devices"][0]
    assert row["kind"] == "temporary" and row["expired"] is False
    assert row["expires_at"] > 0 and row["method"] == "password"
    for k in ("os", "browser", "ip_last", "via"):
        assert k in row
    assert "ua" not in row and "hash" not in row


# --- client_info ------------------------------------------------------------------


@pytest.mark.parametrize("ua,os_,browser", [
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1", "iOS 18.1", "Safari 18"),
    ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/140.0.0.0 Mobile Safari/537.36", "Android 15", "Chrome 140"),
    ("Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) "
     "SamsungBrowser/27.0 Chrome/125.0.0.0 Mobile Safari/537.36", "Android 14", "Samsung Internet 27"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
     "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0", "Windows", "Edge 140"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:131.0) Gecko/20100101 Firefox/131.0",
     "macOS", "Firefox 131"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) CriOS/140.0.0.0 Mobile/15E148 Safari/604.1", "iOS 17.6", "Chrome 140"),
    ("", "", ""),
    ("curl/8.7.1", "", ""),
])
def test_parse_user_agent(ua, os_, browser):
    assert client_info.parse_user_agent(ua) == {"os": os_, "browser": browser}


def test_clean_ua_strips_control_chars_and_truncates():
    s = client_info.clean_ua("a\x00b\nc\x7f" + "x" * 1000)
    assert s.startswith("abc") and len(s) == client_info.UA_MAX_LEN


@pytest.mark.parametrize("host,headers,expected", [
    ("127.0.0.1", {"cf-connecting-ip": "203.0.113.5"}, ("203.0.113.5", "tunnel")),
    ("::1", {"cf-connecting-ip": "2001:db8::1"}, ("2001:db8::1", "tunnel")),
    ("127.0.0.1", {}, ("127.0.0.1", "local")),
    ("127.0.0.1", {"cf-connecting-ip": "not-an-ip"}, ("127.0.0.1", "local")),
    ("100.95.1.2", {"cf-connecting-ip": "203.0.113.5"}, ("100.95.1.2", "tailscale")),
    ("192.168.0.10", {"cf-connecting-ip": "203.0.113.5"}, ("192.168.0.10", "lan")),
    ("8.8.8.8", {}, ("8.8.8.8", "direct")),
    ("testclient", {}, ("", "")),
    (None, {}, ("", "")),
])
def test_classify_client(host, headers, expected):
    """CF 헤더는 루프백(cloudflared)에서 온 요청에서만 본다 — LAN에서 직접 온 요청이
    헤더를 꾸며도 기록이 바뀌지 않는다."""
    assert client_info.classify_client(host, headers) == expected


# --- 티켓 용도(S4) -----------------------------------------------------------------
# 노출 포트 `session` 모드 티켓(60초, 노출 앱 주소 `/__fsh/cb?t=`에 실려 다님)으로 메인 로그인을 하면
# 비밀번호·OTP 없이 90일짜리 기기와 셸을 얻는다. 용도가 다르면 거절하고, 태우지도 않는다.


def test_port_share_ticket_cannot_register_a_device(client, auth):
    raw = auth.issue_ticket("port-share", 60, purpose=auth.PURPOSE_PORT_SHARE, share="s1", device="-")
    r = client.post("/api/auth", json={"ticket": raw})
    assert r.status_code == 401 and r.json()["error"] == "ticket_invalid"
    assert "vt_device" not in r.cookies and "vt_session" not in r.cookies
    # 거절은 소멸이 아니다 — 원래 자리(포트 콜백)에서는 여전히 쓸 수 있다.
    assert auth.consume_ticket(raw, auth.PURPOSE_PORT_SHARE)["share"] == "s1"


def test_legacy_port_ticket_without_purpose_is_still_port_share(client, auth):
    """purpose 필드 전에 발급된(업그레이드 직전) 포트 티켓도 share 묶음으로 가려 거절한다."""
    raw = auth.issue_ticket("port-share", 60, share="s1", device="-")
    data = json.loads(auth.TICKETS_PATH.read_text())
    for rec in data["tickets"]:
        rec.pop("purpose", None)
    auth.TICKETS_PATH.write_text(json.dumps(data))
    assert client.post("/api/auth", json={"ticket": raw}).status_code == 401


def test_ticket_purpose_is_required_and_reserved(auth):
    with pytest.raises(TypeError):
        auth.consume_ticket(auth.issue_ticket("x"))  # 용도 없이 소비하는 호출부는 만들 수 없다
    with pytest.raises(ValueError):
        auth.issue_ticket("x", purpose="other")
    raw = auth.issue_ticket("x", purpose=auth.PURPOSE_DEVICE, exp=9_999_999_999)
    rec = auth.consume_ticket(raw, auth.PURPOSE_DEVICE)
    assert rec["purpose"] == "device" and rec["exp"] < 9_999_999_999
