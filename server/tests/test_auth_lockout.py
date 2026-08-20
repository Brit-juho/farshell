"""D14/D15: 비밀번호·OTP 실패 잠금이 키(IP) 단위로 격리되는지 검증.

- D14: 비밀번호도 OTP처럼 무제한 재시도가 불가능해야 한다.
- D15: 한 클라이언트의 실패가 다른 클라이언트(IP)의 시도까지 잠그면 안 된다
  (예전 OTP 구현의 회귀 버그 — 전역 리스트 하나로 추적했었다).

auth.py는 VT_AUTH_PASSWORD_HASH/VT_AUTH_SESSION_KEY 등을 모듈 import 시점에 읽어
전역 상수로 고정하므로, monkeypatch.setattr로 auth 모듈 자체의 속성을 갈아끼운다
(test_auth.py의 관례와 동일).
"""

import importlib

import pytest
from starlette.testclient import TestClient

import auth as auth_mod
import main


@pytest.fixture
def env(tmp_path, monkeypatch):
    importlib.reload(auth_mod)  # conftest가 비운 env 기준으로 깨끗하게 재로딩

    state_dir = tmp_path / "vt"
    monkeypatch.setattr(auth_mod, "STATE_DIR", state_dir)
    monkeypatch.setattr(auth_mod, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(auth_mod, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(auth_mod, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(auth_mod, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(auth_mod, "VT_AUTH_PASSWORD_HASH", auth_mod.hash_password("correct-horse"))
    monkeypatch.setattr(auth_mod, "VT_AUTH_TOKEN", "")
    # main.py는 `import auth`로 같은 모듈 객체를 참조하므로 위 monkeypatch가 그대로 반영된다.
    monkeypatch.setenv("VT_TRUST_PROXY", "1")  # X-Forwarded-For로 클라이언트별 IP를 구분하기 위해
    yield


@pytest.fixture
def client(env):
    with TestClient(main.app) as c:
        yield c


def _login(client, password, ip="1.1.1.1"):
    return client.post(
        "/api/auth",
        json={"token": password},
        headers={"X-Forwarded-For": ip},
    )


# --- D14: 비밀번호 잠금 ----------------------------------------------------------


def test_correct_password_succeeds(client):
    r = _login(client, "correct-horse")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_wrong_password_is_rejected_but_not_locked_on_first_try(client):
    r = _login(client, "wrong")
    assert r.status_code == 401
    assert r.json()["error"] == "invalid"


def test_password_locks_out_after_max_fails(client):
    for _ in range(auth_mod.PASSWORD_MAX_FAILS):
        r = _login(client, "wrong")
        assert r.status_code == 401

    r = _login(client, "wrong")
    assert r.status_code == 429
    assert r.json()["error"] == "password_locked"
    assert r.json()["retry_after"] > 0


def test_password_lock_also_blocks_the_correct_password(client):
    """잠긴 동안엔 뒤늦게 맞는 비밀번호를 넣어도 통과시키면 안 된다 — 그게 잠금의 의미다."""
    for _ in range(auth_mod.PASSWORD_MAX_FAILS):
        _login(client, "wrong")

    r = _login(client, "correct-horse")
    assert r.status_code == 429


def test_successful_login_resets_failure_count(client):
    for _ in range(auth_mod.PASSWORD_MAX_FAILS - 1):
        _login(client, "wrong")

    r = _login(client, "correct-horse")
    assert r.status_code == 200

    # 카운터가 리셋됐으므로, 성공 직후엔 다시 MAX_FAILS번 틀려야 잠긴다(그 전 실패는 안 세짐).
    for _ in range(auth_mod.PASSWORD_MAX_FAILS):
        r = _login(client, "wrong")
        assert r.status_code == 401
    r = _login(client, "wrong")  # MAX_FAILS+1번째 요청에서야 잠금이 걸린다
    assert r.status_code == 429


# --- D15: 잠금은 IP별로 격리 -----------------------------------------------------


def test_lockout_is_scoped_per_ip_not_global(client):
    """IP A가 잠겨도 IP B는 여전히 정상 로그인할 수 있어야 한다."""
    for _ in range(auth_mod.PASSWORD_MAX_FAILS):
        _login(client, "wrong", ip="9.9.9.9")
    r = _login(client, "wrong", ip="9.9.9.9")
    assert r.status_code == 429

    # 다른 IP는 전혀 영향받지 않는다.
    r = _login(client, "correct-horse", ip="8.8.8.8")
    assert r.status_code == 200


# --- OTP 잠금도 같은 방식으로 키가 넘어가는지(D15 재확인) --------------------------


def test_otp_lock_key_isolated_per_ip(client, monkeypatch):
    monkeypatch.setattr(auth_mod, "_otp_lockout", auth_mod._KeyedLockout(auth_mod.OTP_MAX_FAILS, auth_mod.OTP_LOCK_SEC))
    auth_mod.totp_new_secret()

    known_ip = "5.5.5.5"
    for _ in range(auth_mod.OTP_MAX_FAILS):
        r = client.post(
            "/api/auth",
            json={"token": "correct-horse", "otp": "000000"},
            headers={"X-Forwarded-For": known_ip},
        )
        assert r.status_code == 401

    r = client.post(
        "/api/auth",
        json={"token": "correct-horse", "otp": "000000"},
        headers={"X-Forwarded-For": known_ip},
    )
    assert r.status_code == 429
    assert r.json()["error"] == "otp_locked"

    # 다른 IP의 새 기기 등록 시도는 여전히 OTP를 요구할 뿐, 잠겨 있지 않다.
    r = client.post(
        "/api/auth",
        json={"token": "correct-horse"},
        headers={"X-Forwarded-For": "6.6.6.6"},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "otp_required"
