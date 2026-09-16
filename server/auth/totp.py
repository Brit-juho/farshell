"""TOTP — 새 기기 등록 관문. `~/.vt/totp.json`.

**`fsh otp setup`으로 실제 인증 앱을 연동하기 전까지 완전히 비활성**이다.
미연동 상태에서는 기기가 조용히 등록만 되고 동작은 기존과 100% 동일하다 →
나중에 OTP를 켜면 지금 쓰던 기기들은 이미 등록돼 있어 잠기지 않는다.

마지막 성공 카운터를 저장해 리플레이를 막는다 — 같은 6자리를 두 번 쓰지
못한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time

# ⚠ 경로·설정은 **호출 시점에** `auth.X`로 읽는다. 모듈 상단에서
# `from auth import DEVICES_PATH`로 당겨오면 그 순간 값이 굳어서, 테스트가
# `auth.DEVICES_PATH`를 monkeypatch해도 여기는 계속 진짜 ~/.vt를 본다 —
# **테스트는 통과하면서 사용자의 실제 파일을 건드린다.** 조용히 틀리는
# 종류라 가장 나쁘고, server/tests/test_auth_isolation.py가 이 성질을
# 실제로 확인한다.
import auth
from auth import fileio

def _totp_state() -> dict:
    st = fileio._read_json(auth.TOTP_PATH, {})
    return st if isinstance(st, dict) else {}


def totp_enabled() -> bool:
    """`vt otp setup`으로 실제 연동이 끝났을 때만 True.

    이 값이 False인 동안은 OTP 관련 동작이 전부 우회되고 기존과 동일하게 굴러간다.
    """
    st = _totp_state()
    return bool(st.get("secret")) and st.get("enabled", True) is not False


def totp_new_secret() -> str:
    """새 TOTP 시크릿(base32) 생성 + 저장. 기존 시크릿은 덮어쓴다."""
    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    fileio._write_json_secure(auth.TOTP_PATH, {
        "secret": secret,
        "enabled": True,
        "last_counter": -1,
        "created_at": int(time.time()),
    })
    return secret


def totp_disable() -> bool:
    """OTP 연동 해제. 이후 새 기기도 비밀번호만으로 등록된다."""
    if not auth.TOTP_PATH.exists():
        return False
    try:
        auth.TOTP_PATH.unlink()
        return True
    except OSError:
        return False


def totp_uri(secret: str, account: str = "", issuer: str = "FarShell") -> str:
    """인증 앱 등록용 otpauth:// URI (QR로 뿌린다)."""
    from urllib.parse import quote
    acct = account or (os.environ.get("USER") or "fsh")
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(acct)}"
        f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
    )


def _hotp(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def verify_totp(code: str) -> bool:
    """TOTP 검증 — ±1 스텝(±30초) 허용 + 재사용 차단.

    마지막으로 성공한 카운터를 저장해 같거나 더 오래된 코드를 거부한다.
    어깨너머로 본 코드나 로그에 남은 코드를 그대로 되쓰는 걸 막는다.
    """
    st = _totp_state()
    secret = st.get("secret")
    if not secret:
        return False
    digits = re.sub(r"\D", "", code or "")
    if len(digits) != 6:
        return False
    counter = int(time.time()) // 30
    last = int(st.get("last_counter", -1))
    for offset in (0, -1, 1):
        c = counter + offset
        if c <= last:
            continue  # 이미 쓴 코드 — 리플레이
        if hmac.compare_digest(_hotp(secret, c), digits):
            st["last_counter"] = c
            fileio._write_json_secure(auth.TOTP_PATH, st)
            return True
    return False


# 실패 잠금 — 단일 프로세스 전제(개인용)라 메모리에만 둔다.
#
# D15: 예전엔 OTP 실패를 프로세스 전역 리스트(단일 키)로 추적해서, 한 클라이언트의
# 실패한 시도가 모든 클라이언트의 새 기기 등록까지 함께 잠갔다(가용성 문제 — 스크립트화된
# 재시도나 오설정 클라이언트 하나가 본인 것 아닌 등록까지 막을 수 있었다). 키(보통 클라이언트
# IP)별로 분리해서, 한 클라이언트의 실패가 다른 클라이언트를 잠그지 않게 한다.
