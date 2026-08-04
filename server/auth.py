"""사람용 웹 로그인 인증 — 비밀번호 해시 + 서명 세션 쿠키 + 기기 등록(OTP 관문).

설계 원칙:
- 비밀번호 원문은 **어디에도 저장하지 않는다.** `VT_AUTH_PASSWORD_HASH`에 scrypt 해시만 둔다.
  파일이 통째로 유출돼도 원문 비밀번호는 복원 불가(단방향 해시).
- 세션 쿠키는 비밀번호가 아니라 `v2.<만료 unix>.<기기id>.<hmac>` 형식의 **만료되는 서명표**다.
  `VT_AUTH_SESSION_KEY`(랜덤, bin/vt가 자동 생성)로 HMAC 서명 → 위조 불가, 만료되면 무효.
  기기를 revoke하면 그 기기로 발급된 세션도 즉시 무효가 된다(별도 세션 저장소 불필요).
- `VT_AUTH_TOKEN`은 데몬용 **기계 토큰**으로 그대로 병존한다. 사람은 비밀번호로,
  기계(clipboard_daemon·tui·hook)는 Bearer 토큰으로 인증 → 하위 호환 유지.

기기 등록 + OTP (2026-08 추가):
- 로그인은 **항상 비밀번호**로 한다. OTP는 "처음 보는 기기를 등록할 때"만 요구하는 관문이다.
- 한 번 등록된 기기는 `vt_device` 장기 쿠키를 갖고, 이후로는 비밀번호만으로 통과한다.
  (폰이 LTE↔wifi를 오가도 안 끊기도록 IP가 아니라 기기 단위로 신뢰한다)
- **OTP는 `vt otp setup`으로 실제 인증 앱을 연동하기 전까지 완전히 비활성**이다.
  미연동 상태에서는 기기가 조용히 등록만 되고 동작은 기존과 100% 동일하다 →
  나중에 OTP를 켜면 지금 쓰던 기기들은 이미 등록돼 있어 잠기지 않는다.
- QR(`vt mobile`)은 상시 토큰 대신 **1회용 기기 등록 티켓**(5분)을 싣는다. QR을 띄우는
  시점에 맥에 대한 물리적 접근이 이미 증명된 것이므로, 스캔 = 기기 등록 승인으로 본다.

상태 파일(모두 ~/.vt/, 0600):
- devices.json : 등록된 기기 목록. 쿠키 원문이 아니라 sha256 해시만 저장한다.
- totp.json    : TOTP 시크릿 + 마지막 성공 카운터(리플레이 차단).
- tickets.json : 1회용 등록 티켓(해시 + 만료).
  ~/.vt.env가 아니라 이쪽에 두는 이유: 서버 재시작 없이 즉시 반영되고,
  vt_env.sh가 관리하는 "설정"과 런타임에 갱신되는 "상태"를 섞지 않기 위해서다.

환경변수(모두 ~/.vt.env, gitignored). 괄호는 하위호환 레거시 이름:
- VT_AUTH_PASSWORD_HASH (= VT_PASSWORD_HASH) : `scrypt$n$r$p$salt_hex$hash_hex`
- VT_AUTH_SESSION_KEY   (= VT_SECRET_KEY)    : 세션 서명용 랜덤 hex
- VT_AUTH_TOKEN         (= VT_TOKEN)         : 기계 토큰(선택)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import time
from pathlib import Path
from typing import Optional

def _env(*names, default=""):
    """새 이름 우선, 없으면 레거시 이름으로 fallback."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default

# 정식 이름은 VT_AUTH_*, 기존 VT_PASSWORD_HASH/VT_SECRET_KEY/VT_TOKEN은 하위호환 fallback.
VT_AUTH_PASSWORD_HASH = _env("VT_AUTH_PASSWORD_HASH", "VT_PASSWORD_HASH")
VT_AUTH_SESSION_KEY = _env("VT_AUTH_SESSION_KEY", "VT_SECRET_KEY")
VT_AUTH_TOKEN = _env("VT_AUTH_TOKEN", "VT_TOKEN")

SESSION_TTL = 86400  # 24h
DEVICE_TTL = 90 * 86400  # 등록 기기 쿠키 수명 90일

