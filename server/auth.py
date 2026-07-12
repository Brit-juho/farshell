"""사람용 웹 로그인 인증 — 비밀번호 해시 + 서명 세션 쿠키.

설계 원칙:
- 비밀번호 원문은 **어디에도 저장하지 않는다.** `VT_PASSWORD_HASH`에 scrypt 해시만 둔다.
  파일이 통째로 유출돼도 원문 비밀번호는 복원 불가(단방향 해시).
- 세션 쿠키는 비밀번호가 아니라 `v1.<만료 unix>.<hmac>` 형식의 **만료되는 서명표**다.
  `VT_SECRET_KEY`(랜덤, bin/vt가 자동 생성)로 HMAC 서명 → 위조 불가, 만료되면 무효.
- `VT_TOKEN`(기존)은 데몬/QR용 **기계 토큰**으로 그대로 병존한다. 사람은 비밀번호로,
  기계(clipboard_daemon·tui·hook)는 Bearer 토큰으로 인증 → 하위 호환 유지.

환경변수(모두 ~/.vt.env, gitignored):
- VT_PASSWORD_HASH : `scrypt$n$r$p$salt_hex$hash_hex`
- VT_SECRET_KEY    : 세션 서명용 랜덤 hex
- VT_TOKEN         : 기계 토큰(선택)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

VT_PASSWORD_HASH = os.environ.get("VT_PASSWORD_HASH", "")
VT_SECRET_KEY = os.environ.get("VT_SECRET_KEY", "")
VT_TOKEN = os.environ.get("VT_TOKEN", "")

SESSION_TTL = 86400  # 24h

# scrypt 파라미터 — 대화형 로그인에 충분하면서 과하지 않은 값
_N, _R, _P, _DKLEN = 16384, 8, 1, 32


# ---------------------------------------------------------------------------
# 비밀번호 해시 (단방향)
# ---------------------------------------------------------------------------

def hash_password(pw: str) -> str:
    """평문 비밀번호 → 저장용 scrypt 해시 문자열. 랜덤 salt 포함(self-describing)."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


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


# ---------------------------------------------------------------------------
# 서명 세션 쿠키
# ---------------------------------------------------------------------------

def make_session(ttl: int = SESSION_TTL) -> str:
    """로그인 성공 시 발급할 서명 세션 값 생성.

    서명키가 있으면 만료 서명표(`v1.<exp>.<hmac>`)를, 없으면(레거시) 기계 토큰을 반환.
    """
    if VT_SECRET_KEY:
        exp = int(time.time()) + ttl
        payload = f"v1.{exp}"
        sig = hmac.new(VT_SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"
    return VT_TOKEN  # 레거시: 서명키 미설정 시 기존처럼 토큰을 쿠키로


def verify_session(value: str) -> bool:
    """세션 쿠키 값이 유효한(서명·만료 OK) 세션표인지 검증."""
    if not VT_SECRET_KEY or not value:
        return False
    try:
        ver, exp_s, sig = value.split(".")
        payload = f"{ver}.{exp_s}"
        expected = hmac.new(VT_SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return False
        return int(exp_s) > int(time.time())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 통합 인증 판정
# ---------------------------------------------------------------------------

def is_protected() -> bool:
    """비밀번호 또는 기계 토큰 중 하나라도 설정돼 있으면 인증 활성."""
    return bool(VT_PASSWORD_HASH or VT_TOKEN)


def check_credential(cred: str) -> bool:
    """로그인 폼/쿼리로 제출된 자격증명(비밀번호 또는 기계 토큰) 검증."""
    if not cred:
        return False
    if VT_PASSWORD_HASH and verify_password(cred, VT_PASSWORD_HASH):
        return True
    if VT_TOKEN and hmac.compare_digest(cred, VT_TOKEN):
        return True
    return False


def check_request(token: str) -> bool:
    """미들웨어/WS: 요청에 실린 값(세션 쿠키 또는 기계 토큰)으로 인증."""
    if not token:
        return False
    if verify_session(token):
        return True
    if VT_TOKEN and hmac.compare_digest(token, VT_TOKEN):
        return True
    return False
