"""E2E 장기 identity 키(Ed25519) — TOFU-safe MITM 방어 (Fix 1).

검증 대상:
  - load_or_create_identity_key()가 디스크에 영속화되고, 재호출해도 같은 키를 반환한다
    (매번 새로 생성되면 TOFU 핀닝이 무의미해진다).
  - ephemeral 공개키에 대한 서명이 정상 검증된다.
  - ephemeral 공개키가 변조되면 검증이 실패한다(활성 MITM이 다른 공개키를 끼워넣는 상황).
  - new_server_keypair()가 identity_pub/sig를 포함해 반환하고, 그 서명이 실제로
    검증 가능하다 (server/routes/pty.py가 그대로 실어보내는 필드들).
"""

import base64

import pytest

nacl = pytest.importorskip("nacl")

import crypto_channel  # noqa: E402


@pytest.fixture()
def isolated_state_dir(tmp_path, monkeypatch):
    """각 테스트마다 독립된 ~/.vt 대체 경로 + 모듈 캐시(identity 파일 경로) 리셋."""
    state_dir = tmp_path / ".vt"
    monkeypatch.setattr(crypto_channel, "STATE_DIR", state_dir)
    monkeypatch.setattr(crypto_channel, "IDENTITY_KEY_PATH", state_dir / "e2e_identity.json")
    return state_dir


def test_identity_key_persists_across_calls(isolated_state_dir):
    sk1 = crypto_channel.load_or_create_identity_key()
    pub1 = bytes(sk1.verify_key)

    sk2 = crypto_channel.load_or_create_identity_key()
    pub2 = bytes(sk2.verify_key)

    assert pub1 == pub2, "identity 키가 호출마다 재생성되면 TOFU 핀닝이 무의미해진다"
    assert crypto_channel.IDENTITY_KEY_PATH.exists()


def test_identity_key_file_is_secure(isolated_state_dir):
    import os
    import stat

    crypto_channel.load_or_create_identity_key()
    mode = stat.S_IMODE(os.stat(crypto_channel.IDENTITY_KEY_PATH).st_mode)
    assert mode == 0o600


def test_sign_and_verify_ephemeral_pub(isolated_state_dir):
    identity_sk = crypto_channel.load_or_create_identity_key()
    ephemeral_pub = b"\x01" * 32  # X25519 공개키 자리 — 실제 값 여부는 서명 로직과 무관

    sig = crypto_channel.sign_ephemeral_pub(identity_sk, ephemeral_pub)
    identity_pub = bytes(identity_sk.verify_key)

    assert crypto_channel.verify_ephemeral_pub(identity_pub, ephemeral_pub, sig) is True


def test_tampered_ephemeral_pub_fails_verification(isolated_state_dir):
    identity_sk = crypto_channel.load_or_create_identity_key()
    real_pub = b"\x01" * 32
    tampered_pub = b"\x02" * 32  # MITM이 자기 ephemeral 키로 바꿔치기했다고 가정

    sig = crypto_channel.sign_ephemeral_pub(identity_sk, real_pub)
    identity_pub = bytes(identity_sk.verify_key)

    assert crypto_channel.verify_ephemeral_pub(identity_pub, tampered_pub, sig) is False


def test_tampered_signature_fails_verification(isolated_state_dir):
    identity_sk = crypto_channel.load_or_create_identity_key()
    real_pub = b"\x01" * 32
    sig = bytearray(crypto_channel.sign_ephemeral_pub(identity_sk, real_pub))
    sig[0] ^= 0xFF  # 서명 바이트 변조
    identity_pub = bytes(identity_sk.verify_key)

    assert crypto_channel.verify_ephemeral_pub(identity_pub, real_pub, bytes(sig)) is False


def test_wrong_identity_key_fails_verification(isolated_state_dir):
    """다른 서버(다른 identity 키)가 서명한 것처럼 위조 — TOFU가 막아야 하는 핵심 시나리오."""
    identity_sk = crypto_channel.load_or_create_identity_key()
    real_pub = b"\x01" * 32
    sig = crypto_channel.sign_ephemeral_pub(identity_sk, real_pub)

    from nacl.signing import SigningKey
    attacker_sk = SigningKey.generate()
    attacker_pub = bytes(attacker_sk.verify_key)

    assert crypto_channel.verify_ephemeral_pub(attacker_pub, real_pub, sig) is False


def test_new_server_keypair_includes_valid_signature(isolated_state_dir):
    kp = crypto_channel.new_server_keypair()
    assert kp is not None
    assert kp.identity_pub_b64
    assert kp.sig_b64

    ephemeral_pub_bytes = bytes(kp.public)
    identity_pub_bytes = crypto_channel._b64u_dec(kp.identity_pub_b64)
    sig_bytes = crypto_channel._b64u_dec(kp.sig_b64)

    assert crypto_channel.verify_ephemeral_pub(identity_pub_bytes, ephemeral_pub_bytes, sig_bytes) is True


def test_new_server_keypair_identity_pub_stable_across_sessions(isolated_state_dir):
    """ephemeral 키는 세션마다 달라지되(forward secrecy), identity_pub은 안정적이어야
    클라이언트의 TOFU 핀닝이 "같은 서버"를 식별할 수 있다."""
    kp1 = crypto_channel.new_server_keypair()
    kp2 = crypto_channel.new_server_keypair()

    assert kp1.identity_pub_b64 == kp2.identity_pub_b64
    assert kp1.public_b64 != kp2.public_b64
