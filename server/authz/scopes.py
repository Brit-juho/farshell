"""권한 어휘: scope 17개 + 조건, 옛 이름 변환, 프리셋, elevated·확대 판정.

원칙: **scope 는 "어떤 종류의 힘"만 가리키고, 세부는 자원 선택자와 조건(`conds`)이 가진다.**
조건은 scope 마다 정해진 키(`modes`·`ops`·`groups`·`role` …)로, grant 의 `sconds[scope][키] = [값…]` 에 적는다
(grant 전체에 걸리는 일반 조건 `conds` 와 다르다 — `max_lines`, `path_glob`, `ops`(동작 허용 목록) 등).

판정 규칙
  - 각 scope 는 자신이 요구하는 조건 키를 선언한다(`REQUIRED_CONDS`). 호출 지점은 판정 컨텍스트(ctx)에 그 값을
    넣는다(`mode`, `op`, `group`, `role`). **필요한 값이 없으면 거부**한다 — 모르는 scope 거부와 같은 원칙이다.
  - grant 에 조건이 없으면 scope 별 기본값을 쓴다(최소 권한): 입력 modes=[line], 관리 ops=[], relay groups=[],
    파일 쓰기 ops=[write], 브라우저 ops=열기·이동·읽기·입력·닫기(js 제외).
  - 판정에 들어오는 scope 는 항상 카탈로그의 구체 이름이다. grant 에는 와일드카드(`session.*`, `*`)를 적을 수 있다.
  - `ping`·`api.access` 는 인증 계층의 내부 메타 판정이라 어휘 밖이다(`INTERNAL`).

옛 67개 이름은 `LEGACY_TO_COARSE` 표로 (새 scope, 조건)에 대응한다. 저장소를 읽을 때 `schema` 가 2가 아닌 레코드를
이 표로 변환하고(`upgrade_record`), 쓸 때는 새 형태로 쓴다. 감사 체인의 과거 기록은 바꾸지 않는다.
"""

from __future__ import annotations

import fnmatch
import functools
import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional

SCHEMA = 2

# --- 어휘 -------------------------------------------------------------------------------

CATALOG_ORDER: tuple[str, ...] = (
    "session.view", "session.read", "session.input", "session.manage",
    "msg.send",
    "deleg.spawn", "deleg.control",
    "fs.read", "fs.write",
    "browser", "browser.js", "browser.sensitive",
    "relay",
    "authz.request", "authz.admin",
    "pair.manage", "agent.report",
)
CATALOG: frozenset[str] = frozenset(CATALOG_ORDER)

# 인증 계층의 메타 판정 — 어휘(17개)에 들지 않는다. 판정은 받되 grant 에는 저장하지 않는다.
INTERNAL: tuple[str, ...] = ("ping", "api.access")

