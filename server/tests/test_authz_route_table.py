"""라우트 인가 표 메타 시험 — 앱의 모든 HTTP·WebSocket 라우트가 `authz/route_table.py` 에 정확히 한 분류로 적혀 있고,
그 분류가 실제 관문(라우터 의존성·미들웨어 예외 목록·소켓 허용표·peer `NEEDS`·relay 모듈)과 어긋나지 않는지 본다.

실패 메시지는 표에 더할(또는 지울·고칠) 줄을 그대로 알려 준다. 검사 본체는 `problems()` 라서, 아래 「검사기 자체」 시험이
라우트를 일부러 빼거나 가짜 라우트를 더한 앱으로 같은 검사를 돌려 실제로 실패하는지 확인한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import pytest
from fastapi import APIRouter, Depends, FastAPI
from starlette.routing import compile_path

from authz import peer_svc, sock
from authz import route_table as RT
from authz import scopes as S

HUMAN_GATES = {"require_human_session", "require_elevated"}
HUMAN_OR_CLI_GATE = "_human_or_cli"
RELAY_MODULES = {"routes.peer_relay", "routes.host_relay"}
PEER_PREFIX = "/api/peer/"
HTTP_SKIP = {"HEAD", "OPTIONS"}


# --- 앱의 라우트 목록 ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Info:
    method: str                 # GET… | WS | MOUNT | OTHER:<종류>
    path: str                   # 접두사가 붙은 템플릿
    module: str = ""
    func: str = ""
    deps: tuple = ()            # 의존성 호출 이름(라우터 수준 포함)
    dep_needs: tuple = ()       # 의존성이 달고 있는 (scope, 조건) — host_gate.dependency
    peer_action: str | None = None


def _call_info(dependant) -> tuple[tuple, tuple]:
    names, needs = [], []
    stack = list(getattr(dependant, "dependencies", []) or [])
    while stack:
        d = stack.pop()
        call = getattr(d, "call", None)
        if call is not None:
            names.append(getattr(call, "__name__", repr(call)))
            n = getattr(call, "authz_need", None)
            if n is not None:
                needs.append(n)
        stack.extend(getattr(d, "dependencies", []) or [])
    return tuple(sorted(names)), tuple(needs)


def walk(routes, prefix: str = "", ctx_deps: tuple = ()) -> list[Info]:
    """FastAPI 의 라우트 트리를 평평하게 편다. 새 FastAPI 는 include 된 라우터를 `_IncludedRouter` 로 지연해서 들고 있고,
    옛 버전은 라우트를 앱에 복사해 넣는다 — 둘 다 같은 결과가 나온다."""
    from fastapi.routing import APIRoute, APIWebSocketRoute
    from starlette import routing as sr

    out: list[Info] = []
    for r in routes:
        if hasattr(r, "original_router") and hasattr(r, "include_context"):
            ctx = r.include_context
            extra = tuple(getattr(d.dependency, "__name__", "?") for d in ctx.dependencies)
            out += walk(r.original_router.routes, ctx.prefix, ctx_deps + extra)
        elif isinstance(r, (APIRoute, APIWebSocketRoute, sr.Route, sr.WebSocketRoute)):
            ws = isinstance(r, (APIWebSocketRoute, sr.WebSocketRoute))
            ep = r.endpoint
            names, needs = _call_info(getattr(r, "dependant", None))
            methods = ["WS"] if ws else sorted(set(r.methods or ()) - HTTP_SKIP)
            for m in methods:
                out.append(Info(m, prefix + r.path, getattr(ep, "__module__", ""), getattr(ep, "__name__", ""),
                                tuple(sorted(set(names) | set(ctx_deps))), needs, getattr(ep, "peer_action", None)))
        elif isinstance(r, sr.Mount):
            out.append(Info("MOUNT", prefix + r.path))
        else:
            out.append(Info(f"OTHER:{type(r).__name__}", prefix + (getattr(r, "path", "") or "")))
    return out


# --- 허용표 -------------------------------------------------------------------------------------------------------------


def expand(pattern: str) -> list[str]:
    """소켓 허용표 패턴(`(a|b)`·`(...)?`·`[^/]+` 만 쓴다)이 여는 구체 경로를 모두 펼친다."""
    def seq(i: int) -> tuple[list[str], int]:
        alts: list[str] = []
        cur = [""]
        while i < len(pattern):
            c = pattern[i]
            if c == ")":
                break
            if c == "|":
                alts += cur
                cur = [""]
                i += 1
            elif c == "(":
                inner, i = seq(i + 1)
                i += 1
                if i < len(pattern) and pattern[i] == "?":
                    inner = [""] + inner
                    i += 1
                cur = [a + b for a in cur for b in inner]
            else:
                if pattern.startswith("[^/]+", i):
                    lit, i = "x", i + 5
                elif c in "\\[]*+?.{}^$":
                    raise ValueError(f"소켓 허용표 패턴 {pattern!r} 에 이 시험이 펼칠 줄 모르는 문법({c!r})이 있습니다 — expand() 를 넓히세요")
                else:
                    lit, i = c, i + 1
                cur = [a + lit for a in cur]
        return alts + cur, i

    return seq(0)[0]


def _matches(info: Info, method: str, path: str) -> bool:
    if info.method != method:
        return False
    rx = compile_path(info.path)[0]
    return rx.fullmatch(path) is not None


# --- 검사 ---------------------------------------------------------------------------------------------------------------


def _exempt(path: str) -> bool:
    import main
    return main.auth_exempt(RT.sample_path(path))


def _legacy_ok(method: str, path: str) -> bool:
    from authz import legacy_gate
    return legacy_gate.scope_allowed(method, RT.sample_path(path))


def derive(i: Info) -> tuple[str, str]:
    """실제 관문이 가리키는 분류와 그 근거."""
    http = not (i.method == "WS" or i.method.startswith(("MOUNT", "OTHER")))
    if i.module in RELAY_MODULES:
        return RT.RELAY, f"endpoint in {i.module}"
    if i.path.startswith(PEER_PREFIX) or i.peer_action:
        return RT.PEER, "path under /api/peer/ (HMAC signature)"
    if _exempt(i.path):
        return RT.PUBLIC, "path is on main.AUTH_EXEMPT_PATHS / AUTH_EXEMPT_PREFIXES"
    if set(i.deps) & HUMAN_GATES:
        return RT.HUMAN, "router/route dependency " + "+".join(sorted(set(i.deps) & HUMAN_GATES))
    if http and RT.socket_entry(i.method, i.path):
        return RT.SOCKET, f"unix-socket allowlist entry {RT.socket_entry(i.method, i.path)!r}"
    if http and _legacy_ok(i.method, i.path):
        return RT.LEGACY_TOKEN, "machine-token allowlist"
    return RT.AUTHENTICATED, "no dedicated gate: any authenticated caller (middleware / handshake auth)"


def suggest(i: Info) -> str:
    cls, _ = derive(i)
    if cls == RT.PUBLIC:
        rule = 'public("<what verifies the caller instead of the token middleware>")'
    elif cls == RT.HUMAN:
        rule = "human(elevated=True)" if "require_elevated" in i.deps else "human()"
    elif cls == RT.SOCKET:
        rule = "sock_()"
    elif cls == RT.PEER:
        rule = f'peer("{i.peer_action or "<key of peer_svc.NEEDS>"}")'
    elif cls == RT.RELAY:
        rule = 'relay_out()' if i.module == "routes.host_relay" else 'relay("<relay|relay-ws>")'
    else:
        rule = "auth()"
    hint = '  # add scope/conds if the handler asks the engine: auth("<scope>", <cond>=...)' if cls == RT.AUTHENTICATED else ""
    return f'    ("{i.method}", "{i.path}", {rule}),{hint}'


def problems(routes: list[Info], table: dict | None = None, *, gate_only: dict | None = None) -> list[str]:
    table = RT.TABLE if table is None else table
    gate_only = RT.GATE_ONLY if gate_only is None else gate_only
    out: list[str] = []
    keys = {(i.method, i.path) for i in routes}

    for i in routes:
        if i.method.startswith("OTHER"):
            out.append(f"unrecognised route kind {i.method} {i.path!r}: teach walk() in {__file__} how to read it, then declare it")

    # 1. 선언 누락 · 낡은 항목
    for i in routes:
        if (i.method, i.path) not in table and not i.method.startswith("OTHER"):
            cls, why = derive(i)
            out.append(f"MISSING {i.method} {i.path} ({i.module}.{i.func}): no authorization class declared.\n"
                       f"  Add to _ROWS in server/authz/route_table.py (derived class: {cls} — {why}); confirm it is who may really call it:\n{suggest(i)}")
    for (m, p) in table:
        if (m, p) not in keys:
            out.append(f"STALE {m} {p}: in server/authz/route_table.py but the app has no such route — remove the entry (or fix the path)")

    # 2. 선언한 분류 ↔ 실제 관문
    by_key = {(i.method, i.path): i for i in routes}
    for key, rule in table.items():
        i = by_key.get(key)
        if i is None:
            continue
        where = f"{i.method} {i.path}"
        real, why = derive(i)
        if rule.cls not in RT.CLASSES:
            out.append(f"{where}: unknown class {rule.cls!r} (use one of {', '.join(RT.CLASSES)})")
            continue
        http = not (i.method == "WS" or i.method.startswith(("MOUNT", "OTHER")))
        in_sock = bool(http and RT.socket_entry(i.method, i.path))
        in_legacy = bool(http and _legacy_ok(i.method, i.path))
        humanish = bool(set(i.deps) & HUMAN_GATES)

        if rule.cls == RT.HUMAN:
            if not humanish:
                out.append(f"{where}: declared human but neither require_human_session nor require_elevated guards it ({real}: {why}). "
                           f"Put the route on a human/elevated router, or declare it {real}")
            if in_sock:
                out.append(f"{where}: declared human but reachable through the unix-socket allowlist entry {RT.socket_entry(i.method, i.path)!r} — "
                           f"remove it from sock.ROUTES or declare the route socket")
            if in_legacy:
                out.append(f"{where}: declared human but the machine-token allowlist opens it — remove it from the allowlist or declare the route socket/legacy_token")
            if rule.elevated != ("require_elevated" in i.deps):
                out.append(f"{where}: elevated={rule.elevated} but the real gate "
                           f"{'has' if 'require_elevated' in i.deps else 'has no'} require_elevated — write human(elevated={'require_elevated' in i.deps})")
        elif humanish and rule.cls not in (RT.PUBLIC, RT.PEER, RT.RELAY):
            out.append(f"{where}: guarded by {'+'.join(sorted(set(i.deps) & HUMAN_GATES))} but declared {rule.cls} — declare human")
        if rule.elevated and rule.cls != RT.HUMAN:
            out.append(f"{where}: elevated=True is only meaningful for human routes")

        if rule.cls == RT.SOCKET:
            want = RT.socket_entry(i.method, i.path)
            if want is None:
                out.append(f"{where}: declared socket but no sock.ROUTES entry opens it — add the (method, path) to sock.ROUTES or declare {real}")
            elif rule.via != want:
                out.append(f"{where}: socket entry recorded as {rule.via!r} but the allowlist entry is {want!r}")
        elif in_sock:
            out.append(f"{where}: declared {rule.cls} but the unix-socket allowlist entry {RT.socket_entry(i.method, i.path)!r} opens it — declare socket")
        if rule.cls == RT.LEGACY_TOKEN and not in_legacy:
            out.append(f"{where}: declared legacy_token but the machine-token allowlist does not open it")
        if rule.cls not in (RT.SOCKET, RT.LEGACY_TOKEN, RT.HUMAN) and in_legacy and not in_sock:
            out.append(f"{where}: the machine-token allowlist opens it, so declare legacy_token (declared {rule.cls})")
        if HUMAN_OR_CLI_GATE in i.deps and not in_sock:
            out.append(f"{where}: guarded by {HUMAN_OR_CLI_GATE} (human or socket CLI) but no sock.ROUTES entry opens it — the CLI path is dead")

        if rule.cls == RT.PUBLIC:
            if not _exempt(i.path):
                out.append(f"{where}: declared public but main.AUTH_EXEMPT_* does not list it — the token middleware still guards it; "
                           f"declare authenticated, or add it to the exempt list AND the «Exceptions» table in docs/ref/auth.md")
            if humanish or in_sock or in_legacy:
                out.append(f"{where}: declared public but it also has a human gate / allowlist entry — pick one meaning")
            if not rule.note:
                out.append(f"{where}: public needs a note: what verifies the caller instead of the token middleware")
        elif _exempt(i.path) and rule.cls not in (RT.PEER, RT.RELAY):
            out.append(f"{where}: skips the token middleware (main.AUTH_EXEMPT_*) but is declared {rule.cls} — declare public")

        if rule.cls in (RT.PEER, RT.RELAY):
            if i.module in RELAY_MODULES and i.module == "routes.host_relay":
                if rule.cls != RT.RELAY or rule.via != "host_gate":
                    out.append(f"{where}: host_relay routes are relay_out() (host_gate)")
            else:
                action = i.peer_action
                if action is None:
                    if i.path not in peer_svc.ROUTE_ACTIONS:
                        out.append(f"{where}: a peer route without a need — decorate it with peer_protocol.capability(...) or add the path to "
                                   f"peer_svc.ROUTE_ACTIONS, and put (scope, conditions) for its action in peer_svc.NEEDS")
                        continue
                    action = peer_svc.ROUTE_ACTIONS[i.path]
                if action is None:
                    if rule.via is not None:
                        out.append(f"{where}: this route has no engine need (ROUTE_ACTIONS maps it to None) — write peer(None, '<what authenticates it>')")
                elif action not in peer_svc.NEEDS:
                    out.append(f"{where}: peer action {action!r} has no entry in peer_svc.NEEDS — add its (scope, conditions) there")
                else:
                    n = peer_svc.NEEDS[action]
                    if rule.via != action or rule.scope != n.scope or rule.conds != dict(n.ctx):
                        out.append(f"{where}: table says via={rule.via!r} scope={rule.scope!r} conds={rule.conds!r} but peer_svc.NEEDS[{action!r}] is "
                                   f"({n.scope!r}, {dict(n.ctx)!r}) — write {rule.cls}({action!r})")
        elif i.peer_action or i.path.startswith(PEER_PREFIX):
            out.append(f"{where}: a /api/peer/ route (HMAC) must be declared peer or relay, not {rule.cls}")

        # scope / 조건
        if rule.scope is not None:
            if not (S.known(rule.scope) or rule.scope in S.INTERNAL):
                out.append(f"{where}: scope {rule.scope!r} is not in the scope vocabulary (authz/scopes.py)")
            elif S.missing_conds(rule.scope, dict(rule.conds)):
                out.append(f"{where}: scope {rule.scope!r} needs condition(s) {S.missing_conds(rule.scope, dict(rule.conds))} — add them to the entry")
        for need in i.dep_needs:
            if (rule.scope, rule.conds) != (need[0], dict(need[1])):
                out.append(f"{where}: its dependency asks the engine for {need} but the table says ({rule.scope!r}, {rule.conds!r})")

    # 3. 소켓 허용표의 죽은 항목 — 앱에 없는 라우트를 여는 줄
    http_routes = [i for i in routes if not (i.method == "WS" or i.method.startswith(("MOUNT", "OTHER")))]
    for method, rx, who in sock._ROUTES_RE:
        try:
            paths = expand(rx.pattern)
        except ValueError as e:
            out.append(str(e))
            continue
        for path in paths:
            if (method, path) in gate_only:
                continue
            if not any(_matches(i, method, path) for i in http_routes):
                out.append(f"DEAD allowlist entry: sock.ROUTES opens {method} {path} ({who}) but the app has no such route — "
                           f"fix or remove the entry in server/authz/sock.py")

    # 4. 앱 앞에서 게이트가 직접 답하는 경로
    for (method, path), _why in gate_only.items():
        if any(_matches(i, method, path) for i in http_routes):
            out.append(f"GATE_ONLY {method} {path} is also an app route — declare it as a normal route and drop it from GATE_ONLY")
    return out


# --- 시험 ---------------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app_routes() -> list[Info]:
    import main
    return walk(main.app.router.routes)


def test_the_walker_finds_the_routes(app_routes):
    """공허하게 통과하는 일을 막는다 — 라우트·WS·정적 마운트·peer·relay 가 모두 보여야 한다."""
    assert len(app_routes) >= 200, len(app_routes)
    methods = {i.method for i in app_routes}
    assert {"GET", "POST", "WS", "MOUNT"} <= methods
    assert any(i.peer_action for i in app_routes)
    assert any(i.module in RELAY_MODULES for i in app_routes)
    assert any("require_human_session" in i.deps for i in app_routes)
    assert any("require_elevated" in i.deps for i in app_routes)


def test_every_route_declares_exactly_one_authorization_class_consistent_with_its_gate(app_routes):
    found = problems(app_routes)
    assert not found, "\n" + "\n".join(found)


def test_classes_cover_the_expected_shapes(app_routes):
    by = {}
    for i in app_routes:
        by.setdefault(RT.TABLE[(i.method, i.path)].cls, []).append(i)
    assert set(by) <= set(RT.CLASSES)
    assert {RT.PUBLIC, RT.HUMAN, RT.SOCKET, RT.PEER, RT.RELAY, RT.AUTHENTICATED} <= set(by)
    # 소켓 게이트가 앱 앞에서 직접 답하는 경로는 앱에 라우트가 없다
    assert (("GET", sock.SELFTEST_PATH) in RT.GATE_ONLY)


def test_gate_answered_whoami_is_still_declared(app_routes):
    """whoami 는 게이트가 앞에서 답하지만 앱에도 같은 라우트가 있다 — 표에는 앱 라우트로 적혀야 한다."""
    assert RT.TABLE[("GET", sock.WHOAMI_PATH)].cls == RT.SOCKET


# --- 검사기 자체: 일부러 망가뜨리면 실패해야 한다 ----------------------------------------------------------------------------


def _msgs(app_routes, **kw):
    return "\n".join(problems(app_routes, **kw))


def test_checker_flags_a_route_missing_from_the_table(app_routes):
    table = dict(RT.TABLE)
    del table[("POST", "/api/queue")]
    msg = _msgs(app_routes, table=table)
    assert 'MISSING POST /api/queue' in msg and '("POST", "/api/queue", auth())' in msg


def test_checker_flags_a_new_unlisted_route_in_a_test_app():
    import main
    app = FastAPI()
    r = APIRouter()

    @r.get("/api/brand-new")
    async def brand_new():
        return {}

    @r.websocket("/ws-brand-new")
    async def ws_brand_new(ws):
        pass

    app.include_router(r)
    routes = walk(main.app.router.routes) + walk(app.router.routes)
    msg = _msgs(routes)
    assert 'MISSING GET /api/brand-new' in msg and 'MISSING WS /ws-brand-new' in msg
    assert 'server/authz/route_table.py' in msg


def test_checker_flags_a_stale_table_entry(app_routes):
    table = {**RT.TABLE, ("GET", "/api/removed-long-ago"): RT.auth()}
    assert "STALE GET /api/removed-long-ago" in _msgs(app_routes, table=table)


def test_checker_flags_a_human_route_that_the_socket_allowlist_opens(app_routes, monkeypatch):
    rx = (("POST", re.compile(r"/api/agent/ack"), "sneaked in"),)
    monkeypatch.setattr(sock, "_ROUTES_RE", sock._ROUTES_RE + rx)
    msg = _msgs(app_routes)
    assert "POST /api/agent/ack: declared human but reachable through the unix-socket allowlist" in msg


def test_checker_flags_a_human_route_that_the_machine_token_reaches(app_routes, monkeypatch):
    from authz import legacy_gate
    real = legacy_gate.scope_allowed
    monkeypatch.setattr(legacy_gate, "scope_allowed", lambda m, p: True if p == "/api/authz/panic" else real(m, p))
    assert "POST /api/authz/panic: declared human but the machine-token allowlist opens it" in _msgs(app_routes)


def test_checker_flags_a_socket_route_missing_from_the_allowlist(app_routes, monkeypatch):
    kept = tuple(e for e in sock._ROUTES_RE if e[1].pattern != "/api/agent/event")
    monkeypatch.setattr(sock, "_ROUTES_RE", kept)
    assert "POST /api/agent/event: declared socket but no sock.ROUTES entry opens it" in _msgs(app_routes)


def test_checker_flags_an_allowlist_entry_for_a_route_the_app_does_not_have(app_routes, monkeypatch):
    rx = (("POST", re.compile(r"/api/no-such-endpoint"), "ghost client"),)
    monkeypatch.setattr(sock, "_ROUTES_RE", sock._ROUTES_RE + rx)
    assert "DEAD allowlist entry: sock.ROUTES opens POST /api/no-such-endpoint" in _msgs(app_routes)


def test_checker_flags_a_peer_route_without_a_need(app_routes, monkeypatch):
    monkeypatch.setattr(peer_svc, "NEEDS", {k: v for k, v in peer_svc.NEEDS.items() if k != "sessions"})
    assert "peer action 'sessions' has no entry in peer_svc.NEEDS" in _msgs(app_routes)


def test_checker_flags_a_class_that_contradicts_the_gate(app_routes):
    table = dict(RT.TABLE)
    table[("GET", "/api/ports")] = RT.public("wrong")
    table[("POST", "/api/auth/logout")] = RT.auth()
    table[("POST", "/api/ports/{port}/expose")] = RT.human()           # 승격 표시를 빠뜨림
    msg = _msgs(app_routes, table=table)
    assert "GET /api/ports: declared public but main.AUTH_EXEMPT_*" in msg
    assert "POST /api/auth/logout: skips the token middleware" in msg
    assert "elevated=False but the real gate has require_elevated" in msg


def test_expand_unfolds_the_allowlist_patterns():
    assert sorted(expand(r"/api/delegations(/report|/[^/]+/(send|close))?")) == sorted(
        ["/api/delegations", "/api/delegations/report", "/api/delegations/x/send", "/api/delegations/x/close"])
    assert sorted(expand(r"/voice/(cancel|input)")) == ["/voice/cancel", "/voice/input"]
    with pytest.raises(ValueError):
        expand(r"/api/\d+")
