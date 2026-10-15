"""authz 특성 사례 목록 — 그룹(테스트 파일)별로 결정적으로 만든다.

여기 있는 사례 id 가 matrix.json 의 키다. id 는 `그룹.라우트표시.메서드.호출자.모드[.변형]` 모양이고
**한 번 정하면 바꾸지 않는다** — 이후 단계(shadow 비교)가 같은 id 로 새 엔진의 판정과 맞춘다.
기대값은 여기 적지 않는다: 지금 서버가 내리는 판정을 gen_matrix 가 기록하고 테스트가 재생한다.
"""

from __future__ import annotations

import re
from functools import lru_cache

from authz_harness import MODES, OTHER_TMUX_ENV, TMUX_ENV, Case

HUMAN = ("session", "session_elev", "machine", "none")
AGENTS = ("agent_verified", "agent_child", "agent_foreign")
PEERS = ("peer_view", "peer_control", "peer_control_agent", "peer_forged")


def _slug(s: str) -> str:
    s = re.sub(r"\{[^}]+\}", "X", s)
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def _cid(group: str, route: str, method: str, caller: str, mode: str, var: str = "") -> str:
    return ".".join(p for p in (group, _slug(route), method.lower(), caller, mode, var) if p)


# --- agent_share ----------------------------------------------------------------------------

def agent_share_cases() -> list[Case]:
    g = "agent_share"
    callers = HUMAN + ("agent_verified", "agent_foreign", "peer_control")
    routes = [
        # (method, route template, path, body, variant)
        ("GET", "/api/agent/shared", "/api/agent/shared", None, ""),
        ("POST", "/api/agent/shared/read", "/api/agent/shared/read", {"nickname": "shell", "lines": 5}, "no_grant"),
        ("POST", "/api/agent/shared/send", "/api/agent/shared/send", {"nickname": "shell", "text": "ls"}, "no_grant"),
        ("POST", "/api/tmux/{name}/share", "/api/tmux/other/share", {"nick": "other1", "mode": "read"}, ""),
        ("DELETE", "/api/agent/share/sessions/{name}", "/api/agent/share/sessions/target", None, ""),
        ("GET", "/api/agent/share/requests", "/api/agent/share/requests", None, ""),
        ("POST", "/api/agent/share/requests/{request_id}/decision", "/api/agent/share/requests/{rid}/decision",
         {"decision": "allow"}, ""),
        ("GET", "/api/agent/share/grants", "/api/agent/share/grants", None, ""),
        ("DELETE", "/api/agent/share/grants/{grant_id}", "/api/agent/share/grants/{gid}", None, ""),
        ("GET", "/api/agent/share/audit", "/api/agent/share/audit", None, ""),
    ]
    out = []
    for method, route, path, body, var in routes:
        for mode in MODES:
            for c in callers:
                out.append(Case(_cid(g, route, method, c, mode, var), g, c, mode, method, route, path, body))
        # tmux 상태 축 — tmux 서버가 없을 때(사람·확인된 에이전트만, 비밀번호 모드).
        for c in ("session", "agent_verified"):
            out.append(Case(_cid(g, route, method, c, "password", (var + "_" if var else "") + "tmux_down"),
                            g, c, "password", method, route, path, body, tmux="down"))
    return out


# --- delegation -----------------------------------------------------------------------------

