"""— 클라이언트 쪽: fsh_client / lib/fsh_client.sh 와 이를 쓰는 저장소 안 모든 클라이언트.

규칙: 소켓이 있으면 소켓(Authorization 없음), 없거나 `VT_AUTHZ_SOCKET=0` 이면 예전 TCP + Bearer. 가짜 서버는
stdlib 로 만든 기록용 UDS/TCP 서버(`sock_testkit`)이고, 서버 쪽 게이트와의 왕복은 test_authz_sock.py 가 본다.
여기서 HOME·VT_CONFIG 는 conftest 가 격리한다. 소켓 경로는 AF_UNIX 한도 때문에 /tmp 아래 짧은 디렉터리.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

import pytest
from fastapi import FastAPI, Request

import fsh_client
from sock_testkit import LiveSock, TcpRecorder, UnixRecorder, rmtree, short_dir

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "server"
TOKEN = "tok-secret-9f3a"


@pytest.fixture
def w(monkeypatch):
    d = short_dir()
    path = os.path.join(d, "run", "fsh.sock")
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "1")
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", path)
    monkeypatch.delenv("VT_SERVER", raising=False)
    monkeypatch.delenv("VT_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("VT_TOKEN", raising=False)
    tcp = TcpRecorder()
    made = []

    class W:
        dir, sock_path = d, path

        def unix(self, respond=None, mode=0o600):
            u = UnixRecorder(path, respond, mode)
            made.append(u)
            return u

    W.tcp = tcp
    yield W()
    for u in made:
        u.close()
    tcp.close()
    rmtree(d)


def auth_of(rec):
    return rec["headers"].get("authorization")


# --- fsh_client 자체 --------------------------------------------------------------------------------


def test_socket_path_depends_on_port_and_state_dir(monkeypatch):
    monkeypatch.delenv("VT_AUTHZ_SOCKET_PATH", raising=False)
    monkeypatch.setenv("VT_STATE_DIR", "/tmp/xx-state")
    assert fsh_client.socket_path(7777) == "/tmp/xx-state/run/fsh.sock"
    assert fsh_client.socket_path("7788") == "/tmp/xx-state/run/fsh-7788.sock"
    monkeypatch.delenv("VT_STATE_DIR")
    monkeypatch.setenv("HOME", "/tmp/hh")
    assert fsh_client.socket_path(None).endswith("/tmp/hh/.vt/run/fsh.sock") or "/.vt/run/" in fsh_client.socket_path(None)


def test_server_path_matches_client_path(monkeypatch, tmp_path):
    from authz import sock
    monkeypatch.delenv("VT_AUTHZ_SOCKET_PATH", raising=False)
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path))
    for port in ("7777", "7790"):
        monkeypatch.setenv("VT_PORT", port)
        assert str(sock.socket_path()) == fsh_client.socket_path(port)


def test_usable_path_requires_private_own_socket(w):
    assert fsh_client.usable_path() is None                      # 없음
    w.unix(mode=0o666)
    assert fsh_client.usable_path() is None                      # 그룹/기타가 쓸 수 있는 소켓은 안 쓴다
    os.chmod(w.sock_path, 0o600)
    assert fsh_client.usable_path() == w.sock_path
    os.chmod(os.path.dirname(w.sock_path), 0o777)
    assert fsh_client.usable_path() is None                      # 남이 쓸 수 있는 디렉터리


def test_usable_path_honours_off_switch_and_remote_server(w, monkeypatch):
    w.unix()
    assert fsh_client.usable_path(server="http://127.0.0.1:7777") == w.sock_path
    assert fsh_client.usable_path(server="http://localhost:9") == w.sock_path
    assert fsh_client.usable_path(server="https://dev.example.com") is None      # 원격 서버에 로컬 소켓으로 말하지 않는다
    assert fsh_client.usable_path(server="http://100.64.0.5:7777") is None
    for off in ("0", "off", "false", "no", "OFF"):
        monkeypatch.setenv("VT_AUTHZ_SOCKET", off)
        assert fsh_client.usable_path() is None


def test_regular_file_is_not_a_socket(w):
    os.makedirs(os.path.dirname(w.sock_path), mode=0o700)
    Path(w.sock_path).write_text("x")
    os.chmod(w.sock_path, 0o600)
    assert fsh_client.usable_path() is None


def test_socket_round_trip_json_and_headers(w):
    u = w.unix(lambda rec: (200, {"echo": json.loads(rec["body"] or b"{}"), "svc": rec["headers"].get("x-fsh-svc")}))
    st, data = fsh_client.call_json("POST", "/api/agent/report", body={"a": [1, 2], "k": "한글"}, svc="cli",
                                    server=w.tcp.url, token=TOKEN)
    assert st == 200 and data == {"echo": {"a": [1, 2], "k": "한글"}, "svc": "cli"}
    rec = u.records[0]
    assert rec["method"] == "POST" and rec["path"] == "/api/agent/report"
    assert auth_of(rec) is None and rec["headers"]["content-type"] == "application/json"
    assert w.tcp.records == []                                   # TCP 는 건드리지 않았다


def test_socket_get_and_error_status_passthrough(w):
    w.unix(lambda rec: (404, {"error": "nope"}))
    assert fsh_client.call_json("GET", "/x", server=w.tcp.url, token=TOKEN) == (404, {"error": "nope"})
    assert w.tcp.records == []                                   # 라우트가 낸 404 로 TCP 재시도하지 않는다


def test_raise_for_status_matches_urlopen_shape(w):
    w.unix(lambda rec: (418, {"error": "teapot"}))
    with pytest.raises(urllib.error.HTTPError) as e:
        fsh_client.call("GET", "/x", raise_for_status=True)
    assert e.value.code == 418 and json.loads(e.value.read())["error"] == "teapot"


def test_non_json_body_is_empty_dict(w):
    w.unix(lambda rec: (200, b"plain text"))
    assert fsh_client.call_json("GET", "/x") == (200, {})


def test_timeout_after_connect_propagates_and_does_not_retry_on_tcp(w):
    w.unix(lambda rec: (time.sleep(1.5), (200, {}))[1])
    t = time.time()
    with pytest.raises(OSError):                                 # socket.timeout
        fsh_client.call("POST", "/x", body={}, timeout=0.3, server=w.tcp.url, token=TOKEN)
    assert time.time() - t < 1.2 and w.tcp.records == []        # 처리됐을 수 있는 요청을 다시 보내지 않는다


def test_missing_socket_falls_back_to_tcp_with_bearer(w):
    st, data = fsh_client.call_json("POST", "/api/clipboard/push", body={"text": "t"}, svc="clipboard",
                                    server=w.tcp.url, token=TOKEN)
    assert (st, data["via"]) == (200, "tcp")
    rec = w.tcp.records[0]
    assert auth_of(rec) == f"Bearer {TOKEN}" and rec["headers"]["x-fsh-svc"] == "clipboard"
    assert json.loads(rec["body"]) == {"text": "t"}


def test_stale_socket_file_falls_back_to_tcp(w):
    os.makedirs(os.path.dirname(w.sock_path), mode=0o700)
    s = socket.socket(socket.AF_UNIX)
    s.bind(w.sock_path)
    s.close()                                                    # 리스너 없는 파일
    os.chmod(w.sock_path, 0o600)
    st, data = fsh_client.call_json("GET", "/x", server=w.tcp.url, token=TOKEN)
    assert data["via"] == "tcp" and auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def test_off_switch_uses_tcp_even_with_live_socket(w, monkeypatch):
    u = w.unix()
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "0")
    st, data = fsh_client.call_json("GET", "/x", server=w.tcp.url, token=TOKEN)
    assert data["via"] == "tcp" and u.records == []


@pytest.mark.parametrize("status,err", [(403, "socket_route_not_allowed"), (503, "socket_not_armed"),
                                        (403, "peer_unidentified"), (503, "identity_unavailable")])
def test_socket_layer_rejection_retries_on_tcp(w, status, err):
    u = w.unix(lambda rec: (status, {"error": err}))
    st, data = fsh_client.call_json("GET", "/x", server=w.tcp.url, token=TOKEN)
    assert data["via"] == "tcp" and len(u.records) == 1 and auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def test_route_level_403_is_not_retried(w):
    u = w.unix(lambda rec: (403, {"error": "human_session_required"}))
    st, data = fsh_client.call_json("GET", "/x", server=w.tcp.url, token=TOKEN)
    assert st == 403 and w.tcp.records == []


def test_ambiguous_400_is_not_retried(w):
    w.unix(lambda rec: (400, {"error": "ambiguous_principal"}))
    assert fsh_client.call_json("GET", "/x", server=w.tcp.url, token=TOKEN)[0] == 400
    assert w.tcp.records == []


def test_tcp_down_raises_urlerror_like_urlopen(w):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(urllib.error.URLError):
        fsh_client.call("GET", "/x", server=f"http://127.0.0.1:{port}", token=TOKEN, timeout=1)


def test_no_token_means_no_authorization_header_on_tcp(w):
    fsh_client.call("GET", "/x", server=w.tcp.url, token="")
    assert auth_of(w.tcp.records[0]) is None


def test_round_trip_against_real_uvicorn_gate(w):
    """소켓 서버(진짜 uvicorn + Gate)와 fsh_client 의 왕복."""
    from authz import sock

    app = FastAPI()

    @app.post("/api/agent/report")
    async def rep(request: Request):
        return {"got": await request.json(), "auth": request.headers.get("authorization"),
                "kind": request.state.fsh_principal.kind}

    pid = os.getpid()
    res = sock.Resolver(rows_fn=lambda: [], ps_fn=lambda: {pid: (1, 0, 0, "py")})
    ls = LiveSock(app, res)
    try:
        assert ls.start()["state"] == "listening"
        st, data = fsh_client.call_json("POST", "/api/agent/report", body={"x": 1}, server=w.tcp.url, token=TOKEN)
        assert (st, data) == (200, {"got": {"x": 1}, "auth": None, "kind": "local"})
        assert w.tcp.records == []
    finally:
        ls.stop()


# --- 저장소 안 클라이언트: 소켓 우선 · 토큰 fallback ------------------------------------------------


def _mcp(w, monkeypatch):
    import mcp_farshell
    monkeypatch.setenv("VT_SERVER", w.tcp.url)
    monkeypatch.setenv("VT_AUTH_TOKEN", TOKEN)
    st, data = mcp_farshell._api("POST", "/api/agent/message", {"to": "x"}, headers={"X-Fsh-Pane": "%1", "X-Fsh-Pid": "1"})
    assert st == 200
    return "/api/agent/message"


def _clipboard(w, monkeypatch):
    import clipboard_daemon
    monkeypatch.setattr(clipboard_daemon, "_PORT", str(w.tcp.port))
    monkeypatch.setattr(clipboard_daemon, "_TOKEN", TOKEN)
    clipboard_daemon._push("hello")
    return "/api/clipboard/push"


def _stub_pynput(monkeypatch):
    """voice.config 가 import 시점에 `pynput` 을 요구한다 — 음성 미설치 환경(CI)에서는 빈 대역으로 대신한다.
    여기서 보는 건 소켓 클라이언트 규칙이지 핫키 파싱이 아니다. 대역이 남지 않게 voice.* 도 시험 뒤에 되돌린다."""
    try:
        import pynput  # noqa: F401
        return
    except ImportError:
        pass
    import types
    from unittest import mock
    kb = mock.MagicMock(name="pynput.keyboard")
    pk = types.ModuleType("pynput")
    pk.keyboard = kb
    monkeypatch.setitem(sys.modules, "pynput", pk)
    monkeypatch.setitem(sys.modules, "pynput.keyboard", kb)
    for m in [m for m in sys.modules if m == "voice" or m.startswith("voice.")]:
        monkeypatch.delitem(sys.modules, m)


def _voice_client(w, monkeypatch):
    _stub_pynput(monkeypatch)
    from voice import server_client
    monkeypatch.setenv("VT_SERVER", w.tcp.url)
    monkeypatch.setenv("VT_AUTH_TOKEN", TOKEN)
    monkeypatch.setitem(server_client._VT_ENV, "VT_AUTH_TOKEN", TOKEN)
    assert server_client.request("/voice/cancel", json.dumps({"server": True}).encode()) == {"ok": True, "via": ANY}
    return "/voice/cancel"


class _Any:
    def __eq__(self, o):
        return True


ANY = _Any()


def _tui(w, monkeypatch):
    from tui import helpers
    monkeypatch.setattr(helpers, "VT_PORT", w.tcp.port)
    monkeypatch.setattr(helpers, "VT_TOKEN", TOKEN)
    ok, data = helpers.server_request("GET", "/api/capabilities")
    assert ok and data["ok"]
    return "/api/capabilities"


CLIENTS = {"mcp_farshell": _mcp, "clipboard_daemon": _clipboard, "voice.server_client": _voice_client,
           "tui.helpers": _tui}


@pytest.mark.parametrize("name", sorted(CLIENTS))
def test_client_uses_socket_when_present(w, monkeypatch, name):
    u = w.unix()
    path = CLIENTS[name](w, monkeypatch)
    assert [r["path"] for r in u.records] == [path]
    assert auth_of(u.records[0]) is None                          # 소켓에는 토큰을 싣지 않는다
    assert w.tcp.records == []


@pytest.mark.parametrize("name", sorted(CLIENTS))
def test_client_falls_back_to_token_without_socket(w, monkeypatch, name):
    path = CLIENTS[name](w, monkeypatch)
    assert [r["path"] for r in w.tcp.records] == [path]
    assert auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


@pytest.mark.parametrize("name", sorted(CLIENTS))
def test_client_skips_socket_when_switched_off(w, monkeypatch, name):
    u = w.unix()
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "0")
    CLIENTS[name](w, monkeypatch)
    assert u.records == [] and auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def test_mcp_keeps_identity_headers_on_socket_and_tcp(w, monkeypatch):
    u = w.unix()
    _mcp(w, monkeypatch)
    h = u.records[0]["headers"]
    assert h["x-fsh-pane"] == "%1" and h["x-fsh-pid"] == "1"


def test_mcp_reports_unreachable_server_without_raising(w, monkeypatch):
    import mcp_farshell
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setenv("VT_SERVER", f"http://127.0.0.1:{port}")
    st, data = mcp_farshell._api("GET", "/api/agent/directory")
    assert st == 0 and "연결할 수 없습니다" in data["reason"]


def test_tui_returns_false_on_error_status_and_down_server(w, monkeypatch):
    from tui import helpers
    monkeypatch.setattr(helpers, "VT_PORT", w.tcp.port)
    w.unix(lambda rec: (500, {"error": "x"}))
    assert helpers.server_request("GET", "/api/capabilities") == (False, None)


def test_clipboard_push_swallows_failures(w, monkeypatch, caplog):
    import clipboard_daemon
    monkeypatch.setattr(clipboard_daemon, "_PORT", "1")
    monkeypatch.setattr(clipboard_daemon, "_TOKEN", "")
    clipboard_daemon._push("x")                                  # 서버 없음 — 예외 없이 경고만
    assert "push 실패" in caplog.text                             # 실패는 조용히 사라지지 않고 로그에 남는다


def _env_for_script(w, tmp_path, **extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cfg = tmp_path / "vt.env"
    cfg.write_text("")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("VT_", "TMUX"))}
    env.update(HOME=str(home), VT_CONFIG=str(cfg), VT_LANG="ko", VT_AUTHZ_SOCKET="1",
               VT_AUTHZ_SOCKET_PATH=w.sock_path, PATH=os.environ["PATH"])
    env.update(extra)
    return env


def run_voice_bind(w, tmp_path, **extra):
    env = _env_for_script(w, tmp_path, PYTHONPATH=str(SERVER), **extra)
    Path(env["VT_CONFIG"]).write_text(f"VT_PORT='{w.tcp.port}'\n")
    return subprocess.run([sys.executable, str(SERVER / "voice_bind.py"), "claude", "run1"], env=env,
                          capture_output=True, text=True, timeout=20, cwd=str(SERVER))


BIND_OK = lambda rec: (200, {"target": {"name": "n1", "pane": "%1"}})  # noqa: E731


def test_voice_bind_uses_socket_then_token(w, tmp_path):
    u = w.unix(BIND_OK)
    r = run_voice_bind(w, tmp_path)
    assert r.returncode == 0 and "n1" in r.stdout, r.stderr
    assert u.records[0]["path"] == "/voice/bind" and auth_of(u.records[0]) is None and w.tcp.records == []
    u.close()
    os.unlink(w.sock_path)
    w.tcp.srv.respond = BIND_OK
    r = run_voice_bind(w, tmp_path, VT_AUTH_TOKEN=TOKEN)
    assert r.returncode == 0, r.stderr
    assert auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"
    r = run_voice_bind(w, tmp_path, VT_AUTH_TOKEN=TOKEN, VT_AUTHZ_SOCKET="0")
    assert r.returncode == 0 and len(w.tcp.records) == 2


# --- agent_hook.sh ----------------------------------------------------------------------------------


def run_hook(w, tmp_path, *, stdin='{"session_id":"s"}', hook=None, timeout=20, **extra):
    env = _env_for_script(w, tmp_path, **extra)
    return subprocess.run(["bash", str(hook or SERVER / "agent_hook.sh"), "pre", "claude"], env=env, input=stdin,
                          capture_output=True, text=True, timeout=timeout)


def test_hook_prefers_socket_without_token_and_declares_svc_hook(w, tmp_path):
    u = w.unix()
    r = run_hook(w, tmp_path, VT_SERVER=w.tcp.url, VT_AUTH_TOKEN=TOKEN)
    assert r.returncode == 0, r.stderr
    rec = u.records[0]
    assert rec["path"] == "/api/agent/event" and auth_of(rec) is None and rec["headers"]["x-fsh-svc"] == "hook"
    body = json.loads(rec["body"])
    assert body["event"] == "pre" and body["agent"] == "claude" and "hook_pid" in body   # 옛 페이로드 호환(AGENTS.md 8)
    assert w.tcp.records == []


def test_hook_falls_back_to_token_without_socket(w, tmp_path):
    r = run_hook(w, tmp_path, VT_SERVER=w.tcp.url, VT_AUTH_TOKEN=TOKEN)
    assert r.returncode == 0
    assert auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def test_hook_off_switch(w, tmp_path):
    u = w.unix()
    r = run_hook(w, tmp_path, VT_SERVER=w.tcp.url, VT_AUTH_TOKEN=TOKEN, VT_AUTHZ_SOCKET="0")
    assert r.returncode == 0 and u.records == [] and auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.parametrize("case", ["no-server", "stale-socket", "http-500", "http-403-socket", "garbage", "hang"])
def test_hook_never_fails_or_blocks(w, tmp_path, case):
    extra = {"VT_SERVER": f"http://127.0.0.1:{closed_port()}"}
    if case == "stale-socket":
        os.makedirs(os.path.dirname(w.sock_path), mode=0o700)
        s = socket.socket(socket.AF_UNIX)
        s.bind(w.sock_path)
        s.close()
        os.chmod(w.sock_path, 0o600)
    elif case == "http-500":
        w.unix(lambda rec: (500, {"error": "boom"}))
    elif case == "http-403-socket":
        w.unix(lambda rec: (403, {"error": "socket_route_not_allowed"}))
    elif case == "garbage":
        w.unix(lambda rec: (200, b"\xff\xfe not json"))
    elif case == "hang":
        w.unix(lambda rec: (time.sleep(4), (200, {}))[1])
    t = time.time()
    r = run_hook(w, tmp_path, **extra)
    assert r.returncode == 0, r.stderr
    assert time.time() - t < 9


def test_hook_survives_missing_fsh_client_module(w, tmp_path):
    """설치가 깨져 fsh_client.py 가 없어도 훅은 예전 경로로 내려가고 죽지 않는다."""
    d = tmp_path / "hookcopy"
    d.mkdir()
    (d / "agent_hook.sh").write_text((SERVER / "agent_hook.sh").read_text())
    r = run_hook(w, tmp_path, hook=d / "agent_hook.sh", VT_SERVER=w.tcp.url, VT_AUTH_TOKEN=TOKEN)
    assert r.returncode == 0
    assert w.tcp.records and w.tcp.records[0]["path"] == "/api/agent/event"
    assert auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def test_hook_skips_background_jobs(w, tmp_path):
    u = w.unix()
    r = run_hook(w, tmp_path, VT_SERVER=w.tcp.url, CLAUDE_JOB_DIR="/x")
    assert r.returncode == 0 and u.records == [] and w.tcp.records == []


# --- lib/fsh_client.sh · bin/fsh · tmux_client_notify.sh ----------------------------------------------


def bash(script, env):
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)


def test_fsh_api_socket_then_tcp(w, tmp_path):
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port), VT_AUTH_TOKEN=TOKEN)
    script = f'. {REPO}/lib/fsh_client.sh; FSH_SVC=cli fsh_api POST /api/x \'{{"a":1}}\'; echo " rc=$? via=$FSH_API_VIA code=$FSH_API_CODE"'
    u = w.unix()
    r = bash(script, env)
    assert "rc=0 via=socket code=200" in r.stdout.replace("\n", " "), (r.stdout, r.stderr)
    assert auth_of(u.records[0]) is None and u.records[0]["headers"]["x-fsh-svc"] == "cli"
    assert json.loads(u.records[0]["body"]) == {"a": 1}
    u.close()
    os.unlink(w.sock_path)
    r = bash(script, env)
    assert "rc=0 via=tcp code=200" in r.stdout.replace("\n", " ")
    assert auth_of(w.tcp.records[-1]) == f"Bearer {TOKEN}" and w.tcp.records[-1]["headers"]["x-fsh-svc"] == "cli"


@pytest.mark.parametrize("flavor", ["gnu", "bsd"])
@pytest.mark.parametrize("mode,usable", [(0o600, True), (0o400, True), (0o644, False), (0o660, False)])
def test_sock_usable_stat_flavors(w, tmp_path, flavor, mode, usable):
    """GNU stat 은 `-f '%Lp'` 를 "파일시스템 정보"로 읽어 stdout 에 쓰레기를 남기고 실패한다(Linux CI 에서 소켓이 항상
    거부됐다). 어느 쪽 stat 이든 권한 판정이 같아야 한다 — PATH 앞의 가짜 stat 으로 두 방언을 흉내 낸다."""
    w.unix(mode=mode)
    fake = tmp_path / "fakebin"
    fake.mkdir()
    # 실제 stat 에 기대지 않는다(이 맥의 BSD stat 으로만 도는 시험이 되지 않게). 소켓 파일의 권한은 시험이 아는 값이다.
    octal = f"{mode:o}"
    if flavor == "gnu":
        body = (f'if [ "$1" = -c ]; then printf "%s\\n" "{octal}"; exit 0; fi\n'
                'echo "  File: filesystem info garbage"; echo "    ID: 0 Namelen: 255"; exit 1\n')
    else:
        body = (f'if [ "$1" = -f ]; then printf "%s\\n" "{octal}"; exit 0; fi\n'
                'echo "stat: illegal option -- c" >&2; exit 1\n')
    (fake / "stat").write_text("#!/bin/bash\n" + body)
    (fake / "stat").chmod(0o755)
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port))
    env["PATH"] = f"{fake}:{env.get('PATH', os.environ['PATH'])}"
    r = bash(f". {REPO}/lib/fsh_client.sh; fsh_sock_usable && echo USABLE || echo NO", env)
    assert r.stdout.strip() == ("USABLE" if usable else "NO"), (r.stdout, r.stderr)


def test_fsh_api_socket_rejection_falls_back(w, tmp_path):
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port), VT_AUTH_TOKEN=TOKEN)
    w.unix(lambda rec: (403, {"error": "socket_route_not_allowed"}))
    r = bash(f'. {REPO}/lib/fsh_client.sh; fsh_api GET /api/y; echo "via=$FSH_API_VIA"', env)
    assert "via=tcp" in r.stdout and auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def test_fsh_api_route_error_is_returned_not_retried(w, tmp_path):
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port), VT_AUTH_TOKEN=TOKEN)
    w.unix(lambda rec: (404, {"error": "nf"}))
    r = bash(f'. {REPO}/lib/fsh_client.sh; fsh_api GET /api/y; echo " rc=$? via=$FSH_API_VIA code=$FSH_API_CODE"', env)
    assert "rc=1 via=socket code=404" in r.stdout.replace("\n", " ") and w.tcp.records == []


def test_fsh_api_token_with_quotes_survives(w, tmp_path):
    odd = 'a"b\\c;d'
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port), VT_AUTH_TOKEN=odd)
    bash(f'. {REPO}/lib/fsh_client.sh; fsh_api GET /api/y', env)
    assert auth_of(w.tcp.records[0]) == f"Bearer {odd}"


def test_fsh_api_ignores_unusable_socket_permissions(w, tmp_path):
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port), VT_AUTH_TOKEN=TOKEN)
    u = w.unix(mode=0o666)
    r = bash(f'. {REPO}/lib/fsh_client.sh; fsh_api GET /api/y; echo "via=$FSH_API_VIA"', env)
    assert "via=tcp" in r.stdout and u.records == []


def _extract_fn(name):
    text = (REPO / "bin" / "fsh").read_text()
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
    assert m, name
    return m.group(0)


def test_bin_fsh_authed_curl_socket_first_then_token(w, tmp_path):
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port), VT_AUTH_TOKEN=TOKEN)
    fn = _extract_fn("_authed_curl")
    call = f'_authed_curl -sf -X POST "http://localhost:{w.tcp.port}/api/ports/_proxy/sync" -o /dev/null; echo "rc=$?"'
    script = f'. {REPO}/lib/fsh_client.sh\n{fn}\n{call}'
    u = w.unix()
    r = bash(script, env)
    assert "rc=0" in r.stdout and [x["path"] for x in u.records] == ["/api/ports/_proxy/sync"]
    assert auth_of(u.records[0]) is None and w.tcp.records == []
    u.close()
    os.unlink(w.sock_path)
    r = bash(script, env)
    assert "rc=0" in r.stdout and auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def test_bin_fsh_authed_curl_socket_reject_falls_back(w, tmp_path):
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port), VT_AUTH_TOKEN=TOKEN)
    w.unix(lambda rec: (403, {"error": "socket_route_not_allowed"}))
    script = f'. {REPO}/lib/fsh_client.sh\n{_extract_fn("_authed_curl")}\n_authed_curl -sf -X POST "http://localhost:{w.tcp.port}/api/ports/_proxy/sync" -o /dev/null; echo "rc=$?"'
    r = bash(script, env)
    assert "rc=0" in r.stdout and auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def _pane_script():
    text = (REPO / "bin" / "fsh").read_text()
    i = text.index("cmd_pane() {")
    a = text.index("<<'PYEOF'\n", i) + len("<<'PYEOF'\n")
    return text[a:text.index("\nPYEOF", a) + 1]


def run_pane_report(w, tmp_path, **extra):
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port), TMUX_PANE="%3", **extra)
    r = subprocess.run([sys.executable, "-", "working", "claude", str(SERVER)], input=_pane_script(), env=env,
                       capture_output=True, text=True, timeout=20)
    return r.stdout.strip()


def test_bin_fsh_pane_report_socket_then_token(w, tmp_path):
    respond = lambda rec: (200, {"ok": True, "resolved_by": "pane"})  # noqa: E731
    u = w.unix(respond)
    assert run_pane_report(w, tmp_path, VT_AUTH_TOKEN=TOKEN) == "pane"
    assert u.records[0]["path"] == "/api/agent/report" and auth_of(u.records[0]) is None
    assert json.loads(u.records[0]["body"])["pane"] == "%3" and w.tcp.records == []
    u.close()
    os.unlink(w.sock_path)
    w.tcp.srv.respond = respond
    assert run_pane_report(w, tmp_path, VT_AUTH_TOKEN=TOKEN) == "pane"
    assert auth_of(w.tcp.records[0]) == f"Bearer {TOKEN}"


def test_bin_fsh_pane_report_failure_prints_err(w, tmp_path):
    assert run_pane_report(w, tmp_path, VT_SERVER=f"http://127.0.0.1:{closed_port()}").startswith("ERR")


STUB_CURL = """#!/bin/bash
# argv 를 파일에 덤프하고, -K 설정 파일이 있으면 내용도 따로 덤프한다. 응답은 200.
out="$FSH_STUB_DIR/argv.$$"
printf '%s\\n' "$@" > "$out"
prev=""
for a in "$@"; do
  if [ "$prev" = "-K" ]; then cat "$a" > "$FSH_STUB_DIR/cfg.$$" 2>/dev/null; fi
  prev="$a"
