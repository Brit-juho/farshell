"""소켓의 whoami 요약, 에이전트의 요청/내 권한, 에이전트·CLI 의 읽기 경계 — 실제 앱(`main.app`)을 실제 unix socket 에 올리고
가짜 프로세스 표로 호출자를 정한다(tmux·실제 `~/.vt` 에는 닿지 않는다)."""

from __future__ import annotations

import json
import os

import pytest

import share_identity
from authz import store

# 같은 소켓 시험 도구(실제 UDS 서버·가짜 프로세스 표)를 쓴다.
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


def call(env, method, path, body=None, svc=""):
    s, raw = sreq(env, method, path, body=json.dumps(body).encode() if body is not None else None, svc=svc)
    try:
        return s, json.loads(raw.decode() or "{}")
    except ValueError:
        return s, {}


# --- 소켓 whoami ------------------------------------------------------------------------------------


def test_whoami_on_the_socket_summarises_scopes_conds_and_presets_for_an_agent(live, env, world):
    live(app=world, resolver=fake_resolver([row()], IN_PANE))
    s, b = call(env, "GET", "/api/authz/whoami")
    assert s == 200 and b["principal"]["kind"] == "agent" and b["principal"]["via"] == "socket"
    assert {"session.view", "session.read", "session.input", "authz.request", "agent.report", "deleg.spawn", "msg.send"} <= set(b["scopes"])
    assert b["caps"]["session.input"] == {"modes": ["line"], "via": ["direct"]}
    assert "authz.admin" not in b["scopes"] and "browser.js" not in b["scopes"]
    assert [p["id"] for p in b["presets"]] == [] or b["presets"][0]["id"] == "view"        # 자기 세션뿐이라 프리셋 전체는 못 채운다
    assert b["authz"] == "ok" and any(g["default"] for g in b["grants"])


def test_whoami_for_cli_has_no_agent_powers(live, env, world):
    live(app=world, resolver=fake_resolver([row()], OUT))
    s, b = call(env, "GET", "/api/authz/whoami")
    assert s == 200 and b["principal"]["kind"] == "local" and b["principal"]["name"] == "cli"
    assert "authz.request" not in b["scopes"] and "session.input" not in b["scopes"] and "authz.admin" not in b["scopes"]


def test_whoami_includes_approved_grants(live, env, world):
    live(app=world, resolver=fake_resolver([row()], IN_PANE))
    snap_before = call(env, "GET", "/api/authz/whoami")[1]
    assert "browser" not in snap_before["scopes"]
    with store.txn() as docs:
        docs["grants"]["items"].append({"id": "gg-t", "origin": "authz:grant:t", "kind": "generic", "subject": "agent:local/$1:111",
                                        "resource": "browser:local/*", "scopes": ["browser", "session.view"], "state": "active",
                                        "expires": None, "conds": {}, "approved_by": "human:dev:1", "approved_via": "manual", "schema": 2})
    b = call(env, "GET", "/api/authz/whoami")[1]
    assert "browser" in b["scopes"] and any(g["id"] == "gg-t" and not g["default"] for g in b["grants"])


# --- 에이전트: 요청 · 내 권한 ---------------------------------------------------------------------------------


def test_agent_requests_access_over_the_socket_and_sees_only_its_own(live, env, world):
    live(app=world, resolver=fake_resolver([row()], IN_PANE))
    s, b = call(env, "POST", "/api/authz/requests", {"resource": "session:local/$2:222", "scopes": ["session.read"], "reason": "log", "ttl": 300})
    assert s == 202 and b["state"] == "pending" and b["requestId"].startswith("gq-") and b["kind"] == "generic"
    s2, mine = call(env, "GET", "/api/authz/requests")
    assert [r["requestId"] for r in mine["requests"]] == [b["requestId"]]
    assert call(env, "GET", "/api/authz/my-grants")[1] == {"grants": []}
    # 승인되기 전에는 아무 권한도 생기지 않는다
    assert "browser" not in call(env, "GET", "/api/authz/whoami")[1]["scopes"]


def test_agent_cannot_read_the_inbox_or_decide_over_the_socket(live, env, world):
    live(app=world, resolver=fake_resolver([row()], IN_PANE))
    for path in ("/api/authz/approvals", "/api/authz/grants", "/api/authz/audit"):
        s, b = call(env, "GET", path)
        assert s == 403 and b["error"] == "human_or_cli_required", path
    for m, path in (("POST", "/api/authz/approvals/x/decision"), ("DELETE", "/api/authz/grants/x"), ("POST", "/api/authz/panic")):
        s, b = call(env, m, path, {"decision": "allow"})
        assert s == 403 and b["error"] == "socket_route_not_allowed", (m, path)     # 소켓 허용표에 없다


def test_cli_cannot_create_requests_and_a_hook_cannot_either(live, env, world):
    live(app=world, resolver=fake_resolver([row()], OUT))
    s, b = call(env, "POST", "/api/authz/requests", {"resource": "session:local/$2:222", "scopes": ["session.read"]})
    assert s == 403 and b["error"] == "request_not_allowed"
    s, b = call(env, "GET", "/api/authz/my-grants")
    assert s == 200 and b == {"grants": []}