def delegation_cases() -> list[Case]:
    g = "delegation"
    callers = HUMAN + AGENTS + ("peer_control",)
    routes = [
        ("GET", "/api/delegations/role", "/api/delegations/role", None),
        ("GET", "/api/delegations", "/api/delegations", None),
        # cwd 는 아무 절대 경로나 받는다(H9 — fsguard 미적용).
        ("POST", "/api/delegations", "/api/delegations", {"task": "char task", "cwd": "/"}),
        ("POST", "/api/delegations/report", "/api/delegations/report", {"outcome": "succeeded", "summary": "done"}),
        ("POST", "/api/delegations/{did}/send", "/api/delegations/{did}/send", {"text": "more"}),
        ("POST", "/api/delegations/{did}/close", "/api/delegations/{did}/close", None),
        ("POST", "/api/delegations/{did}/close-by-user", "/api/delegations/{did}/close-by-user", None),
        # 3b — `fsh delegate close` 의 서버 경로. unix socket 로만 닿는다: HTTP(TCP·쿠키·토큰)로는 누구에게나 403 이다.
        ("POST", "/api/delegations/{did}/close-by-cli", "/api/delegations/{did}/close-by-cli", None),
    ]
    out = []
    for method, route, path, body in routes:
        for mode in MODES:
            for c in callers:
                hole = "H9" if route == "/api/delegations" and method == "POST" and c in AGENTS[:2] else None
                out.append(Case(_cid(g, route, method, c, mode), g, c, mode, method, route, path, body, hole=hole))
    return out


# --- agent_msg ------------------------------------------------------------------------------

def agent_msg_cases() -> list[Case]:
    g = "agent_msg"
    callers = HUMAN + ("agent_verified", "agent_foreign", "peer_control")
    # 보낸 세션은 **본문의 pane/tmux** 로 정해진다 — 여기선 일부러 남의 칸(other, %3)을 주장한다.
    spoof = {"to": "local/target", "text": "hi", "pane": "%3", "tmux": TMUX_ENV}
    variants = [
        ("POST", "/api/agent/message", "/api/agent/message", spoof, "local_spoofed_sender", "H2"),
        ("POST", "/api/agent/message", "/api/agent/message",
         {"to": "local/target", "text": "hi", "pane": "%3", "tmux": OTHER_TMUX_ENV}, "local_foreign_socket", None),
        ("POST", "/api/agent/message", "/api/agent/message", {"to": "m5/dev", "text": "hi", "pane": "%3",
                                                               "tmux": TMUX_ENV}, "remote", "H2"),
        ("GET", "/api/agent/messages", "/api/agent/messages", None, "", None),
        ("GET", "/api/agent/directory", "/api/agent/directory", None, "", None),
    ]
    out = []
    for method, route, path, body, var, hole in variants:
        for mode in MODES:
            for c in callers:
                h = hole if c not in ("none",) else None
                out.append(Case(_cid(g, route, method, c, mode, var), g, c, mode, method, route, path, body, hole=h))
    return out


# --- peer (@capability 등) ------------------------------------------------------------------

def peer_cases() -> list[Case]:
    g = "peer"
    callers = ("none", "machine", "session") + PEERS
    caps = [
        ("GET", "/api/peer/sessions", "/api/peer/sessions", None, ()),
        ("GET", "/api/peer/clients", "/api/peer/clients?session=target", None, ()),
        ("POST", "/api/peer/clients/detach", "/api/peer/clients/detach", {"session": "target", "ttys": []}, ()),
        ("POST", "/api/peer/clients/solo", "/api/peer/clients/solo", {"session": "target"}, ()),
        ("GET", "/api/peer/search", "/api/peer/search?q=foo", None, ()),
        ("GET", "/api/peer/ping", "/api/peer/ping", None, ()),
        ("POST", "/api/peer/input", "/api/peer/input", {"session": "target", "data": "ls"}, ()),
        ("POST", "/api/peer/sessions/create", "/api/peer/sessions/create", {"name": "newone"}, ()),
        ("POST", "/api/peer/sessions/kill", "/api/peer/sessions/kill", {"name": "other"}, ()),
        ("POST", "/api/peer/sessions/rename", "/api/peer/sessions/rename", {"name": "other", "new": "other2"}, ()),
        ("POST", "/api/peer/agent/message", "/api/peer/agent/message",
         {"to": "target", "text": "hi", "from": "dev", "id": "m1"}, ()),
        ("POST", "/api/peer/file", "/api/peer/file", b"file-bytes", (("X-Peer-File-Name", "a.txt"),)),
        ("WS", "/api/peer/ws/{tmux_name}", "/api/peer/ws/nosuch", None, ()),
    ]
    out = []
    for mode in MODES:
        for c in callers:
            for method, route, path, body, hdrs in caps:
                hole = None
                if route == "/api/peer/input" and c == "peer_control":
                    hole = "H5"
                elif route in ("/api/peer/sessions", "/api/peer/search") and c == "peer_view":
                    hole = "H10"
                out.append(Case(_cid(g, route, method, c, mode), g, c, mode, method, route, path, body,
                                headers=hdrs, hole=hole))
            # 페어링은 서명이 아니라 1회용 티켓이 인증이다.
            out.append(Case(_cid(g, "/api/peer/pair", "POST", c, mode, "valid_ticket"), g, c, mode, "POST",
                            "/api/peer/pair", "/api/peer/pair", {"ticket": "{ticket}", "id": "newpeer"}))
        out.append(Case(_cid(g, "/api/peer/pair", "POST", "none", mode, "bad_ticket"), g, "none", mode, "POST",
                        "/api/peer/pair", "/api/peer/pair", {"ticket": "nope", "id": "newpeer"}))
        out.append(Case(_cid(g, "/api/peer/pair", "POST", "none", mode, "reserved_id"), g, "none", mode, "POST",
                        "/api/peer/pair", "/api/peer/pair", {"ticket": "{ticket}", "id": "local"}))
    return out


