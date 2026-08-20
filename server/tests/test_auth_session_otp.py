"""D11: 세션 만료 / OTP 재사용(리플레이) 방지 / otp_lock_remaining 타이밍.

test_auth.py의 핵심 3종(HMAC 변조 거부·기기 폐기→세션 무효화·티켓 1회성)과
test_auth_lockout.py의 D14/D15(잠금 자체의 IP 격리)는 이미 다룬다. 여기서는
그 사이에 비어 있던 브랜치를 채운다.

auth.py는 STATE_DIR 등을 모듈 import 시점에 읽어 전역 상수로 고정하므로,
monkeypatch.setattr로 auth 모듈 자체의 속성을 갈아끼운다(test_auth.py와 동일한 관례).
"""

import importlib

import pytest


@pytest.fixture
def auth(tmp_path, monkeypatch):
    import auth as _auth

    importlib.reload(_auth)  # conftest가 비운 env 기준으로 깨끗하게 재로딩

    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    yield _auth


# --- 세션 만료 -------------------------------------------------------------------


def test_expired_session_is_rejected(auth):
    """ttl을 음수로 주면 발급 즉시 과거 시각이 만료시각이 된다 — time.time()을
    건드리지 않고도 '만료된 세션'을 그대로 재현할 수 있다."""
    token = auth.make_session(device_id="-", ttl=-1)
    assert auth.verify_session(token) is False


def test_session_valid_just_before_expiry_then_invalid_after(auth):
    token_short = auth.make_session(device_id="-", ttl=1)
    assert auth.verify_session(token_short) is True  # 아직 안 지남

    token_expired = auth.make_session(device_id="-", ttl=0)
    # exp == now 인 경우 session_device는 "exp <= now"를 무효로 판정한다.
    assert auth.verify_session(token_expired) is False


@pytest.mark.parametrize(
    "garbage",
    ["", "not-a-session", "v2.123", "v2.123.did.sig.extra", "v3.123.did.sig", "v2.notanumber.did.sig"],
)
def test_malformed_session_values_are_rejected_not_raise(auth, garbage):
    """형식이 깨진 값이 예외 없이 그냥 False로 처리돼야 한다 — 여기서 500이 나면
    형식 파싱 실패가 인증 우회나 서버 크래시로 번질 수 있다."""
    assert auth.verify_session(garbage) is False


def test_v1_legacy_session_accepted_until_expiry(auth):
    """v2 이전에 발급된 v1 쿠키(기기 미결부)도 만료 전까지는 그대로 인정해야
    하위 호환이 깨지지 않는다."""
    exp = int(__import__("time").time()) + 3600
    payload = f"v1.{exp}"
    sig = auth._sign(payload)
    v1_token = f"{payload}.{sig}"
    assert auth.session_device(v1_token) == "-"
    assert auth.verify_session(v1_token) is True


def test_v1_legacy_session_rejected_after_expiry(auth):
    exp = int(__import__("time").time()) - 10
    payload = f"v1.{exp}"
    sig = auth._sign(payload)
    v1_token = f"{payload}.{sig}"
    assert auth.verify_session(v1_token) is False


def test_device_unbound_session_survives_any_device_revocation(auth):
    """device_id 없이 발급된 세션("-")은 특정 기기에 안 묶여 있으므로, 다른(또는
    존재하지도 않는) 기기를 revoke해도 영향받지 않아야 한다."""
    secret, device_id = auth.register_device(label="phone")
    session = auth.make_session(device_id="-")
    assert auth.verify_session(session) is True

    auth.revoke_device(device_id)
    assert auth.verify_session(session) is True  # 여전히 유효


