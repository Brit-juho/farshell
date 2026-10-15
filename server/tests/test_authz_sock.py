"""plan-2.6 2단계 — unix socket 서버(authz/sock.py): peer 자격, 게이트, 주체 해석, 시작·정리, 자가 시험.

실제 uvicorn UDS 서버를 프로세스 안에 띄워 확인한다(`sock_testkit.LiveSock`). tmux·프로세스 표는 가짜로 대체하고
실제 `~/.vt/tmux`·사용자의 tmux 서버에는 닿지 않는다(HOME 은 conftest 가 격리). 소켓 경로는 AF_UNIX 한도 때문에
`/tmp` 아래 짧은 디렉터리를 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import struct
import sys

import pytest
from fastapi import FastAPI, Request

import fsh_client
import share_identity
from authz import principals as P
from authz import sock
from sock_testkit import LiveSock, rmtree, short_dir

ME = os.geteuid()


def row(pane="%1", pane_pid=5000, sid="$1", created=111, name="work"):
    return share_identity.PaneRow(pane_id=pane, pane_pid=pane_pid, sid=sid, created=created, name=name,
                                  active=True, command="zsh", nick="", mode="")


def fake_resolver(rows, table):
    """rows: 우리 tmux 의 pane 목록 · table: pid → (ppid, pgid, tpgid, comm) 가짜 프로세스 표."""
    return sock.Resolver(rows_fn=lambda: rows, ps_fn=lambda: table)


@pytest.fixture
def env(monkeypatch):
    d = short_dir()
    path = os.path.join(d, "run", "fsh.sock")
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "1")
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", path)
    yield path
    rmtree(d)


def make_app():
    app = FastAPI()

    @app.post("/api/agent/report")
    async def report(request: Request):
        p = request.state.fsh_principal
        return {"id": p.id, "kind": p.kind, "auth_kind": request.state.auth_kind, "svc": p.attrs.get("svc")}

    @app.get("/api/agent/shared")
    async def shared(request: Request):
        return {"peer": request.scope.get("fsh_peer")}

    @app.get("/api/tmux/{name}/secret")
    async def not_listed():
        return {"leak": True}

    return app


@pytest.fixture
def live(env):
    started = []

    def start(resolver=None, app=None):
        ls = LiveSock(app or make_app(), resolver or fake_resolver([], {os.getpid(): (1, 0, 0, "py")}))
        started.append(ls)
        return ls, ls.start()

    yield start
    for ls in started:
        ls.stop()


def sreq(env, method, path, **kw):
    return fsh_client.socket_request(method, path, sock_path=env, **kw)


# --- peer 자격 읽기 --------------------------------------------------------------------------


def test_peer_creds_real_connection_reports_own_uid_and_pid(env):
    os.makedirs(os.path.dirname(env), mode=0o700)
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(env)
    srv.listen(1)
    cli = socket.socket(socket.AF_UNIX)
    cli.connect(env)
    conn, _ = srv.accept()
    try:
        assert sock.peer_creds(conn) == (ME, os.getpid())
        assert sock.peer_creds(cli) == (ME, os.getpid())     # 반대편에서도 상대(=서버=이 프로세스)
    finally:
        for s in (conn, cli, srv):
            s.close()


class FakeSock:
    def __init__(self, answers):
        self.answers = answers

    def getsockopt(self, level, opt, size):
        v = self.answers[(level, opt)]
        if isinstance(v, Exception):
            raise v
        return v


def test_peer_creds_macos_parsing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    xucred = struct.pack("=IIh2x16I", 0, 777, 1, *([0] * 16))
    assert len(xucred) == 76
    s = FakeSock({(0, 1): xucred, (0, 2): struct.pack("=i", 4242)})
    assert sock.peer_creds(s) == (777, 4242)


def test_peer_creds_linux_parsing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)
    s = FakeSock({(socket.SOL_SOCKET, 17): struct.pack("3i", 4243, 778, 20)})
    assert sock.peer_creds(s) == (778, 4243)


@pytest.mark.parametrize("answers", [
    {(0, 1): OSError(22, "bad"), (0, 2): struct.pack("=i", 1)},
    {(0, 1): struct.pack("=IIh2x16I", 5, 1, 1, *([0] * 16)), (0, 2): struct.pack("=i", 1)},   # 모르는 xucred 버전
    {(0, 1): struct.pack("=IIh2x16I", 0, 1, 1, *([0] * 16)), (0, 2): struct.pack("=i", 0)},   # pid 0
    {(0, 1): b"short", (0, 2): struct.pack("=i", 1)},
])
def test_peer_creds_failure_is_error_not_guess(monkeypatch, answers):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(sock.PeerCredError):
        sock.peer_creds(FakeSock(answers))


# --- 시작 · 자가 시험 · fail closed -------------------------------------------------------------


def test_start_creates_0600_socket_in_0700_dir_and_selftest_passes(live, env):
    os.makedirs(os.path.dirname(env), mode=0o755)           # 이미 넓은 권한으로 있던 디렉터리도 좁힌다
    os.chmod(os.path.dirname(env), 0o755)
    ls, st = live()
    assert st["state"] == "listening" and st["selftest"] is True, st
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600
    assert stat.S_ISSOCK(os.lstat(env).st_mode)
    assert stat.S_IMODE(os.stat(os.path.dirname(env)).st_mode) == 0o700
    s, body = sreq(env, "GET", "/api/authz/whoami")
    assert s == 200 and json.loads(body)["principal"]["pid"] == os.getpid()


def test_socket_is_chmodded_before_listen(monkeypatch, env):
    """uvicorn 의 uds= 경로는 listen 뒤에 chmod 한다 — 우리는 bind → chmod → listen 순서여야 한다."""
    order = []
    real_chmod, real_listen = os.chmod, socket.socket.listen

    def chmod(p, mode, *a, **k):
        if str(p) == env:
            order.append(("chmod", mode))
        return real_chmod(p, mode, *a, **k)

    class S(socket.socket):
        def listen(self, *a):
            order.append(("listen",))
            return real_listen(self, *a)

    monkeypatch.setattr(os, "chmod", chmod)
    monkeypatch.setattr(sock.socket, "socket", S)
    s = sock.prepare_socket(sock.Path(env))
    s.close()
    assert order == [("chmod", 0o600), ("listen",)]


def test_selftest_pid_mismatch_fails_closed(live, env, monkeypatch):
    """커널 값을 못 믿게 되면(uvicorn 업그레이드·플랫폼 차이) 소켓을 열어 두지 않는다."""
    monkeypatch.setattr(sock, "peer_creds", lambda s: (ME, os.getpid() + 7))
    ls, st = live()
    assert st["state"] == "failed" and "불일치" in st["reason"]
    assert not os.path.exists(env)                              # 파일도 없다
    with pytest.raises(fsh_client.SocketUnavailable):
        sreq(env, "GET", "/api/authz/whoami")
    rec = json.loads(open(sock.status_path(sock.Path(env))).read())
    assert rec["state"] == "failed" and rec["selftest"] is False


def test_selftest_unreadable_creds_fails_closed(live, env, monkeypatch):
    def boom(s):
        raise sock.PeerCredError("nope")
    monkeypatch.setattr(sock, "peer_creds", boom)
    ls, st = live()
    assert st["state"] == "failed"
    assert not os.path.exists(env)


def test_unarmed_gate_serves_nothing_but_selftest():
    app_called = []

    async def app(scope, receive, send):
        app_called.append(1)

    g = sock.Gate(app)
    out = run_gate(g, "GET", "/api/agent/shared", peer={"uid": ME, "pid": os.getpid()})
    assert out[0] == 503 and out[1]["error"] == "socket_not_armed" and not app_called


def test_start_disabled_by_env(live, env, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "0")
    ls, st = live()
    assert st["state"] == "disabled" and not os.path.exists(env)


def test_start_skips_overlong_path(monkeypatch):
    d = short_dir()
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "1")
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", os.path.join(d, "x" * 120, "fsh.sock"))
    ls = LiveSock(make_app())
    try:
        st = ls.start()
        assert st["state"] == "skipped" and "한도" in st["reason"]
    finally:
        ls.stop()
        rmtree(d)


def test_start_skips_wsl_mnt(monkeypatch, tmp_path):
    monkeypatch.setattr(sock, "_on_wsl_drvfs", lambda p: True)
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "1")
    d = short_dir()
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", os.path.join(d, "fsh.sock"))
    ls = LiveSock(make_app())
    try:
        st = ls.start()
        assert st["state"] == "skipped" and "/mnt/" in st["reason"]
        assert not os.path.exists(os.path.join(d, "fsh.sock"))
    finally:
        ls.stop()
        rmtree(d)


def test_wsl_detection_only_on_linux_wsl(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert sock._on_wsl_drvfs(sock.Path("/mnt/c/x")) is False


# --- stale 정리 -------------------------------------------------------------------------------


def test_stale_socket_file_is_removed_and_replaced(live, env):
    os.makedirs(os.path.dirname(env), mode=0o700)
    dead = socket.socket(socket.AF_UNIX)
    dead.bind(env)
    dead.close()                                                 # 파일만 남고 리스너는 없다
    assert stat.S_ISSOCK(os.lstat(env).st_mode)
    old_ino = os.lstat(env).st_ino
    ls, st = live()
    assert st["state"] == "listening"
    s, _ = sreq(env, "GET", "/api/authz/whoami")
    assert s == 200


def test_live_listener_is_never_unlinked(live, env):
    os.makedirs(os.path.dirname(env), mode=0o700)
    other = socket.socket(socket.AF_UNIX)
    other.bind(env)
    other.listen(1)
    try:
        ino = os.lstat(env).st_ino
        ls, st = live()
        assert st["state"] == "skipped" and "리스너" in st["reason"]
        assert os.lstat(env).st_ino == ino                       # 남의 소켓은 그대로
    finally:
        other.close()


def test_regular_file_is_never_deleted(live, env):
    os.makedirs(os.path.dirname(env), mode=0o700)
    open(env, "w").write("precious")
    ls, st = live()
    assert st["state"] == "skipped"
    assert open(env).read() == "precious"


def test_stop_unlinks_only_own_socket(live, env):
    ls, st = live()
    assert os.path.exists(env)
    ls.stop()
    assert not os.path.exists(env)
    rec = json.loads(open(sock.status_path(sock.Path(env))).read())
    assert rec["state"] == "stopped"


def test_stop_leaves_replaced_socket_alone(live, env):
    ls, st = live()
    os.unlink(env)                                               # 누군가 지우고 자기 소켓을 놓았다
    other = socket.socket(socket.AF_UNIX)
    other.bind(env)
    try:
        ino = os.lstat(env).st_ino
        ls.stop()
        assert os.lstat(env).st_ino == ino
    finally:
        other.close()


# --- 게이트 단위 ---------------------------------------------------------------------------------


def run_gate(gate, method, path, *, peer, headers=(), query=b"", typ="http"):
    sent = []

    async def send(m):
        sent.append(m)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": typ, "method": method, "path": path, "query_string": query, "fsh_peer": peer,
             "headers": [(k.encode(), v.encode()) for k, v in headers]}
    asyncio.run(gate(scope, receive, send))
    if not sent:
        return None
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(body or b"{}")


def armed_gate(app=None, resolver=None):
    seen = {}

    async def inner(scope, receive, send):
        seen["scope"] = scope
        from starlette.responses import JSONResponse
        await JSONResponse({"ok": True})(scope, receive, send)

    g = sock.Gate(app or inner, resolver or fake_resolver([], {os.getpid(): (1, 0, 0, "py")}))
    g.armed = True
    g.seen = seen
    return g


def test_gate_rejects_other_uid():
    g = armed_gate()
    st, body = run_gate(g, "POST", "/api/agent/report", peer={"uid": ME + 1, "pid": os.getpid()})
    assert st == 403 and body["error"] == "peer_uid_mismatch" and "scope" not in g.seen


@pytest.mark.parametrize("peer", [None, {}, {"uid": None, "pid": None, "error": "x"}])
def test_gate_rejects_unreadable_peer(peer):
    g = armed_gate()
    st, body = run_gate(g, "POST", "/api/agent/report", peer=peer)
    assert st == 403 and body["error"] == "peer_unidentified" and "scope" not in g.seen


def test_gate_rejects_websocket():
    g = armed_gate()
    sent = []

    async def send(m):
        sent.append(m)

    asyncio.run(g({"type": "websocket", "fsh_peer": {"uid": ME, "pid": 1}}, None, send))
    assert sent == [{"type": "websocket.close", "code": 1008}]


@pytest.mark.parametrize("hdr,query,what", [
    ([("authorization", "Bearer abc")], b"", "authorization"),
    ([("cookie", "a=1; vt_session=zzz")], b"", "cookie"),
    ([("x-peer-id", "m5")], b"", "peer_hmac"),
    ([], b"x=1&token=abc", "query_token"),
])
def test_ambiguous_credentials_are_400_on_socket(hdr, query, what):
    g = armed_gate()
    st, body = run_gate(g, "POST", "/api/agent/report", peer={"uid": ME, "pid": os.getpid()}, headers=hdr, query=query)
    assert st == 400 and body["error"] == "ambiguous_principal" and what in body["reason"]
    assert "scope" not in g.seen


def test_ambiguity_checked_before_route_allowlist():
    g = armed_gate()
    st, body = run_gate(g, "GET", "/api/files", peer={"uid": ME, "pid": os.getpid()},
                        headers=[("authorization", "Bearer abc")])
    assert (st, body["error"]) == (400, "ambiguous_principal")


def test_unrelated_cookie_is_not_a_credential():
    g = armed_gate()
    st, _ = run_gate(g, "POST", "/api/agent/report", peer={"uid": ME, "pid": os.getpid()},
                     headers=[("cookie", "theme=dark")])
    assert st == 200


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/files"), ("POST", "/api/agent/share/requests"), ("GET", "/api/agent/share/grants"),
    ("DELETE", "/api/agent/share/grants/abc"), ("POST", "/api/delegations/abc/close-by-user"),
    ("GET", "/api/authz/shadow"), ("GET", "/api/peer/whoami"), ("POST", "/api/agent/report/x"),
    ("GET", "/api/agent/report"), ("POST", "/api/tmux/kill/a"), ("GET", "/"), ("POST", "/api/auth"),
    ("POST", "/api/tmux/a/share"), ("POST", "/api/hosts/pair"),
])
def test_route_outside_allowlist_is_403(method, path):
    g = armed_gate()
    st, body = run_gate(g, method, path, peer={"uid": ME, "pid": os.getpid()})
    assert st == 403 and body["error"] == "socket_route_not_allowed", (method, path)
    assert "scope" not in g.seen


@pytest.mark.parametrize("method,path", [(m, p) for m, p in [
    ("POST", "/api/agent/event"), ("POST", "/api/agent/report"), ("POST", "/api/clipboard/push"),
    ("POST", "/voice/cancel"), ("POST", "/voice/input"), ("POST", "/voice/stt/preload"), ("POST", "/voice/target"),
    ("POST", "/voice/bind"), ("GET", "/api/capabilities"), ("GET", "/api/tmux/sessions"),
    ("DELETE", "/api/tmux/kill/dev"), ("PATCH", "/api/sessions/w1"), ("POST", "/api/notify/client-event"),
    ("POST", "/api/agent/message"), ("GET", "/api/agent/messages"), ("GET", "/api/agent/directory"),
    ("GET", "/api/agent/shared"), ("POST", "/api/agent/shared/read"), ("POST", "/api/agent/shared/send"),
    ("GET", "/api/delegations"), ("GET", "/api/delegations/role"), ("POST", "/api/delegations"),
    ("POST", "/api/delegations/report"), ("POST", "/api/delegations/d1/send"), ("POST", "/api/delegations/d1/close"),
    ("POST", "/api/queue/run"), ("POST", "/api/ports/_proxy/sync")]])
def test_every_in_repo_client_route_is_allowed(method, path):
    assert sock.route_allowed(method, path)
    g = armed_gate()
    st, _ = run_gate(g, method, path, peer={"uid": ME, "pid": os.getpid()})
    assert st == 200 and g.seen["scope"]["fsh_sock"] is True


def test_allowlist_has_no_wildcards_beyond_client_needs():
    # 허용표에 .* 가 들어가면 새 라우트가 조용히 열린다 — 경로 한 단계짜리 [^/]+ 외의 열린 패턴은 금지.
    for _, pat, _ in sock.ROUTES:
        assert ".*" not in pat and ".+" not in pat, pat


def test_gate_exposes_principal_and_marker_to_app():
    g = armed_gate(resolver=fake_resolver([], {os.getpid(): (1, 0, 0, "py")}))
    st, _ = run_gate(g, "POST", "/api/agent/report", peer={"uid": ME, "pid": os.getpid()})
    sc = g.seen["scope"]
    assert st == 200 and sc["state"]["fsh_principal"].kind == "cli" and sc["state"]["auth_kind"] == "socket"


# --- 주체 해석 ------------------------------------------------------------------------------------


PID = os.getpid()
# 가짜 프로세스 표: 이 프로세스 → 셸(6000) → pane_pid(5000) → 1
IN_PANE = {PID: (6000, 0, 0, "py"), 6000: (5000, 0, 0, "zsh"), 5000: (1, 0, 0, "zsh")}
OUT_OF_PANE = {PID: (7000, 0, 0, "py"), 7000: (1, 0, 0, "zsh")}


def resolve(table, rows, svc="", pid=PID, uid=ME):
    return fake_resolver(rows, table).resolve_sync({"uid": uid, "pid": pid}, svc)


def test_pane_chain_resolves_to_agent_with_session_key():
    p = resolve(IN_PANE, [row()])
    assert p.kind == P.AGENT and p.id == "agent:local/$1:111"
    assert p.attrs["pane"] == "%1" and p.attrs["pid"] == PID


def test_non_pane_process_is_cli_not_human():
    p = resolve(OUT_OF_PANE, [row()])
    assert p.kind == P.CLI and p.id == f"cli:uid{ME}:pid{PID}"
    assert p.kind != P.HUMAN and not p.elevated


def test_foreign_tmux_process_is_not_agent():
    """사용자 개인 tmux(다른 소켓)의 pane 안 프로세스 — 우리 pane_pid 와 체인이 겹치지 않으므로 cli."""
    foreign = {PID: (9100, 0, 0, "py"), 9100: (9000, 0, 0, "zsh"), 9000: (1, 0, 0, "tmux")}
    p = resolve(foreign, [row(pane_pid=5000)])
    assert p.kind == P.CLI


def test_agent_declaring_svc_stays_agent():
    p = resolve(IN_PANE, [row()], svc="clipboard")
    assert p.kind == P.AGENT and p.attrs["svc"] == "clipboard"
    p = resolve(IN_PANE, [row()], svc="cli")
    assert p.kind == P.AGENT


def test_hook_in_pane_is_p5_hook():
    p = resolve(IN_PANE, [row()], svc="hook")
    assert p.kind == P.HOOK and p.id == "hook:local/$1:111"


def test_hook_claim_outside_pane_is_only_svc_not_hook_or_agent():
    p = resolve(OUT_OF_PANE, [row()], svc="hook")
    assert p.kind == P.SVC and p.id == "svc:hook"


def test_unknown_svc_name_is_ignored():
    p = resolve(OUT_OF_PANE, [row()], svc="root-admin")
    assert p.kind == P.CLI


@pytest.mark.parametrize("name", ["clipboard", "voice", "tui", "tmux-notify", "tunnel-hook", "mcp-probe"])
def test_declared_service_names_outside_pane(name):
    assert resolve(OUT_OF_PANE, [row()], svc=name).id == f"svc:{name}"


def test_dead_peer_is_rejected():
    with pytest.raises(sock.Reject) as e:
        resolve(OUT_OF_PANE, [row()], pid=2_000_000_000)
    assert e.value.status == 403 and e.value.error == "peer_gone"


def test_unreadable_tmux_fails_closed():
    with pytest.raises(sock.Reject) as e:
        sock.Resolver(rows_fn=lambda: None, ps_fn=lambda: OUT_OF_PANE).resolve_sync({"uid": ME, "pid": PID}, "")
    assert e.value.status == 503 and e.value.error == "identity_unavailable"


def test_empty_chain_fails_closed():
    with pytest.raises(sock.Reject) as e:
        resolve({}, [row()])         # 프로세스 표에 peer 자신이 없다 → 체인을 못 만든다
    assert e.value.error == "peer_gone"


def test_cycle_and_depth_limits_do_not_hang():
    cyc = {PID: (6000, 0, 0, "py"), 6000: (PID, 0, 0, "zsh")}
    assert resolve(cyc, [row()]).kind == P.CLI


def test_principal_cache_expires(monkeypatch):
    r = fake_resolver([row()], IN_PANE)
    t = [100.0]
    monkeypatch.setattr(sock.time, "monotonic", lambda: t[0])
    assert r.resolve_sync({"uid": ME, "pid": PID}, "").kind == P.AGENT
    r._rows_fn = lambda: []                                      # pane 이 사라졌다
    assert r.resolve_sync({"uid": ME, "pid": PID}, "").kind == P.AGENT      # TTL 안
    t[0] += sock.Resolver.TTL + 0.1
    assert r.resolve_sync({"uid": ME, "pid": PID}, "").kind == P.CLI


# --- share_identity: 소켓 peer pid ----------------------------------------------------------------


@pytest.fixture
def ours(monkeypatch):
    import pane_resolve
    monkeypatch.setattr(pane_resolve, "is_our_socket", lambda t: bool(t) and t.startswith("/ours"))


def test_verify_caller_uses_peer_pid_and_never_lsof(ours, monkeypatch):
    monkeypatch.setattr(share_identity, "socket_owner_pid", lambda *a, **k: pytest.fail("lsof 경로를 탔다"))
    monkeypatch.setattr(share_identity, "parent_of", lambda pid: {4000: 5000, 5000: 1}.get(pid))
    c = share_identity.verify_caller("%1", "/ours/tmux,1,0", "999999", client=None, rows=[row(pane_pid=5000)],
                                     peer_pid=4000)
    assert c.sid == "$1" and c.created == 111 and c.weak is False


def test_verify_caller_peer_pid_outside_pane_rejected(ours, monkeypatch):
    monkeypatch.setattr(share_identity, "parent_of", lambda pid: {4000: 7000, 7000: 1}.get(pid))
    with pytest.raises(share_identity.IdentityError, match="부모 체인"):
        share_identity.verify_caller("%1", "/ours/tmux,1,0", "4000", client=None, rows=[row(pane_pid=5000)],
                                     peer_pid=4000)


def test_verify_caller_foreign_tmux_rejected_even_with_good_peer(ours, monkeypatch):
    monkeypatch.setattr(share_identity, "parent_of", lambda pid: {4000: 5000, 5000: 1}.get(pid))
    with pytest.raises(share_identity.IdentityError, match="소켓이 아닙니다"):
        share_identity.verify_caller("%1", "/tmp/tmux-501/default,1,0", "4000", client=None,
                                     rows=[row(pane_pid=5000)], peer_pid=4000)


def test_verify_caller_claimed_pid_cannot_override_peer_pid(ours, monkeypatch):
    # 신고 pid 는 pane 안 프로세스(4000)인데 실제 peer 는 밖(8000) — peer 가 이긴다
    monkeypatch.setattr(share_identity, "parent_of", lambda pid: {4000: 5000, 8000: 1, 5000: 1}.get(pid))
    with pytest.raises(share_identity.IdentityError):
        share_identity.verify_caller("%1", "/ours/tmux,1,0", "4000", client=None, rows=[row(pane_pid=5000)],
                                     peer_pid=8000)


def test_verify_caller_tcp_path_unchanged_without_peer_pid(ours, monkeypatch):
    monkeypatch.setattr(share_identity, "socket_owner_pid", lambda *a, **k: None)
    with pytest.raises(share_identity.IdentityError, match="확인하지 못했습니다"):
        share_identity.verify_caller("%1", "/ours/tmux,1,0", "4000", client=("127.0.0.1", 5555),
                                     rows=[row(pane_pid=5000)])


# --- 실제 서버 통과 -------------------------------------------------------------------------------


def test_live_request_gets_kernel_peer_and_principal(live, env):
    ls, st = live(resolver=fake_resolver([row()], IN_PANE | {PID: (6000, 0, 0, "py")}))
    s, body = sreq(env, "POST", "/api/agent/report", body=b"{}")
    d = json.loads(body)
    assert s == 200 and d["kind"] == "agent" and d["auth_kind"] == "socket"
    s, body = sreq(env, "GET", "/api/agent/shared")
    assert json.loads(body)["peer"] == {"uid": ME, "pid": PID}


def test_live_client_cannot_spoof_peer_or_marker(live, env):
    ls, st = live()
    s, body = sreq(env, "GET", "/api/agent/shared", headers={"X-Fsh-Pid": "1", "X-Fsh-Pane": "%1", "fsh_peer": "x"})
    assert json.loads(body)["peer"] == {"uid": ME, "pid": PID}


def test_live_route_outside_table_never_reaches_app(live, env):
    ls, st = live()
    s, body = sreq(env, "GET", "/api/tmux/x/secret")
    assert s == 403 and json.loads(body)["error"] == "socket_route_not_allowed"


def test_live_ambiguous_request_is_400(live, env):
    ls, st = live()
    s, body = sreq(env, "POST", "/api/agent/report", body=b"{}", headers={"Authorization": "Bearer t"})
    assert s == 400 and json.loads(body)["error"] == "ambiguous_principal"


def test_live_other_uid_connection_is_closed(live, env, monkeypatch):
    ls, st = live()
    monkeypatch.setattr(sock, "peer_creds", lambda s: (ME + 1, os.getpid()))
    with pytest.raises(OSError):
        sreq(env, "GET", "/api/authz/whoami")                   # 응답 없이 닫힌다


def test_live_whoami_reports_cli_outside_tmux(live, env):
    ls, st = live(resolver=fake_resolver([row()], {PID: (1, 0, 0, "py")}))
    s, body = sreq(env, "GET", "/api/authz/whoami", svc="")
    p = json.loads(body)["principal"]
    assert p["kind"] == "cli" and p["uid"] == ME and p["pid"] == PID


# --- 실제 앱 통과: 토큰 없는 소켓 요청이 미들웨어를 지난다 -----------------------------------------


@pytest.fixture
def real_app(monkeypatch):
    import auth
    import main
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "tok-for-test")
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "")
    return main.app


def test_real_app_socket_request_passes_token_middleware_without_token(live, env, real_app):
    ls, st = live(app=real_app, resolver=fake_resolver([], {PID: (1, 0, 0, "py")}))
    s, body = sreq(env, "POST", "/api/agent/report", body=json.dumps({"state": "idle"}).encode())
    assert s != 401, body                                        # 토큰이 없어도 미들웨어를 지났다
    from fastapi.testclient import TestClient
    assert TestClient(real_app).post("/api/agent/report", json={"state": "idle"}).status_code == 401  # TCP 는 여전히


def test_real_app_human_only_route_not_reachable_even_if_forced(real_app):
    """허용표를 우회해 앱까지 닿았다고 가정해도(게이트가 세운 scope 를 흉내) 사람 전용 라우트는 403 이다 —
    소켓 요청은 request_kind 가 none 이다. 소켓 주체는 human 이 될 수 없다."""
    sent = []

    async def send(m):
        sent.append(m)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
             "path": "/api/agent/share/requests", "raw_path": b"/api/agent/share/requests", "query_string": b"",
             "headers": [(b"host", b"localhost")], "client": None, "server": None, "scheme": "http",
             "root_path": "", "fsh_sock": True, "fsh_peer": {"uid": ME, "pid": PID}, "state": {}}
    asyncio.run(real_app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))
    assert start["status"] == 403 and body["error"] == "human_session_required"
