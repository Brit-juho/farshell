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
    token = _share(client, fid, "pin", pin="123456")["token"]
    r = anon.get(f"/s/{token}")
    assert r.status_code == 200
    assert "PIN" in r.text
    assert r.content != b"top secret"


def test_pin_mode_correct_pin_then_downloads(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="123456")["token"]
    r1 = anon.post(f"/s/{token}/pin", data={"pin": "123456"}, follow_redirects=False)
    assert r1.status_code == 303
    r2 = anon.get(f"/s/{token}")  # anon 클라이언트가 쿠키를 그대로 들고 있다
    assert r2.status_code == 200
    assert r2.content == b"top secret"
    assert r2.headers["x-content-type-options"] == "nosniff"


def test_pin_mode_wrong_pin_does_not_download(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="123456")["token"]
    r = anon.post(f"/s/{token}/pin", data={"pin": "0000"})
    assert r.status_code == 401
    assert "set-cookie" not in r.headers  # 다운로드 쿠키는 정답일 때만 발급된다


def test_pin_mode_five_failures_cancels_share(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="123456")["token"]
    for _ in range(4):
        r = anon.post(f"/s/{token}/pin", data={"pin": "0000"})
        assert r.status_code == 401
    r = anon.post(f"/s/{token}/pin", data={"pin": "0000"})
    assert r.status_code == 404
    # 취소됐으므로 이후 올바른 PIN을 넣어도 이미 404 — 공유 자체가 사라졌다.
    r2 = anon.post(f"/s/{token}/pin", data={"pin": "123456"})
    assert r2.status_code == 404


def test_pin_download_cookie_is_single_use(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="123456")["token"]
    anon.post(f"/s/{token}/pin", data={"pin": "123456"}, follow_redirects=False)
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


# --- 파일 탭 경로 공유(스냅샷 복사) ------------------------------------------


