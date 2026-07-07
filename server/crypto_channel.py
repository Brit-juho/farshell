"""E2E Crypto Channel — cloudflared 터널 너머 페이로드 암호화.

설계 (opt-in, 쿼리 ?e2e=1 또는 환경변수 VT_E2E=1로 활성화):
  1. 서버가 세션당 X25519 ephemeral keypair 생성
  2. 서버 공개키를 첫 WebSocket 텍스트로 전송: {"type":"e2e-hello","pub":"<b64u>"}
  3. 클라이언트가 자체 keypair 생성 → {"type":"e2e-ack","pub":"<b64u>"} 응답
  4. 양쪽이 X25519 ECDH (HSalsa20 hash 내장) → 동일한 32바이트 SecretBox 키
  5. 이후 WebSocket 바이트 메시지 전부: [24B nonce | ciphertext+MAC(16)]

서버: PyNaCl Box.shared_key() == 클라(tweetnacl) nacl.box.before()
      → 둘 다 crypto_box_beforenm (HSalsa20 of ECDH 결과) 이라 자동 호환

PyNaCl 미설치 시 is_available() == False, 상위 레이어는 E2E 우회.

한계 (C4): 메시지마다 독립 random nonce라 순서/리플레이 시퀀스 번호가 없다. 따라서
와이어에서 ciphertext chunk를 재정렬하거나 리플레이하면 터미널 출력 순서를 조작할 수
있다(무결성은 SecretBox MAC로 보장, 순서는 아님). backpressure로 chunk를 drop해도
복호화는 안전하다(각 메시지 독립). cloudflare TLS 아래라 실제 위험은 낮지만, TLS 없이
직접 노출하는 경우 이 한계를 인지할 것 — 필요 시 상위에 시퀀스 번호 추가.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from nacl.public import PrivateKey, PublicKey, Box
    from nacl.secret import SecretBox
    _NACL_AVAILABLE = True
except ImportError:
    _NACL_AVAILABLE = False
    logger.debug("PyNaCl 미설치 — E2E 비활성")


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

    @property
    def public_b64(self) -> str:
        return _b64u_enc(bytes(self.public))


def new_server_keypair() -> Optional[ServerKeypair]:
    if not _NACL_AVAILABLE:
        return None
    sk = PrivateKey.generate()
    return ServerKeypair(private=sk, public=sk.public_key)


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
