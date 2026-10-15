"""`fsh authz`(CLI)와 MCP 도구(`whoami`·`request_access`·`list_my_grants`, 도구 목록 거르기).

CLI 는 같은 프로세스에서 `authz.cli.main` 을 실제 unix socket(실제 앱 `main.app`)에 대고 부른다(bin/fsh 가 `python -m authz cli` 로 부르는 것과 같은 함수).
MCP 는 `_api` 를 가짜로 바꿔 도구 함수와 도구 목록 규칙을 본다."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout

import pytest

import fsh_client
import share_identity
from authz import cli, inbox, store
from authz import principals as P

from test_authz_sock import PID, env, fake_resolver, live, row, sreq  # noqa: F401

IN_PANE = {PID: (6000, 0, 0, "py"), 6000: (5000, 0, 0, "zsh"), 5000: (1, 0, 0, "zsh")}
OUT = {PID: (7000, 0, 0, "py"), 7000: (1, 0, 0, "zsh")}
TOKEN = "-".join(["tok", "inbox", "clients"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    import auth
    import main
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")           # 이 시험은 API 자체를 본다 — 집행 플래그(특히 legacy 의 「비밀번호 없는 서버」 차단)와 무관하게
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", TOKEN)
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "")
    rows = [row(), row(pane="%2", pane_pid=5100, sid="$2", created=222, name="target")]
    monkeypatch.setattr(share_identity, "list_pane_rows", lambda: rows)
    return main.app


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


# --- CLI -----------------------------------------------------------------------------------------------------


@pytest.fixture
def cli_world(live, env, world):
    live(app=world, resolver=fake_resolver([row()], OUT))
    return env


def test_cli_whoami_and_lists_are_read_only_views(cli_world):
    rc, out, _ = run_cli("whoami")
    assert rc == 0 and "local" in out and "cli" in out
    rc, out, _ = run_cli("whoami", "--json")
    assert json.loads(out)["principal"]["name"] == "cli"
    rc, out, _ = run_cli("grants")
    assert rc == 0 and "pg-" not in out          # 가짜 세계에 등록된 호스트가 없다
    rc, out, _ = run_cli("requests")
    assert rc == 0 and "승인 대기 중인 요청이 없습니다" in out


def test_cli_requests_lists_pending_and_says_approval_is_elsewhere(cli_world, world):
    p = P.agent("$1:111", name="work")
    inbox.create_request(p, "session:local/$2:222", ["session.read"], None, "need it", 600, alive={"$1:111", "$2:222"})
    rc, out, _ = run_cli("requests")
    assert rc == 0 and "gq-" in out and "generic" in out and "work" in out and "보기" in out
    assert "웹 또는 폰의 승인함" in out and "need it" not in out        # 사유 원문은 CLI 목록에 싣지 않는다
    rc, out, _ = run_cli("requests", "--json")
    assert json.loads(out)["approvals"][0]["kind"] == "generic"


def test_cli_revoke_and_panic_never_call_the_server(cli_world, monkeypatch):
    calls = []
    monkeypatch.setattr(fsh_client, "socket_request", lambda *a, **k: calls.append(a) or (200, b"{}"))
    rc, out, _ = run_cli("revoke", "gg-1")
    assert rc == 3 and "gg-1" in out and "웹 또는 폰" in out
    rc, out, _ = run_cli("panic")
    assert rc == 3 and "재인증한 사람" in out
    assert calls == []
    assert run_cli("revoke")[0] == 2


def test_cli_audit_since_follow_and_chain_state(cli_world, monkeypatch):
    from authz import audit
    audit.append({"principal": "human:dev:1", "action": "authz.decide", "resource": "session:local/a", "decision": "allow", "result": "ok"})
    rc, out, _ = run_cli("audit")
    assert rc == 0 and "감사 체인: 정상" in out and "authz.decide" in out and "chain" in out
    rc, out, _ = run_cli("audit", "--since", "1h", "--limit", "5", "--json")
    assert rc == 0 and json.loads(out.splitlines()[0])["action"] == "authz.decide"
    assert run_cli("audit", "--since", "yesterday")[0] == 2
    # --follow: 한 번 돌고 Ctrl-C
    import time as _t
    n = {"i": 0}
    real_sleep = _t.sleep

    def sleep(sec):
        n["i"] += 1
        audit.append({"principal": "human:dev:1", "action": "authz.revoke", "resource": "session:local/b", "decision": "allow", "result": "ok"})
        if n["i"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli.time, "sleep", sleep)
    rc, out, _ = run_cli("audit", "--follow")
    assert rc == 0 and out.count("authz.revoke") >= 1 and out.count("authz.decide") == 1


def test_cli_inside_a_pane_is_refused_like_any_agent(live, env, world):
    live(app=world, resolver=fake_resolver([row()], IN_PANE))
    rc, out, err = run_cli("grants")
    assert rc == 1 and "로그인한 사람 또는 터미널의 fsh 명령" in err
    assert run_cli("requests")[0] == 1


def test_cli_without_a_socket_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_SOCKET", "1")
    monkeypatch.setenv("VT_AUTHZ_SOCKET_PATH", str(tmp_path / "nope.sock"))
    rc, out, err = run_cli("whoami")
    assert rc == 1 and "unix socket" in err
    assert cli.parse_since("10m", now=1000.0) == 400.0 and cli.parse_since("", now=1) == 0.0 and cli.parse_since("77") == 77.0


def test_cli_usage_and_unknown(capsys):
    assert run_cli()[0] == 1 and run_cli("bogus")[0] == 1
    rc, out, _ = run_cli("--help")
    assert rc == 0 and "fsh authz" in out


# --- MCP -----------------------------------------------------------------------------------------------------


def test_mcp_new_tools_exist_and_have_handlers():
    import mcp_farshell as m
    names = {t["name"] for t in m.TOOLS}
    assert {"whoami", "request_access", "list_my_grants"} <= names and {"whoami", "request_access", "list_my_grants"} <= set(m.HANDLERS)
    ra = next(t for t in m.TOOLS if t["name"] == "request_access")
    assert ra["inputSchema"]["required"] == ["resource", "scopes"] and set(ra["inputSchema"]["properties"]) == {"resource", "scopes", "reason", "ttl"}
    assert "승인" not in "".join(m.HANDLERS)


def test_mcp_request_access_pending_text_matches_the_other_tools(monkeypatch):
    import mcp_farshell as m
    seen = {}

    def fake(method, path, body=None, headers=None):
        seen.update(method=method, path=path, body=body, headers=headers)
        return 202, {"state": "pending", "requestId": "gq-abc", "reason": "사용자 승인 대기 중 — 승인되면 같은 호출을 다시 하세요"}

    monkeypatch.setattr(m, "_api", fake)
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "request_access", "arguments": {
        "resource": "session:local/$2:222", "scopes": ["session.read"], "reason": "why", "ttl": 300}}})["result"]
    assert r["isError"] is False and "승인되면 같은 호출을 다시 하세요" in r["content"][0]["text"] and "gq-abc" in r["content"][0]["text"]
    assert seen["path"] == "/api/authz/requests" and seen["body"] == {"resource": "session:local/$2:222", "scopes": ["session.read"], "reason": "why", "ttl": 300}
    assert "X-Fsh-Pid" in seen["headers"]
    monkeypatch.setattr(m, "_api", lambda *a, **k: (400, {"error": "scope_not_requestable", "reason": "이 scope 는 요청할 수 없습니다"}))
    r = m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "request_access", "arguments": {
        "resource": "authz:*", "scopes": ["authz.admin"]}}})["result"]
    assert r["isError"] is True and "요청할 수 없습니다" in r["content"][0]["text"]


def test_mcp_whoami_and_list_my_grants_format(monkeypatch):
    import mcp_farshell as m
    answers = {
        "/api/authz/whoami": (200, {"principal": {"kind": "agent", "id": "agent:local/$1:1", "via": "socket"}, "scopes": ["session.read", "msg.send"],
                                    "caps": {"session.read": {}, "session.input": {"modes": ["line"]}}, "presets": [{"id": "view", "label": "보기"}]}),
        "/api/authz/my-grants": (200, {"grants": [{"id": "gg-1", "resource": "session:local/x", "scopes": ["session.read"], "maxUses": 1, "uses": 0}]}),
        "/api/authz/requests": (200, {"requests": [{"requestId": "gq-2", "state": "pending", "resource": "fs:local/a", "scopes": ["fs.read"]}]}),
    }
    monkeypatch.setattr(m, "_api", lambda method, path, body=None, headers=None: answers[path])
    t, err = m.tool_whoami({})
    assert not err and "agent:local/$1:1" in t and "session.read, msg.send" in t and "modes=line" in t and "보기" in t
    t, err = m.tool_list_my_grants({})
    assert not err and "gg-1" in t and "1회 남음" in t and "gq-2" in t and "pending" in t


def test_tools_for_caller_filters_by_whoami_scopes(monkeypatch):
    import mcp_farshell as m
    state = {"scopes": ["deleg.spawn", "msg.send", "authz.request", "session.read"], "role": {"is_child": False, "can_spawn": True}}

    def fake(method, path, body=None, headers=None):
        if path == "/api/authz/whoami":
            return (200, {"scopes": state["scopes"]}) if state["scopes"] is not None else (403, {"error": "socket_only"})
        return 200, state["role"]

    monkeypatch.setattr(m, "_api", fake)
    names = lambda: {t["name"] for t in m.tools_for_caller()}      # noqa: E731
    full = names()
    assert {"spawn_session", "send_message", "request_access", "whoami", "list_my_grants", "read_session"} <= full
    state["scopes"] = ["session.read"]                              # 만들기·메시지·요청 권한이 없다
    n = names()
    assert not ({"spawn_session", "send_message", "request_access"} & n) and {"whoami", "list_my_grants", "read_session", "list_agents"} <= n
    state["scopes"] = None                                          # TCP(whoami 불가) — 예전처럼 거르지 않는다
    assert names() == full
    state["scopes"] = ["deleg.spawn", "msg.send", "authz.request"]
    state["role"] = {"is_child": True, "can_spawn": False}          # 깊이 한도 규칙은 그대로
    n = names()
    assert "spawn_session" not in n and "report_back" in n
