"""라우트 인가 표 — 앱의 모든 HTTP·WebSocket 라우트가 어느 인가 분류에 드는지 **한 곳**에 적는다.

새 라우트를 만들면 이 표에 한 줄을 더해야 한다. 안 더하면 `server/tests/test_authz_route_table.py` 가 실패하고,
그 메시지가 더할 줄을 그대로 알려 준다. 표는 동작을 바꾸지 않는다 — 실제 관문(라우터 의존성·미들웨어 예외·소켓 허용표·peer `NEEDS`·
relay 모듈)이 정본이고, 시험이 표와 그 관문이 어긋나면 잡는다. 표의 일은 「이 라우트는 누가 부를 수 있는가」를 라우트를 만드는 사람이
의식적으로 한 번 적게 하는 것이다.

## 분류 (라우트마다 정확히 하나)
  public         로그인 전에도 열린 목록(`main.AUTH_EXEMPT_PATHS`·`/static`·`/s/`). 각 라우트가 자기 방식으로 검증한다.
  human          로그인한 사람의 세션만(`require_human_session`) 또는 승격 세션(`require_elevated`, `elevated=True`).
                 기계 토큰·unix socket 허용표에 들어 있으면 안 된다.
  socket         unix socket 허용표(`sock.ROUTES`)에 든 (메서드, 경로) — 에이전트·로컬 CLI·데몬이 소켓으로 부른다.
                 `via` 가 해당 허용표 항목이다. `VT_AUTHZ_ENFORCE=legacy` 의 기계 토큰 범위가 같은 표라서 그 길도 같이 열린다.
  legacy_token   기계 토큰 허용표에는 있으나 소켓 허용표에는 없는 (메서드, 경로). 두 표가 같은 지금은 쓰이지 않는다.
  peer           다른 맥이 HMAC 서명으로 부르는 `/api/peer/*`. `via` 는 `peer_svc.NEEDS` 의 키다(필요한 scope·조건이 거기 있다).
  relay          호스트 전환 중계(`peer_relay`: 받는 쪽, `host_relay`: 보내는 쪽). 그룹별 판정은 `relay_policy`·`scopes.RELAY_*`.
  authenticated  인증된 어떤 주체든(쿠키·기계 토큰·소켓) — 그 밖의 판정은 라우트 안의 논리다. 엔진 scope 가 걸리면 `scope`·`conds`.

## 읽는 법
  scope·conds  이 라우트가 엔진에 묻는 (scope, 조건)이다. 없으면 엔진이 묻지 않는 라우트(미들웨어 인증만).
  gate-only    앱에 라우트가 없고 소켓 게이트가 앱 앞에서 직접 답하는 경로(`GATE_ONLY`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Optional

from . import peer_svc, sock
from . import scopes as S

PUBLIC = "public"
HUMAN = "human"
SOCKET = "socket"
LEGACY_TOKEN = "legacy_token"
PEER = "peer"
RELAY = "relay"
AUTHENTICATED = "authenticated"
CLASSES = (PUBLIC, HUMAN, SOCKET, LEGACY_TOKEN, PEER, RELAY, AUTHENTICATED)


@dataclass(frozen=True)
class Rule:
    cls: str
    scope: Optional[str] = None
    conds: dict = field(default_factory=dict)
    elevated: bool = False
    via: Optional[str] = None      # socket: 허용표 패턴 · peer/relay: NEEDS 키 · relay(보내는 쪽): "host_gate"
    note: str = ""


def public(note: str = "") -> Rule:
    return Rule(PUBLIC, note=note)


def human(elevated: bool = False) -> Rule:
    return Rule(HUMAN, elevated=elevated)


def sock_(scope: Optional[str] = None, **conds) -> Rule:
    return Rule(SOCKET, scope, dict(conds))        # via 는 아래 `_resolve` 가 허용표에서 채운다


def auth(scope: Optional[str] = None, **conds) -> Rule:
    return Rule(AUTHENTICATED, scope, dict(conds))


def peer(action: Optional[str], note: str = "") -> Rule:
    if action is None:
        return Rule(PEER, note=note)
    n = peer_svc.NEEDS[action]
    return Rule(PEER, n.scope, dict(n.ctx), via=action, note=note)


def relay(action: str) -> Rule:
    n = peer_svc.NEEDS[action]
    return Rule(RELAY, n.scope, dict(n.ctx), via=action)


def relay_out() -> Rule:
    return Rule(RELAY, "relay", {"group": S.ANY_GROUP}, via="host_gate")


# 앱에 라우트가 없고 소켓 게이트가 직접 답하는 경로. (메서드, 경로) -> 설명.
GATE_ONLY: dict[tuple[str, str], str] = {
    ("GET", sock.SELFTEST_PATH): "socket gate self-test; answered before the app, only until the gate is armed",
}

# 라우트 인가 표. 키는 (메서드, 경로 템플릿): 메서드는 `GET`…, WebSocket 은 `WS`, 정적 파일 마운트는 `MOUNT`.
# 한 줄에 메서드를 공백으로 이어 쓸 수 있다.
_ROWS: tuple = (
# --- static mount
    ("MOUNT", "/static", public("static assets")),
# --- routes/pty.py
    ("GET POST", "/api/sessions", auth()),
    ("DELETE", "/api/sessions/{session_id}", auth()),
    ("POST", "/api/sessions/{session_id}/keys", auth()),
    ("POST", "/api/sessions/{session_id}/paste", auth()),
    ("PATCH", "/api/sessions/{session_id}", sock_()),
    ("POST", "/api/upload", auth()),
    ("WS", "/ws/{session_id}", auth()),
    ("WS", "/ws-notify", auth()),
# --- routes/tmux.py
    ("GET", "/api/tmux/sessions", sock_()),
    ("POST", "/api/tmux/create", auth()),
    ("POST", "/api/tmux/open-on-mac", auth()),
    ("POST", "/api/tmux/attach", auth()),
    ("DELETE", "/api/tmux/kill/{tmux_name}", sock_()),
    ("GET", "/api/tmux/preview/{tmux_name}", auth()),
    ("GET", "/api/tmux/clients", auth()),
    ("POST", "/api/tmux/detach-client", auth()),
    ("POST", "/api/tmux/clients/solo", auth()),
    ("WS", "/ws-preview/{tmux_name}", auth()),
# --- routes/voice.py
    ("POST", "/voice/cancel", sock_()),
    ("POST", "/voice/target", sock_()),
    ("GET PUT", "/voice/defaults", auth()),
    ("GET PUT", "/voice/session", auth()),
    ("POST", "/voice/bind", sock_()),
    ("POST", "/voice/test", auth()),
    ("WS", "/ws-voice", auth()),
    ("POST", "/voice/input", sock_()),
    ("POST", "/voice/output", auth()),
    ("GET", "/voice/notify/clients", auth()),
    ("GET", "/voice/stt/status", auth()),
    ("GET", "/voice/stt/catalog", auth()),
    ("POST", "/voice/stt/setup", auth()),
    ("POST", "/voice/stt/preload", sock_()),
    ("POST", "/voice/stt/unload", auth()),
    ("GET DELETE", "/voice/stt/model", auth()),
    ("POST", "/voice/local/start", auth()),
    ("POST", "/voice/local/stop", auth()),
    ("POST", "/api/watch/{session_id}", auth()),
    ("GET", "/api/notify/status", auth()),
    ("POST", "/api/notify/test", auth()),
# --- routes/agents.py
    ("GET", "/api/agents", auth()),
    ("GET", "/api/agents/coverage", auth()),
    ("GET", "/api/agents/{tmux_name}", auth()),
    ("POST", "/api/agent/event", sock_()),
    ("POST", "/api/agent/report", sock_()),
    ("GET", "/api/agent/status", auth()),
    ("WS", "/ws-agent", auth()),
    ("POST", "/api/agent/ack", human()),
# --- routes/system.py
    ("GET", "/api/capabilities", sock_()),
    ("GET", "/api/usage", auth()),
    ("GET POST", "/api/usage/counter", auth()),
    ("GET", "/api/tailscale/status", auth()),
    ("POST", "/api/notify/client-event", sock_()),
    ("GET", "/api/safe-mode", auth()),
    ("GET", "/api/system/stats", auth()),
    ("GET", "/api/hooks/status", auth()),
    ("GET PUT", "/api/device-settings", auth()),
    ("PUT", "/api/lang", auth()),
    ("GET PUT", "/api/workspace", auth()),
    ("POST", "/api/tmux/kick", public("X-VT-Kick secret made at server start; only nudges the watcher")),
    ("WS", "/ws-workspace", auth()),
    ("GET", "/p/authorize", public("checks session + device itself; redirects instead of 401")),
# --- routes/clipboard.py
    ("POST", "/api/clipboard/push", sock_()),
# --- routes/files.py
    ("GET", "/api/fs/roots", auth()),
    ("GET", "/api/fs/search", auth()),
    ("GET", "/api/fs/tree", auth()),
    ("GET", "/api/fs/file", auth()),
    ("GET", "/api/fs/raw", auth()),
    ("WS", "/ws-fs-watch", auth()),
    ("GET", "/api/files", auth()),
    ("GET", "/api/files/{file_id}/download", auth()),
    ("DELETE", "/api/files/{file_id}", auth()),
    ("POST", "/api/files/delete", auth()),
    ("GET", "/api/files/{file_id}/path", auth()),
    ("POST", "/api/files/{file_id}/send", auth()),
    ("POST", "/api/files/{file_id}/insert", auth()),
# --- routes/git.py
    ("GET", "/api/git/status", auth()),
    ("GET", "/api/git/diff", auth()),
    ("GET", "/api/git/log", auth()),
    ("GET", "/api/git/branches", auth()),
    ("GET", "/api/git/show", auth()),
    ("POST", "/api/git/stage", auth()),
    ("POST", "/api/git/unstage", auth()),
    ("POST", "/api/git/commit", auth()),
# --- routes/ports.py
    ("GET", "/api/ports", auth()),
    ("DELETE", "/api/ports/{port}", auth()),
    ("POST", "/api/ports/_proxy/sync", sock_()),
    ("GET", "/api/tunnel/list", auth()),
    ("POST DELETE", "/api/ports/{port}/expose", human(elevated=True)),
    ("POST", "/api/ports/{port}/share/pause", human(elevated=True)),
    ("POST", "/api/ports/{port}/share/resume", human(elevated=True)),
    ("POST", "/api/ports/{port}/share/refresh-url", human(elevated=True)),
    ("POST", "/api/ports/{port}/share/pin", human(elevated=True)),
    ("PATCH", "/api/ports/{port}/share", human(elevated=True)),
# --- routes/queue.py
    ("GET POST", "/api/queue", auth()),
    ("DELETE", "/api/queue/{item_id}", auth()),
    ("POST", "/api/queue/{item_id}/unblock", auth()),
    ("GET", "/api/queue/transitions", auth()),
    ("POST", "/api/queue/run", sock_()),
# --- routes/snippets.py
    ("GET", "/api/snippets", auth()),
    ("GET", "/api/snippets/project", auth()),
    ("POST", "/api/snippets", auth()),
    ("DELETE PATCH", "/api/snippets/{item_id}", auth()),
# --- routes/push.py
    ("GET", "/api/push/status", auth()),
    ("GET", "/api/push/key", auth()),
    ("POST DELETE", "/api/push/subscribe", auth()),
    ("POST", "/api/push/test", auth()),
# --- routes/search.py
    ("GET", "/api/search/scrollback", auth()),
# --- routes/git_accounts.py
    ("GET", "/api/git/accounts", auth()),
    ("GET", "/api/git/binding", auth()),
    ("POST", "/api/git/accounts", human(elevated=True)),
    ("DELETE", "/api/git/accounts/{account_id}", human(elevated=True)),
    ("PUT", "/api/git/binding", human(elevated=True)),
# --- routes/worktree.py
    ("GET", "/api/worktrees", auth()),
    ("POST", "/api/worktrees/hidden", auth()),
    ("GET", "/api/worktrees/precheck", auth()),
    ("POST", "/api/worktrees", auth()),
    ("DELETE", "/api/worktrees/{wt_id}", auth()),
    ("POST", "/api/worktrees/{wt_id}/open", auth()),
# --- routes/repos.py
    ("GET", "/api/repos", auth()),
# --- routes/security.py
    ("GET", "/api/devices", auth()),
    ("GET", "/api/auth/elevation", auth()),
# --- routes/authz.py
    ("GET", "/api/authz/shadow", human()),
    ("POST", "/api/authz/approvals/{approval_id}/decision", human()),
    ("DELETE", "/api/authz/grants/{grant_id}", human()),
    ("POST", "/api/authz/panic", human(elevated=True)),
    ("GET", "/api/authz/approvals", sock_()),
    ("GET", "/api/authz/grants", sock_()),
    ("GET", "/api/authz/audit", sock_()),
    ("GET", "/api/authz/whoami", sock_()),
    ("POST", "/api/authz/requests", sock_("authz.request")),
    ("GET", "/api/authz/requests", sock_()),
    ("GET", "/api/authz/my-grants", sock_()),
    ("GET", "/api/authz/leases", human()),
    ("DELETE", "/api/authz/leases/{lease_id}", human()),
# --- routes/leases.py
    ("POST", "/api/leases", sock_("fs.write", op="write")),
    ("POST", "/api/leases/check", sock_()),
    ("POST", "/api/leases/{lease_id}/renew", sock_()),
    ("DELETE", "/api/leases/{lease_id}", sock_()),
    ("GET", "/api/leases", sock_()),
# --- routes/pairs.py
    ("POST", "/api/pairs", sock_()),
    ("POST", "/api/pairs/join", sock_()),
    ("POST", "/api/pairs/{pair_id}/code", sock_()),
    ("GET", "/api/pairs/{pair_id}/status", sock_()),
    ("GET", "/api/pairs", sock_()),
    ("POST", "/api/pairs/{pair_id}/handoff", sock_()),
    ("POST", "/api/pairs/{pair_id}/handoff/accept", sock_()),
    ("DELETE", "/api/pairs/{pair_id}", sock_()),
# --- routes/share.py
    ("POST", "/api/files/{file_id}/share", human(elevated=True)),
    ("DELETE", "/api/files/{file_id}/share/{share_id}", human(elevated=True)),
    ("GET", "/s/{token}", public("share token signature/expiry/mode verified by the route")),
    ("POST", "/s/{token}/pin", public("share token + PIN verified by the route")),
# --- routes/peer.py
    ("POST", "/api/peer/pair", peer(None, "one-time pairing ticket instead of a signature")),
    ("GET", "/api/peer/sessions", peer("sessions")),
    ("GET", "/api/peer/clients", peer("clients")),
    ("POST", "/api/peer/clients/detach", peer("clients/detach")),
    ("POST", "/api/peer/clients/solo", peer("clients/solo")),
    ("GET", "/api/peer/search", peer("search")),
    ("GET", "/api/peer/ping", peer("ping")),
    ("POST", "/api/peer/input", peer("input")),
    ("POST", "/api/peer/sessions/create", peer("sessions/create")),
    ("POST", "/api/peer/sessions/kill", peer("sessions/kill")),
    ("POST", "/api/peer/sessions/rename", peer("sessions/rename")),
    ("WS", "/api/peer/ws/{tmux_name}", peer("ws")),
    ("POST", "/api/peer/agent/message", peer("agent/message")),
    ("POST", "/api/peer/file", peer("file")),
# --- routes/hosts.py
    ("GET", "/api/hosts", auth("session.view")),
    ("GET", "/api/hosts/self", auth()),
    ("GET", "/api/hosts/{host_id}/clients", auth("session.view")),
    ("POST", "/api/hosts/{host_id}/clients/detach", auth("session.manage", op="detach")),
    ("POST", "/api/hosts/{host_id}/clients/solo", auth("session.manage", op="detach")),
    ("GET", "/api/hosts/{host_id}/search", auth("session.read")),
    ("POST", "/api/hosts/{host_id}/sessions", auth("session.manage", op="create")),
    ("DELETE", "/api/hosts/{host_id}/sessions/{name}", auth("session.manage", op="kill")),
    ("PATCH", "/api/hosts/{host_id}/sessions/{name}", auth("session.manage", op="rename")),
    ("POST", "/api/hosts/{host_id}/ping", auth("ping")),
    ("POST", "/api/host-grants/{peer_id}/change", sock_()),
# --- routes/peer_proxy.py
    ("WS", "/ws/remote/{host_id}/{tmux_name}", auth()),
# --- routes/peer_relay.py
    ("DELETE GET PATCH POST PUT", "/api/peer/relay/{rest:path}", relay("relay")),
    ("WS", "/api/peer/relay-ws/{rest:path}", relay("relay-ws")),
# --- routes/host_relay.py
    ("DELETE GET PATCH POST PUT", "/api/hosts/{host_id}/relay/{rest:path}", relay_out()),
    ("WS", "/ws/hosts/{host_id}/relay/{rest:path}", relay_out()),
# --- routes/agent_msg.py
    ("POST", "/api/agent/message", sock_("msg.send")),
    ("GET", "/api/agent/messages", sock_()),
    ("GET", "/api/agent/directory", sock_()),
# --- routes/agent_share.py
    ("GET", "/api/agent/shared", sock_()),
    ("POST", "/api/agent/shared/read", sock_("session.read")),
    ("POST", "/api/agent/shared/send", sock_("session.input", mode="line")),
    ("POST", "/api/tmux/{name}/share", human()),
    ("DELETE", "/api/agent/share/sessions/{name}", human()),
    ("GET", "/api/agent/share/requests", human()),
    ("POST", "/api/agent/share/requests/{request_id}/decision", human()),
    ("GET", "/api/agent/share/grants", human()),
    ("DELETE", "/api/agent/share/grants/{grant_id}", human()),
    ("GET", "/api/agent/share/audit", human()),
# --- routes/delegation.py
    ("GET", "/api/delegations/role", sock_()),
    ("GET", "/api/delegations", sock_()),
    ("POST", "/api/delegations", sock_("deleg.spawn")),
    ("POST", "/api/delegations/report", sock_("deleg.control", role="child")),
    ("POST", "/api/delegations/{did}/send", sock_("deleg.control", role="parent")),
    ("POST", "/api/delegations/{did}/close", sock_("deleg.control", role="parent")),
    ("POST", "/api/delegations/{did}/close-by-cli", sock_("deleg.control", role="parent")),
    ("POST", "/api/delegations/{did}/close-by-user", human()),
# --- routes/mcp.py
    ("GET", "/api/mcp/skills", auth()),
    ("GET", "/api/mcp/library", auth()),
    ("GET", "/api/mcp/library/candidates", auth()),
    ("POST", "/api/mcp/library/import-preview", auth()),
    ("POST", "/api/mcp/library/refresh-preview", auth()),
    ("GET", "/api/mcp/sync", auth()),
    ("POST", "/api/mcp/sync/preview", auth()),
    ("POST", "/api/mcp/library/preview", auth()),
    ("POST", "/api/mcp/skills/preview", auth()),
    ("GET", "/api/mcp", auth()),
    ("POST", "/api/mcp/tags", auth()),
    ("POST", "/api/mcp/tags/rename", auth()),
    ("POST", "/api/mcp/tags/delete", auth()),
    ("GET", "/api/mcp/creds", auth()),
    ("GET", "/api/mcp/plugins", auth()),
    ("POST", "/api/mcp/library/import-candidates", human(elevated=True)),
    ("POST", "/api/mcp/library/save", human(elevated=True)),
    ("POST", "/api/mcp/library/refresh", human(elevated=True)),
    ("POST", "/api/mcp/sync/toggle", human(elevated=True)),
    ("POST", "/api/mcp/sync/apply", human(elevated=True)),
    ("POST", "/api/mcp/library/targets", human(elevated=True)),
    ("POST", "/api/mcp/library/apply", human(elevated=True)),
    ("POST", "/api/mcp/skills/toggle", human(elevated=True)),
    ("POST", "/api/mcp/skills/import", human(elevated=True)),
    ("POST", "/api/mcp/group", human(elevated=True)),
    ("POST", "/api/mcp/toggle", human(elevated=True)),
    ("POST", "/api/mcp/toggle-batch", human(elevated=True)),
    ("POST", "/api/mcp/creds", human(elevated=True)),
    ("POST", "/api/mcp/creds/delete", human(elevated=True)),
    ("POST", "/api/mcp/deploy", human(elevated=True)),
    ("POST", "/api/mcp/plugins/toggle", human(elevated=True)),
    ("POST", "/api/mcp/servers/{scope}/{name}/verify", human(elevated=True)),
# --- main.py
    ("GET", "/", public("login gate page; has to render unauthenticated")),
    ("GET", "/sw.js", public("static asset of the login gate")),
    ("GET", "/manifest.json", public("static asset of the login gate")),
    ("POST", "/api/auth", public("is the authentication mechanism")),
    ("GET", "/api/auth/status", public("is the authentication mechanism")),
    ("POST", "/api/auth/elevate", auth()),
    ("POST", "/api/auth/logout", public("is the authentication mechanism")),
    ("GET", "/favicon.ico", public("static asset")),

)


def _build() -> dict[tuple[str, str], Rule]:
    out: dict[tuple[str, str], Rule] = {}
    for methods, path, rule in _ROWS:
        for m in methods.split():
            if (m, path) in out:
                raise ValueError(f"route_table: duplicate entry {m} {path}")
            out[(m, path)] = rule
    return out


TABLE: dict[tuple[str, str], Rule] = _build()


def _resolve(table: dict[tuple[str, str], Rule]) -> dict[tuple[str, str], Rule]:
    """`socket` 규칙의 `via` 를 허용표에서 채운다(첫 일치 항목의 패턴). 일치가 없으면 비워 두고 시험이 잡는다."""
    out = {}
    for (m, path), r in table.items():
        if r.cls == SOCKET:
            r = replace(r, via=socket_entry(m, path))
        out[(m, path)] = r
    return out


def sample_path(template: str) -> str:
    """경로 템플릿(`/api/x/{id}`, `{rest:path}`)을 허용표와 맞춰 볼 구체 경로로 바꾼다."""
    return re.sub(r"\{[^}]+\}", "x", template)


def socket_entry(method: str, template: str) -> Optional[str]:
    """이 (메서드, 경로)를 여는 소켓 허용표 항목의 패턴. 없으면 None."""
    path = sample_path(template)
    for m, rx, _who in sock._ROUTES_RE:
        if m == method.upper() and rx.fullmatch(path):
            return rx.pattern
    return None


TABLE = _resolve(TABLE)
