"""로그인 시스템 핵심 회귀 3종 — 공인터넷에 노출되는 유일한 관문이라 깨지면 조용히 뚫린다.

auth.py는 STATE_DIR/DEVICES_PATH/TICKETS_PATH/TOTP_PATH와 VT_AUTH_SESSION_KEY를
**모듈 import 시점**에 읽어 모듈 전역 상수로 고정한다. 그래서 monkeypatch.setenv만으로는
효과가 없고(이미 import가 끝난 뒤라 재평가되지 않는다), 테스트는 그 전역들 자체를
monkeypatch.setattr로 갈아끼워 실제 ~/.vt/ 상태를 절대 건드리지 않는다.

다루는 3가지:
1. HMAC 서명 세션 쿠키 변조 거부 — 위조된 쿠키로 로그인 우회가 되면 인증이 무의미해진다.
2. 기기 revoke가 이미 발급된 세션까지 즉시 죽이는지 — "폰 분실 시 세션까지 함께 무효"라는
   README/CLAUDE.md의 명시적 약속이 실제 코드에서 지켜지는지.
3. 1회용 등록 티켓의 진짜 단발성 — 재전송/재사용으로 티켓이 두 번 먹히면 QR 하나로
   기기가 무한정 등록될 수 있다.
"""

import importlib

import pytest


@pytest.fixture
def auth(tmp_path, monkeypatch):
    """auth 모듈을 가져오되, 상태 파일 경로와 세션 서명키를 tmp_path로 격리."""
    import auth as _auth

    importlib.reload(_auth)  # conftest가 비운 env 기준으로 깨끗하게 재로딩

    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    yield _auth


def test_tampered_hmac_session_rejected(auth):
    """서명된 세션 쿠키를 변조하면 검증에서 반드시 거부돼야 한다."""
    token = auth.make_session(device_id="-")
    assert auth.verify_session(token) is True  # 변조 전엔 유효함을 먼저 확인

    # 1) 서명은 그대로 두고 payload(만료시각)만 바꿔치기 — 세션 연장 위조 시도
    payload_part, sig = token.rsplit(".", 1)
    parts = payload_part.split(".")
    assert parts[0] == "v2"
    forged_payload = f"v2.9999999999.{parts[2]}"
    forged_token = f"{forged_payload}.{sig}"
    assert auth.verify_session(forged_token) is False

    # 2) 서명 문자열 한 글자를 뒤집는 단순 비트플립 변조
    flipped_char = "0" if sig[-1] != "0" else "1"
    flipped_token = f"{payload_part}.{sig[:-1]}{flipped_char}"
    assert auth.verify_session(flipped_token) is False

    # 3) 원본은 여전히 유효해야 한다 (검증 로직 자체가 항상 False를 뱉는 게 아님을 보장)
    assert auth.verify_session(token) is True


def test_revoked_device_invalidates_existing_session(auth):
    """기기를 revoke하면 그 기기로 이미 발급된 세션도 즉시 무효가 돼야 한다."""
    secret, device_id = auth.register_device(label="my-phone")
    assert auth.verify_device(secret) is not None  # 등록 직후엔 유효

    session = auth.make_session(device_id=device_id)
    assert auth.verify_session(session) is True  # revoke 전엔 유효

    removed = auth.revoke_device(device_id)
    assert len(removed) == 1 and removed[0]["id"] == device_id

    # revoke 이후: 같은 세션 토큰이 여전히 유효하면 "폰 분실 시 세션까지 함께 무효"라는
    # 설계 약속이 깨진 것이다.
    assert auth.verify_session(session) is False
    assert auth.verify_device(secret) is None


def test_ticket_single_use(auth):
    """1회용 등록 티켓은 정확히 한 번만 소모돼야 한다."""
    raw = auth.issue_ticket(label="qr-scan")

    first = auth.consume_ticket(raw)
    assert first is not None
    assert first["label"] == "qr-scan"

    second = auth.consume_ticket(raw)
    assert second is None, "같은 티켓이 두 번 소모되면 QR 하나로 기기가 무한 등록된다"