# relay 그룹 — `relay_policy._HTTP_RULES` 패턴 하나하나에 이름을 붙인다(그룹은 인가 단위, 허용 목록 자체는
# 라우팅 정책으로 relay_policy 에 남는다). 패턴 문자열이 키라서 relay_policy 에 규칙이 늘면 test_authz_engine 이
# 이 표의 누락을 잡는다.
RELAY_HTTP_GROUPS: dict[str, str] = {
    r"/api/sessions(/[^/]+(/(keys|paste))?)?": "sessions",
    r"/api/tmux/(sessions|create|attach|clients|clients/solo|detach-client)": "tmux",
    r"/api/tmux/kill/[^/]+": "tmux.kill",
    r"/api/tmux/preview/[^/]+": "tmux.preview",
    r"/api/agents(/coverage|/[^/]+)?": "agents",
    r"/api/agent/status": "agents",
    r"/api/agents/[^/]+/subagents/[A-Za-z0-9_-]{1,64}/transcript": "agents",
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
    r"/api/(capabilities|hooks/status|safe-mode|tunnel/list|tailscale/status|system/stats)": "status",
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
ALL_RELAY_GROUPS: tuple[str, ...] = tuple(sorted({*RELAY_HTTP_GROUPS.values(), *RELAY_WS_GROUPS.values()}))
# 명시해야만 열리는 relay 그룹 — 기본 그룹 목록(`ALL_RELAY_GROUPS`)·프리셋·`*`·사람 기본 권한에 들지 않는다. grant 가 이름으로 적어야 하고,
# deny 규칙(금지)은 넓게 막으므로 이 그룹도 덮는다. 규칙 패턴은 `relay_policy._OPTIN_HTTP_RULES`.
OPTIN_RELAY_GROUPS: tuple[str, ...] = ("browser",)
OPTIN_RELAY_HTTP_GROUPS: dict[str, str] = {r"/api/browser/(adapters|tabs)(/.*)?": "browser"}
# 「relay 를 쓸 수 있는가」(연결 관문) — 그룹 하나 이상을 가졌는지를 묻는다.
ANY_GROUP = "*"

# --- 조건 ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cond:
    ctx: str                      # 판정 컨텍스트의 키
    key: str                      # grant `sconds[scope]` 의 키
    domain: tuple                 # 가능한 값
    default: tuple                # grant 에 조건이 없을 때의 값(최소 권한)
    required: bool = True         # 컨텍스트에 값이 없으면 거부하는가
    extra: tuple = ()             # 명시해야만 허용되는 값 — `*`·기본값·사람 기본에 들지 않는다(deny 는 포함)


INPUT_MODES = ("line", "raw")
INPUT_VIA = ("direct", "queue")
MANAGE_OPS = ("create", "kill", "rename", "detach")
FS_WRITE_OPS = ("write", "commit", "drop")
BROWSER_OPS = ("open", "navigate", "read", "input", "close")
SENSITIVE_OPS = ("cookies", "storage", "network", "download", "attach")
DELEG_ROLES = ("parent", "child")

COND_SPEC: dict[str, tuple[Cond, ...]] = {
    "session.input": (Cond("mode", "modes", INPUT_MODES, ("line",)),
                      Cond("via", "via", INPUT_VIA, ("direct",), required=False)),
    "session.manage": (Cond("op", "ops", MANAGE_OPS, ()),),
    "deleg.control": (Cond("role", "role", DELEG_ROLES, ()),),
    "fs.write": (Cond("op", "ops", FS_WRITE_OPS, ("write",)),),
    "browser": (Cond("op", "ops", BROWSER_OPS, BROWSER_OPS),),
    "browser.sensitive": (Cond("op", "ops", SENSITIVE_OPS, ()),),
    "relay": (Cond("group", "groups", ALL_RELAY_GROUPS, (), extra=OPTIN_RELAY_GROUPS),),
}
# (scope -> 판정 컨텍스트에 반드시 있어야 하는 키). 메타테스트가 모든 호출 지점과 대조한다.
REQUIRED_CONDS: dict[str, tuple[str, ...]] = {
    s: tuple(c.ctx for c in cs if c.required) for s, cs in COND_SPEC.items() if any(c.required for c in cs)}

Caps = dict  # {scope: {조건 키: frozenset(값)}}


def _expand_new(p: str) -> set[str]:
    if p == "*":
        return set(CATALOG)
    if any(c in p for c in "*?["):
        return {s for s in CATALOG if fnmatch.fnmatchcase(s, p)}
    return {p} if (p in CATALOG or p in INTERNAL) else set()


def _values(c: Cond, given, *, deny: bool) -> frozenset:
    if given is None:
        return frozenset((*c.domain, *c.extra) if deny else c.default)
    if isinstance(given, str):
        given = [given]
    out: set = set()
    for v in given:
        v = str(v)
        if v == "*":
            out |= set((*c.domain, *c.extra) if deny else c.domain)
        elif v in c.domain or v in c.extra:
            out.add(v)
    return frozenset(out)


@functools.lru_cache(maxsize=4096)
def _effective(scopes: tuple, sconds_json: str, deny: bool) -> tuple:
    sconds = json.loads(sconds_json) if sconds_json else {}
    names: set[str] = set()
    for p in scopes:
        names |= _expand_new(str(p))
    caps: dict[str, dict] = {}
    for s in names:
        entry: dict = {}
        given = sconds.get(s) if isinstance(sconds, dict) else None
        given = given if isinstance(given, dict) else {}
        for c in COND_SPEC.get(s, ()):
            entry[c.key] = _values(c, given.get(c.key), deny=deny)
        caps[s] = entry
    # 함의(명시한 것만): raw 입력은 한 줄 입력을 포함, 파일 쓰기(write)는 읽기를 포함.
    inp = caps.get("session.input")
    if inp is not None and "raw" in inp["modes"]:
        inp["modes"] = frozenset(inp["modes"] | {"line"})
    fw = caps.get("fs.write")
    if fw is not None and "write" in fw["ops"] and "fs.read" not in caps:
        caps["fs.read"] = {}
    return tuple(sorted(((s, tuple(sorted((k, tuple(sorted(v))) for k, v in e.items()))) for s, e in caps.items())))


def effective(scopes: Iterable[str], sconds: Optional[dict] = None, *, deny: bool = False) -> Caps:
    """grant 의 (scopes, sconds) → `{scope: {조건 키: frozenset(값)}}`. 와일드카드·기본값·함의를 모두 푼 정규형.
    `deny=True` 면 조건이 없을 때 기본값이 아니라 **전체 값**이다(금지 규칙은 넓게 막는다). 모르는 이름·값은 버린다."""
    raw = _effective(tuple(str(x) for x in (scopes or ())),
                     json.dumps(sconds, sort_keys=True) if sconds else "", bool(deny))
    return {s: {k: frozenset(v) for k, v in e} for s, e in raw}


def caps_of(g: dict, *, deny: bool = False) -> Caps:
    return effective(g.get("scopes") or (), g.get("sconds"), deny=deny)


def caps_view(caps: Caps) -> dict:
    """감사·API 용 JSON 모양 `{scope: {키: [값…]}}`."""
    return {s: {k: sorted(v) for k, v in e.items()} for s, e in sorted(caps.items())}


def known(scope: str) -> bool:
    return scope in CATALOG or scope in INTERNAL


def closure(scopes: Iterable[str]) -> frozenset[str]:
    """grant 의 scope 목록(와일드카드 포함)이 가리키는 구체 scope 이름 집합(함의 포함). 모르는 이름은 버린다."""
    return frozenset(effective(scopes))


def missing_conds(scope: str, ctx: dict) -> list[str]:
    """이 scope 가 요구하는 조건 키 중 컨텍스트에 값이 없는 것."""
    return [k for k in REQUIRED_CONDS.get(scope, ()) if not (ctx or {}).get(k)]


def conds_match(scope: str, entry: dict, ctx: dict) -> tuple[bool, str]:
    """grant 가 가진 조건 값(`entry`)이 컨텍스트의 값을 허용하는가. (허용, 거부 이유)."""
    for c in COND_SPEC.get(scope, ()):
        v = (ctx or {}).get(c.ctx)
        if v in (None, ""):
            if c.required:
                return False, "cond_missing"
            v = c.default[0]
        allowed = entry.get(c.key, frozenset())
        if scope == "relay" and v == ANY_GROUP:
            ok = bool(allowed)
        else:
            ok = str(v) in allowed
        if not ok:
            return False, f"cond_{c.key}"
    return True, ""


# --- 옛 이름 -> 새 어휘 (67개) ---------------------------------------------------------------

LEGACY_CATALOG: tuple[str, ...] = (
    "session.list", "session.meta", "session.read", "session.stream",
    "session.input.line", "session.input.raw", "session.queue",
    "session.create", "session.kill", "session.rename",
    "session.clients.view", "session.clients.detach",
    "msg.send", "msg.read",
    "deleg.spawn", "deleg.control", "deleg.report",
    "fs.read", "fs.write", "fs.commit", "fs.drop",
    "browser.tab.open", "browser.tab.navigate", "browser.tab.read", "browser.tab.input", "browser.tab.close",
    "browser.js", "browser.cookies", "browser.storage", "browser.network", "browser.download", "browser.attach",
    "authz.request", "authz.admin", "authz.audit",
    "pair.manage", "agent.event", "agent.report", "ping",
    "relay.connect",
    "relay.sessions", "relay.tmux", "relay.tmux.kill", "relay.tmux.preview", "relay.agents",
    "relay.files", "relay.files.write", "relay.fs", "relay.git", "relay.git.write", "relay.ports",
    "relay.queue", "relay.snippets", "relay.search", "relay.usage", "relay.workspace", "relay.worktrees",
    "relay.repos", "relay.status", "relay.mcp", "relay.watch",
    "relay.ws.term", "relay.ws.agent", "relay.ws.workspace", "relay.ws.preview", "relay.ws.fswatch",
    "api.access",
)

_INT = "internal"        # 어휘 밖(인증 계층 메타 판정)
LEGACY_TO_COARSE: dict[str, tuple] = {
    "session.list": ("session.view", {}),
    "session.meta": ("session.view", {}),
    "session.clients.view": ("session.view", {}),
    "session.read": ("session.read", {}),
    "session.stream": ("session.read", {}),
    "session.input.line": ("session.input", {"modes": ["line"]}),
    "session.input.raw": ("session.input", {"modes": ["line", "raw"]}),
    "session.queue": ("session.input", {"modes": ["line"], "via": ["queue"]}),
    "session.create": ("session.manage", {"ops": ["create"]}),
    "session.kill": ("session.manage", {"ops": ["kill"]}),
    "session.rename": ("session.manage", {"ops": ["rename"]}),
    "session.clients.detach": ("session.manage", {"ops": ["detach"]}),
    "msg.send": ("msg.send", {}),
    "msg.read": (None, {}),                       # 본인 기록이라 모든 주체 기본 허용 — grant 에서 뺀다
    "deleg.spawn": ("deleg.spawn", {}),
    "deleg.control": ("deleg.control", {"role": ["parent"]}),
    "deleg.report": ("deleg.control", {"role": ["child"]}),
    "fs.read": ("fs.read", {}),
    "fs.write": ("fs.write", {"ops": ["write"]}),
    "fs.commit": ("fs.write", {"ops": ["commit"]}),
    "fs.drop": ("fs.write", {"ops": ["drop"]}),
    "browser.tab.open": ("browser", {"ops": ["open"]}),
    "browser.tab.navigate": ("browser", {"ops": ["navigate"]}),
    "browser.tab.read": ("browser", {"ops": ["read"]}),
    "browser.tab.input": ("browser", {"ops": ["input"]}),
    "browser.tab.close": ("browser", {"ops": ["close"]}),
    "browser.js": ("browser.js", {}),
    "browser.cookies": ("browser.sensitive", {"ops": ["cookies"]}),
    "browser.storage": ("browser.sensitive", {"ops": ["storage"]}),
    "browser.network": ("browser.sensitive", {"ops": ["network"]}),
    "browser.download": ("browser.sensitive", {"ops": ["download"]}),
    "browser.attach": ("browser.sensitive", {"ops": ["attach"]}),
    "authz.request": ("authz.request", {}),
    "authz.admin": ("authz.admin", {}),
    "authz.audit": ("authz.admin", {}),
    "pair.manage": ("pair.manage", {}),
    "agent.event": ("agent.report", {}),
    "agent.report": ("agent.report", {}),
    "ping": (_INT, {}),
    "relay.connect": ("relay", {}),               # 연결 관문은 그룹 지정이 대신한다(그룹이 하나라도 있으면 통과)
    "relay.sessions": ("relay", {"groups": ["sessions"]}),
    "relay.tmux": ("relay", {"groups": ["tmux"]}),
    "relay.tmux.kill": ("relay", {"groups": ["tmux.kill"]}),
    "relay.tmux.preview": ("relay", {"groups": ["tmux.preview"]}),
    "relay.agents": ("relay", {"groups": ["agents"]}),
    "relay.files": ("relay", {"groups": ["files"]}),
    "relay.files.write": ("relay", {"groups": ["files.write"]}),
    "relay.fs": ("relay", {"groups": ["fs"]}),
    "relay.git": ("relay", {"groups": ["git"]}),
    "relay.git.write": ("relay", {"groups": ["git.write"]}),
    "relay.ports": ("relay", {"groups": ["ports"]}),
    "relay.queue": ("relay", {"groups": ["queue"]}),
    "relay.snippets": ("relay", {"groups": ["snippets"]}),
    "relay.search": ("relay", {"groups": ["search"]}),
    "relay.usage": ("relay", {"groups": ["usage"]}),
    "relay.workspace": ("relay", {"groups": ["workspace"]}),
    "relay.worktrees": ("relay", {"groups": ["worktrees"]}),
    "relay.repos": ("relay", {"groups": ["repos"]}),
    "relay.status": ("relay", {"groups": ["status"]}),
    "relay.mcp": ("relay", {"groups": ["mcp"]}),
    "relay.watch": ("relay", {"groups": ["watch"]}),
    "relay.ws.term": ("relay", {"groups": ["ws.term"]}),
    "relay.ws.agent": ("relay", {"groups": ["ws.agent"]}),
    "relay.ws.workspace": ("relay", {"groups": ["ws.workspace"]}),
    "relay.ws.preview": ("relay", {"groups": ["ws.preview"]}),
    "relay.ws.fswatch": ("relay", {"groups": ["ws.fswatch"]}),
    "api.access": (_INT, {}),
}

# 옛 이름 중 같은 (새 scope, 조건)으로 합쳐져 의미가 같아지는 묶음 — 변환 뒤에는 서로 구별되지 않는다.
LEGACY_MERGED_GROUPS: tuple[frozenset, ...] = (
    frozenset({"session.list", "session.meta", "session.clients.view"}),
    frozenset({"session.read", "session.stream"}),
    frozenset({"authz.admin", "authz.audit"}),
    frozenset({"agent.event", "agent.report"}),
)


def legacy_expand(scopes: Iterable[str]) -> set[str]:
    """옛 형태 grant 의 scope 목록(와일드카드 포함)을 옛 이름 집합으로 푼다(옛 함의 포함). 모르는 이름은 버린다."""
    out: set[str] = set()
    for p in scopes or ():
        p = str(p)
        if p == "*":
            out |= set(LEGACY_CATALOG)
        elif any(c in p for c in "*?["):
            out |= {s for s in LEGACY_CATALOG if fnmatch.fnmatchcase(s, p)}
        elif p in LEGACY_CATALOG:
            out.add(p)
    changed = True
    implies = {"session.input.raw": ("session.input.line",), "session.stream": ("session.read",),
               "fs.write": ("fs.read",)}
    while changed:
        changed = False
        for s in list(out):
            for t in implies.get(s, ()):
                if t not in out:
                    out.add(t)
                    changed = True
    return out


def translate_legacy(scopes: Iterable[str]) -> tuple[list[str], dict]:
    """옛 이름 목록 -> (새 scope 목록, sconds). 같은 새 scope 로 모인 이름은 조건 값을 합집합으로 합친다.
    옛 함의(raw ⊃ line, stream ⊃ read, fs.write ⊃ fs.read)는 펼친 채로 변환한다. 어휘 밖(ping 등)·`msg.read` 는 버린다."""
    scopes = list(scopes or ())
    names = legacy_expand(scopes)
    members: dict[str, list[dict]] = {}              # 새 scope -> 거기로 모인 옛 이름들의 조건
    for n in sorted(names):
        sc, conds = LEGACY_TO_COARSE[n]
        if sc in (None, _INT):
            continue
        members.setdefault(sc, []).append(conds)
    # 새 이름이 섞여 있으면(옛 형태 레코드에 새 이름) 그대로 둔다 — 기본 조건을 쓴다.
    for p in scopes:
        p = str(p)
        if p not in LEGACY_CATALOG and not any(c in p for c in "*?[") and p in CATALOG:
            members.setdefault(p, []).append({})
    out_scopes = [s for s in CATALOG_ORDER if s in members]
    sconds: dict = {}
    for s in out_scopes:
        slot = {}
        for c in COND_SPEC.get(s, ()):
            # 이름이 그 조건을 적지 않았으면 기본값을 뜻한다(예: input.line 은 직접 입력 = via 기본 direct) — 합집합도 그렇게 낸다.
            vals: set = set()
            for conds in members[s]:
                vals |= set(conds[c.key]) if c.key in conds else set(c.default)
            declared = any(c.key in conds for conds in members[s])
            if declared or vals != set(c.default):
                slot[c.key] = [v for v in c.domain if v in vals]
        if slot:
            sconds[s] = slot
    return out_scopes, sconds


def upgrade_record(rec):
    """저장소에서 읽은 grant·approval 레코드를 새 형태(`schema` 2)로 — 이미 2 이면 그대로. 순수 함수·멱등.
    `scopes` 가 없는 레코드는 `schema` 표시만 더한다."""
    if not isinstance(rec, dict) or rec.get("schema") == SCHEMA:
        return rec
    out = dict(rec)
    sc = rec.get("scopes")
    if isinstance(sc, (list, tuple)):
        new_scopes, sconds = translate_legacy(sc)
        out["scopes"] = new_scopes
        if sconds:
            out["sconds"] = sconds
        else:
            out.pop("sconds", None)
    out["schema"] = SCHEMA
    return out


def coarse_call(old: str) -> tuple[Optional[str], dict]:
    """옛 이름 하나가 가리키는 판정 호출 `(새 scope, ctx)`. 변환 표의 왕복·parity 시험이 쓴다.
    조건 값이 여럿이면 가장 센 쪽을 묻는다(raw ⊃ line). 어휘 밖·scope 없음이면 `(None, {})`."""
    sc, conds = LEGACY_TO_COARSE[old]
    if sc in (None, _INT):
        return None, {}
    ctx: dict = {}
    for c in COND_SPEC.get(sc, ()):
        vs = conds.get(c.key)
        if vs:
            ctx[c.ctx] = vs[-1]
    if sc == "relay" and "group" not in ctx:
        ctx["group"] = ANY_GROUP
    return sc, ctx


# --- 프리셋 ---------------------------------------------------------------------------------


def _full(scope: str) -> dict:
    return {c.key: list(c.domain) for c in COND_SPEC[scope] if c.required or c.key == "via"}


PRESET_ORDER = ("view", "collab", "drive", "control")
PRESET_LABELS = {"view": "보기", "collab": "협업", "drive": "운전", "control": "제어"}

_VIEW = (["session.view", "session.read"], {})
_COLLAB = (_VIEW[0] + ["msg.send", "fs.read", "browser"], {"browser": {"ops": ["read"]}})
_DRIVE = (_COLLAB[0] + ["session.input", "fs.write", "deleg.spawn"],
          {"browser": {"ops": list(BROWSER_OPS)}, "session.input": {"modes": ["line"]},
           "fs.write": {"ops": ["write"]}})
_CONTROL = (_DRIVE[0] + ["session.manage", "relay"],
            {**_DRIVE[1], "session.input": {"modes": ["line", "raw"]},
             "session.manage": {"ops": list(MANAGE_OPS)}, "relay": {"groups": list(ALL_RELAY_GROUPS)}})
PRESETS: dict[str, tuple[list[str], dict]] = {"view": _VIEW, "collab": _COLLAB, "drive": _DRIVE, "control": _CONTROL}


def preset(name: str) -> tuple[list[str], dict]:
    """프리셋(보기·협업·운전·제어) -> (scopes, sconds). 복사본을 돌려준다."""
    sc, cd = PRESETS[name]
    return list(sc), json.loads(json.dumps(cd))


def preset_requires_elevated(name: str, kind: str = "agent") -> set[str]:
    sc, cd = preset(name)
    return requires_elevated(kind, sc, "", cd)


# --- elevated(재인증한 사람의 승인) 판정 -------------------------------------------------------

# 비human 주체에게 주려면 elevated 승인이 필요한 relay 그룹(쓰기·입력 성격).
ELEVATED_RELAY_GROUPS = frozenset({"files.write", "git.write", "queue", "ws.term"})
# peer grant 에서 이 집합을 넘으면 elevated 승인.
PEER_PLAIN = frozenset({"session.view", "session.read"})
# 비elevated 로 얻는 human 기본에서 빠지는 것.
# grant 가 어떻게 승인됐는가(`approved_by` 옆의 `approved_via`) — 출처일 뿐 위임 권한이 아니다.
VIA_MANUAL, VIA_WINDOW, VIA_SYSTEM, VIA_MIGRATED = "manual", "window", "system", "migrated"
APPROVED_VIA = (VIA_MANUAL, VIA_WINDOW, VIA_SYSTEM, VIA_MIGRATED)


def via_of(rec) -> str:
    """레코드의 승인 경로. 필드가 없거나 모르는 값이면(옛 레코드) `migrated`."""
    v = rec.get("approved_via") if isinstance(rec, dict) else None
    return v if v in APPROVED_VIA else VIA_MIGRATED


# 전송로별 상한(N-24) — 다른 Mac 이 서명으로 보증한 에이전트(대리 주체 `peer:X/obo:agent:X/…`)가 **어떤 grant 를 받았더라도** 쓸 수 있는 scope 의 좁은 허용 목록.
# 이 경로는 공개 터널로도 닿으므로 로컬 에이전트보다 좁다: 세션 보기·읽기·한 줄 입력, 원격 위임, 메시지, 승인 요청뿐이다. 승인함·권한 관리(`authz.admin`)·
# pair 관리·세션 관리·relay·브라우저·파일은 grant 가 있어도 이 표면으로는 열리지 않는다(엔진이 `surface_forbidden` 으로 거부 — 관리 경로가 터널로 올라오지 않게).
PEER_SURFACE_SCOPES = frozenset({"ping", "authz.request", "session.view", "session.read", "session.input", "msg.send",
                                 "deleg.spawn", "deleg.control"})

HUMAN_EXCLUDED = frozenset({"browser.js", "browser.sensitive"})
# 자동 승인 window 가 절대 덮지 못하는 것.
NEVER_WINDOW = frozenset({"authz.request", "authz.admin", "pair.manage"})


def requires_elevated(principal_kind: str, scopes: Iterable[str], resource: str = "",
                      sconds: Optional[dict] = None) -> set[str]:
    """(scope, 조건)으로 본 elevated 사유 — 빈 집합이면 일반 승인으로 충분하다. scope 이름만이 아니라 조건 값을 본다."""
    if principal_kind == "human":
        return set()
    caps = effective(scopes, sconds)
    out: set[str] = set()
    if "raw" in caps.get("session.input", {}).get("modes", ()):
        out.add("session.input[raw]")
    for g in sorted(caps.get("relay", {}).get("groups", frozenset()) & ELEVATED_RELAY_GROUPS):
        out.add(f"relay[{g}]")
    for s in ("browser.js", "browser.sensitive"):
        if s in caps:
            out.add(s)
    if principal_kind == "peer":
        out |= {s for s in caps if s not in PEER_PLAIN}
    if "deleg.spawn" in caps and resource and not resource.startswith("deleg-quota:agent:local/"):
        out.add("deleg.spawn(remote)")
    return out


# --- 자식 grant (위임 상속) ------------------------------------------------------------------

# 어떤 자식에게도 상속되지 않는 scope. 부모가 가졌어도 자식 grant 에 들어가지 않는다: 브라우저 JS·민감 데이터, 세션 관리(생성·종료 …),
# 릴레이 전체, 권한 관리(`authz.admin`)·기기 페어링(`pair.manage`). 사람의 승인 채널은 사람 세션에만 있는 것이라 어느 scope 로도 상속되지 않는다.
NEVER_INHERIT_SCOPES = frozenset({"browser.js", "browser.sensitive", "session.manage", "relay", "authz.admin", "pair.manage"})
# 상속되지 않는 조건 값 — `session.input` 의 raw 입력은 부모가 가졌어도 자식에게는 한 줄(line) 입력까지만.
NEVER_INHERIT_CONDS: dict = {"session.input": {"modes": frozenset({"raw"})}}
# 자식이 부모와 무관하게 요청만으로 갖는 scope — 자식에게는 사람에게 묻는 길이 이것뿐이다(승인은 사람이 한다).
CHILD_ALWAYS = frozenset({"authz.request"})


def _union_caps(grants: Iterable[dict]) -> Caps:
    out: Caps = {}
    for g in grants or ():
        for s, e in caps_of(g).items():
            cur = out.setdefault(s, {})
            for k, v in e.items():
                cur[k] = frozenset(cur.get(k, frozenset()) | v)
    return out


def child_grants(parent_grants: Iterable[dict], requested: dict) -> dict:
    """자식 grant = (부모 grant 들의 합) ∩ 요청 − 상속 불가. 입력·출력은 `{"scopes": [...], "sconds": {...}}` 모양이다.

    - `parent_grants`: 부모가 지금 가진 **허용** grant 들(금지 규칙·만료·비활성은 호출자가 거른다). 부모에게 없는 것은 요청해도 못 받는다.
    - `requested`: 자식에게 주려는 (scopes, sconds). 와일드카드는 풀어서 본다.
    - 조건은 값마다 교집합이고, 필요한 조건 하나라도 비면(예: raw 만 요청했는데 상속 불가) 그 scope 는 통째로 뺀다.
    - `NEVER_INHERIT_SCOPES`·`NEVER_INHERIT_CONDS` 는 부모가 가져도 뺀다. `CHILD_ALWAYS`(`authz.request`)만 부모와 무관하게 요청대로 남는다.
    결과를 `effective()` 로 풀면 위 규칙을 만족하는 정규형이다(감사·기록용)."""
    have = _union_caps(parent_grants)
    want = effective((requested or {}).get("scopes") or (), (requested or {}).get("sconds"))
    out: Caps = {}
    for scope, entry in want.items():
        if scope in NEVER_INHERIT_SCOPES:
            continue
        if scope in CHILD_ALWAYS:
            out[scope] = {}
            continue
        h = have.get(scope)
        if h is None:
            continue
        got = {}
        for k, vals in entry.items():
            keep = frozenset(vals) & h.get(k, frozenset())
            for cond, banned in NEVER_INHERIT_CONDS.get(scope, {}).items():
                if cond == k:
                    keep = keep - banned
            got[k] = keep
        if any(not v for v in got.values()):
            continue
        out[scope] = got
    scopes = sorted(out)
    sconds = {s: {k: sorted(v) for k, v in e.items()} for s, e in sorted(out.items()) if e}
    return {"scopes": scopes, "sconds": sconds}


def caps_subset(want: Caps, have: Caps) -> bool:
    """want 의 모든 (scope, 조건 값)이 have 안에 있는가."""
    for s, e in want.items():
        h = have.get(s)
        if h is None:
            return False
        for k, v in e.items():
            if not v <= h.get(k, frozenset()):
                return False
    return True


def explicit_in(covers: Iterable[str], cconds: Optional[dict], reason: str) -> bool:
    """window `covers` 가 elevated 사유(`session.input[raw]`·`relay[queue]`·`browser.js` …)를 **이름으로** 적었는가.
    와일드카드·기본값·`*` 로는 덮이지 않는다."""
    named = {c for c in covers if not any(ch in c for ch in "*?[")}
    m = re.fullmatch(r"([a-z.]+)\[(.+)\]", reason)
    if not m:
        base = reason.split("(")[0]
        return base in named
    scope, val = m.group(1), m.group(2)
    if scope not in named:
        return False
    spec = {c.key: c for c in COND_SPEC.get(scope, ())}
    given = ((cconds or {}).get(scope) or {})
    for k in spec:
        if val in [str(x) for x in (given.get(k) or ())] and k in given:
            return True
    return False


# --- 리소스 -----------------------------------------------------------------------------

_RES_RE = re.compile(r"^(session|host|fs|browser|msg|deleg|deleg-quota|relay|authz|pair)(:.*)?$")


def resource_ok(resource: str) -> bool:
    return bool(resource) and bool(_RES_RE.match(resource))


def resource_matches(pattern: str, resource: str) -> bool:
    """grant 의 리소스 선택자가 요청 리소스를 덮는가. 선택자는 glob(`session:local/*`)이다.
    `nick:`·`owned-by:` 선택자는 문자 그대로만 비교한다."""
    if pattern == "*":
        return True
    return fnmatch.fnmatchcase(resource, pattern)


def _res_broader(new: str, old: str) -> bool:
    """new 선택자가 old 가 덮지 않는 것을 덮을 수 있는가(보수적: 같지 않고 old 가 new 를 덮지 못하면 넓다)."""
    if new == old:
        return False
    if old == "*":
        return False
    return not fnmatch.fnmatchcase(new, old)


# --- 확대 판정 --------------------------------------------------------------------------------


def escalation(old: Optional[dict], new: dict) -> list[str]:
    """grant 변경이 권한 확대인가 — 확대 이유 목록(빈 목록이면 확대 아님).

    새 유효 집합이 기존의 부분집합이 아닌 모든 변경이 확대다: 새 scope, **조건 값 추가**(modes·ops·groups·role 을 늘림),
    넓은 선택자, 넓은 URL·path 패턴, 더 늦은 만료, 조건 제거, 새 주체, 상한 상승, navigator -> driver.
    조건이 없는 쪽은 기본값으로 풀어 비교한다. 판정이 애매하면 확대로 본다.
    """
    if not old:
        return ["new_grant"]
    out: list[str] = []
    oc, nc = caps_of(old), caps_of(new)
    extra = set(nc) - set(oc)
    if extra:
        out.append("scopes:" + ",".join(sorted(extra)))
    for s in sorted(set(nc) & set(oc)):
        for k, v in nc[s].items():
            if not v <= oc[s].get(k, frozenset()):
                out.append(f"cond_widened:{s}.{k}")
    if new.get("subject") != old.get("subject") and _res_broader(new.get("subject", ""), old.get("subject", "")):
        out.append("subject")
    if _res_broader(new.get("resource", ""), old.get("resource", "")):
        out.append("resource")
    oe, ne = old.get("expires"), new.get("expires")
    if oe is not None and (ne is None or float(ne) > float(oe)):
        out.append("expires")
    ocd, ncd = old.get("conds") or {}, new.get("conds") or {}
    for k, v in ocd.items():
        if k not in ncd:
            out.append(f"cond_removed:{k}")
        elif ncd[k] != v:
            if isinstance(v, list) and isinstance(ncd[k], list) and set(map(str, ncd[k])) <= set(map(str, v)):
                continue          # 목록(url_allowlist·path_glob) 이 좁아졌다
            if isinstance(v, (int, float)) and isinstance(ncd[k], (int, float)) and ncd[k] <= v:
                continue          # max_lines·max_uses 가 줄었다
            out.append(f"cond_widened:{k}")
    if (old.get("role"), new.get("role")) == ("navigator", "driver"):
        out.append("role:driver")
    return out


# --- relay 그룹 -------------------------------------------------------------------------


def relay_group(method: str, path: str) -> Optional[str]:
    """relay 대상의 그룹 이름(`relay` scope 의 조건 값). 허용 목록 밖이면 None. 명시해야 열리는 그룹(`OPTIN_RELAY_GROUPS`)도 여기서만 찾는다."""
    import relay_policy

    og = relay_policy.optin_group(method, path)
    if og:
        return og
    if not relay_policy.http_allowed(method, path):
        return None
    p = relay_policy._clean(path)
    m = method.upper()
    for rx, methods in relay_policy._HTTP_RULES:
        if rx.fullmatch(p) and m in methods:
            return RELAY_HTTP_GROUPS.get(rx.pattern)
    return None


def relay_ws_group(path: str) -> Optional[str]:
    import relay_policy

    if not relay_policy.ws_allowed(path):
        return None
    p = relay_policy._clean(path)
    for rx in relay_policy._WS_RULES:
        if rx.fullmatch(p):
            return RELAY_WS_GROUPS.get(rx.pattern)
    return None