# --- relay (B: /api/peer/relay — relay_policy 규칙 전부) --------------------------------------

# 규칙 패턴 → 그 규칙에만 걸리는 대표 경로. 메타 테스트가 "대표 경로가 그 규칙과 맞는가"도 본다.
RELAY_SAMPLES = {
    r"/api/sessions(/[^/]+(/(keys|paste))?)?": "/api/sessions/s1/keys",
    r"/api/tmux/(sessions|create|attach|clients|clients/solo|detach-client)": "/api/tmux/sessions",
    r"/api/tmux/kill/[^/]+": "/api/tmux/kill/target",
    r"/api/tmux/preview/[^/]+": "/api/tmux/preview/target",
    r"/api/agents(/coverage|/[^/]+)?": "/api/agents",
    r"/api/agent/status": "/api/agent/status",
    r"/api/files": "/api/files",
    r"/api/files/delete": "/api/files/delete",
    r"/api/files/[^/]+": "/api/files/abc",
    r"/api/files/[^/]+/(download|path)": "/api/files/abc/download",
    r"/api/files/[^/]+/insert": "/api/files/abc/insert",
    r"/api/upload": "/api/upload",
    r"/api/fs/(file|raw|roots|search|tree)": "/api/fs/raw?path=%2Fetc%2Fhosts",
    r"/api/git/(status|diff|log|show)": "/api/git/status",
    r"/api/git/(stage|unstage|commit)": "/api/git/commit",
    r"/api/ports": "/api/ports",
    r"/api/ports/\d+": "/api/ports/3000",
    r"/api/queue(/run|/[^/]+(/unblock)?)?": "/api/queue",
    r"/api/snippets(/project|/[^/]+)?": "/api/snippets",
    r"/api/search/scrollback": "/api/search/scrollback?q=x",
    r"/api/usage(/counter)?": "/api/usage",
    r"/api/workspace": "/api/workspace",
    r"/api/worktrees(/precheck|/hidden|/[^/]+(/open)?)?": "/api/worktrees",
    r"/api/repos": "/api/repos",
    r"/api/(capabilities|hooks/status|safe-mode|tunnel/list|tailscale/status)": "/api/capabilities",
    r"/api/mcp(/plugins)?": "/api/mcp",
    r"/api/watch/[^/]+": "/api/watch/x",
}
RELAY_DENY_SAMPLES = {
    r"/api/files/[^/]+/(share|send)(/.*)?": "/api/files/abc/share",
    r"/api/(tmux|sessions)/open-on-mac": "/api/tmux/open-on-mac",
}
RELAY_WS_SAMPLES = {
    r"/ws/[A-Za-z0-9_\-:.]+": "/ws/abc-123",
    r"/ws-agent": "/ws-agent",
    r"/ws-workspace": "/ws-workspace",
    r"/ws-preview/[^/]+": "/ws-preview/target",
    r"/ws-fs-watch": "/ws-fs-watch",
}
ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def relay_cases() -> list[Case]:
    g = "relay"
    route = "/api/peer/relay/{rest:path}"
    out = []
    levels = ("peer_view", "peer_control", "peer_control_agent")
    for rule, sample in RELAY_SAMPLES.items():
        rs = _slug(sample.split("?")[0])
        for m in ALL_METHODS:
            for c in levels:
                hole = "H5" if (c == "peer_control" and rule.startswith(r"/api/queue")) else None
                out.append(Case(f"{g}.{rs}.{m.lower()}.{c}", g, c, "password", m, route,
                                "/api/peer/relay" + sample, b"", rule=rule, hole=hole))
        out.append(Case(f"{g}.{rs}.get.none", g, "none", "password", "GET", route,
                        "/api/peer/relay" + sample, b"", rule=rule))
        out.append(Case(f"{g}.{rs}.get.peer_forged", g, "peer_forged", "password", "GET", route,
                        "/api/peer/relay" + sample, b"", rule=rule))
    for rule, sample in RELAY_DENY_SAMPLES.items():
        rs = _slug(sample.split("?")[0])
        for m in ("GET", "POST"):
            out.append(Case(f"{g}.deny.{rs}.{m.lower()}.peer_control", g, "peer_control", "password", m, route,
                            "/api/peer/relay" + sample, b"", rule=rule))
    # 허용 목록 밖·경로 우회
    for name, sample in (("auth_status", "/api/auth/status"), ("devices", "/api/devices"),
                         ("hosts_transitive", "/api/hosts/m5/relay/api/workspace"), ("peer_ping", "/api/peer/ping"),
                         ("agent_message", "/api/agent/message"), ("agent_shared_send", "/api/agent/shared/send"),
                         ("delegations", "/api/delegations"), ("dotdot", "/api/files/../auth")):
        out.append(Case(f"{g}.outside.{name}.post.peer_control", g, "peer_control", "password", "POST", route,
                        "/api/peer/relay" + sample, b""))
    # 이 모드 축 — relay 는 미들웨어를 건너뛰므로 서버 모드와 무관한지 확인
    for mode in ("token", "none"):
        out.append(Case(f"{g}.api_queue.post.peer_control.{mode}", g, "peer_control", mode, "POST", route,
                        "/api/peer/relay/api/queue", b"", rule=r"/api/queue(/run|/[^/]+(/unblock)?)?"))
    # relay-ws 관문(허용된 WS 는 안쪽 PTY 를 띄우므로 거절 경로만 끝까지 돈다)
    wsr = "/api/peer/relay-ws/{rest:path}"
    for c in ("none", "peer_view", "peer_forged"):
        out.append(Case(f"{g}.ws.ws_workspace.{c}", g, c, "password", "WS", wsr,
                        "/api/peer/relay-ws/ws-workspace", rule=r"/ws-workspace"))
    out.append(Case(f"{g}.ws.ws_notify_forbidden.peer_control", g, "peer_control", "password", "WS", wsr,
                    "/api/peer/relay-ws/ws-notify"))
    out.append(Case(f"{g}.ws.ws_remote_forbidden.peer_control", g, "peer_control", "password", "WS", wsr,
                    "/api/peer/relay-ws/ws/remote/m5/dev"))
    # WS 규칙 자체(함수 판정) — 규칙마다 1건
    for rule, sample in RELAY_WS_SAMPLES.items():
        out.append(Case(f"{g}.ws_policy.{_slug(rule)}", g, "-", "-", "FUNC", "relay_policy.ws_allowed",
                        sample, rule=rule))
    return out


