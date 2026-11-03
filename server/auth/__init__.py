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

# require_elevated가 FastAPI 의존성으로 쓰일 때, 파라미터에 실제 타입이 있어야
# FastAPI가 이걸 "쿼리 파라미터"가 아니라 Request 객체 주입으로 인식한다
# (`from __future__ import annotations`로 문자열 annotation이 되므로,
# get_type_hints가 이 이름을 이 모듈 전역에서 찾을 수 있어야 함).
# auth는 서버 없이도 `python -m auth <cmd>`로 단독 실행되지만, 그 CLI 경로는
# require_elevated를 호출하지 않으므로 starlette 미설치 환경에서도 문제없다 —
# 다만 이 프로젝트는 fastapi/starlette가 항상 설치돼 있는 걸 전제한다(requirements-core.txt).
from starlette.requests import Request

import vt_paths
from i18n import tr

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
# 로그인 화면이 보낼 수 있는 "기기 기억" 값 → 수명(초). 이 표에 없는 값은 400으로 거절한다
# — 클라이언트가 숫자를 직접 보내게 하면 DEVICE_TTL보다 긴 수명을 만들 길이 생긴다.
REMEMBER_TTLS = {"90d": DEVICE_TTL, "24h": 86400, "1h": 3600}
REMEMBER_DEFAULT = "90d"
TICKET_TEMP_DEVICE_TTL = 86400  # `fsh mobile --temp`로 등록되는 기기의 수명(1일 고정)
DEVICE_EXPIRED_GRACE = 7 * 86400  # 만료된 기기를 목록에 "만료"로 남겨 두는 기간
ELEVATION_TTL = 900  # 승격 세션(N31) 수명 15분 — git 쓰기 등 위험 조작 직전 재확인용

# scrypt 파라미터 — 대화형 로그인에 충분하면서 과하지 않은 값
_N, _R, _P, _DKLEN = 16384, 8, 1, 32

# 런타임 상태 디렉토리 (~/.vt). bin/vt도 같은 경로를 쓴다.
#
# P2-4(ADR-41) — 예전엔 `Path(os.environ.get("VT_STATE_DIR", "") or (Path.home()
# / ".vt"))`였다. `VT_STATE_DIR=""`이면 폴백이 안 걸려 빈 문자열이 그대로
# `Path("")`(서버 CWD)가 됐고, `VT_STATE_DIR="~/vt-test"`는 `~`를 안 펴서
# 리터럴 `~` 디렉터리가 됐다 — 인증 저장소(기기·OTP·티켓)만 다른 15곳과
# 갈라지는 사고였다. 값은 여기서 **한 번**
# 계산하고 그 뒤로는 그대로 쓴다 — 아래 세 파생 경로처럼 이 모듈 attribute를
# 통해서만 읽히고(`devices.py` 등은 `auth.STATE_DIR`로 매번 다시 읽는다),
# 테스트는 env가 아니라 이 attribute 자체를 monkeypatch한다
# (test_auth_isolation.py) — 그 계약은 그대로 둔다.
STATE_DIR = vt_paths.state_dir()
DEVICES_PATH = STATE_DIR / "devices.json"
TOTP_PATH = STATE_DIR / "totp.json"
TICKETS_PATH = STATE_DIR / "tickets.json"

TICKET_TTL = 300  # 1회용 기기 등록 티켓 5분
OTP_MAX_FAILS = 5  # 6자리 코드는 무제한 시도면 실제로 뚫린다 — 잠금은 필수
OTP_LOCK_SEC = 600
PASSWORD_MAX_FAILS = 5  # D14: scrypt 자체가 시도당 비용을 부과하지만, 무제한 시도 자체는 별개 문제
PASSWORD_LOCK_SEC = 600  # OTP와 동일한 정책 — 같은 파일 안에서 자격증명 종류별로 위협모델을 다르게 다루지 않는다
# 등록되지 않은 기기의 로그인 실패 전체 상한(출처 무관): 1분 20회, 1시간 30회. 키별 잠금(5회/10분)은 출처를
# 바꿔 가며(IP 순환) 우회되므로 그 위의 총량이다. 등록된 기기는 빠진다(lockout.py). 1분 값은 출처별 preauth
# 한도(1분 10회)보다 커야 한다 — 같으면 출처 하나가 전체를 막는다.
NEW_DEVICE_FAIL_WINDOWS = ((60, 20), (3600, 30))

