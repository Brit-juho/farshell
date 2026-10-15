"""기본 권한과 아직 정하지 않은 정책의 임시 기본값.

**`# PROVISIONAL(<주제>): current behavior` 표시가 붙은 상수는 사용자가 아직 고르지 않은 정책이다.**
shadow 단계라 지금 동작을 그대로 적어 두었다(그래야 shadow 차이가 "정책이 바뀐 곳"이 아니라
"엔진·이전 규칙이 틀린 곳"만 가리킨다). 정책이 정해지면 이 파일의 해당 상수 하나만 바꾼다.

기본 권한은 grant 와 같은 모양의 dict 다(`approved_by: system`). 엔진은 저장소 grant 와 똑같이 다룬다.
조건이 없는 scope 는 scope 별 기본값(최소 권한)을 쓰므로, 더 넓게 줘야 하는 곳은 `sconds` 에 값을 적는다.
"""

from __future__ import annotations

from . import flags
from . import principals as P
from . import scopes as S

# --- 미정 정책 — 지금 동작 -----------------------------------------------------------------

# PROVISIONAL(peer-view-scope): current behavior — peer `view` 는 모든 세션의 목록·메타·접속 화면과 읽기·실시간 출력·검색.
PEER_VIEW_SCOPES: tuple[str, ...] = ("session.view", "session.read")
PEER_VIEW_SCONDS: dict = {}

# PROVISIONAL(peer-control-scope): current behavior — `control` 은 view + 입력(raw 포함)·세션 수명주기·파일 drop + relay 전 그룹.
PEER_CONTROL_SCOPES: tuple[str, ...] = PEER_VIEW_SCOPES + ("session.input", "session.manage", "fs.write", "relay")
PEER_CONTROL_SCONDS: dict = {
    "session.input": {"modes": ["line", "raw"]},
    "session.manage": {"ops": list(S.MANAGE_OPS)},
    "fs.write": {"ops": ["drop"]},
    "relay": {"groups": list(S.ALL_RELAY_GROUPS)},
}

# PROVISIONAL(peer-session-ceiling): current behavior — peer 의 호스트 단위 grant 에는 세션 공유 상한을 적용하지 않는다
# (지금 peer 는 공유 여부와 무관하게 모든 세션을 본다).
PEER_SESSION_CEILING_EXEMPT = True

# PROVISIONAL(local-msg-open): current behavior — 이 상수는 `VT_AUTHZ_ENFORCE` 에 `msg` 가 없을 때(꺼짐)의 동작이다: 로컬
# agent→agent `msg.send` 는 누구에게나 기본 허용. 켜짐에서는 위임 부모↔자식·형제(열린 위임만)와 pair 구성원끼리만 승인 없이,
# 그 밖은 (보내는 세션 → 받는 세션) grant 가 필요하다.
LOCAL_MSG_SEND_OPEN = True

# PROVISIONAL(cookie-without-password): current behavior — 비밀번호 없이 서명키만 있는 서버(token 모드)에서도 서명 세션
# 쿠키를 human 으로 인정한다.
HUMAN_COOKIE_WITHOUT_PASSWORD = True

# PROVISIONAL(unprotected-open): current behavior — 비밀번호·토큰이 모두 없는 서버의 무인증 호출은 기계 토큰과
# 같은 전체 권한(API 전체 공개).
UNPROTECTED_ANON_FULL = True

# PROVISIONAL(child-inherits): current behavior — 위임 자식은 부모 권한을 상속하지 않는다.
CHILD_INHERITS: tuple[str, ...] = ()

# PROVISIONAL(pane-tokens): current behavior — unix socket 만 있고 pane token 은 없다. pane token 주체는 만들지 않는다.
PANE_TOKENS_ENABLED = False

# 위임 cwd·worktree 기준을 fsguard 루트로 판정하는 지점 이름(grant 조건 `fs_roots`).
SPAWN_FS_PURPOSES: tuple[str, ...] = ("spawn_cwd", "spawn_worktree_base")

# --- 결정된 shim -------------------------------------------------------------------------

# 기계 토큰(`legacy_token`)은 기존 전체 권한을 그대로 갖는다(이행 기간 한정). 사람 전용(require_human_session)
# 경로는 지금도 403 이라 관리 scope 는 뺀다.
LEGACY_TOKEN_EXCLUDED = frozenset({"authz.admin", "pair.manage"})


def peer_preset(level: str) -> tuple[list[str], dict]:
    """hosts.json 의 등급(`view`|`control`) -> (scopes, sconds). 이전이 권한을 넓히면 안 되므로 옛 등급에 있던 것만 준다."""
    import json
    if level == "control":
        return list(PEER_CONTROL_SCOPES), json.loads(json.dumps(PEER_CONTROL_SCONDS))
    return list(PEER_VIEW_SCOPES), json.loads(json.dumps(PEER_VIEW_SCONDS))


