"""주체(principal) — 종류 5개: `human`, `local`, `agent`, `peer`, `legacy_token`(임시).

주체는 **기존 인증 판정을 그대로 감싸서** 만든다(`auth.request_kind`, `share_identity.verify_caller`, peer HMAC).
인증 로직을 여기서 다시 구현하면 두 구현이 갈라지는 날이 오고, 그 차이가 shadow 잡음이 된다.

- `human`: 로그인한 브라우저 세션(기기). elevated 는 human 만 가진다.
- `local`: unix socket 에서 오는 비human·비agent 호출자(CLI·서비스 데몬). 이름은 속성 `name`(`cli` | `svc:<이름>`)이고
  귀속용일 뿐 경계가 아니다.
- `agent`: 우리 tmux pane 안의 에이전트. pane 안의 훅 스크립트도 같은 agent 이고 속성 `via=hook` 만 다르다.
- `peer`: 서명(HMAC)을 통과한 다른 맥. 인증만 — 인가는 grant 로.
- `legacy_token`: 기계 토큰(이행 기간 전용 shim). 비밀번호·토큰이 모두 없는 서버의 무인증 호출도 같은 전체 권한 shim 이다.
- 인증되지 않은 호출자·pair 는 주체가 아니다. pair 는 구성원이 grant 묶음을 만드는 장치일 뿐 단독 인증 수단이 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

HUMAN, LOCAL, AGENT, PEER, LEGACY = "human", "local", "agent", "peer", "legacy_token"
KINDS = (HUMAN, LOCAL, AGENT, PEER, LEGACY)
NO_KIND = ""                    # 인증되지 않은 호출자 — 주체 종류가 아니다(엔진이 거부한다)
LOCAL_HOST = "local"


@dataclass(frozen=True)
class Principal:
    kind: str
    id: str
    authenticated: bool = True
    elevated: bool = False          # human 만 가진다
    obo: Optional[str] = None       # peer 대리 주체
    attrs: dict = field(default_factory=dict, compare=False, hash=False)

    def subject_ids(self) -> tuple[str, ...]:
        """grant 의 subject 선택자와 비교할 이름들 — 자기 id, 대리 중이면 `peer:X/obo:<p>` 도."""
        ids = [self.id]
        if self.obo:
            ids.append(f"{self.id}/obo:{self.obo}")
        return tuple(ids)

    def short(self) -> str:
        return f"{self.id}/obo:{self.obo}" if self.obo else self.id


def human(device_id: str, *, elevated: bool = False) -> Principal:
    return Principal(HUMAN, f"human:dev:{device_id or '-'}", elevated=elevated)


def legacy_token() -> Principal:
    return Principal(LEGACY, "legacy_token")


def open_server() -> Principal:
    """비밀번호·토큰이 모두 없는 서버의 무인증 호출자 — 서버에 인증 수단이 없어 기계 토큰과 같은 전체 권한(임시 shim,
    `legacy_token` 의 한 형태)."""
    return Principal(LEGACY, "legacy:open", attrs={"open_server": True})


def unauthenticated(*, ambiguous: bool = False) -> Principal:
    """인증되지 않은(또는 모호한) 호출자 — 주체가 아니다. 엔진은 어떤 scope 도 허용하지 않는다."""
    return Principal(NO_KIND, "unauthenticated", authenticated=False, attrs={"ambiguous": True} if ambiguous else {})


def agent(key: str, host: str = LOCAL_HOST, *, name: str = "") -> Principal:
    """`agent:<host>/<sid>:<created>` — share_identity 가 pane 소속을 확인한 호출자."""
    return Principal(AGENT, f"agent:{host}/{key}", attrs={"key": key, "name": name})


def peer(peer_id: str, *, obo: Optional[str] = None) -> Principal:
    return Principal(PEER, f"peer:{peer_id}", obo=obo, attrs={"peer_id": peer_id})


SVC_NAMES = ("hook", "clipboard", "voice", "tui", "tmux-notify", "tunnel-hook", "mcp-probe")


def local(name: str, uid: int, pid: int, *, ident: str = "") -> Principal:
    """`local:<name>` — unix socket 에서 오는 비human·비agent 호출자. `name` 은 `cli` 또는 `svc:<이름>`이고
    귀속용 속성일 뿐 보안 경계가 아니다(같은 uid)."""
    attrs = {"name": name, "uid": uid, "pid": pid}
    if name.startswith("svc:"):
        attrs["svc"] = name[4:]
    return Principal(LOCAL, f"local:{ident or name}", attrs=attrs)


def cli(uid: int, pid: int) -> Principal:
    """`local:cli:uid<uid>:pid<pid>` — unix socket 의 peer 가 우리 tmux pane 밖이다. **human 이 아니다**."""
    return local("cli", uid, pid, ident=f"cli:uid{uid}:pid{pid}")


def svc(name: str, uid: int, pid: int) -> Principal:
    """`local:svc:<이름>` — 선언된 이름은 귀속용일 뿐 경계가 아니다(같은 uid)."""
    return local(f"svc:{name}", uid, pid)


def is_cli(p: Principal) -> bool:
    return p.kind == LOCAL and p.attrs.get("name") == "cli"


def is_svc(p: Principal) -> bool:
    return p.kind == LOCAL and str(p.attrs.get("name", "")).startswith("svc:")


def is_hook(p: Principal) -> bool:
    """에이전트 pane 안의 훅 스크립트 — 같은 agent 주체에 속성 `via=hook`."""
    return p.kind == AGENT and p.attrs.get("via") == "hook"


def fails_closed(p: Principal) -> bool:
    """grant 저장소가 손상되면 이 주체는 거부로 닫힌다(human·svc 기본은 동작)."""
    return p.kind in (AGENT, PEER) or is_cli(p)


def from_socket(peer: dict, *, row=None, svc_claim: str = "") -> Principal:
    """unix socket 연결의 peer(`{uid, pid}`) → 주체. 판정 순서가 곧 규칙이다.

    1. peer 의 부모 체인이 우리 tmux pane 에 닿는다(`row`) → **agent**. `X-Fsh-Svc: hook` 이면 같은 agent 에
       속성 `via=hook`. 그 밖의 이름을 선언해도 agent 로 남는다 — pane 안의 프로세스가 `svc:`를 자처해 agent 를 벗어날 수 없다.
    2. pane 밖이고 알려진 svc 이름을 선언했다 → local(`svc:<이름>`).  3. 그 밖 → local(`cli`). 어느 쪽도 human 이 아니다.
    """
    uid, pid = int(peer["uid"]), int(peer["pid"])
    claim = svc_claim if svc_claim in SVC_NAMES else ""
    if row is not None:
        p = agent(f"{row.sid}:{row.created}", name=getattr(row, "name", ""))
        attrs = dict(p.attrs, uid=uid, pid=pid, pane=row.pane_id, via="socket")
        if claim == "hook":
            attrs["via"] = "hook"
        elif claim:
            attrs["svc"] = claim
        return Principal(AGENT, p.id, attrs=attrs)
    p = svc(claim, uid, pid) if claim else cli(uid, pid)
    p.attrs["via"] = "socket"
    return p


def from_caller(caller) -> Principal:
    """`share_identity.Caller`(확인된 pane 호출자) → agent 주체."""
    return agent(f"{caller.sid}:{caller.created}", name=getattr(caller, "name", ""))


def _tokens(cookies, query, headers) -> tuple[str, str, str]:
    cookie = cookies.get("vt_session", "") or ""
    q = query.get("token", "") or ""
    a = headers.get("authorization", "") or ""
    return cookie, q, (a[7:] if a.startswith("Bearer ") else "")


def from_request(request) -> Principal:
    """HTTP 요청 → 주체. 판정은 `auth.request_kind`·`auth.check_request` 그대로다.

    `authenticated`는 미들웨어와 같은 규칙(쿠키 → 쿼리 → Bearer 순으로 **처음 것 하나**를 검사)
    으로 정하고, `kind`는 request_kind 로 정한다. 둘이 어긋나는 요청(예: 유효 쿠키 + 엉뚱한
    Bearer)은 「모호한 주체」다 — 기존 미들웨어는 통과시키지만 이 쪽은 인증되지 않은 호출자가 되어
    shadow 차이로 드러난다(의도: 강제 전에 실제로 그런 요청이 있는지 본다).
    """
    import auth
    from . import defaults, flags

    protected = auth.is_protected()
    cookie, q, bearer = _tokens(request.cookies, request.query_params, request.headers)
    kind = auth.request_kind(request)
    if kind == auth.AUTH_SESSION:
        if auth.VT_AUTH_PASSWORD_HASH or (defaults.HUMAN_COOKIE_WITHOUT_PASSWORD and not flags.enforced("legacy")):
            dev = auth.session_device(cookie) or ""
            import time as _t
            elev = bool(auth.VT_AUTH_PASSWORD_HASH) and auth.session_elevated_until(cookie) > int(_t.time())
            return human(dev, elevated=elev)
        return unauthenticated() if protected else open_server()
    if kind == auth.AUTH_MACHINE:
        return legacy_token()
    if not protected:
        return open_server()
    if (cookie and not q and not bearer and not auth.VT_AUTH_PASSWORD_HASH and flags.enforced("legacy")
            and auth.session_device(cookie) is not None):
        # H14 — 비밀번호 없이 토큰만 있는 서버의 서명 쿠키: 사람이 아니고, 옛날처럼 기계 토큰 수준으로만 쓴다.
        return legacy_token()
    # 미들웨어가 통과시키는(=check_request 참) 요청인데 종류를 못 정했다 -> 모호.
    token = cookie or q or bearer
    if token and auth.check_request(token):
        return unauthenticated(ambiguous=True)
    return unauthenticated()


def from_peer_grant(grant: dict, *, obo: Optional[str] = None) -> Principal:
    """HMAC 을 통과한 peer(`host_store` grant) → peer 주체. 인증만 — 인가는 grant 로."""
    return peer(str(grant.get("id") or ""), obo=obo)
