"""기본 권한(10-policy.md A3)과 아직 정하지 않은 결정(D#)의 임시 기본값.

**`# PROVISIONAL D<n>: current behavior` 표시가 붙은 상수는 사용자가 아직 고르지 않은 결정이다.**
1단계는 shadow 모드라 지금 동작을 그대로 적어 두었다(그래야 shadow 차이가 "정책이 바뀐 곳"이 아니라
"엔진·이전 규칙이 틀린 곳"만 가리킨다). 결정이 나면 이 파일의 해당 상수 하나만 바꾼다.

기본 권한은 grant 와 같은 모양의 dict 다(`approved_by: system`). 엔진은 저장소 grant 와 똑같이 다룬다.
"""

from __future__ import annotations

from . import principals as P

# --- 미결정(D#) — 지금 동작 ---------------------------------------------------------------

# PROVISIONAL D3: current behavior — peer `view` 는 모든 세션 read·stream + cwd(meta) + 검색(H10 유지).
PEER_VIEW_SCOPES: tuple[str, ...] = (
    "session.list", "session.meta", "session.read", "session.stream", "session.clients.view", "ping")

# PROVISIONAL D7: current behavior — `control` 은 view + 입력·수명주기 + relay 전 그룹.
PEER_CONTROL_SCOPES: tuple[str, ...] = PEER_VIEW_SCOPES + (
    "session.input.raw", "session.create", "session.kill", "session.rename", "session.clients.detach",
    "fs.drop", "relay.*")

# PROVISIONAL D3: current behavior — peer 의 호스트 단위 grant 에는 세션 공유 상한을 적용하지 않는다
# (지금 peer 는 공유 여부와 무관하게 모든 세션을 본다).
PEER_SESSION_CEILING_EXEMPT = True

# PROVISIONAL D6: current behavior — 로컬 agent→agent `msg.send` 는 기본 허용(대상 inbox grant 불필요).
LOCAL_MSG_SEND_OPEN = True

# PROVISIONAL D8: current behavior — 비밀번호 없이 서명키만 있는 서버(token 모드)에서도 서명 세션
# 쿠키를 human 으로 인정한다(H14).
HUMAN_COOKIE_WITHOUT_PASSWORD = True

# PROVISIONAL D8: current behavior — 비밀번호·토큰이 모두 없는 서버(H6)의 무인증 호출은 기계 토큰과
# 같은 전체 권한(API 전체 공개).
UNPROTECTED_ANON_FULL = True

# PROVISIONAL D13: current behavior — 위임 자식은 부모 권한을 상속하지 않는다.
CHILD_INHERITS: tuple[str, ...] = ()

# PROVISIONAL D10: current behavior — 1단계는 unix socket·pane token 둘 다 없다(socket 은 2단계).
# pane token 주체는 만들지 않는다.
PANE_TOKENS_ENABLED = False

# 3b — fs.read 를 fsguard 루트로 판정하는 위임 지점 이름(engine `conds.fs_roots`).
SPAWN_FS_PURPOSES: tuple[str, ...] = ("spawn_cwd", "spawn_worktree_base")

# --- 결정된 shim -------------------------------------------------------------------------

# B1: `machine` → `legacy_token`(기존 전체 권한, 8단계 제거). 사람 전용(require_human_session)
# 경로는 지금도 403 이라 관리 scope 는 뺀다.
LEGACY_TOKEN_EXCLUDED = frozenset({"authz.admin", "authz.audit", "pair.manage"})


def _rule(rid: str, subject: str, resource: str, scopes, **extra) -> dict:
    return {"id": f"default:{rid}", "subject": subject, "resource": resource, "scopes": list(scopes),
            "state": "active", "approved_by": "system", "conds": extra.pop("conds", {}), **extra}


def _all_local_except(excluded) -> list[str]:
    from . import scopes as S
    return sorted(s for s in S.CATALOG if s not in excluded and not s.startswith("relay."))


def rules_for(p: P.Principal) -> list[dict]:
    """주체 종류별 기본 권한(A3). 리소스는 이 호스트(`local`)만."""
    from . import scopes as S

    if p.kind == P.HUMAN:
        excl = set(S.HUMAN_EXCLUDED)
        if not p.elevated:
            excl |= {"authz.admin"}      # R4.3 결정은 elevated 에서만(1단계엔 판정 지점 없음)
        return [_rule("human", p.id, "*", _all_local_except(excl))]
    if p.kind == P.LEGACY:
        return [_rule("legacy_token", p.id, "*", _all_local_except(LEGACY_TOKEN_EXCLUDED))]
    if p.kind == P.ANON:
        if not p.protected and UNPROTECTED_ANON_FULL:
            return [_rule("anon_unprotected", p.id, "*", _all_local_except(LEGACY_TOKEN_EXCLUDED))]
        return []
    if p.kind == P.AGENT:
        key = p.attrs.get("key", "")
        own = f"session:local/{key}"
        rules = [
            _rule("agent_own_session", p.id, own, ["session.*"]),
            _rule("agent_own_msg_read", p.id, f"msg:local/{key}", ["msg.read"]),
            _rule("agent_events", p.id, own, ["agent.event", "agent.report"]),
            _rule("agent_authz_request", p.id, "authz:*", ["authz.request"]),
            # 열린 3개·깊이 1·VT_AGENT_SPAWN·분당 5회 — 조건은 engine._conds 가 ctx 로 본다.
            _rule("agent_spawn_quota", p.id, f"deleg-quota:{p.id}", ["deleg.spawn"],
                  conds={"deleg_quota": True}),
        ]
        # H9(3b) — 위임 cwd·worktree 기준은 fsguard 루트 안일 때만. 판정 지점이 ctx.fs 를 넣을 때만 닿는 조건부 규칙이다
        # (그 밖의 fs.read 는 agent 기본에 없다). 5단계에서 lease 로 대체한다.
        rules.append(_rule("agent_spawn_fs_roots", p.id, "fs:local/*", ["fs.read"],
                           conds={"fs_roots": list(SPAWN_FS_PURPOSES)}))
        if LOCAL_MSG_SEND_OPEN:
            rules.append(_rule("agent_local_msg", p.id, "msg:local/*", ["msg.send"]))
        return rules
    if p.kind == P.PEER:
        return [_rule("peer_ping", "peer:*", "host:local", ["ping"])]
    if p.kind == P.CLI:
        # H3(3b, 잠정 정책) — cli 는 위임을 **닫을 수** 있다(세션은 남고 위험이 낮으며 감사된다). 보내기·승인·발급은 못 한다(D1=b).
        return [_rule("cli_deleg_close", p.id, "deleg:local/*", ["deleg.control"], conds={"ops": ["close"]})]
    # svc·hook·pair 주체는 5단계에 생긴다. 지금은 기본 권한 없음(거부 쪽).
    return []