def test_no_session_key_falls_back_to_raw_token(auth, monkeypatch):
    """VT_AUTH_SESSION_KEY가 없는 레거시 배포에서는 make_session이 기계 토큰을
    그대로 세션값으로 쓴다(서명 없음) — 그 값 자체가 세션 검증은 통과하지 못한다
    (verify_session은 서명키가 없으면 항상 거부한다는 걸 함께 확인)."""
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", "")
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "raw-machine-token")
    assert auth.make_session(device_id="-") == "raw-machine-token"
    assert auth.verify_session("raw-machine-token") is False


# --- OTP 재사용(리플레이) 방지 ----------------------------------------------------


def _current_totp(auth, secret):
    import time
    counter = int(time.time()) // 30
    return auth._hotp(secret, counter)


def test_otp_replay_is_rejected(auth):
    """같은 6자리 코드를 두 번 연속 쓰면 두 번째는 거부돼야 한다 — 어깨너머로
    본 코드나 로그에 남은 코드의 재사용을 막는 게 이 기능의 존재 이유다."""
    secret = auth.totp_new_secret()
    code = _current_totp(auth, secret)

    assert auth.verify_totp(code) is True
    assert auth.verify_totp(code) is False  # 리플레이 — 반드시 거부


def test_otp_older_code_after_success_is_rejected(auth):
    """최신 코드로 성공한 뒤에는, 그보다 이전 스텝의 (아직 안 썼던) 코드도 거부돼야
    한다 — last_counter가 시간 역행을 막는다."""
    secret = auth.totp_new_secret()
    import time
    counter = int(time.time()) // 30
    current_code = auth._hotp(secret, counter)
    older_code = auth._hotp(secret, counter - 1)

    assert auth.verify_totp(current_code) is True
    assert auth.verify_totp(older_code) is False


def test_otp_wrong_code_is_rejected(auth):
    auth.totp_new_secret()
    assert auth.verify_totp("000000") is False


def test_otp_malformed_code_is_rejected(auth):
    auth.totp_new_secret()
    assert auth.verify_totp("") is False
    assert auth.verify_totp("12345") is False  # 5자리
    assert auth.verify_totp("abcdef") is False  # 숫자 아님


# --- otp_lock_remaining 타이밍 ----------------------------------------------------


def test_otp_lock_remaining_zero_before_threshold(auth):
    key = "1.2.3.4"
    for _ in range(auth.OTP_MAX_FAILS - 1):
        auth.otp_note_failure(key)
    assert auth.otp_lock_remaining(key) == 0


def test_otp_lock_remaining_positive_at_threshold(auth):
    key = "1.2.3.4"
    for _ in range(auth.OTP_MAX_FAILS):
        auth.otp_note_failure(key)
    remaining = auth.otp_lock_remaining(key)
    assert 0 < remaining <= auth.OTP_LOCK_SEC


def test_otp_lock_remaining_counts_down_and_eventually_expires(auth):
    """실패 타임스탬프를 과거로 되돌려 '시간이 흘렀다'를 흉내낸다 — time.time()
    자체를 몽키패치하면 auth 모듈 밖(pytest 내부 등)까지 영향을 주므로,
    내부 상태(_otp_lockout._failures)를 직접 조작하는 쪽이 더 안전하다."""
    key = "9.9.9.9"
    for _ in range(auth.OTP_MAX_FAILS):
        auth.otp_note_failure(key)
    assert auth.otp_lock_remaining(key) > 0

    # 마지막 실패 시각을 잠금 시간(OTP_LOCK_SEC)보다 더 과거로 되돌린다.
    import time
    auth._otp_lockout._failures[key] = [time.time() - auth.OTP_LOCK_SEC - 1] * auth.OTP_MAX_FAILS
    assert auth.otp_lock_remaining(key) == 0


def test_otp_reset_failures_clears_lock_immediately(auth):
    key = "1.1.1.1"
    for _ in range(auth.OTP_MAX_FAILS):
        auth.otp_note_failure(key)
    assert auth.otp_lock_remaining(key) > 0

    auth.otp_reset_failures(key)
    assert auth.otp_lock_remaining(key) == 0
    assert auth.otp_failure_count(key) == 0