# --- host_relay (A: /api/hosts/{id}/relay, /ws/hosts/{id}/relay) ----------------------------

def host_relay_cases() -> list[Case]:
    g = "host_relay"
    route = "/api/hosts/{host_id}/relay/{rest:path}"
    callers = HUMAN + ("agent_verified", "peer_control")
    out = []
    for mode in MODES:
        for c in callers:
            for m in ALL_METHODS:
                out.append(Case(_cid(g, route, m, c, mode, "workspace"), g, c, mode, m, route,
                                "/api/hosts/m5/relay/api/workspace", b""))
            out.append(Case(_cid(g, route, "GET", c, mode, "forbidden"), g, c, mode, "GET", route,
                            "/api/hosts/m5/relay/api/auth/status", b""))
            out.append(Case(_cid(g, route, "GET", c, mode, "unknown_host"), g, c, mode, "GET", route,
                            "/api/hosts/nohost/relay/api/workspace", b""))
            out.append(Case(_cid(g, route, "POST", c, mode, "queue"), g, c, mode, "POST", route,
                            "/api/hosts/m5/relay/api/queue", b""))
            wr = "/ws/hosts/{host_id}/relay/{rest:path}"
            out.append(Case(_cid(g, wr, "WS", c, mode, "ws_workspace"), g, c, mode, "WS", wr,
                            "/ws/hosts/m5/relay/ws-workspace"))
            out.append(Case(_cid(g, wr, "WS", c, mode, "ws_forbidden"), g, c, mode, "WS", wr,
                            "/ws/hosts/m5/relay/ws-notify"))
    return out


