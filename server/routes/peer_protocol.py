"""peer 프로토콜 — 서명 검증·등급 판정·재생 차단. **변하지 않는 절반이다.**

`routes/peer.py`에서 떼어낸 이유는 줄 수가 아니라 **변경 주기**다. 원격 기능이
하나 늘 때마다 capability 핸들러가 하나 늘지만(2.2 이월만 넷 — 원격 파일 목록·
호스트별 터널·원격 포트 대시보드·원격 워크트리 생성), 여기 있는 것들은 그대로
있다. 한 파일에 두면 "무엇이 계약이고 무엇이 그때그때 늘어나는 표면인가"가
안 보인다.

## 세 가지가 이 파일의 전부다

1. **서명 검증** — `HMAC(secret, "METHOD\npath\nts\nnonce")`. secret은 절대
   전송되지 않는다. 서명 대상에 method/path가 들어가므로 view용 GET 서명을
   control용 POST에 돌려쓸 수 없다.
2. **등급 판정** — view/control. 기본은 항상 안전한 쪽이고, control은 상대가
   명시적으로 켜야 한다.
3. **재생 차단** — nonce 1회용. **서명 검증을 통과한 뒤에** 본다: 그 전에 하면
   아무나 nonce를 채워 넣어 정상 요청을 막을 수 있다(캐시 오염).

## 왜 Request가 아니라 (method, path, headers)를 받는가

WebSocket 엔드포인트도 **같은 서명 규칙**을 써야 하는데 WebSocket에는 Request가
없다. 검증 로직이 둘로 갈리면 한쪽만 고쳐지는 날이 온다.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

import host_store
from authz import hooks as authz_hooks
from authz import peer_svc
from authz import preauth as authz_preauth
from i18n import tr

logger = logging.getLogger(__name__)

_nonces = host_store.NonceCache()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_VERSION: str | None = None


def _version() -> str:
    global _VERSION
    if _VERSION is None:
        try:
            _VERSION = (_REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            _VERSION = ""
    return _VERSION


class PeerDenied(Exception):
    def __init__(self, reason: str, status: int = 401):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _guarded(conn, method: str, path: str, headers, body_hash: str = "") -> dict:
    """`_authenticate_raw` + 출처별 사전 인증 실패 한도(`authz/preauth.py`): 잠긴 출처는 429, 서명을 내세웠다 틀린 요청은 실패로 센다.
    요청/WS 핸들러가 이것을 부른다(`conn` = Request | WebSocket). 서명 규칙 자체는 `_authenticate_raw` 하나다."""
    source = authz_preauth.source_key(_client_host(conn), headers)
    left = authz_preauth.LIMITER.lock_remaining(source)
    if left:
        raise PeerDenied(tr("인증 실패가 많아 잠시 막혔습니다 — {sec}초 뒤에 다시 시도하세요", sec=left), 429)
    try:
        return _authenticate_raw(method, path, headers, body_hash) if body_hash else _authenticate_raw(method, path, headers)
    except PeerDenied:
        if headers.get("x-peer-id"):
            authz_preauth.fail(source, "peer_sig")
        raise


def _authenticate_raw(method: str, path: str, headers, body_hash: str = "") -> dict:
    """서명 검증 → grant. 실패는 PeerDenied. 실패도 전부 감사 로그에 남긴다.

    Request가 아니라 (method, path, headers)를 받는 이유: 3단계의 WebSocket
    엔드포인트도 **같은 서명 규칙**을 써야 하는데 WebSocket에는 Request가 없다.
    검증 로직이 둘로 갈리면 한쪽만 고쳐지는 날이 온다.
    """
    return _authenticate_checked(method, path, headers, body_hash)


def _authenticate_checked(method: str, path: str, headers, body_hash: str = "") -> dict:
    peer_id = headers.get("x-peer-id", "")
    ts_raw = headers.get("x-peer-ts", "")
    nonce = headers.get("x-peer-nonce", "")
    sig = headers.get("x-peer-sig", "")
    if not (peer_id and ts_raw and nonce and sig):
        raise PeerDenied(tr("서명 헤더가 없습니다"))
    try:
        ts = int(ts_raw)
    except ValueError:
        raise PeerDenied(tr("잘못된 타임스탬프"))

    grant = host_store.find_grant(peer_id)
    if grant is None:
        # 취소된 상대가 옛 secret으로 계속 두드리는 경우가 여기다 — 기록해둔다.
        host_store.audit(peer_id, "auth", False, "등록되지 않은 호스트")
        raise PeerDenied(tr("등록되지 않은 호스트입니다"))

    # A2 — 본문 해시를 서명한 요청. 헤더가 **있으면 반드시 맞아야 한다**(없는 척
    # 지우고 보내면 서명 자체가 안 맞으므로 downgrade가 성립하지 않는다).
    claimed = headers.get("x-peer-body", "")
    if claimed and body_hash and not hmac.compare_digest(claimed, body_hash):
        host_store.audit(peer_id, "auth", False, "본문 해시 불일치")
        raise PeerDenied(tr("본문이 서명과 일치하지 않습니다"))
    if not host_store.verify_signature(grant["secret"], method, path, ts, nonce, sig,
                                       body_hash=claimed):
        host_store.audit(peer_id, "auth", False, "서명 불일치 또는 시간창 밖")
        raise PeerDenied(tr("서명이 유효하지 않습니다 — 시계 차이가 크면 'fsh host ping'으로 확인하세요"))

    # 재생 차단은 서명 검증을 통과한 뒤에 본다 — 그 전에 하면 아무나 nonce를
    # 채워 넣어 정상 요청을 막을 수 있다(캐시 오염).
    if not _nonces.check_and_add(f"{peer_id}:{nonce}"):
        host_store.audit(peer_id, "auth", False, "nonce 재사용(재생 시도)")
        raise PeerDenied(tr("이미 사용된 요청입니다"))

    host_store.touch_grant(peer_id)
    return grant


def _client_host(conn) -> str | None:
    c = getattr(conn, "client", None)
    return (c.host if c else None) or "unknown"


def _authenticate(request: Request) -> dict:
    return _guarded(request, request.method, request.url.path, request.headers)


def _level_required(grant: dict, path: str) -> JSONResponse:
    host_store.audit(grant["id"], path, False, "control 등급 필요")
    return JSONResponse(
        {"error": "level_required", "reason":
         tr("이 호스트는 읽기 전용(view)으로 허용돼 있습니다 — 상대 맥에서 'fsh host allow-control <id>'로 켜야 합니다")},
        status_code=403,
    )


def _gate(grant: dict, level: str, action: str | None, path: str) -> JSONResponse | None:
    """등급 관문. 꺼짐: 옛 등급 비교. 켜짐(`peer`): 라우트가 표(`peer_svc.NEEDS`)에 적은 (scope, 조건)을 엔진에 묻는다 —
    결과와 응답은 같다(control 이 필요한 라우트를 view 가 부르면 403 `level_required`)."""
    legacy_ok = not (level == host_store.LEVEL_CONTROL and grant.get("level") != host_store.LEVEL_CONTROL)
    authz_hooks.peer_level(grant, level, legacy_ok)      # shadow
    if not peer_svc.enforced():
        return None if legacy_ok else _level_required(grant, path)
    d = peer_svc.decide(grant, action, level=level)
    if d.allow:
        return None
    if peer_svc.unavailable(d):
        return JSONResponse({"error": "authz_unavailable", "reason": tr("권한 엔진을 쓸 수 없어 요청을 거부했습니다")},
                            status_code=503)
    peer_svc.audit_denied(grant, action or level, d)
    if level == host_store.LEVEL_CONTROL:
        return _level_required(grant, path)
    host_store.audit(grant["id"], path, False, f"권한 없음: {d.reason}")
    return JSONResponse({"error": "peer_forbidden", "reason": tr("이 호스트에는 이 기능을 쓸 권한이 없습니다")},
                        status_code=403)


def _require(request: Request, level: str = host_store.LEVEL_VIEW,
             action: str | None = None) -> tuple[dict | None, JSONResponse | None]:
    """(grant, 오류응답) — 오류응답이 None이 아니면 그대로 반환하면 된다."""
    try:
        grant = _authenticate(request)
    except PeerDenied as e:
        return None, JSONResponse({"error": "peer_denied", "reason": e.reason}, status_code=e.status)
    err = _gate(grant, level, action, request.url.path)
    return (None, err) if err is not None else (grant, None)


async def _require_body(request: Request, level: str = host_store.LEVEL_VIEW, action: str | None = None):
    """본문 해시까지 서명한 요청용 인증(A2). 성공하면 `(grant, (grant, body))`,
    실패하면 `(None, JSONResponse)` — 호출부가 본문을 다시 읽지 않아도 되게
    바이트를 함께 돌려준다(한 번만 읽을 수 있는 스트림이다)."""
    data = await request.body()
    digest = hashlib.sha256(data).hexdigest()
    try:
        grant = _guarded(request, request.method, request.url.path, request.headers, digest)
    except PeerDenied as e:
        return None, JSONResponse({"error": "peer_denied", "reason": e.reason}, status_code=e.status)
    err = _gate(grant, level, action, request.url.path)
    if err is not None:
        return None, err
    return grant, (grant, data)


# --- capability 관문 -------------------------------------------------------------
#
# 원격 capability 핸들러는 전부 같은 네 단계를 밟는다:
#
#     권한 검사 → 로컬 구현 재사용 → **응답 위생** → 감사 로그
#
# 지금까지는 네 단계를 핸들러마다 손으로 썼다(10개 전부 지켜지고 있었다).
# 문제는 2.2에 원격 기능이 넷 더 들어온다는 것이고, **위생을 빠뜨려도 아무
# 일도 안 일어난다**는 것이다. 빠뜨린 채로 동작하고, 틀린 건 A쪽에서 한참
# 뒤에 드러난다.
#
# ## 「응답 위생」이 뭘 막는가
#
# B 안에서만 뜻이 있는 식별자가 A로 넘어가면 A가 그걸 **자기 것으로 오해한다.**
# 실제 사례: `peer_search`가 `session_id`를 지우지 않으면 A가 그 id로 로컬
# 세션을 열려다 엉뚱한 세션을 연다. 2.2에 들어올 것들도 성질이 같다 —
# 원격 파일의 경로, 원격 포트의 PID, 원격 워크트리의 id 전부 B 로컬 값이다.
#
# 그래서 `strip`을 **선언으로** 받는다. 빠뜨리면 선언이 비어 있는 게 눈에
# 띄고, 일부러 안 지우는 경우는 `strip=()`로 그 의도가 코드에 남는다.
# (fsguard·has_literal_secret와 같은 규율 — 검사를 한 곳에 모은다.)

import inspect   # noqa: E402
from functools import wraps   # noqa: E402


def _strip_keys(payload, keys: tuple[str, ...]):
    """응답에서 B 로컬 식별자를 재귀적으로 제거한다.

    리스트·중첩 dict까지 훑는 이유: 검색 결과처럼 `{"results": [{...}, …]}`
    꼴이 흔한데, 최상위만 지우면 정작 위험한 건 그대로 남는다.
    """
    if isinstance(payload, dict):
        return {k: _strip_keys(v, keys) for k, v in payload.items() if k not in keys}
    if isinstance(payload, list):
        return [_strip_keys(v, keys) for v in payload]
    return payload


def capability(action: str, *, level: str = host_store.LEVEL_VIEW,
               strip: tuple[str, ...] = (), body: bool = False):
    """peer capability 핸들러 — 권한·위생·감사를 한 번에 건다.

    감싸는 함수는 `(request, grant)` 또는 `(request, grant, raw_body)`를 받고
    응답을 돌려준다. 실패 응답(JSONResponse)은 그대로 통과시킨다.

    **감사 detail은 핸들러가 정한다.** 여기서 일반화해 버리면 지금 사람이 읽기
    좋게 쓴 문구("12건", "2개 떼어냄")가 전부 경로 문자열로 뭉개진다. 핸들러가
    `(payload, detail)` 튜플을 돌려주면 그 detail을 쓰고, 그냥 payload만
    돌려주면 빈 문자열로 남긴다.
    """
    def decorate(fn):
        @wraps(fn)
        async def wrapper(request: Request, *args, **kwargs):
            if body:
                grant, got = await _require_body(request, level, action)
                if grant is None:
                    return got          # JSONResponse
                _, raw = got
                extra = (raw,)
            else:
                grant, err = _require(request, level, action)
                if err:
                    return err
                extra = ()

            result = await fn(request, grant, *extra, *args, **kwargs)

            # 핸들러는 `payload` 또는 `(payload, detail)`을 돌려준다. 거절도
            # 같은 규칙이다 — `JSONResponse` 또는 `(JSONResponse, detail)`.
            # **감사를 핸들러가 직접 부르지 않게 하는 게 요점이다**: 둘 다
            # 부르면 같은 사건이 로그에 두 번 남고, 어느 쪽이 정본인지 모른다.
            detail = ""
            if isinstance(result, tuple) and len(result) == 2:
                result, detail = result

            if isinstance(result, JSONResponse):
                host_store.audit(grant["id"], action, False,
                                 detail or f"HTTP {result.status_code}")
                return result

            payload = _strip_keys(result, strip) if strip else result
            host_store.audit(grant["id"], action, True, detail)
            return payload
        # ⚠ FastAPI는 `inspect.signature`로 핸들러의 파라미터를 읽어 라우팅을
        # 만드는데, 그게 `@wraps`가 심어둔 `__wrapped__`를 따라가 **원본**을
        # 본다. 그러면 우리가 주입하는 `grant: dict`를 요청 본문 파라미터로
        # 오해해 422를 낸다(실측). 래퍼가 실제로 받는 모양을 명시해 끊는다.
        wrapper.peer_action = action      # 단일 표 `peer_svc.NEEDS` 의 키 — 선언 누락 시험이 읽는다
        wrapper.peer_level = level
        wrapper.__signature__ = inspect.Signature([
            inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              annotation=Request),
        ])
        return wrapper
    return decorate
