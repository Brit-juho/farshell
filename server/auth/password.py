"""비밀번호 해시 — 단방향 scrypt.

**순수 함수다.** 설정도 파일도 안 읽으므로 격리 함정이 없다.
평문은 어디에도 저장되지 않는다 — 저장되는 건 salt를 품은 해시 문자열뿐이라
파일이 통째로 유출돼도 원문은 복원할 수 없다.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import auth

def hash_password(pw: str) -> str:
    """평문 비밀번호 → 저장용 scrypt 해시 문자열. 랜덤 salt 포함(self-describing)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=auth._N, r=auth._R, p=auth._P, dklen=auth._DKLEN)
    return f"scrypt${auth._N}${auth._R}${auth._P}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    """입력 비밀번호가 저장된 해시와 일치하는지 constant-time 비교."""
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            pw.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p),
            dklen=len(hash_hex) // 2,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False