# --- hosts ----------------------------------------------------------------------------------

def hosts_cases() -> list[Case]:
    g = "hosts"
    callers = HUMAN + ("agent_verified", "peer_control")
    routes = [
        ("GET", "/api/hosts", "/api/hosts", None),
        ("GET", "/api/hosts/self", "/api/hosts/self", None),
        ("GET", "/api/hosts/{host_id}/clients", "/api/hosts/m5/clients?session=dev", None),
        ("POST", "/api/hosts/{host_id}/clients/detach", "/api/hosts/m5/clients/detach", {"session": "dev"}),
        ("POST", "/api/hosts/{host_id}/clients/solo", "/api/hosts/m5/clients/solo", {"session": "dev"}),
        ("GET", "/api/hosts/{host_id}/search", "/api/hosts/m5/search?q=foo", None),
        ("POST", "/api/hosts/{host_id}/sessions", "/api/hosts/m5/sessions", {"name": "x"}),
        ("DELETE", "/api/hosts/{host_id}/sessions/{name}", "/api/hosts/m5/sessions/dev", None),
        ("PATCH", "/api/hosts/{host_id}/sessions/{name}", "/api/hosts/m5/sessions/dev", {"name": "dev2"}),
        ("POST", "/api/hosts/{host_id}/ping", "/api/hosts/m5/ping", None),
    ]
    out = []
    for method, route, path, body in routes:
        for mode in MODES:
            for c in callers:
                out.append(Case(_cid(g, route, method, c, mode), g, c, mode, method, route, path, body))
    return out


# --- machine token 구멍 ----------------------------------------------------------

