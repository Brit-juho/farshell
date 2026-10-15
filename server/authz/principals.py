"""주체(principal) — 10-policy.md A1 의 P1~P8 + 1단계 shim `legacy_token`.

1단계에서 주체는 **기존 인증 판정을 그대로 감싸서** 만든다(`auth.request_kind`,
`share_identity.verify_caller`, peer HMAC). 인증 로직을 여기서 다시 구현하면 두 구현이 갈라지는
날이 오고, 그 차이가 shadow 잡음이 된다. 새 인증(unix socket P2~P5)은 2단계 `sock.py`.

- 기계 토큰 → `legacy_token`(20-mapping.md B1: 8단계까지 기존 전체 권한 shim).
- 비밀번호·토큰이 모두 없는 서버(H6)의 무인증 → `anon` + `protected=False`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

HUMAN, CLI, SVC, AGENT, HOOK, PEER, PAIR, ANON, LEGACY = (
    "human", "cli", "svc", "agent", "hook", "peer", "pair", "anon", "legacy_token")
KINDS = (HUMAN, CLI, SVC, AGENT, HOOK, PEER, PAIR, ANON, LEGACY)
LOCAL_HOST = "local"


@dataclass(frozen=True)
class Principal:
    kind: str
    id: str
    authenticated: bool = True
    elevated: bool = False          # R1.3 — human 만
    protected: bool = True          # 서버에 인증 수단이 있는가(False = H6 모드)
    obo: Optional[str] = None       # peer 대리 주체(A8 R8.3)
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


def anon(*, protected: bool) -> Principal:
    return Principal(ANON, "anon", authenticated=not protected, protected=protected)


def agent(key: str, host: str = LOCAL_HOST, *, name: str = "") -> Principal:
    """`agent:<host>/<sid>:<created>` — share_identity 가 pane 소속을 확인한 호출자."""
    return Principal(AGENT, f"agent:{host}/{key}", attrs={"key": key, "name": name})


def peer(peer_id: str, *, obo: Optional[str] = None) -> Principal:
    return Principal(PEER, f"peer:{peer_id}", obo=obo, attrs={"peer_id": peer_id})


SVC_NAMES = ("hook", "clipboard", "voice", "tui", "tmux-notify", "tunnel-hook", "mcp-probe")


def cli(uid: int, pid: int) -> Principal:
    """`cli:uid<uid>:pid<pid>` — unix socket 의 peer 가 우리 tmux pane 밖이다. **human 이 아니다**(A1 P2)."""
    return Principal(CLI, f"cli:uid{uid}:pid{pid}", attrs={"uid": uid, "pid": pid})


def svc(name: str, uid: int, pid: int) -> Principal:
    """`svc:<name>` — 선언된 이름은 귀속용일 뿐 경계가 아니다(같은 uid, 정책 A3)."""
    return Principal(SVC, f"svc:{name}", attrs={"uid": uid, "pid": pid, "svc": name})


def hook(agent_key: str, host: str = LOCAL_HOST, *, uid: int = 0, pid: int = 0) -> Principal:
    """`hook:<agent key>` — 에이전트 pane 안의 훅 스크립트(P5). 훅 신원 판정(hook_identity)은 라우트가 그대로 한다."""
    return Principal(HOOK, f"hook:{host}/{agent_key}", attrs={"key": agent_key, "uid": uid, "pid": pid})


def from_socket(peer: dict, *, row=None, svc_claim: str = "") -> Principal:
    """unix socket 연결의 peer(`{uid, pid}`) → 주체. 판정 순서가 곧 규칙이다.

    1. peer 의 부모 체인이 우리 tmux pane 에 닿는다(`row`) → **agent**. `X-Fsh-Svc: hook` 이면 hook(P5).
       그 밖의 이름을 선언해도 agent 로 남는다 — pane 안의 프로세스가 `svc:`를 자처해 agent 를 벗어날 수 없다.
    2. pane 밖이고 알려진 svc 이름을 선언했다 → svc.  3. 그 밖 → cli. 어느 쪽도 human 이 아니다.
    """
    uid, pid = int(peer["uid"]), int(peer["pid"])
    claim = svc_claim if svc_claim in SVC_NAMES else ""
    if row is not None:
        p = agent(f"{row.sid}:{row.created}", name=getattr(row, "name", ""))
        attrs = dict(p.attrs, uid=uid, pid=pid, pane=row.pane_id, via="socket")
        if claim == "hook":
            h = hook(attrs["key"], uid=uid, pid=pid)
            h.attrs.update(pane=row.pane_id, via="socket")
            return h
        if claim:
            attrs["svc"] = claim
        return Principal(AGENT, p.id, attrs=attrs)
    if claim:
        p = svc(claim, uid, pid)
    else:
        p = cli(uid, pid)
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
    Bearer)은 R1.1 의 "모호한 주체"다 — 기존 미들웨어는 통과시키지만 이 주체는 anon 이 되어
    shadow 차이로 드러난다(의도: 3단계 전에 실제로 그런 요청이 있는지 본다).
    """
    import auth
    from . import defaults

    protected = auth.is_protected()
    cookie, q, bearer = _tokens(request.cookies, request.query_params, request.headers)
    kind = auth.request_kind(request)
    if kind == auth.AUTH_SESSION:
        if auth.VT_AUTH_PASSWORD_HASH or defaults.HUMAN_COOKIE_WITHOUT_PASSWORD:
            dev = auth.session_device(cookie) or ""
            import time as _t
            elev = bool(auth.VT_AUTH_PASSWORD_HASH) and auth.session_elevated_until(cookie) > int(_t.time())
            return human(dev, elevated=elev)
        return anon(protected=protected)
    if kind == auth.AUTH_MACHINE:
        return legacy_token()
    p = anon(protected=protected)
    if not protected:
        return p
    # 미들웨어가 통과시키는(=check_request 참) 요청인데 종류를 못 정했다 → 모호.
    token = cookie or q or bearer
    if token and auth.check_request(token):
        return Principal(ANON, "anon", authenticated=False, protected=True, attrs={"ambiguous": True})
    return p


def from_peer_grant(grant: dict, *, obo: Optional[str] = None) -> Principal:
    """HMAC 을 통과한 peer(`host_store` grant) → peer 주체. 인증만 — 인가는 grant 로(R8.1)."""
    return peer(str(grant.get("id") or ""), obo=obo)