# 잠금 상태를 추적할 키(IP)가 무한정 늘어나는 걸 막는 상한. 스크립트가 IP를 계속 바꿔가며
# 때리는 극단적 경우에도 메모리가 무한 증가하진 않게 한다(개인용 단일 프로세스 전제).
_MAX_LOCKOUT_KEYS = 1000

# ---------------------------------------------------------------------------
# 하위 모듈 — **설정 상수 정의가 끝난 뒤에 가져온다.**
# ---------------------------------------------------------------------------
#
# 774줄 한 파일이던 것을 네 하위 도메인(비밀번호·기기등록·TOTP·티켓)과
# 공통 조각(파일 I/O·잠금)으로 갈랐다. 서로 얽혀 있는 핵심(세션 서명·통합
# 인증 판정·WS 워치독·CLI)은 여기 남는다 — 그 넷은 서로를 직접 부르므로
# 억지로 떼면 모듈 경계가 호출 그래프를 가로지르기만 한다.
#
# ⚠ **순서가 계약이다.** 하위 모듈은 `import auth` 후 `auth.DEVICES_PATH`처럼
# 호출 시점에 읽지만, lockout.py만은 모듈 평가 시점에 `auth.OTP_MAX_FAILS`를
# 읽어 잠금 인스턴스를 만든다. 그래서 상수가 **위에서 이미 정의돼 있어야**
# 한다. 이 import 블록을 파일 위쪽으로 올리면 AttributeError가 난다.
#
# ⚠ 경로를 `from auth import DEVICES_PATH`로 당겨가면 안 된다 — 값이 굳어서
# 테스트의 monkeypatch가 안 먹고, **테스트가 통과하면서 사용자의 실제 ~/.vt를
# 건드린다.** server/tests/test_auth_isolation.py가 그 성질을 확인한다.
from auth import fileio  # noqa: E402
from auth.fileio import _read_json, _write_json_secure  # noqa: E402,F401
from auth.password import hash_password, verify_password  # noqa: E402
from auth.lockout import (  # noqa: E402,F401
    _KeyedLockout,
    # 잠금 인스턴스 자체도 노출한다 — 테스트가 내부 상태를 직접 조작한다.
    # **같은 객체**를 가리키므로 `auth._otp_lockout._failures[...] = …` 같은
    # 변형은 그대로 먹는다. 다만 `setattr(auth, "_otp_lockout", …)`처럼
    # **다시 묶는** 건 안 먹는다 — 그럴 땐 lockout.reset_all()을 쓸 것.
    _otp_lockout,
    _password_lockout,
    new_device_lock_remaining,
    new_device_note_failure,
    reset_all as _reset_lockouts,
    otp_failure_count,
    otp_lock_remaining,
    otp_note_failure,
    otp_reset_failures,
    password_lock_remaining,
    password_note_failure,
    password_reset_failures,
)
from auth.devices import (  # noqa: E402,F401
    _find_device,
    _load_devices,
    _save_devices,
    device_remaining,
    find_alive_device,
    list_devices,
    prune_devices,
    register_device,
    rename_device,
    revoke_device,
    verify_device,
)
from auth.totp import (  # noqa: E402,F401
    _hotp,
    _totp_state,
    totp_disable,
    totp_enabled,
    totp_new_secret,
    totp_uri,
    verify_totp,
)
from auth.tickets import (  # noqa: E402,F401
    PURPOSE_DEVICE,
    PURPOSE_PORT_SHARE,
    _load_tickets,
    consume_ticket,
    issue_ticket,
)
from auth.client_info import request_meta  # noqa: E402,F401

