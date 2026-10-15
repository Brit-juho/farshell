"""판정 지점별 shadow 호출 — 기존 코드에는 이 함수 호출 **한 줄**만 들어간다.

판정 지점마다 "어느 주체가 어느 리소스에 어느 scope 를" 묻는지 여기 한곳에 적는다. 호출부에
흩어 두면 지점 9곳이 각자 다른 규칙으로 엔진을 부르게 된다. 모든 함수는 `_never_raise`로 감싸
어떤 예외도 호출부로 새지 않는다(F8). 반환값은 없다 — 응답은 언제나 기존 판정이 정한다.
"""

from __future__ import annotations

import functools

from . import principals as P
from . import scopes as S
from . import shadow

# peer capability 등급 → 대표 scope(R8.2 의 등급 묶음에서 그 등급을 가르는 scope).
PEER_LEVEL_SCOPE = {"view": "session.list", "control": "session.input.raw"}


def _never_raise(fn):
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            shadow._error(fn.__name__, e)
    return wrapper


@_never_raise
def middleware(request, allowed: bool) -> None:
    """main.py TokenAuthMiddleware — 미들웨어 인증 통과 여부."""
    shadow.shadow_check(allowed, "main.middleware", lambda: P.from_request(request), "api.access", "host:local",
                        use_store=False)


@_never_raise
def agent_share(kind: str, allowed: bool, grant, me, target, snap) -> None:
    """routes/agent_share.py — 공유 세션 읽기(`read`)·쓰기(`write`)의 grant×공유모드 판정."""
    scope = "session.read" if kind == "read" else "session.input.line"
    shadow.shadow_check(
        allowed, f"agent_share.{kind}", lambda: P.from_caller(me), scope, f"session:local/{target.key}",
        {"share": target.share, "share_known": True, "alive": set(snap) if snap is not None else None},
        {"agent_grants": [grant] if grant else []})


@_never_raise
def deleg_spawn(allowed: bool, parent_key: str, data: dict, now: float, *, enabled: bool,
                max_open: int, per_min: int) -> None:
    """delegation.check_spawn — 열린 자식·깊이·분당 횟수·killswitch."""
    from . import limits
    if (max_open, per_min) != (limits.DELEG_MAX_OPEN, limits.DELEG_SPAWN_PER_MIN):
        raise RuntimeError("limits.py 의 위임 상수가 delegation.py 와 다르다")
    items = data.get("items") or []
    open_states = ("running", "reported")
    ctx = {"deleg": {
        "enabled": enabled,
        "is_child": any((x.get("child") or {}).get("key") == parent_key for x in items),
        "open_children": sum(1 for x in items
                             if (x.get("parent") or {}).get("key") == parent_key and x.get("state") in open_states),
        "recent_spawns": sum(1 for t in data.get("spawns") or [] if now - t < 60)}}
    p = P.agent(parent_key)
    shadow.shadow_check(allowed, "delegation.spawn", p, "deleg.spawn", f"deleg-quota:{p.id}", ctx)


@_never_raise
def deleg_owned(allowed: bool, did: str, caller_key: str, role: str, item) -> None:
    """delegation._owned — 부모만 send·close(`deleg.control`)."""
    scope = "deleg.control" if role == "parent" else "deleg.report"
    shadow.shadow_check(allowed, f"delegation.{role}", P.agent(caller_key), scope, f"deleg:local/{did}",
                        None, {"delegations": [item] if item else []})


@_never_raise
def deleg_report(allowed: bool, caller_key: str, mine: list) -> None:
    """delegation.report — 위임받은 자식만 보고."""
    did = mine[0].get("id") if mine else "-"
    shadow.shadow_check(allowed, "delegation.report", P.agent(caller_key), "deleg.report", f"deleg:local/{did}",
                        None, {"delegations": list(mine)})


@_never_raise
def agent_msg_local(request, to_session: str) -> None:
    """routes/agent_msg.py — 로컬 메시지에는 지금 인가가 없다(D6 기본 허용). 송신 주체는 요청 인증."""
    shadow.shadow_check(True, "agent_msg.local", lambda: P.from_request(request), "msg.send",
                        f"msg:local/{to_session}")


@_never_raise
def peer_level(grant: dict, level: str, allowed: bool) -> None:
    """routes/peer_protocol.py `_require`/`_require_body` — capability 등급."""
    shadow.shadow_check(allowed, f"peer.level.{level}", P.from_peer_grant(grant),
                        PEER_LEVEL_SCOPE.get(level, f"level.{level}"), "host:local", None,
                        {"peer_grants": [grant]})


@_never_raise
def peer_agent_messages(grant: dict, allowed: bool) -> None:
    """routes/peer.py `agent/message` — `fsh host allow-agent` 스위치."""
    pid = str(grant.get("id") or "")
    shadow.shadow_check(allowed, "peer.agent_message", P.from_peer_grant(grant, obo=f"agent:{pid}/-"),
                        "msg.send", "msg:local/-", None, {"peer_grants": [grant]})


@_never_raise
def peer_ws_control(grant: dict, tmux_name: str, allowed: bool) -> None:
    """routes/peer.py `peer_ws` — control 이면 입력 허용."""
    shadow.shadow_check(allowed, "peer.ws_input", P.from_peer_grant(grant), "session.input.raw",
                        f"session:local/name:{tmux_name}", None, {"peer_grants": [grant]})


@_never_raise
def relay_level(grant: dict, allowed: bool, ws: bool = False) -> None:
    """routes/peer_relay.py — relay 를 쓸 수 있는 등급인가."""
    shadow.shadow_check(allowed, "relay_ws.level" if ws else "relay.level", P.from_peer_grant(grant),
                        "relay.connect", "relay:local", None, {"peer_grants": [grant]})


@_never_raise
def relay_rule(grant: dict, method: str, target: str, allowed: bool, ws: bool = False) -> None:
    """routes/peer_relay.py — 허용 목록(라우팅 정책) + 그룹 scope."""
    group = S.relay_ws_group(target) if ws else S.relay_group(method, target)
    scope = group or "relay.unrouted"           # 카탈로그에 없다 → 엔진은 unknown_scope 로 거부
    shadow.shadow_check(allowed, "relay_ws.rule" if ws else "relay.rule", P.from_peer_grant(grant), scope,
                        f"relay:local/{(group or 'unrouted')[len('relay.'):] if group else 'unrouted'}",
                        None, {"peer_grants": [grant]})
