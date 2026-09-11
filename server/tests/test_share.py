"""N21 — 공유 링크 회귀 (50-files-share.md §3 수용 기준).

test_auth_elevate.py의 client 픽스처 패턴(auth 모듈 전역 monkeypatch + 비밀번호
설정)을 그대로 따른다 — 공유 발급은 승격이 필요하고, `/s/{token}`은 별도의
미인증 클라이언트(`anon`)로 접근해야 "다른 사람의 브라우저"를 정확히 흉내낸다.
"""

from __future__ import annotations

import time

import pytest
from starlette.testclient import TestClient

import auth as _auth
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password("s3cret-pw"))
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")

    with TestClient(main.app) as c:
        r = c.post("/api/auth", json={"token": "s3cret-pw"})
        assert r.status_code == 200, r.text
        r = c.post("/api/auth/elevate", json={"password": "s3cret-pw"})
        assert r.status_code == 200, r.text
        yield c


@pytest.fixture
def anon():
    """비밀번호도 device도 모르는 별도 방문자의 브라우저 — 쿠키 저장소를 공유하지 않는다."""
    with TestClient(main.app) as c:
        yield c


def _upload(client):
    r = client.post("/api/upload", files={"file": ("secret.txt", b"top secret")})
    assert r.status_code == 200
    return r.json()["id"]


def _share(client, file_id, mode, ttl=3600, once=False, pin=None):
    body = {"mode": mode, "ttl": ttl, "once": once}
    if pin is not None:
        body["pin"] = pin
    r = client.post(f"/api/files/{file_id}/share", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _set_device_cookies(browser, label="테스트기기"):
    """등록된 기기 + 유효 세션을 가진 '내 폰' 브라우저 쿠키를 그 클라이언트에 심는다."""
    secret, device_id = _auth.register_device(label)
    session = _auth.make_session(device_id)
    browser.cookies.set("vt_device", secret)
    browser.cookies.set("vt_session", session)


# --- device 모드 --------------------------------------------------------------


def test_device_mode_without_cookies_redirects_to_login_gate(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "device")["token"]
    r = anon.get(f"/s/{token}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == f"/?next=/s/{token}"


def test_device_mode_with_registered_device_downloads(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "device")["token"]
    _set_device_cookies(anon)
    r = anon.get(f"/s/{token}")
    assert r.status_code == 200
    assert r.content == b"top secret"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in r.headers["cache-control"]
    assert "attachment" in r.headers["content-disposition"]


def test_device_mode_with_unregistered_device_cookie_redirects(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "device")["token"]
    anon.cookies.set("vt_device", "not-a-real-secret")
    r = anon.get(f"/s/{token}", follow_redirects=False)
    assert r.status_code == 302


# --- pin 모드 ------------------------------------------------------------------


def test_pin_mode_get_shows_pin_page_not_file(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="1234")["token"]
    r = anon.get(f"/s/{token}")
    assert r.status_code == 200
    assert "PIN" in r.text
    assert r.content != b"top secret"


def test_pin_mode_correct_pin_then_downloads(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="1234")["token"]
    r1 = anon.post(f"/s/{token}/pin", data={"pin": "1234"}, follow_redirects=False)
    assert r1.status_code == 303
    r2 = anon.get(f"/s/{token}")  # anon 클라이언트가 쿠키를 그대로 들고 있다
    assert r2.status_code == 200
    assert r2.content == b"top secret"
    assert r2.headers["x-content-type-options"] == "nosniff"


def test_pin_mode_wrong_pin_does_not_download(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="1234")["token"]
    r = anon.post(f"/s/{token}/pin", data={"pin": "0000"})
    assert r.status_code == 401
    assert "set-cookie" not in r.headers  # 다운로드 쿠키는 정답일 때만 발급된다


def test_pin_mode_five_failures_cancels_share(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="1234")["token"]
    for _ in range(4):
        r = anon.post(f"/s/{token}/pin", data={"pin": "0000"})
        assert r.status_code == 401
    r = anon.post(f"/s/{token}/pin", data={"pin": "0000"})
    assert r.status_code == 404
    # 취소됐으므로 이후 올바른 PIN을 넣어도 이미 404 — 공유 자체가 사라졌다.
    r2 = anon.post(f"/s/{token}/pin", data={"pin": "1234"})
    assert r2.status_code == 404


def test_pin_download_cookie_is_single_use(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="1234")["token"]
    anon.post(f"/s/{token}/pin", data={"pin": "1234"}, follow_redirects=False)
    r2 = anon.get(f"/s/{token}")
    assert r2.status_code == 200
    # 같은 쿠키로 다시 받으면 서버가 지운 쿠키라 다시 PIN 페이지로 떨어진다.
    r3 = anon.get(f"/s/{token}")
    assert r3.status_code == 200
    assert "PIN" in r3.text


# --- once / 취소 ---------------------------------------------------------------


def test_once_share_is_consumed_after_first_download(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "device", once=True)["token"]
    _set_device_cookies(anon)
    r1 = anon.get(f"/s/{token}")
    assert r1.status_code == 200
    r2 = anon.get(f"/s/{token}")
    assert r2.status_code == 404


def test_cancelled_share_is_404_even_with_valid_signature(client, anon):
    fid = _upload(client)
    share = _share(client, fid, "device")
    token = share["token"]
    share_id = share["share"]["shareId"]
    r = client.delete(f"/api/files/{fid}/share/{share_id}")
    assert r.status_code == 200
    _set_device_cookies(anon)
    r2 = anon.get(f"/s/{token}")
    assert r2.status_code == 404


def test_expired_token_is_404(client, anon, monkeypatch):
    fid = _upload(client)
    token = _share(client, fid, "device", ttl=3600)["token"]
    # 토큰 자체에 exp가 박혀 있으므로 미래로 시계를 돌려 만료를 흉내낸다 — time
    # 모듈 객체는 전역 하나뿐이라 이 monkeypatch가 file_store/routes.share
    # 양쪽의 time.time() 호출에 그대로 반영된다.
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 4000)
    _set_device_cookies(anon)
    r = anon.get(f"/s/{token}")
    assert r.status_code == 404


def test_unknown_token_shape_is_404(anon):
    r = anon.get("/s/not-a-real-token")
    assert r.status_code == 404


# --- 발급 승격 요구 -------------------------------------------------------------


def test_share_create_requires_elevation(tmp_path, monkeypatch):
    """승격 없이 로그인만 한 상태에서 발급을 시도하면 401이어야 한다."""
    state_dir = tmp_path / "vt2"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key-2")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password("s3cret-pw"))
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    with TestClient(main.app) as c:
        c.post("/api/auth", json={"token": "s3cret-pw"})
        fid = _upload(c)
        r = c.post(f"/api/files/{fid}/share", json={"mode": "device", "ttl": 3600})
        assert r.status_code == 401
        assert r.json() == {"error": "elevation_required"}


# --- 파일 목록에서 shares 반영 --------------------------------------------------


def test_files_list_reflects_active_share(client):
    fid = _upload(client)
    _share(client, fid, "device")
    r = client.get("/api/files")
    item = next(x for x in r.json()["items"] if x["id"] == fid)
    assert len(item["shares"]) == 1
