"""호스트 전환 중계(relay)가 **상대 맥의 어느 API까지** 닿을 수 있는가 — 허용 목록.

2026-09-28(사용자 요청) — 레일 스위처로 M5를 고르면 세션만이 아니라 탭·포트·
파일·첨부·설정까지 전부 M5 것이 되어야 한다. 그래서 브라우저 → 로그인한 서버(A)
→ 상대 서버(B)로 일반 API를 통째로 중계한다(`routes/host_relay.py` → `routes/
peer_relay.py`). `routes/peer.py` 머리말의 "peer는 별도 네임스페이스로만 닿는다"
원칙을 여기서 **넓히는 것**이므로, 넓히는 범위를 이 파일 한 곳에 목록으로 둔다.

## 규칙
- **허용 목록이다(거부 목록이 아니다).** 새 API가 생기면 기본은 중계 불가 —
  여기에 적어야 열린다. 거꾸로 하면 인증 라우트가 새로 생기는 날 조용히 뚫린다.
- 판정은 **B가** 한다(`peer_relay.py`). A도 같은 함수로 미리 거르지만 그건 헛걸음을
  줄이려는 것일 뿐, A를 믿는 게 아니다(등급 판정을 B가 하는 것과 같은 이유).
- 절대 열지 않는 것: 로그인·기기·OTP·승격(`/api/auth*`, `/api/devices`), peer·호스트
  관리(`/api/peer*`, `/api/hosts*`, `/ws/remote`, `/ws/hosts` — 여기를 열면 A→B→C
  전이 중계가 된다), 공유 링크·포트 노출·git 계정·MCP 쓰기(승격 라우트), 푸시·
  클립보드·음성(그 기기에 붙은 것), 훅 이벤트(기계 계약, AGENTS.md 계약 8).

## 파일 기능의 원격 범위 (2026-11-07 확정)
- 파일 **읽기** 기능(뷰어 읽기, 구간 읽기, 조건부 요청, 미디어, 내용 검색)은 원격에서도
  제공한다 — 새 읽기 경로가 생기면 `GET`으로 여기에 더한다.
- 파일 **쓰기**(경로 기반 저장·생성)와 **경로 기반 다운로드**는 로컬 전용이다. 여기에 넣지
  않는다. 중계는 쿠키를 넘기지 않아(`peer_relay.py`) B의 승격을 통과할 방법이 없고, 원격
  쓰기에는 중계용 승격 설계가 따로 필요하다.
- 응답 보안 헤더(CSP, `nosniff`)와 `if-range`는 중계를 지나도 남는다(`peer_relay.py`의 전달 목록).
"""

from __future__ import annotations

import re

_GET = frozenset({"GET"})
_ANY = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

# (패턴, 허용 메서드). 패턴은 경로 **전체**와 맞춘다(fullmatch).
_HTTP_RULES: tuple[tuple[re.Pattern, frozenset], ...] = tuple(
    (re.compile(p), m) for p, m in (
        # 터미널 세션·tmux
        (r"/api/sessions(/[^/]+(/(keys|paste))?)?", _ANY),
        (r"/api/tmux/(sessions|create|attach|clients|clients/solo|detach-client)", _ANY),
        (r"/api/tmux/kill/[^/]+", _ANY),
        (r"/api/tmux/preview/[^/]+", _GET),
        # 에이전트 상태
        (r"/api/agents(/coverage|/[^/]+)?", _GET),
        (r"/api/agent/status", _GET),
        (r"/api/agents/[^/]+/subagents/[A-Za-z0-9_-]{1,64}/transcript", _GET),
        # 첨부·파일·코드 뷰어 (공유 링크는 승격 라우트라 제외, send는 전이라 제외)
        (r"/api/files", _GET),
        (r"/api/files/delete", _ANY),
        (r"/api/files/[^/]+", _ANY),
        (r"/api/files/[^/]+/(download|path)", _GET),
        (r"/api/files/[^/]+/insert", _ANY),
        (r"/api/upload", _ANY),
        (r"/api/fs/(file|raw|roots|search|tree)", _GET),
        # git (계정·바인딩 제외)
        (r"/api/git/(status|diff|log|show)", _GET),
        (r"/api/git/(stage|unstage|commit)", _ANY),
        # 포트 (노출·공유는 승격 라우트라 제외)
        (r"/api/ports", _GET),
        (r"/api/ports/\d+", _ANY),
        # 큐·스니펫·검색·사용량
        (r"/api/queue(/run|/[^/]+(/unblock)?)?", _ANY),
        (r"/api/snippets(/project|/[^/]+)?", _ANY),
        (r"/api/search/scrollback", _GET),
        (r"/api/usage(/counter)?", _ANY),
        # 워크스페이스(탭·레이아웃)·워크트리·저장소
        (r"/api/workspace", _ANY),
        (r"/api/worktrees(/precheck|/hidden|/[^/]+(/open)?)?", _ANY),
        (r"/api/repos", _GET),
        # 읽기 전용 상태
        (r"/api/(capabilities|hooks/status|safe-mode|tunnel/list|tailscale/status|system/stats)", _GET),
        (r"/api/mcp(/plugins)?", _GET),
        (r"/api/watch/[^/]+", _ANY),
    )
)

