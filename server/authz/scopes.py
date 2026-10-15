"""scope 카탈로그·함의·R4.3 집합·확대(escalation) 판정 (10-policy.md A2, R2.1, R4.3, R4.4).

scope 이름은 문자열 하나다. grant 에는 와일드카드(`session.*`, `relay.*`, `*`)를 적을 수 있지만
**판정에 들어오는 scope 는 항상 카탈로그의 구체 이름**이어야 한다(R2.2 — 모르는 scope 는 거부).
"""

from __future__ import annotations

import fnmatch
import re
from typing import Iterable, Optional

# --- 카탈로그 ---------------------------------------------------------------------------

SESSION = (
    "session.list", "session.meta", "session.read", "session.stream",
    "session.input.line", "session.input.raw", "session.queue",
    "session.create", "session.kill", "session.rename",
    "session.clients.view", "session.clients.detach",
)
MSG = ("msg.send", "msg.read")
DELEG = ("deleg.spawn", "deleg.control", "deleg.report")
FS = ("fs.read", "fs.write", "fs.commit", "fs.drop")
BROWSER = (
    "browser.tab.open", "browser.tab.navigate", "browser.tab.read", "browser.tab.input", "browser.tab.close",
    "browser.js", "browser.cookies", "browser.storage", "browser.network", "browser.download",
    "browser.attach",
)
AUTHZ = ("authz.request", "authz.admin", "authz.audit")
MISC = ("pair.manage", "agent.event", "agent.report", "ping")

# relay 그룹 — `relay_policy._HTTP_RULES`의 패턴 하나하나에 이름을 붙인다(그룹은 인가 단위,
# 허용 목록 자체는 라우팅 정책으로 relay_policy 에 남는다 — 20-mapping.md B1).
# 패턴 문자열이 키라서 relay_policy 에 규칙이 늘면 test_authz_engine 이 이 표의 누락을 잡는다.
RELAY_HTTP_GROUPS: dict[str, str] = {
    r"/api/sessions(/[^/]+(/(keys|paste))?)?": "sessions",
    r"/api/tmux/(sessions|create|attach|clients|clients/solo|detach-client)": "tmux",
    r"/api/tmux/kill/[^/]+": "tmux.kill",
    r"/api/tmux/preview/[^/]+": "tmux.preview",
    r"/api/agents(/coverage|/[^/]+)?": "agents",
    r"/api/agent/status": "agents",
    r"/api/files": "files",
    r"/api/files/delete": "files.write",
    r"/api/files/[^/]+": "files.write",
    r"/api/files/[^/]+/(download|path)": "files",
    r"/api/files/[^/]+/insert": "files.write",
    r"/api/upload": "files.write",
    r"/api/fs/(file|raw|roots|search|tree)": "fs",
    r"/api/git/(status|diff|log|show)": "git",
    r"/api/git/(stage|unstage|commit)": "git.write",
    r"/api/ports": "ports",
    r"/api/ports/\d+": "ports",
    r"/api/queue(/run|/[^/]+(/unblock)?)?": "queue",
    r"/api/snippets(/project|/[^/]+)?": "snippets",
    r"/api/search/scrollback": "search",
    r"/api/usage(/counter)?": "usage",
    r"/api/workspace": "workspace",
    r"/api/worktrees(/precheck|/hidden|/[^/]+(/open)?)?": "worktrees",
    r"/api/repos": "repos",
    r"/api/(capabilities|hooks/status|safe-mode|tunnel/list|tailscale/status)": "status",
    r"/api/mcp(/plugins)?": "mcp",
    r"/api/watch/[^/]+": "watch",
}
RELAY_WS_GROUPS: dict[str, str] = {
    r"/ws/[A-Za-z0-9_\-:.]+": "ws.term",
    r"/ws-agent": "ws.agent",
    r"/ws-workspace": "ws.workspace",
    r"/ws-preview/[^/]+": "ws.preview",
    r"/ws-fs-watch": "ws.fswatch",
}
# `relay.connect` — "이 peer 가 relay 를 쓸 수 있는가"(그룹 판정 전 등급 관문, peer_relay.py).
RELAY = ("relay.connect",) + tuple(sorted({f"relay.{g}" for g in
                                           (*RELAY_HTTP_GROUPS.values(), *RELAY_WS_GROUPS.values())}))

# `api.access` — 미들웨어 인증 통과(main.py). 1단계 shadow 가 미들웨어 판정을 비교하려고 둔
# 메타 scope 다. 리소스는 `host:local`.
META = ("api.access",)

CATALOG: frozenset[str] = frozenset(SESSION + MSG + DELEG + FS + BROWSER + AUTHZ + MISC + RELAY + META)

# R2.1 — 명시한 함의만. 키가 값을 포함한다.
IMPLIES: dict[str, tuple[str, ...]] = {
    "session.input.raw": ("session.input.line",),
    "session.stream": ("session.read",),
    "fs.write": ("fs.read",),
}

# R4.3 — 비human 주체에게 주려면 elevated 승인이 필요한 scope.
ELEVATED_FOR_NON_HUMAN = frozenset({
    "session.input.raw", "relay.files.write", "relay.git.write", "relay.queue", "relay.ws.term",
    "browser.js", "browser.cookies", "browser.storage", "browser.attach",
})
# peer grant 에서 이 집합을 넘으면 elevated 승인(R4.3 "session.list/read/stream 을 넘는 peer:* grant").
PEER_PLAIN = frozenset({"session.list", "session.read", "session.stream", "ping"})
# 비elevated human 기본에서 빠지는 것(A3).
HUMAN_EXCLUDED = frozenset({"browser.js", "browser.cookies", "browser.attach"})
# 자동 승인 window 가 절대 덮지 못하는 것(R4.5).
NEVER_WINDOW = frozenset({"authz.request", "authz.admin", "authz.audit", "pair.manage"})