@pytest.fixture
def browse(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    return root


def test_share_import_snapshots_and_shares(client, anon, browse):
    (browse / "note.md").write_text("v1")
    r = client.post("/api/fs/share-import", json={"path": str(browse / "note.md")})
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["name"] == "note.md" and item["size"] == 2
    # 같은 원본·크기·mtime이면 같은 스냅샷을 재사용한다
    again = client.post("/api/fs/share-import", json={"path": str(browse / "note.md")}).json()["item"]
    assert again["id"] == item["id"]
    # 발급 → PIN 모드로 받기
    token = _share(client, item["id"], "pin", pin="123456")["token"]
    assert anon.post(f"/s/{token}/pin", data={"pin": "123456"}, follow_redirects=False).status_code == 303
    assert anon.get(f"/s/{token}").content == b"v1"
    # 원본이 바뀌어도 스냅샷은 그대로, 새로 가져오면 새 스냅샷이다
    (browse / "note.md").write_text("version-2")
    assert client.get(f"/api/files/{item['id']}/download").content == b"v1"
    newer = client.post("/api/fs/share-import", json={"path": str(browse / "note.md")}).json()["item"]
    assert newer["id"] != item["id"]


def test_share_import_enforces_browse_boundary(client, browse, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (browse / ".env").write_text("SECRET=1")
    (browse / "link").symlink_to(outside)
    (browse / "d").mkdir()
    for path, status in [
        (str(outside), 403),                  # 루트 밖
        (str(browse / ".env"), 403),          # 거부 목록
        (str(browse / "link"), 403),          # 루트 밖을 가리키는 심링크
        (str(browse / "d"), 400),             # 디렉토리
        (str(browse / "nope.txt"), 404),      # 없음
        ("", 400),
    ]:
        r = client.post("/api/fs/share-import", json={"path": path})
        assert r.status_code == status, (path, r.status_code, r.text)
    assert client.post("/api/fs/share-import", json={}).status_code == 400


def test_share_import_size_cap_and_elevation(client, anon, browse, monkeypatch):
    import file_store
    (browse / "big.bin").write_bytes(b"0123456789")
    monkeypatch.setattr(file_store, "MAX_UPLOAD_BYTES", 5)
    r = client.post("/api/fs/share-import", json={"path": str(browse / "big.bin")})
    assert r.status_code == 413
    # 승격이 없는 클라이언트는 거부된다
    assert anon.post("/api/fs/share-import", json={"path": str(browse / "big.bin")}).status_code in (401, 403)


# --- S8: PIN 시도 제한 --------------------------------------------------------------
# 예전에는 요청 처음에 읽어 둔 공유로 횟수를 판단하고 `읽은 값 + 1`을 덮어써서, 동시에 몰려 온 요청이 전부
# attempts=0 을 보고 검증까지 마쳤다 — 5회 제한이 무력했다.


def test_concurrent_pin_guesses_are_bounded_by_the_attempt_limit(client, anon, monkeypatch):
    import threading
    import time as _time
    import file_store
    # 읽기와 쓰기 사이의 틈(옛 코드는 hash_pin 이 그 안에 있었다)을 넓혀 경쟁을 결정적으로 만든다 — 로직은 그대로다.
    real_hash = file_store.hash_pin
    monkeypatch.setattr(file_store, "hash_pin", lambda pin, salt: (_time.sleep(0.15), real_hash(pin, salt))[1])
    fid = _upload(client)
    share = _share(client, fid, "pin", pin="123456")
    token = share["token"]
    seen = []
    lock = threading.Lock()
    gate = threading.Barrier(40)

    def guess(i):
        gate.wait()
        with TestClient(main.app) as c:
            r = c.post(f"/s/{token}/pin", data={"pin": f"{i:06d}"})
        with lock:
            seen.append(r.status_code)

    threads = [threading.Thread(target=guess, args=(i,)) for i in range(40)]
    [t.start() for t in threads]
    [t.join(timeout=60) for t in threads]
    assert len(seen) == 40
    verified = seen.count(401)          # 틀린 PIN 으로 실제 검증까지 간 요청
    assert verified <= file_store.MAX_PIN_ATTEMPTS - 1, seen
    assert seen.count(404) >= 40 - file_store.MAX_PIN_ATTEMPTS
    assert anon.post(f"/s/{token}/pin", data={"pin": "123456"}).status_code == 404  # 공유가 사라졌다


def test_reserve_pin_attempt_is_atomic_in_the_store(client):
    import threading
    import file_store
    fid = _upload(client)
    sid = _share(client, fid, "pin", pin="123456")["share"]["shareId"]
    out = []
    gate = threading.Barrier(30)

    def go():
        gate.wait()
        out.append(file_store.reserve_pin_attempt(fid, sid)[0])

    ts = [threading.Thread(target=go) for _ in range(30)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert out.count("ok") == file_store.MAX_PIN_ATTEMPTS
    assert out.count("exhausted") == 1 and out.count("gone") == 30 - file_store.MAX_PIN_ATTEMPTS - 1


def test_wrong_pins_also_count_toward_the_source_lockout(client, anon):
    from authz import preauth
    fid = _upload(client)
    for n in range(2):  # 공유마다 5회에서 취소되므로 둘에 걸쳐 출처 한도(10회)를 채운다
        token = _share(client, fid, "pin", pin="123456")["token"]
        for _ in range(5):
            anon.post(f"/s/{token}/pin", data={"pin": "000000"})
    assert preauth.LIMITER.lock_remaining(preauth.source_key("testclient", {})) > 0


def test_oversized_pin_form_is_refused_without_reading_it_all(client, anon):
    fid = _upload(client)
    token = _share(client, fid, "pin", pin="123456")["token"]
    r = anon.post(f"/s/{token}/pin", data={"pin": "x" * 100_000})
    assert r.status_code == 413


def test_new_pins_must_be_at_least_six_characters(client):
    fid = _upload(client)
    for bad in ("1234", "12345", "x" * 21):
        r = client.post(f"/api/files/{fid}/share", json={"mode": "pin", "ttl": 3600, "once": False, "pin": bad})
        assert r.status_code == 400 and r.json()["error"] == "bad_pin", bad
    assert client.post(f"/api/files/{fid}/share", json={"mode": "pin", "ttl": 3600, "once": False, "pin": "123456"}).status_code == 200


# --- PIN 다운로드 쿠키는 정확히 한 번 ------------------------------------------------
# 예전에는 쿠키를 검증만 하고 서버 쪽 기록을 지우지 않았다. 브라우저에 「쿠키 삭제」를 보낼 뿐이라, 쿠키 값을
# 가진 사람은 60초 안에 몇 번이고(동시에 여러 개도) 받을 수 있었다.


def _pin_cookie(client, anon, token, share_id):
    r = anon.post(f"/s/{token}/pin", data={"pin": "123456"}, follow_redirects=False)
    assert r.status_code == 303
    name = f"vt_share_dl_{share_id}"
    value = r.cookies.get(name)
    assert value
    return name, value


def test_pin_download_cookie_cannot_be_replayed(client, anon):
    fid = _upload(client)
    share = _share(client, fid, "pin", pin="123456")
    token = share["token"]
    name, value = _pin_cookie(client, anon, token, share["share"]["shareId"])
    with TestClient(main.app) as a, TestClient(main.app) as b:
        a.cookies.set(name, value)
        b.cookies.set(name, value)   # 같은 값을 손에 넣은 다른 브라우저
        assert a.get(f"/s/{token}").content == b"top secret"
        r = b.get(f"/s/{token}")
        assert r.status_code == 200 and r.content != b"top secret" and "PIN" in r.text


# --- 이어받기: 소비한 쿠키는 원래 기한까지 `Range` 요청에만 통한다 ----------------------------------
# 1회용이 된 뒤로 끊긴 다운로드를 브라우저·다운로드 관리자가 `Range`로 다시 요청하면 PIN 화면을 받았다.


def _pin_download(client, anon, *, once=False, data=b"0123456789"):
    r = client.post("/api/upload", files={"file": ("big.bin", data)})
    fid = r.json()["id"]
    share = _share(client, fid, "pin", pin="123456", once=once)
    token = share["token"]
    name, value = _pin_cookie(client, anon, token, share["share"]["shareId"])
    first = anon.get(f"/s/{token}")
    assert first.status_code == 200 and first.content == data
    return token, name, value, first


def test_pin_download_resumes_with_range_until_the_cookie_expires(client, anon, monkeypatch):
    token, name, value, first = _pin_download(client, anon)
    assert anon.cookies.get(name) == value, "다운로드 응답이 이어받기에 쓸 쿠키를 지웠다"
    r = anon.get(f"/s/{token}", headers={"Range": "bytes=4-", "If-Range": first.headers["etag"]})
    assert r.status_code == 206 and r.content == b"456789"
    assert r.headers["content-range"] == "bytes 4-9/10"
    assert "no-store" in r.headers.get("cache-control", "")
    r = anon.get(f"/s/{token}", headers={"Range": "bytes=2-3"})
    assert r.status_code == 206 and r.content == b"23"
    # 원래 기한(PIN 통과 후 60초)이 지나면 끝 — PIN부터 다시.
    import file_store
    real = time.time
    monkeypatch.setattr(file_store.time, "time", lambda: real() + 61)
    r = anon.get(f"/s/{token}", headers={"Range": "bytes=4-"})
    assert r.status_code == 200 and "PIN" in r.text and r.content != b"456789"


def test_pin_download_plain_or_whole_file_requests_are_still_refused(client, anon):
    token, name, value, first = _pin_download(client, anon)
    for headers in ({}, {"Range": "bytes=0-"}, {"Range": "bytes=0-9"}, {"Range": "bytes=-10"},
                    {"Range": "bytes=1-2,4-5"}, {"Range": "bytes=10-"},
                    {"Range": "bytes=4-", "If-Range": '"stale-etag"'}):
        r = anon.get(f"/s/{token}", headers=headers)
        assert r.status_code == 200 and "PIN" in r.text, headers
        assert b"0123456789" not in r.content and r.headers.get("content-range") is None, headers


def test_pin_download_range_without_a_claimed_cookie_is_refused(client, anon):
    r = client.post("/api/upload", files={"file": ("big.bin", b"0123456789")})
    share = _share(client, r.json()["id"], "pin", pin="123456")
    token = share["token"]
    r = anon.get(f"/s/{token}", headers={"Range": "bytes=4-"})
    assert r.status_code == 200 and "PIN" in r.text
    with TestClient(main.app) as other:
        other.cookies.set(f"vt_share_dl_{share['share']['shareId']}", "made-up-value")
        r = other.get(f"/s/{token}", headers={"Range": "bytes=4-"})
        assert r.status_code == 200 and "PIN" in r.text


def test_once_pin_share_resumes_but_never_serves_a_second_whole_download(client, anon):
    token, name, value, first = _pin_download(client, anon, once=True)
    r = anon.get(f"/s/{token}", headers={"Range": "bytes=6-"})
    assert r.status_code == 206 and r.content == b"6789"
    assert anon.get(f"/s/{token}").status_code == 404           # 공유는 첫 다운로드에서 소비됐다
    assert anon.get(f"/s/{token}", headers={"Range": "bytes=0-"}).status_code == 404
    assert anon.post(f"/s/{token}/pin", data={"pin": "123456"}, follow_redirects=False).status_code == 404


def _widen_race_window(monkeypatch):
    """두 요청이 모두 공유 조회를 마친 뒤에 다운로드 판정으로 가게 틈을 넓힌다 — 경쟁을 결정적으로 만든다."""
    import file_store
    import time as _time
    real = file_store.get_item_and_share
    monkeypatch.setattr(file_store, "get_item_and_share", lambda *a: (real(*a), _time.sleep(0.2))[0])


def test_pin_download_cookie_concurrent_requests_get_one_file(client, anon, monkeypatch):
    import threading
    fid = _upload(client)
    share = _share(client, fid, "pin", pin="123456")
    token = share["token"]
    name, value = _pin_cookie(client, anon, token, share["share"]["shareId"])
    _widen_race_window(monkeypatch)
    got = []
    lock = threading.Lock()
    gate = threading.Barrier(2)

    def fetch():
        with TestClient(main.app) as c:
            c.cookies.set(name, value)
            gate.wait()
            r = c.get(f"/s/{token}")
        with lock:
            got.append(r.content == b"top secret")

    ts = [threading.Thread(target=fetch) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert sorted(got) == [False, True], got


def test_once_device_share_concurrent_requests_get_one_file(client, monkeypatch):
    import threading
    fid = _upload(client)
    token = _share(client, fid, "device", once=True)["token"]
    _widen_race_window(monkeypatch)
    statuses = []
    lock = threading.Lock()
    gate = threading.Barrier(2)

    def fetch():
        with TestClient(main.app) as c:
            _set_device_cookies(c)
            gate.wait()
            r = c.get(f"/s/{token}")
        with lock:
            statuses.append((r.status_code, r.content == b"top secret"))

    ts = [threading.Thread(target=fetch) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert sorted(statuses) == [(200, True), (404, False)], statuses


# --- share-import: 판정 뒤 부모 디렉토리를 심링크로 바꿔치기 ----------------------------
# 예전에는 마지막 조각만 O_NOFOLLOW로 열어서, resolve 뒤에 중간 디렉토리를 경계 밖을 가리키는 심링크로 바꾸면 경계 밖
# 파일이 저장소로 복사되고 공유 링크로 나갈 수 있었다.


def test_share_import_refuses_parent_swapped_to_symlink_after_check(client, browse, tmp_path, monkeypatch):
    import fsguard
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note.txt").write_text("OUTSIDE-SECRET")
    sub = browse / "sub"
    sub.mkdir()
    (sub / "note.txt").write_text("inside")

    real_resolve = fsguard.resolve_under_roots

    def resolve_then_swap(raw, **kw):
        p = real_resolve(raw, **kw)
        sub.rename(browse / "sub.real")   # 판정이 끝난 직후, 여는 사이에 바꿔치기
        sub.symlink_to(outside)
        return p

    monkeypatch.setattr(fsguard, "resolve_under_roots", resolve_then_swap)
    r = client.post("/api/fs/share-import", json={"path": str(sub / "note.txt")})
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "not_a_file"
    for x in client.get("/api/files").json()["items"]:
        assert client.get(f"/api/files/{x['id']}/download").content != b"OUTSIDE-SECRET"


def test_share_import_refuses_last_component_swapped_to_symlink(client, browse, tmp_path, monkeypatch):
    import fsguard
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-SECRET")
    target = browse / "note.txt"
    target.write_text("inside")
    real_resolve = fsguard.resolve_under_roots

    def resolve_then_swap(raw, **kw):
        p = real_resolve(raw, **kw)
        target.unlink()
        target.symlink_to(outside)
        return p

    monkeypatch.setattr(fsguard, "resolve_under_roots", resolve_then_swap)
    r = client.post("/api/fs/share-import", json={"path": str(target)})
    assert r.status_code == 400, r.text


# --- 공유 다운로드가 저장소 잠금을 기다리는 동안 이벤트 루프가 돈다 ---------------------


def test_share_download_waits_for_the_store_lock_without_blocking_the_loop(client):
    import asyncio
    import threading
    import time as _time
    import httpx
    import file_store

    fid = _upload(client)
    token = _share(client, fid, "device")["token"]
    secret, device_id = _auth.register_device("테스트기기")
    cookies = {"vt_device": secret, "vt_session": _auth.make_session(device_id)}

    held = threading.Event()
    release = threading.Event()

    def hold_lock():
        with file_store._locked():
            held.set()
            release.wait(5)

    async def scenario():
        gaps = []
        done = False

        async def ticker():
            last = _time.monotonic()
            while not done:
                await asyncio.sleep(0.02)
                now = _time.monotonic()
                gaps.append(now - last)
                last = now

        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", cookies=cookies) as ac:
            tick = asyncio.create_task(ticker())
            await asyncio.sleep(0.05)   # 틱이 먼저 돌기 시작해야 요청 중의 멈춤이 간격으로 잡힌다
            asyncio.get_running_loop().call_later(0.6, release.set)   # 루프가 멈추면 이것도 못 돈다
            r = await ac.get(f"/s/{token}")
            done = True
            await tick
        return r, max(gaps)

    t = threading.Thread(target=hold_lock)
    t.start()
    assert held.wait(5)
    try:
        r, gap = asyncio.run(scenario())
    finally:
        release.set()
        t.join(5)
    assert r.status_code == 200 and r.content == b"top secret", r.text
    assert gap < 0.3, f"이벤트 루프가 {gap:.2f}초 멈췄다"