# ---------------------------------------------------------------------------
# 서명 세션 쿠키
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    return hmac.new(
        VT_AUTH_SESSION_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def sign_payload(payload: str) -> str:
    """`_sign`의 공개 진입점 — 세션 쿠키 서명과 같은 키를 다른 모듈(routes/share.py의
    공유 링크 토큰, 50-files-share.md §3)이 재사용할 때 밑줄 붙은 내부 이름을
    직접 건드리지 않도록 한다."""
    return _sign(payload)


def make_session(device_id: str = "", ttl: int = SESSION_TTL, elev_exp: int | None = None) -> str:
    """로그인 성공 시(또는 승격 시) 발급할 서명 세션 값 생성.

    기본(elev_exp 없음): `v2.<exp>.<device_id|->.<hmac>`.
    승격 포함(N31, `POST /api/auth/elevate` 성공 시): `v3.<exp>.<device_id|->.<elevExp>.<hmac>`.
    device_id를 서명 안에 넣어두면 기기를 revoke하는 것만으로 그 기기의 세션까지 함께
    죽는다 — 별도 세션 저장소 없이 얻는 revocation. elevExp도 같은 서명 안에 있어
    위조로 승격을 연장할 수 없다. 서명키가 없으면(레거시) 기존처럼 기계 토큰을 그대로
    쿠키로 쓴다(이 경우 승격 개념 자체가 없다 — 호출부에서 걸러야 함).
    """
    if not VT_AUTH_SESSION_KEY:
        return VT_AUTH_TOKEN
    exp = int(time.time()) + ttl
    did = device_id or "-"
    if elev_exp is not None:
        payload = f"v3.{exp}.{did}.{int(elev_exp)}"
    else:
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
        if len(parts) == 5 and parts[0] == "v3":
            # 승격 클레임 포함 쿠키 — 기본 인증 판정에서는 elev 부분을 무시한다
            # (elev 검증은 session_elevated_until이 별도로 담당).
            _, exp_s, did, elev_s, sig = parts
            payload = f"v3.{exp_s}.{did}.{elev_s}"
        elif len(parts) == 4 and parts[0] == "v2":
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
    # 기기에 묶인 세션인데 그 기기가 revoke됐거나 만료됐으면 무효. 예전엔 존재 여부만
    # 봐서, 기기가 만료돼도 그 전에 받은 세션이 최대 SESSION_TTL(24h) 더 살았다 —
    # 1시간짜리 임시 기기에서는 수명이 사실상 24배가 되는 틈이다. WS는
    # spawn_session_watchdog이 같은 check_request를 다시 돌려 닫는다.
    if did != "-" and not find_alive_device(did):
        return None
    return did


def session_signed(value: str) -> bool:
    """세션 쿠키가 **서명은 맞는가**(만료·기기 회수와 무관). 서명이 맞는 만료/회수 쿠키는 위조가 아니라 낡은 것이다 —
    사전 인증 실패 한도(`authz/preauth.py`)가 추측으로 세지 않으려고 쓴다."""
    if not VT_AUTH_SESSION_KEY or not value:
        return False
    parts = value.split(".")
    try:
        if len(parts) == 5 and parts[0] == "v3":
            payload, sig = ".".join(parts[:4]), parts[4]
        elif len(parts) == 4 and parts[0] == "v2":
            payload, sig = ".".join(parts[:3]), parts[3]
        elif len(parts) == 3 and parts[0] == "v1":
            payload, sig = ".".join(parts[:2]), parts[2]
        else:
            return False
        return hmac.compare_digest(_sign(payload), sig)
    except (ValueError, TypeError):
        return False


def verify_session(value: str) -> bool:
    """세션 쿠키 값이 유효한(서명·만료·기기 OK) 세션표인지 검증."""
    return session_device(value) is not None


def session_elevated_until(value: str) -> int:
    """세션 쿠키의 승격(elev) 클레임 만료 unix 시각. 없거나 무효면 0.

    v3 형식만 승격 클레임을 갖는다. 서명·기본 세션 만료·기기 revoke 여부까지 전부
    다시 검증한다 — 승격 여부만 따로 신뢰하고 기본 세션 유효성을 건너뛰면 만료된
    세션에 승격만 살아있는 상태가 생길 수 있다.
    """
    if not VT_AUTH_SESSION_KEY or not value:
        return 0
    parts = value.split(".")
    if len(parts) != 5 or parts[0] != "v3":
        return 0
    _, exp_s, did, elev_s, sig = parts
    payload = f"v3.{exp_s}.{did}.{elev_s}"
    try:
        if not hmac.compare_digest(_sign(payload), sig):
            return 0
        exp = int(exp_s)
        elev = int(elev_s)
    except (ValueError, TypeError):
        return 0
    now = int(time.time())
    if exp <= now:
        return 0
    if did != "-" and not find_alive_device(did):
        return 0
    if elev <= now:
        return 0
    return elev


def require_elevated(request: Request) -> None:
    """git 쓰기류 라우터에 `APIRouter(dependencies=[Depends(require_elevated)])`로 묶어 쓴다
    (R4 — 핸들러마다 개별로 붙이면 하나라도 빠뜨릴 수 있다).

    표준 `starlette.exceptions.HTTPException`을 던진다(전용 예외 클래스를 새로 만들지
    않는다) — 테스트가 `importlib.reload(auth)`로 이 모듈을 다시 로드하면 이 모듈에서
    정의한 클래스는 매번 새 클래스 객체가 되어, main.py가 처음 import 시점에 등록해둔
    `@app.exception_handler(그 옛날 클래스)`가 더 이상 같은 클래스로 안 잡히는 사고가
    난다(실제로 겪음 — 전체 스위트에서만 재현되고 파일 단독 실행에선 재현 안 됨).
    HTTPException은 이 모듈 밖에서 정의돼 reload로 흔들리지 않는다. `detail`을
    dict로 주면 main.py의 전역 핸들러가 `{"error": ...}` 형태 그대로 펼쳐 응답한다.

    정책(40-dock-git.md §2):
    - 비밀번호 자체가 설정 안 된 환경 → 승격 개념 없음 → 통과. 단 `VT_NETWORK_MODE=all`이면
      공개 터널 + 무인증 + 쓰기 조합을 막기 위해 403 password_required.
    - 비밀번호가 설정된 환경 → 세션에 유효한(만료 안 된) elev 클레임이 있어야 통과,
      없으면 401 elevation_required(클라이언트가 비밀번호 다이얼로그를 띄우고 재시도).
    """
    from starlette.exceptions import HTTPException

    if not VT_AUTH_PASSWORD_HASH:
        mode = os.environ.get("VT_NETWORK_MODE", "all").strip().lower()
        if mode == "all":
            raise HTTPException(status_code=403, detail={"error": "password_required"})
        return
    token = request.cookies.get("vt_session", "")
    if session_elevated_until(token) <= int(time.time()):
        raise HTTPException(status_code=401, detail={"error": "elevation_required"})

# ---------------------------------------------------------------------------
# 통합 인증 판정
# ---------------------------------------------------------------------------

def is_protected() -> bool:
    """비밀번호 또는 기계 토큰 중 하나라도 설정돼 있으면 인증 활성."""
    return bool(VT_AUTH_PASSWORD_HASH or VT_AUTH_TOKEN)


def is_https(request: Request) -> bool:
    """터널 뒤에서도 정확한 https 판정 — 쿠키 Secure 플래그 판정에 쓴다.

    cloudflared가 TLS를 종단하고 서버에는 평문 HTTP로 전달하므로 request.url.scheme은
    항상 http다 → 예전엔 원격 접속에서 세션 쿠키에 Secure가 **한 번도** 붙지 않았다.
    X-Forwarded-Proto를 믿어도 안전하다: 이 헤더로 할 수 있는 건 쿠키를 더 엄격하게
    만드는 것뿐이고, 약화시키는 방향은 불가능하다. main.py(로그인)와 routes/share.py
    (공유 다운로드 쿠키)가 함께 쓴다 — 원래 main.py에만 있던 것을 여기로 옮겼다.
    """
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https"


def credential_kind(cred: str) -> Optional[str]:
    """로그인 폼에 제출된 자격증명의 종류 — "password" | None.

    **기계 토큰은 여기서 받지 않는다(2026-09-17).** 예전에는 비밀번호 검증에
    실패하면 `VT_AUTH_TOKEN`과 한 번 더 비교해서, 그 값을 비밀번호 칸에 그대로
    쳐도 로그인이 됐다. 그 결과 사람이 쓰는 비밀번호가 **둘**이 되는데, 둘째 값은
    `fsh password`로 바뀌지 않고 수명도 없어서 "비밀번호를 바꿨는데 예전 값으로
    계속 들어가진다"가 실제로 벌어졌다. 기계 토큰의 쓰임새는 데몬·훅이지
    로그인 화면이 아니다 — 그 경로(`Authorization: Bearer` / `?token=`)는
    check_request가 그대로 받으므로 clipboard_daemon·tui·훅은 영향이 없다.

    영향받는 것은 **레거시 `?token=` 링크의 쿠키 교환**뿐이다
    (frontend/js/core/env.js). 교환이 401로 실패하면 그 링크는 쿠키 없이 쿼리
    토큰으로 계속 동작한다 — 접속이 끊기지는 않지만 토큰이 URL에 남으므로,
    기기 등록은 `fsh mobile`의 1회용 티켓으로 넘어가는 것이 옳다.
    """
    if not cred:
        return None
    if VT_AUTH_PASSWORD_HASH and verify_password(cred, VT_AUTH_PASSWORD_HASH):
        return "password"
    return None


def check_credential(cred: str) -> bool:
    """로그인 폼/쿼리로 제출된 자격증명(비밀번호 또는 기계 토큰) 검증."""
    return credential_kind(cred) is not None


# 호출자 종류 (2026-10-06, 에이전트 세션 공유) -----------------------------------
# 같은 서버가 세 종류의 호출자를 받는다: 브라우저 로그인 세션(사람), 기계 토큰(데몬·훅·에이전트의
# MCP), peer 서명(다른 맥). "에이전트가 자기 접근을 스스로 승인"하는 길을 막으려면 **사람만 할 수
# 있는 일**을 가려낼 수단이 필요하다 — 기계 토큰은 에이전트가 환경·`~/.vt.env`에서 읽을 수 있기
# 때문에 "인증됐다"는 사람이라는 뜻이 아니다. 여기서 가른다(peer 서명 경로 `/api/peer/`는 다른
# 미들웨어 예외로 처리되고 이 함수는 거기서 쓰이지 않는다 — 사람 전용 라우트는 그 경로에 없다).
AUTH_SESSION = "session"   # 서명 세션 쿠키(사람이 비밀번호로 로그인했다)
AUTH_MACHINE = "machine"   # 기계 토큰(Bearer / ?token= / 쿠키로 실린 토큰)
AUTH_NONE = "none"         # 인증 수단 없음(또는 무효)


def _cookie_may_be_human() -> bool:
    """서명 쿠키를 사람 세션으로 칠 수 있는가. 비밀번호가 있으면 언제나. 비밀번호 없이 토큰만 있는 서버는 사람이 로그인할 방법이
    없으므로, `VT_AUTHZ_ENFORCE=legacy` 가 켜지면 서명 쿠키도 사람이 아니다(H14). 꺼짐이면 옛 판정 그대로."""
    if VT_AUTH_PASSWORD_HASH:
        return True
    from authz import legacy_gate
    return not legacy_gate.active()


def request_kind(request) -> str:
    """요청의 인증 종류 — `session` | `machine` | `none`.

    `session`은 **서명 세션 쿠키만**이고, `Authorization`·`?token=`이 하나라도 같이 실려 오면
    사람의 브라우저 요청이 아니라고 보아 `session`으로 치지 않는다(브라우저는 둘 다 안 보낸다).
    서명키가 없는 레거시 환경에서는 쿠키 값이 기계 토큰 그대로라 세션이 존재할 수 없다 → `session` 불가.
    """
    cookie = request.cookies.get("vt_session", "")
    has_header_cred = bool(request.headers.get("authorization")) or bool(request.query_params.get("token"))
    if cookie and not has_header_cred and session_device(cookie) is not None and _cookie_may_be_human():
        return AUTH_SESSION
    if VT_AUTH_TOKEN:
        authz = request.headers.get("authorization", "")
        for cand in (authz[7:] if authz.startswith("Bearer ") else "",
                     request.query_params.get("token", ""), cookie):
            if cand and hmac.compare_digest(cand, VT_AUTH_TOKEN):
                return AUTH_MACHINE
    return AUTH_NONE


def require_human_session(request: Request) -> None:
    """사람(브라우저 로그인 세션)만 통과하는 FastAPI 의존성 — 에이전트 접근 승인·공유·철회용.

    기계 토큰·peer 서명·무인증은 전부 403 `human_session_required`. 비밀번호가 설정되지 않은
    서버(로그인 자체가 없다)도 403이다 — 그 환경에서는 "사람"을 증명할 방법이 없다.
    """
    from starlette.exceptions import HTTPException

    if request_kind(request) != AUTH_SESSION:
        raise HTTPException(status_code=403, detail={
            "error": "human_session_required",
            "reason": tr("이 작업은 브라우저에서 로그인한 사람만 할 수 있습니다 (기계 토큰으로는 불가)")})


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
# WS 세션 만료 재검사 (실사용 중 발견 — 2026-09-11)
# ---------------------------------------------------------------------------
# HTTP 요청은 TokenAuthMiddleware가 매번 `check_request`로 재검사하지만, WS는
# ASGI scope["type"] != "http"라 그 미들웨어를 아예 안 탄다 — 라우트 핸들러가
# 핸드셰이크 시점에 딱 한 번만 `_ws_auth`로 검사한다. 그래서 브라우저 탭을
# 새로고침 안 하고 24시간(SESSION_TTL)을 넘기면, 세션 쿠키는 이미 만료됐는데도
# 그 전에 열어둔 WS(터미널 포함)는 서버 재시작 전까지 영원히 살아있었다 —
# 24시간 세션 TTL이 "새로고침 안 하면 사실상 무제한"이 되는 구멍이었다.
# 새 REST 요청은 401을 제대로 받지만 화면 일부만 깨지고 터미널 조작은 계속
# 되는 게 바로 이 증상(실사용자 보고로 재현).
#
# 해결: 핸드셰이크 때 인증에 쓰인 토큰 문자열을 쥐고 있다가, 연결이 살아있는
# 동안 주기적으로 `check_request`를 다시 돌린다 — 세션 만료는 서명 안에 든
# exp 타임스탬프 재검사라 상태 저장 없이 그냥 다시 부르면 된다. 실패하면 WS를
# 4001(핸드셰이크 인증 실패와 같은 코드)로 닫는다 — 프런트(`term/ws.js`)가
# 이미 4001을 "재연결 안 함, 탭을 새로 열라" 영구 실패로 처리하고 있어서
# 프런트를 따로 안 고쳐도 된다.
def spawn_session_watchdog(ws, token: str, interval: float = 60.0):
    """WS 연결 동안 주기적으로 `token`이 아직 유효한지 재검사하는 백그라운드
    태스크를 만들어 반환한다. 호출부는 연결 종료 시(finally) 반드시
    `task.cancel()`해야 한다 — 안 그러면 연결이 끊긴 뒤에도 태스크가 남는다.

    `token`이 빈 문자열이면(비밀번호/기계 토큰 자체가 없는 로컬 전용 환경 —
    `is_protected()`가 False일 때 `_ws_auth`류가 그렇게 반환한다) 재검사할
    대상이 없으므로 아무 것도 안 하는 태스크를 반환한다.
    """
    import asyncio

    async def _loop():
        if not token:
            return
        while True:
            await asyncio.sleep(interval)
            if not check_request(token):
                try:
                    await ws.close(code=4001, reason="session_expired")
                except Exception:
                    pass
                return

    return asyncio.create_task(_loop())

# ---------------------------------------------------------------------------
# CLI — bin/fsh가 서버 없이 직접 호출한다 (python -m auth <cmd>). 진입점은 __main__.py
# ---------------------------------------------------------------------------

def _via_label(key: str) -> str:
    return {"tunnel": tr("터널"), "tailscale": "Tailscale", "lan": "LAN", "local": tr("이 맥"),
            "direct": tr("직접")}.get(key, "")


def _method_label(key: str) -> str:
    return {"password": tr("비밀번호"), "password+otp": tr("비밀번호+OTP"), "ticket": "QR"}.get(key, "")


def _span(sec: int) -> str:
    sec = max(0, int(sec))
    if sec < 3600:
        return tr("{n}분", n=max(1, sec // 60))
    if sec < 86400:
        return tr("{n}시간", n=sec // 3600)
    return tr("{n}일", n=sec // 86400)


def _ago(sec: int) -> str:
    return tr("방금") if sec < 60 else tr("{span} 전", span=_span(sec))


def _device_line(d: dict, now: int) -> str:
    """`fsh device list`의 첫 줄 — id, 라벨, 등록/최근 사용, 남은 기간."""
    added = int(d.get("added_at", now) or now)
    seen = int(d.get("last_seen", now) or now)
    exp = int(d.get("expires_at", now) or now)
    label = d.get("label", "?")
    tag = tr("[임시] ") if d.get("kind") == "temporary" else ""
    if d.get("expired"):
        life = tr("만료됨 ({ago})", ago=_ago(now - exp))
    else:
        life = tr("남은 기간 {span}", span=_span(exp - now))
    return tr("    {id}  {tag}{label} 등록 {added} / 최근 사용 {seen} / {life}",
              id=d["id"][:8], tag=tag, label=f"{label:<10}", added=_ago(now - added),
              seen=_ago(now - seen), life=life)


def _device_detail(d: dict) -> str:
    """둘째 줄 — OS·브라우저·마지막 IP(경로)·등록 방식. 예전 레코드는 빈 문자열."""
    client = d.get("client") if isinstance(d.get("client"), dict) else {}
    parts = [p for p in (client.get("os", ""), client.get("browser", "")) if p]
    ip = d.get("ip_last") or ""
    if ip:
        via = _via_label(d.get("via", ""))
        parts.append(f"{ip} ({via})" if via else ip)
    method = _method_label(d.get("method", ""))
    if method:
        parts.append(method)
    return " · ".join(parts)


def _cli(argv: list) -> int:
    cmd = argv[0] if argv else "status"
    arg = argv[1] if len(argv) > 1 else ""

    if cmd == "generate-password":
        # 인증 없이 기동하려 할 때 bin/fsh가 부른다(lib/auth_bootstrap.sh). 1행 원문, 2행 scrypt 해시.
        # 원문은 이 stdout(셸 변수)으로만 나간다 — argv·환경변수·파일에는 남지 않는다.
        # 20자 × 31글자 알파벳(헷갈리는 0/o/1/l/i 제외 소문자+숫자) ≈ 99비트. 소문자·숫자·하이픈만이라
        # 로그인 폼의 한글 IME 역변환(gate.js)과 폰 키보드에서도 그대로 입력된다.
        import secrets as _secrets
        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
        raw = "".join(_secrets.choice(alphabet) for _ in range(20))
        pw = "-".join(raw[i:i + 5] for i in range(0, 20, 5))
        print(pw)
        print(hash_password(pw))
        return 0
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
        now = int(time.time())
        print()
        if not devs:
            print(tr("  등록된 기기가 없습니다 (아직 로그인한 기기가 없거나 인증이 꺼져 있음)"))
        else:
            active = [d for d in devs if not d.get("expired")]
            expired = [d for d in devs if d.get("expired")]
            print(tr("  📱 등록된 기기 — 유효 {n}개", n=len(active))
                  + (tr(" · 만료 {n}개", n=len(expired)) if expired else ""))
            print()
            for d in active + expired:
                print(_device_line(d, now))
                detail = _device_detail(d)
                if detail:
                    print(f"              {detail}")
            print()
            if expired:
                grace = DEVICE_EXPIRED_GRACE // 86400
                print(tr("  만료된 기기는 접속할 수 없고, 만료 {grace}일 뒤 목록에서 자동으로 지워집니다.", grace=grace))
                print(tr("  지금 정리:  fsh device prune"))
            print(tr("  이름 변경: fsh device rename <id앞자리> <별명>"))
            print(tr("  폐기:      fsh device revoke <id앞자리>"))
        print()
        return 0
    if cmd == "device-prune":
        removed = prune_devices()
        print(json.dumps(removed, ensure_ascii=False))
        return 0
    if cmd == "device-rename":
        renamed = rename_device(arg, argv[2] if len(argv) > 2 else "")
        if renamed is None:
            return 1
        print(json.dumps(renamed, ensure_ascii=False))
        return 0
    if cmd == "device-revoke":
        removed = revoke_device(arg)
        print(json.dumps(removed, ensure_ascii=False))
        return 0 if removed else 1
    if cmd == "ticket-new":
        # `ticket-new <label> temp` → 이 티켓으로 등록되는 기기는 1일짜리 임시 기기가 된다.
        # 티켓 자체의 수명(5분)과는 별개다.
        if len(argv) > 2 and argv[2] == "temp":
            print(issue_ticket(label=arg, device_ttl=TICKET_TEMP_DEVICE_TTL))
        else:
            print(issue_ticket(label=arg))
        return 0
    if cmd in ("status", "is-protected"):
        # bin/vt가 서버 프로세스 없이 "인증이 켜져 있는가"만 물을 때 쓴다(예: `vt mobile`이
        # 공개 터널을 열기 전 사전 점검). exit code로만 판단 가능하게 truthy/falsy 출력도
        # 함께 찍는다 — 0=인증 있음(protected), 1=인증 없음(unprotected).
        protected = is_protected()
        print(json.dumps({"protected": protected}, ensure_ascii=False))
        return 0 if protected else 1
    print(f"unknown command: {cmd}", file=__import__("sys").stderr)
    return 2