def known(scope: str) -> bool:
    return scope in CATALOG


def _expand_pattern(p: str) -> set[str]:
    if p == "*":
        return set(CATALOG)
    if any(c in p for c in "*?["):
        return {s for s in CATALOG if fnmatch.fnmatchcase(s, p)}
    return {p} if p in CATALOG else set()


def closure(scopes: Iterable[str]) -> frozenset[str]:
    """grant 의 scope 목록(와일드카드 포함)을 함의까지 펼친 구체 집합. 모르는 이름은 버린다(R2.2)."""
    out: set[str] = set()
    for p in scopes or ():
        out |= _expand_pattern(str(p))
    changed = True
    while changed:
        changed = False
        for s in list(out):
            for t in IMPLIES.get(s, ()):
                if t not in out:
                    out.add(t)
                    changed = True
    return frozenset(out)


def requires_elevated(principal_kind: str, scopes: Iterable[str], resource: str = "") -> set[str]:
    """R4.3 — 이 주체에게 이 scope 들을 주는 승인이 elevated 를 요구하는 이유 목록(빈 집합이면 일반 승인)."""
    if principal_kind == "human":
        return set()
    c = closure(scopes)
    out = {s for s in c if s in ELEVATED_FOR_NON_HUMAN}
    if principal_kind == "peer":
        out |= {s for s in c if s not in PEER_PLAIN}
    if "deleg.spawn" in c and resource and not resource.startswith("deleg-quota:agent:local/"):
        out.add("deleg.spawn(remote)")
    return out


# --- 리소스 -----------------------------------------------------------------------------

_RES_RE = re.compile(r"^(session|host|fs|browser|msg|deleg|deleg-quota|relay|authz)(:.*)?$")


def resource_ok(resource: str) -> bool:
    return bool(resource) and bool(_RES_RE.match(resource))


def resource_matches(pattern: str, resource: str) -> bool:
    """grant 의 리소스 선택자가 요청 리소스를 덮는가. 선택자는 glob(`session:local/*`)이다.
    `nick:`·`owned-by:` 선택자는 3단계(공유 이관)에서 해석한다 — 지금은 문자 그대로만 비교."""
    if pattern == "*":
        return True
    return fnmatch.fnmatchcase(resource, pattern)


def _res_broader(new: str, old: str) -> bool:
    """new 선택자가 old 가 덮지 않는 것을 덮을 수 있는가(보수적: 같지 않고 old 가 new 를 덮지 못하면 넓다)."""
    if new == old:
        return False
    if old == "*":
        return False
    # old 가 new 를 패턴으로 덮으면(new 가 더 좁거나 같으면) 넓지 않다.
    return not fnmatch.fnmatchcase(new, old)


# --- 확대 판정 (R4.4) ---------------------------------------------------------------------


def escalation(old: Optional[dict], new: dict) -> list[str]:
    """grant 변경이 권한 확대인가 — 확대 이유 목록(빈 목록이면 확대 아님).

    R4.4: 새 유효 집합이 기존의 부분집합이 아닌 모든 변경 — 새 scope, 넓은 선택자, 넓은 URL·path
    패턴, 더 늦은 만료, 조건 제거, 새 주체, 상한 상승, navigator→driver. 판정이 애매하면 확대로 본다.
    """
    if not old:
        return ["new_grant"]
    out: list[str] = []
    extra = closure(new.get("scopes") or ()) - closure(old.get("scopes") or ())
    if extra:
        out.append("scopes:" + ",".join(sorted(extra)))
    if new.get("subject") != old.get("subject") and _res_broader(new.get("subject", ""), old.get("subject", "")):
        out.append("subject")
    if _res_broader(new.get("resource", ""), old.get("resource", "")):
        out.append("resource")
    oe, ne = old.get("expires"), new.get("expires")
    if oe is not None and (ne is None or float(ne) > float(oe)):
        out.append("expires")
    oc, nc = old.get("conds") or {}, new.get("conds") or {}
    for k, v in oc.items():
        if k not in nc:
            out.append(f"cond_removed:{k}")
        elif nc[k] != v:
            if isinstance(v, list) and isinstance(nc[k], list) and set(map(str, nc[k])) <= set(map(str, v)):
                continue          # 목록(url_allowlist·path_glob) 이 좁아졌다
            if isinstance(v, (int, float)) and isinstance(nc[k], (int, float)) and nc[k] <= v:
                continue          # max_lines·max_uses 가 줄었다
            out.append(f"cond_widened:{k}")
    if (old.get("role"), new.get("role")) == ("navigator", "driver"):
        out.append("role:driver")
    return out


# --- relay 그룹 -------------------------------------------------------------------------


def relay_group(method: str, path: str) -> Optional[str]:
    """relay 대상의 그룹 scope(`relay.<group>`). 허용 목록 밖이면 None(→ 모르는 scope → 거부)."""
    import relay_policy

    if not relay_policy.http_allowed(method, path):
        return None
    p = relay_policy._clean(path)
    m = method.upper()
    for rx, methods in relay_policy._HTTP_RULES:
        if rx.fullmatch(p) and m in methods:
            g = RELAY_HTTP_GROUPS.get(rx.pattern)
            return f"relay.{g}" if g else None
    return None


def relay_ws_group(path: str) -> Optional[str]:
    import relay_policy

    if not relay_policy.ws_allowed(path):
        return None
    p = relay_policy._clean(path)
    for rx in relay_policy._WS_RULES:
        if rx.fullmatch(p):
            g = RELAY_WS_GROUPS.get(rx.pattern)
            return f"relay.{g}" if g else None
    return None