done
printf '\\n200'
exit 0
"""


def run_notify(w, tmp_path, *, stub=True, **extra):
    stubdir = tmp_path / "stub"
    stubdir.mkdir(exist_ok=True)
    if stub:
        c = stubdir / "curl"
        c.write_text(STUB_CURL)
        c.chmod(0o755)
    env = _env_for_script(w, tmp_path, VT_PORT=str(w.tcp.port), FSH_STUB_DIR=str(stubdir),
                          PATH=(f"{stubdir}:" if stub else "") + os.environ["PATH"], **extra)
    r = subprocess.run(["bash", str(SERVER / "hooks" / "tmux_client_notify.sh"), "attached", "/dev/ttys009", "dev"],
                       env=env, capture_output=True, text=True, timeout=30)
    return r, stubdir


def test_tmux_client_notify_token_never_in_argv(w, tmp_path):
    """H4: 소켓이 없어 토큰 fallback 을 타도 토큰은 curl argv(= ps)에 오르지 않는다. 설정 파일(-K)로만 간다."""
    r, stubdir = run_notify(w, tmp_path, VT_AUTH_TOKEN=TOKEN)
    assert r.returncode == 0, r.stderr
    argvs = list(stubdir.glob("argv.*"))
    assert len(argvs) == 1
    argv = argvs[0].read_text()
    assert TOKEN not in argv and "token=" not in argv and "?" not in argv.split("/api/notify")[0]
    assert "/api/notify/client-event" in argv and "-K" in argv.split("\n")
    cfg = next(stubdir.glob("cfg.*")).read_text()
    assert f"Authorization: Bearer {TOKEN}" in cfg


def test_tmux_client_notify_uses_socket_when_present(w, tmp_path):
    w.unix()
    r, stubdir = run_notify(w, tmp_path, VT_AUTH_TOKEN=TOKEN)
    argv = next(stubdir.glob("argv.*")).read_text().split("\n")
    assert r.returncode == 0 and "--unix-socket" in argv and w.sock_path in argv
    assert TOKEN not in "\n".join(argv) and "-K" not in argv and not list(stubdir.glob("cfg.*"))
    assert "X-Fsh-Svc: tmux-notify" in argv


def test_tmux_client_notify_real_curl_socket_and_tcp(w, tmp_path):
    u = w.unix()
    r, _ = run_notify(w, tmp_path, stub=False, VT_AUTH_TOKEN=TOKEN)
    assert r.returncode == 0
    rec = u.records[0]
    assert rec["path"] == "/api/notify/client-event" and auth_of(rec) is None
    assert json.loads(rec["body"])["event"] == "attached"
    u.close()
    os.unlink(w.sock_path)
    r, _ = run_notify(w, tmp_path, stub=False, VT_AUTH_TOKEN=TOKEN)
    rec = w.tcp.records[0]
    assert rec["path"] == "/api/notify/client-event" and auth_of(rec) == f"Bearer {TOKEN}"   # 쿼리 문자열 토큰 없음


def test_tmux_client_notify_never_fails_when_server_down(w, tmp_path):
    env = _env_for_script(w, tmp_path, VT_PORT=str(closed_port()), VT_AUTH_TOKEN=TOKEN)
    r = subprocess.run(["bash", str(SERVER / "hooks" / "tmux_client_notify.sh"), "detached", "", "dev"], env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