def machine_token_cases() -> list[Case]:
    g = "machine_token"
    q = {"text": "echo pwned", "target": {"session": "target"}}
    out = []
    for c in ("machine", "agent_verified", "agent_foreign", "session", "none"):
        for mode in ("password", "token"):
            h1 = "H1" if c in ("machine", "agent_verified", "agent_foreign") else None
            out += [
                Case(_cid(g, "/api/sessions/{session_id}/keys", "POST", c, mode), g, c, mode, "POST",
                     "/api/sessions/{session_id}/keys", "/api/sessions/pty-1/keys", {"text": "y\r"}, hole=h1),
                Case(_cid(g, "/api/queue", "POST", c, mode), g, c, mode, "POST", "/api/queue", "/api/queue", q,
                     hole=h1),
                Case(_cid(g, "/api/tmux/kill/{tmux_name}", "DELETE", c, mode), g, c, mode, "DELETE",
                     "/api/tmux/kill/{tmux_name}", "/api/tmux/kill/target", None, hole=h1),
                # 대비: 같은 호출자가 공유 경로로는 승인 대기(202)에 걸린다 — grant 는 MCP 경로에만 있다.
                Case(_cid(g, "/api/agent/shared/send", "POST", c, mode), g, c, mode, "POST",
                     "/api/agent/shared/send", "/api/agent/shared/send", {"nickname": "shell", "text": "ls"}),
            ]
    # H6 — 비밀번호·token 둘 다 없는 서버는 무인증 호출자에게 같은 것을 다 연다.
    for route, path, method, body in (("/api/queue", "/api/queue", "POST", q),
                                      ("/api/tmux/kill/{tmux_name}", "/api/tmux/kill/target", "DELETE", None),
                                      ("/api/sessions/{session_id}/keys", "/api/sessions/pty-1/keys", "POST",
                                       {"text": "y\r"})):
        out.append(Case(_cid(g, route, method, "none", "none"), g, "none", "none", method, route, path, body,
                        hole="H6"))
    # H2 — 보낸 세션을 본문이 정한다(테스트가 저장된 from 도 직접 본다).
    for c in ("machine", "agent_foreign"):
        out.append(Case(_cid(g, "/api/agent/message", "POST", c, "password", "spoof_other"), g, c, "password",
                        "POST", "/api/agent/message", "/api/agent/message",
                        {"to": "local/target", "text": "hi", "pane": "%3", "tmux": TMUX_ENV}, hole="H2"))
    # H5 — control peer: allow-agent 없이도 큐·입력에 닿는다(agent/message 만 막힌다).
    relay = "/api/peer/relay/{rest:path}"
    out += [
        Case(f"{g}.relay_queue_post.peer_control.password", g, "peer_control", "password", "POST", relay,
             "/api/peer/relay/api/queue", b'{"text": "echo via relay", "target": {"session": "target"}}',
             headers=(("Content-Type", "application/json"),), hole="H5", real_dispatch=True,
             rule=r"/api/queue(/run|/[^/]+(/unblock)?)?"),
        Case(f"{g}.peer_input.peer_control.password", g, "peer_control", "password", "POST", "/api/peer/input",
             "/api/peer/input", {"session": "target", "data": "echo via input", "enter": True}, hole="H5"),
        Case(f"{g}.peer_agent_message.peer_control.password", g, "peer_control", "password", "POST",
             "/api/peer/agent/message", "/api/peer/agent/message",
             {"to": "target", "text": "hi", "from": "dev", "id": "m1"}, hole="H5"),
        Case(f"{g}.peer_agent_message.peer_control_agent.password", g, "peer_control_agent", "password", "POST",
             "/api/peer/agent/message", "/api/peer/agent/message",
             {"to": "target", "text": "hi", "from": "dev", "id": "m1"}),
    ]
    return out


# --- CLI 직접 쓰기 ------------------------------------------------------------------------

CLI_FUNCS = (
    ("host_store._cli", ("allow-control", "pv")),
    ("host_store._cli", ("deny-control", "pc")),
    ("host_store._cli", ("allow-agent", "pc")),
    ("host_store._cli", ("deny-agent", "pca")),
    ("host_store._cli", ("rm", "pc")),
    ("delegation._cli", ("close", "dseed")),
)


def cli_cases() -> list[Case]:
    g = "cli_writes"
    return [Case(f"{g}.{fn.split('.')[0]}.{'_'.join(a).replace('-', '_')}", g, "local_cli", "-", "FUNC", fn,
                 " ".join(a), hole="H3") for fn, a in CLI_FUNCS]


BUILDERS = {
    "agent_share": agent_share_cases, "delegation": delegation_cases, "agent_msg": agent_msg_cases,
    "peer": peer_cases, "relay": relay_cases, "host_relay": host_relay_cases, "hosts": hosts_cases,
    "machine_token": machine_token_cases, "cli_writes": cli_cases,
}


@lru_cache(maxsize=None)
def all_cases() -> tuple[Case, ...]:
    out = [c for b in BUILDERS.values() for c in b()]
    ids = [c.id for c in out]
    dup = {i for i in ids if ids.count(i) > 1}
    assert not dup, f"사례 id 중복: {sorted(dup)[:5]}"
    return tuple(out)


def cases_for(group: str) -> list[Case]:
    return [c for c in all_cases() if c.group == group]
