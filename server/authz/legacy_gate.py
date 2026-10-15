"""공용 기계 토큰 정리 — `VT_AUTHZ_ENFORCE=legacy`.

기본(꺼짐)에서는 이 모듈의 어떤 함수도 동작을 바꾸지 않는다. 켜면 아래 네 가지가 달라진다.

1. **범위(H1)** — 기계 토큰(Bearer)은 더 이상 API 전체를 열지 않는다. unix socket 게이트가 저장소 안 클라이언트에게 열어 주는
   (메서드, 경로) 허용표(`sock.ROUTES`)만 쓸 수 있고, 그 밖은 403 `legacy_token_scope`. 거절은 `legacy_use` 가 센다.
2. **전달 방식(H4)** — `?token=` 쿼리와 「쿠키 값이 곧 기계 토큰」은 받지 않는다(URL·쿠키로 새는 비밀). Bearer 헤더만 남는다.
3. **승인 불가 서버(H6/H14)** — 웹 비밀번호가 없고 토큰만 있는 서버에서는 사람이 승인할 방법이 없다. 승인을 새로 만들어야 하는
   기능(공유·메시지·호스트 권한 변경)은 멈춰 두는 대신 403 `approval_unavailable_no_password` 로 이유를 알린다. 이미 부여된 권한은
   그대로 동작한다. 서명 쿠키도 이런 서버에서는 「사람」이 아니다. 비밀번호도 토큰도 없는 서버는 지금과 같다.
"""

from __future__ import annotations

from . import flags
from .errors import ShareError

SCOPE_ERROR = "legacy_token_scope"
NO_PASSWORD_ERROR = "approval_unavailable_no_password"
TOKEN_URL_ERROR = "token_query_unsupported"
TOKEN_COOKIE_ERROR = "token_cookie_unsupported"


def active() -> bool:
    return flags.enforced("legacy")


def _auth():
    import auth
    return auth


def scope_allowed(method: str, path: str) -> bool:
    """기계 토큰이 쓸 수 있는 (메서드, 경로) — 소켓 허용표와 같은 표다."""
    from . import sock
    return sock.route_allowed(method, path)


def human_cookie_ok() -> bool:
    """서명 쿠키를 사람(human)으로 인정하는가. 비밀번호가 없는 서버에서는 켜짐일 때 인정하지 않는다(H14)."""
    return bool(_auth().VT_AUTH_PASSWORD_HASH) or not active()


def approval_unavailable() -> bool:
    """켜짐 + 토큰만 있고 비밀번호가 없는 서버 — 승인할 사람이 없다."""
    a = _auth()
    return active() and a.is_protected() and not a.VT_AUTH_PASSWORD_HASH


def require_approval_available() -> None:
    """승인을 새로 만드는 지점의 맨 앞에서 부른다. 승인 불가 서버면 ShareError(403)."""
    if approval_unavailable():
        from i18n import tr
        raise ShareError(NO_PASSWORD_ERROR,
                         tr("웹 로그인 비밀번호가 없어 승인할 사람이 없습니다 — fsh password 로 비밀번호를 설정하세요"), 403)


def scope_denied_body() -> dict:
    from i18n import tr
    return {"error": SCOPE_ERROR,
            "reason": tr("공용 기계 토큰으로는 이 API 를 쓸 수 없습니다 — 같은 맥에서는 fsh 명령(unix socket)을, 브라우저에서는 비밀번호 로그인을 쓰세요")}


def token_url_body() -> dict:
    from i18n import tr
    return {"error": TOKEN_URL_ERROR,
            "reason": tr("URL 의 ?token= 은 더 이상 받지 않습니다 — 비밀번호로 로그인하거나 fsh mobile 로 접속하세요")}


def token_cookie_body() -> dict:
    from i18n import tr
    return {"error": TOKEN_COOKIE_ERROR,
            "reason": tr("기계 토큰을 세션 쿠키로 쓰는 방식은 더 이상 받지 않습니다 — 비밀번호로 로그인하거나 fsh mobile 로 접속하세요")}


def notice_for_start() -> str:
    """서버 시작 로그에 한 번 남길 안내. 해당 없으면 빈 문자열."""
    if not approval_unavailable():
        return ""
    return ("[보안] 웹 로그인 비밀번호가 없어 승인이 필요한 에이전트·peer 기능(공유·메시지·호스트 권한 변경)을 쓸 수 없습니다. "
            "이미 부여된 권한은 그대로 동작합니다. 'fsh password' 로 비밀번호를 설정하세요.")