def _full_conds(scopes) -> dict:
    """주어진 scope 들에 대해 조건 값 전체를 적은 sconds — 사람·기계 토큰 shim 같은 「전부」 규칙이 쓴다."""
    out = {}
    for s in scopes:
        cs = S.COND_SPEC.get(s)
        if cs:
            out[s] = {c.key: list(c.domain) for c in cs}
    return out


def _rule(rid: str, subject: str, resource: str, scopes, sconds=None, **extra) -> dict:
    rec = {"id": f"default:{rid}", "subject": subject, "resource": resource, "scopes": list(scopes),
           "state": "active", "approved_by": "system", "conds": extra.pop("conds", {}), "schema": S.SCHEMA, **extra}
    if sconds:
        rec["sconds"] = sconds
    return rec


def _all_local_except(excluded) -> list[str]:
    """이 호스트의 어휘 전부(relay 는 따로 준다 — 기본에 없다) + 내부 메타 판정."""
    return [s for s in S.CATALOG_ORDER if s not in excluded and s != "relay"] + list(S.INTERNAL)


def approver_rules(p: P.Principal) -> list[dict]:
    """승인자 확인 모드(`ctx.approving`)에서만 쓰는 추가 규칙 — 사람은 기본 권한에 없는 relay(그룹 전체)를 승인할 수 있고,
    elevated 사람은 거기에 browser.js·browser.sensitive 도 승인할 수 있다(R4.3: 승인은 elevated 가 한다). 일반 판정에는 닿지 않는다."""
    if p.kind != P.HUMAN:
        return []
    sc = ["relay"] + (["browser.js", "browser.sensitive"] if p.elevated else [])
    return [_rule("human_approver", p.id, "*", sc, _full_conds(sc))]


def rules_for(p: P.Principal) -> list[dict]:
    """주체 종류별 기본 권한. 리소스는 이 호스트(`local`)만."""
    if p.kind == P.HUMAN:
        excl = set(S.HUMAN_EXCLUDED)
        if not p.elevated:
            excl |= {"authz.admin"}      # 결정은 elevated 에서만
        sc = _all_local_except(excl)
        return [_rule("human", p.id, "*", sc, _full_conds(sc))]
    if p.kind == P.LEGACY:
        sc = _all_local_except(LEGACY_TOKEN_EXCLUDED)
        if p.attrs.get("open_server") and not UNPROTECTED_ANON_FULL:
            return []
        return [_rule("legacy_token", p.id, "*", sc, _full_conds(sc))]
    if p.kind == P.AGENT:
        key = p.attrs.get("key", "")
        own = f"session:local/{key}"
        rules = [
            # 자기 세션: 보기·읽기·한 줄 입력. 관리(생성·종료 …)와 raw 입력은 명시 grant 가 있어야 한다.
            _rule("agent_own_session", p.id, own, ["session.view", "session.read", "session.input"],
                  {"session.input": {"modes": ["line"], "via": ["direct"]}}),
            _rule("agent_events", p.id, own, ["agent.report"]),
            _rule("agent_authz_request", p.id, "authz:*", ["authz.request"]),
            # 열린 3개·깊이 1·VT_AGENT_SPAWN·분당 5회 — 조건은 engine._conds_ok 가 ctx 로 본다.
            _rule("agent_spawn_quota", p.id, f"deleg-quota:{p.id}", ["deleg.spawn"], conds={"deleg_quota": True}),
        ]
        # 위임 cwd·worktree 기준은 fsguard 루트 안일 때만. 판정 지점이 ctx.fs 를 넣을 때만 닿는 조건부 규칙이다
        # (그 밖의 fs.read 는 agent 기본에 없다). 파일 lease 가 생기면 그쪽으로 대체한다.
        rules.append(_rule("agent_spawn_fs_roots", p.id, "fs:local/*", ["fs.read"],
                           conds={"fs_roots": list(SPAWN_FS_PURPOSES)}))
        if flags.enforced("msg"):
            # 관계는 판정 지점이 ctx.msg.related 로 알려 준다(위임 장부·pair — authz/msg_svc.relation). 관계가 없으면 이 규칙은
            # 닿지 않고, 보내는→받는 세션 grant(`msg:local/<받는 세션 key>`)가 있어야 한다.
            rules.append(_rule("agent_local_msg_related", p.id, "msg:local/*", ["msg.send"], conds={"msg_related": True}))
        elif LOCAL_MSG_SEND_OPEN:
            rules.append(_rule("agent_local_msg", p.id, "msg:local/*", ["msg.send"]))
        return rules
    if p.kind == P.PEER:
        return [_rule("peer_ping", "peer:*", "host:local", ["ping"])]
    if P.is_cli(p):
        # cli 는 위임을 **닫을 수** 있다(세션은 남고 위험이 낮으며 감사된다). 보내기·승인·발급은 못 한다.
        return [_rule("cli_deleg_close", p.id, "deleg:local/*", ["deleg.control"], {"deleg.control": {"role": ["parent"]}},
                      conds={"ops": ["close"]})]
    # 서비스 데몬(`svc:*`)은 기본 권한이 없다(거부 쪽).
    return []
