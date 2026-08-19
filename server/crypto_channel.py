"""E2E Crypto Channel — cloudflared 터널 너머 페이로드 암호화.

설계 (opt-in, 쿼리 ?e2e=1 또는 환경변수 VT_E2E=1로 활성화):
  1. 서버가 세션당 X25519 ephemeral keypair 생성 (forward secrecy 목적 — 매 세션
     새로 생성되며, 이 동작은 그대로 유지한다)
  2. 서버가 장기 Ed25519 identity 키(최초 1회 생성, 디스크에 영구 보관)로 방금
     만든 ephemeral 공개키에 서명한다
  3. 서버 공개키+identity 공개키+서명을 첫 WebSocket 텍스트로 전송:
     {"type":"e2e-hello","pub":"<ephemeral_pub_b64>","identity_pub":"<identity_pub_b64>","sig":"<sig_b64>"}
  4. 클라이언트가 자체 keypair 생성 → {"type":"e2e-ack","pub":"<b64u>"} 응답
  5. 양쪽이 X25519 ECDH (HSalsa20 hash 내장) → 동일한 32바이트 SecretBox 키
  6. 이후 WebSocket 바이트 메시지 전부: [24B nonce | ciphertext+MAC(16)]

서버: PyNaCl Box.shared_key() == 클라(tweetnacl) nacl.box.before()
      → 둘 다 crypto_box_beforenm (HSalsa20 of ECDH 결과) 이라 자동 호환

PyNaCl 미설치 시 is_available() == False, 상위 레이어는 E2E 우회.

위협 모델 (identity 키 도입 후):
  - "cloudflared가 평문을 볼 수 없다"는 주장은 어디까지나 수동 관찰자를 막을 뿐,
    터널 경로에 올라앉은 능동 MITM(가짜 ephemeral 공개키를 끼워넣는 공격)은 막지
    못했다. 장기 Ed25519 identity 서명 + 클라이언트 TOFU 핀닝을 더해 이 능동 MITM도
    방어한다 — 단, **최초 접속(TOFU 순간)이 안전하다는 전제**가 성립할 때에 한한다.
    최초 접속 자체가 이미 MITM 경유였다면 그 가짜 identity_pub이 그대로 핀에 저장돼
    이후로도 통과한다(TOFU의 근본적 한계, 여기서 새로 만든 결함이 아니다).
  - identity 키는 세션 간 안정적이다(ephemeral 키와 달리 서버 재시작에도 동일) —
    클라이언트는 이 안정성을 이용해 "같은 서버인지"를 검증한다. identity_pub이
    이전과 달라지면(키 파일 삭제/서버 재설치/실제 MITM) 클라이언트는 경고하고
    사용자의 명시적 재신뢰 없이는 진행하지 않는다.

한계 (C4): 메시지마다 독립 random nonce라 순서/리플레이 시퀀스 번호가 없다. 따라서
와이어에서 ciphertext chunk를 재정렬하거나 리플레이하면 터미널 출력 순서를 조작할 수
있다(무결성은 SecretBox MAC로 보장, 순서는 아님). backpressure로 chunk를 drop해도
복호화는 안전하다(각 메시지 독립). cloudflare TLS 아래라 실제 위험은 낮지만, TLS 없이
직접 노출하는 경우 이 한계를 인지할 것 — 필요 시 상위에 시퀀스 번호 추가.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from nacl.public import PrivateKey, PublicKey, Box
    from nacl.secret import SecretBox
    from nacl.signing import SigningKey, VerifyKey
    from nacl.exceptions import BadSignatureError
    _NACL_AVAILABLE = True
except ImportError:
    _NACL_AVAILABLE = False
    logger.debug("PyNaCl 미설치 — E2E 비활성")

# 장기 identity 키 저장 경로. auth.py의 STATE_DIR과 동일한 ~/.vt 아래에 둔다
# (bin/vt·server가 공유하는 런타임 상태 디렉토리 관례를 따름).
STATE_DIR = Path(os.environ.get("VT_STATE_DIR", "") or (Path.home() / ".vt"))
IDENTITY_KEY_PATH = STATE_DIR / "e2e_identity.json"


def _write_json_secure(path: Path, data) -> None:
    """0600으로 원자적 저장. 디렉토리도 0700으로 맞춘다 (auth.py의 동일 패턴 재사용)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(str(tmp), str(path))