# 명시해야만 열리는 규칙 — 기본 허용 목록(`_HTTP_RULES`)과 `http_allowed` 에 **들지 않는다**. 권한 엔진이 켜진 서버에서만 그룹 이름
# (`scopes.OPTIN_RELAY_GROUPS`)으로 판정하며, 옛 등급(view/control)만으로는 절대 열리지 않는다. 브라우저 broker(`/api/browser/*`).
_OPTIN_HTTP_RULES: tuple[tuple[re.Pattern, frozenset], ...] = (
    (re.compile(r"/api/browser/(adapters|tabs)(/.*)?"), _ANY),
)

# 탭의 files 규칙이 넓어서(`/api/files/[^/]+`) 아래를 먼저 막는다 — 공유 링크
# 생성(승격)과 다른 peer로 보내기(전이).
_HTTP_DENY = (
    re.compile(r"/api/files/[^/]+/(share|send)(/.*)?"),
    re.compile(r"/api/(tmux|sessions)/open-on-mac"),
)

_WS_RULES = (
    re.compile(r"/ws/[A-Za-z0-9_\-:.]+"),
    re.compile(r"/ws-agent"),
    re.compile(r"/ws-workspace"),
    re.compile(r"/ws-preview/[^/]+"),
    # 코드 뷰어 자동 갱신. 내용 없이 "바뀌었다"만 알리고 경로는 fsguard가 거른다 —
    # 이미 열려 있는 /api/fs/(tree|file) GET보다 넓어지지 않는다.
    re.compile(r"/ws-fs-watch"),
)
# `/ws/remote/...`·`/ws/hosts/...`는 _WS_RULES 첫 줄(한 세그먼트)에 안 걸린다.


def _clean(path: str) -> str | None:
    """경로 우회 차단. `..`·빈 세그먼트·역슬래시·NUL이 있으면 거부한다 —
    라우터와 이 목록이 같은 경로를 다르게 읽는 틈을 없앤다."""
    if not path.startswith("/") or "\\" in path or "\x00" in path or "//" in path:
        return None
    if any(seg in (".", "..") for seg in path.split("/")):
        return None
    return path


def http_allowed(method: str, path: str) -> bool:
    p = _clean(path)
    if p is None:
        return False
    if any(d.fullmatch(p) for d in _HTTP_DENY):
        return False
    m = method.upper()
    return any(rx.fullmatch(p) and m in methods for rx, methods in _HTTP_RULES)


def optin_group(method: str, path: str) -> str | None:
    """명시해야 열리는 그룹의 이름(`browser`). 경로 정리(`_clean`)를 통과하고 메서드가 맞을 때만."""
    p = _clean(path)
    if p is None:
        return None
    m = method.upper()
    for rx, methods in _OPTIN_HTTP_RULES:
        if rx.fullmatch(p) and m in methods:
            from authz import scopes
            return scopes.OPTIN_RELAY_HTTP_GROUPS.get(rx.pattern)
    return None


def ws_allowed(path: str) -> bool:
    p = _clean(path)
    return p is not None and any(rx.fullmatch(p) for rx in _WS_RULES)


def strip_token(query: str) -> str:
    """쿼리에서 `token`을 뺀다 — **A의 기계 토큰이 B로 새지 않게.** 브라우저는
    인증용으로 `?token=`을 붙여 보내는데, 그건 A에서만 뜻이 있는 비밀이다."""
    from urllib.parse import parse_qsl, urlencode

    if not query:
        return ""
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != "token"]
    return urlencode(pairs)