# scrypt 파라미터 — 대화형 로그인에 충분하면서 과하지 않은 값
_N, _R, _P, _DKLEN = 16384, 8, 1, 32

# 런타임 상태 디렉토리 (~/.vt). bin/vt도 같은 경로를 쓴다.
STATE_DIR = Path(os.environ.get("VT_STATE_DIR", "") or (Path.home() / ".vt"))
DEVICES_PATH = STATE_DIR / "devices.json"
TOTP_PATH = STATE_DIR / "totp.json"
TICKETS_PATH = STATE_DIR / "tickets.json"

TICKET_TTL = 300  # 1회용 기기 등록 티켓 5분
OTP_MAX_FAILS = 5  # 6자리 코드는 무제한 시도면 실제로 뚫린다 — 잠금은 필수
OTP_LOCK_SEC = 600


# ---------------------------------------------------------------------------
# 상태 파일 I/O (0600 보장 + 원자적 교체)
# ---------------------------------------------------------------------------

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json_secure(path: Path, data) -> None:
    """0600으로 원자적 저장. 디렉토리도 0700으로 맞춘다."""
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

def _sign(payload: str) -> str:
    return hmac.new(
        VT_AUTH_SESSION_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def make_session(device_id: str = "", ttl: int = SESSION_TTL) -> str:
    """로그인 성공 시 발급할 서명 세션 값 생성.

    `v2.<exp>.<device_id|->.<hmac>`. device_id를 서명 안에 넣어두면 기기를 revoke하는
    것만으로 그 기기의 세션까지 함께 죽는다 — 별도 세션 저장소 없이 얻는 revocation.
    서명키가 없으면(레거시) 기존처럼 기계 토큰을 그대로 쿠키로 쓴다.
    """
    if not VT_AUTH_SESSION_KEY:
        return VT_AUTH_TOKEN
    exp = int(time.time()) + ttl
    did = device_id or "-"
    payload = f"v2.{exp}.{did}"
    return f"{payload}.{_sign(payload)}"


def session_device(value: str) -> Optional[str]:
    """세션 쿠키를 검증하고 결부된 기기 id를 반환. 무효면 None.

    반환값 `"-"`는 "유효하지만 기기에 묶이지 않은 세션"(기계 토큰 로그인/레거시 v1).
    """
    if not VT_AUTH_SESSION_KEY or not value:
        return None
    parts = value.split(".")
    try:
        if len(parts) == 4 and parts[0] == "v2":
            _, exp_s, did, sig = parts
            payload = f"v2.{exp_s}.{did}"
        elif len(parts) == 3 and parts[0] == "v1":
            # 기존에 발급된 v1 쿠키 — TTL이 끝날 때까지 그대로 인정(하위 호환).
            _, exp_s, sig = parts
            did = "-"
            payload = f"v1.{exp_s}"
        else:
            return None
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        if int(exp_s) <= int(time.time()):
            return None
    except (ValueError, TypeError):
        return None
    # 기기에 묶인 세션인데 그 기기가 revoke됐으면 무효.
    if did != "-" and not _find_device(did):
        return None
    return did


def verify_session(value: str) -> bool:
    """세션 쿠키 값이 유효한(서명·만료·기기 OK) 세션표인지 검증."""
    return session_device(value) is not None


# ---------------------------------------------------------------------------
# 등록 기기 (화이트리스트)
# ---------------------------------------------------------------------------

def _load_devices() -> list:
    data = _read_json(DEVICES_PATH, {})
    devices = data.get("devices") if isinstance(data, dict) else None
    return devices if isinstance(devices, list) else []


def _save_devices(devices: list) -> None:
    _write_json_secure(DEVICES_PATH, {"version": 1, "devices": devices})


def _find_device(device_id: str) -> Optional[dict]:
    for d in _load_devices():
        if d.get("id") == device_id:
            return d
    return None


def register_device(label: str = "") -> tuple[str, str]:
    """새 기기 등록 → (쿠키에 심을 secret 원문, device_id).

    저장하는 건 sha256 해시뿐이다. devices.json이 통째로 새도 쿠키를 만들어낼 수 없다
    — 비밀번호를 scrypt 해시로만 두는 것과 같은 원칙.
    """
    secret = secrets.token_hex(32)
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    device_id = digest[:16]
    now = int(time.time())
    devices = [d for d in _load_devices() if d.get("id") != device_id]
    devices.append({
        "id": device_id,
        "hash": digest,
        "label": (label or "기기")[:60],
        "added_at": now,
        "last_seen": now,
    })
    _save_devices(devices)
    return secret, device_id


def verify_device(secret: str) -> Optional[dict]:
    """`vt_device` 쿠키 값 → 등록된 기기 레코드. 미등록/만료면 None."""
    if not secret:
        return None
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    now = int(time.time())
    devices = _load_devices()
    for d in devices:
        stored = d.get("hash", "")
        if stored and hmac.compare_digest(stored, digest):
            if now - int(d.get("added_at", now)) > DEVICE_TTL:
                return None
            # last_seen은 하루 단위로만 갱신 — 매 요청 디스크 쓰기를 피한다.
            if now - int(d.get("last_seen", 0)) > 86400:
                d["last_seen"] = now
                try:
                    _save_devices(devices)
                except OSError:
                    pass
            return d
    return None


def list_devices() -> list:
    """등록 기기 목록(해시 제외)."""
    return [
        {k: v for k, v in d.items() if k != "hash"}
        for d in sorted(_load_devices(), key=lambda x: x.get("added_at", 0))
    ]


def revoke_device(prefix: str) -> list:
    """id 접두사로 기기 폐기. 폐기된 기기 목록 반환(해당 기기의 세션도 함께 죽는다)."""
    prefix = (prefix or "").strip().lower()
    if not prefix:
        return []
    devices = _load_devices()
    removed = [d for d in devices if d.get("id", "").startswith(prefix)]
    if removed:
        _save_devices([d for d in devices if d not in removed])
    return [{k: v for k, v in d.items() if k != "hash"} for d in removed]


# ---------------------------------------------------------------------------
# TOTP — 새 기기 등록 관문 (연동 전까지는 완전 비활성)
# ---------------------------------------------------------------------------

def _totp_state() -> dict:
    st = _read_json(TOTP_PATH, {})
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
    _write_json_secure(TOTP_PATH, {
        "secret": secret,
        "enabled": True,
        "last_counter": -1,
        "created_at": int(time.time()),
    })
    return secret


def totp_disable() -> bool:
    """OTP 연동 해제. 이후 새 기기도 비밀번호만으로 등록된다."""
    if not TOTP_PATH.exists():
        return False
    try:
        TOTP_PATH.unlink()
        return True
    except OSError:
        return False


def totp_uri(secret: str, account: str = "", issuer: str = "voice-terminal") -> str:
    """인증 앱 등록용 otpauth:// URI (QR로 뿌린다)."""
    from urllib.parse import quote
    acct = account or (os.environ.get("USER") or "vt")
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
            _write_json_secure(TOTP_PATH, st)
            return True
    return False


# OTP 실패 잠금 — 단일 프로세스 전제(개인용)라 메모리에만 둔다.
_otp_failures: list[float] = []


def otp_lock_remaining() -> int:
    """잠금 중이면 남은 초, 아니면 0."""
    if len(_otp_failures) < OTP_MAX_FAILS:
        return 0
    elapsed = time.time() - _otp_failures[-1]
    return max(0, int(OTP_LOCK_SEC - elapsed))


def otp_note_failure() -> None:
    now = time.time()
    _otp_failures[:] = [t for t in _otp_failures if now - t < OTP_LOCK_SEC]
    _otp_failures.append(now)


def otp_reset_failures() -> None:
    _otp_failures.clear()


# ---------------------------------------------------------------------------
# 1회용 기기 등록 티켓 (QR)
# ---------------------------------------------------------------------------

def _load_tickets() -> list:
    data = _read_json(TICKETS_PATH, {})
    items = data.get("tickets") if isinstance(data, dict) else None
    now = int(time.time())
    return [t for t in (items or []) if int(t.get("exp", 0)) > now]


def issue_ticket(label: str = "") -> str:
    """1회용 기기 등록 티켓 발급 → URL/QR에 실을 원문 반환.

    QR을 띄우는 시점에 맥에 대한 물리적 접근이 이미 증명된 것이므로, 스캔을
    기기 등록 승인으로 인정한다(상시 토큰을 URL에 박는 기존 방식의 대체).
    """
    raw = secrets.token_urlsafe(24)
    tickets = _load_tickets()
    tickets.append({
        "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "exp": int(time.time()) + TICKET_TTL,
        "label": (label or "")[:60],
    })
    _write_json_secure(TICKETS_PATH, {"version": 1, "tickets": tickets})
    return raw


def consume_ticket(raw: str) -> Optional[dict]:
    """티켓 검증 + 즉시 소멸(1회용). 유효하면 티켓 레코드, 아니면 None."""
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    tickets = _load_tickets()
    hit = None
    for t in tickets:
        if hmac.compare_digest(t.get("hash", ""), digest):
            hit = t
            break
    if hit is None:
        return None
    tickets.remove(hit)
    _write_json_secure(TICKETS_PATH, {"version": 1, "tickets": tickets})
    return hit


# ---------------------------------------------------------------------------
# 통합 인증 판정
# ---------------------------------------------------------------------------

def is_protected() -> bool:
    """비밀번호 또는 기계 토큰 중 하나라도 설정돼 있으면 인증 활성."""
    return bool(VT_AUTH_PASSWORD_HASH or VT_AUTH_TOKEN)


def credential_kind(cred: str) -> Optional[str]:
    """제출된 자격증명의 종류 — "password" | "token" | None.

    사람(비밀번호)만 기기로 등록한다. 데몬이 쓰는 기계 토큰은 기기를 만들지 않는다.
    """
    if not cred:
        return None
    if VT_AUTH_PASSWORD_HASH and verify_password(cred, VT_AUTH_PASSWORD_HASH):
        return "password"
    if VT_AUTH_TOKEN and hmac.compare_digest(cred, VT_AUTH_TOKEN):
        return "token"
    return None


def check_credential(cred: str) -> bool:
    """로그인 폼/쿼리로 제출된 자격증명(비밀번호 또는 기계 토큰) 검증."""
    return credential_kind(cred) is not None


def check_request(token: str) -> bool:
    """미들웨어/WS: 요청에 실린 값(세션 쿠키 또는 기계 토큰)으로 인증."""
    if not token:
        return False
    if verify_session(token):
        return True
    if VT_AUTH_TOKEN and hmac.compare_digest(token, VT_AUTH_TOKEN):
        return True
    return False


# ---------------------------------------------------------------------------
# CLI — bin/vt가 서버 없이 직접 호출한다 (python auth.py <cmd>)
# ---------------------------------------------------------------------------

def _cli(argv: list) -> int:
    cmd = argv[0] if argv else "status"
    arg = argv[1] if len(argv) > 1 else ""

    if cmd == "otp-setup":
        secret = totp_new_secret()
        print(json.dumps({"secret": secret, "uri": totp_uri(secret)}, ensure_ascii=False))
        return 0
    if cmd == "otp-status":
        print(json.dumps({"enabled": totp_enabled()}, ensure_ascii=False))
        return 0
    if cmd == "otp-disable":
        print(json.dumps({"disabled": totp_disable()}, ensure_ascii=False))
        return 0
    if cmd == "otp-verify":
        ok = verify_totp(arg)
        print(json.dumps({"ok": ok}, ensure_ascii=False))
        return 0 if ok else 1
    if cmd == "device-list":
        print(json.dumps(list_devices(), ensure_ascii=False))
        return 0
    if cmd == "device-list-pretty":
        devs = list_devices()
        now = time.time()
        print()
        if not devs:
            print("  등록된 기기가 없습니다 (아직 로그인한 기기가 없거나 인증이 꺼져 있음)")
        else:
            print("  📱 등록된 기기")
            print()
            for d in devs:
                age = int((now - d.get("added_at", now)) / 86400)
                seen = int((now - d.get("last_seen", now)) / 86400)
                label = d.get("label", "?")
                print(f"    {d['id'][:8]}  {label:<10} 등록 {age}일 전 / 최근 사용 {seen}일 전")
            print()
            print("  폐기: vt device revoke <id앞자리>")
        print()
        return 0
    if cmd == "device-revoke":
        removed = revoke_device(arg)
        print(json.dumps(removed, ensure_ascii=False))
        return 0 if removed else 1
    if cmd == "ticket-new":
        print(issue_ticket(label=arg))
        return 0
    print(f"unknown command: {cmd}", file=__import__("sys").stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