def load_or_create_identity_key() -> "SigningKey":
    """서버 장기 Ed25519 identity 서명키. 없으면 생성 후 0600으로 영구 저장, 있으면 로드.

    이 키는 ephemeral X25519 키(세션마다 재생성)와 달리 서버 재시작·재접속 사이에도
    동일하게 유지돼야 클라이언트의 TOFU 핀닝이 의미를 가진다.
    """
    if not _NACL_AVAILABLE:
        raise RuntimeError("PyNaCl 미설치")
    try:
        data = json.loads(IDENTITY_KEY_PATH.read_text(encoding="utf-8"))
        seed = base64.b64decode(data["seed_b64"])
        return SigningKey(seed)
    except (OSError, ValueError, KeyError):
        pass
    sk = SigningKey.generate()
    seed_b64 = base64.b64encode(bytes(sk)).decode()
    _write_json_secure(IDENTITY_KEY_PATH, {"version": 1, "seed_b64": seed_b64})
    return sk


def sign_ephemeral_pub(identity_sk: "SigningKey", ephemeral_pub_bytes: bytes) -> bytes:
    """장기 identity 개인키로 ephemeral 공개키 바이트에 서명. 서명(64B)만 반환."""
    return identity_sk.sign(ephemeral_pub_bytes).signature


def verify_ephemeral_pub(identity_pub_bytes: bytes, ephemeral_pub_bytes: bytes, sig_bytes: bytes) -> bool:
    """identity 공개키로 서명을 검증. 유효하면 True, 위/변조 시 False (예외를 던지지 않음)."""
    if not _NACL_AVAILABLE:
        return False
    try:
        VerifyKey(identity_pub_bytes).verify(ephemeral_pub_bytes, sig_bytes)
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def is_available() -> bool:
    return _NACL_AVAILABLE


def is_enabled() -> bool:
    """환경변수로 opt-in (기본 OFF)."""
    return _NACL_AVAILABLE and os.environ.get("VT_E2E", "").strip() in ("1", "true", "yes")


def _b64u_enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


@dataclass
class ServerKeypair:
    private: "PrivateKey"
    public: "PublicKey"
    identity_pub_b64: str
    sig_b64: str

    @property
    def public_b64(self) -> str:
        return _b64u_enc(bytes(self.public))


def new_server_keypair() -> Optional[ServerKeypair]:
    """세션마다 새 ephemeral X25519 키쌍(forward secrecy) + 장기 identity 서명.

    identity 키 자체는 세션마다 재생성하지 않는다 — load_or_create_identity_key()가
    디스크에서 로드하거나 없으면 최초 1회만 생성한다.
    """
    if not _NACL_AVAILABLE:
        return None
    sk = PrivateKey.generate()
    identity_sk = load_or_create_identity_key()
    ephemeral_pub_bytes = bytes(sk.public_key)
    sig = sign_ephemeral_pub(identity_sk, ephemeral_pub_bytes)
    identity_pub_bytes = bytes(identity_sk.verify_key)
    return ServerKeypair(
        private=sk,
        public=sk.public_key,
        identity_pub_b64=_b64u_enc(identity_pub_bytes),
        sig_b64=_b64u_enc(sig),
    )


@dataclass
class Channel:
    """X25519 ECDH로 도출한 대칭키로 SecretBox 암복호화."""
    box: "SecretBox"

    @classmethod
    def derive(cls, server_sk: "PrivateKey", client_pub_b64: str) -> "Channel":
        if not _NACL_AVAILABLE:
            raise RuntimeError("PyNaCl 미설치")
        client_pub = PublicKey(_b64u_dec(client_pub_b64))
        # Box.shared_key() → crypto_box_beforenm (HSalsa20 of scalarmult)
        # tweetnacl의 nacl.box.before()와 동일한 32바이트 결과
        shared = Box(server_sk, client_pub).shared_key()
        return cls(box=SecretBox(shared))

    def encrypt_simple(self, plaintext: bytes) -> bytes:
        """SecretBox.encrypt() 결과: [nonce(24) | ciphertext+mac(16)]."""
        return bytes(self.box.encrypt(plaintext))

    def decrypt(self, wire: bytes) -> bytes:
        """[nonce(24) | ciphertext+mac] 복호화."""
        return self.box.decrypt(wire)
