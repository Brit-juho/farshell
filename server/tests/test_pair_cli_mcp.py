"""`fsh pair …` 명령과 MCP pair 도구의 입출력 — 서버 호출은 가짜로 막고, 보내는 요청과 사람이 읽는 출력을 본다."""

from __future__ import annotations

import os

import pytest

import mcp_farshell as m
from authz import pair_cli

PAIR = {"id": "pr-1", "state": "active", "generation": 2, "role": "driver", "isDriver": True,
        "members": [{"seat": "initiator", "kind": "agent", "name": "alpha", "role": "driver", "you": True},
                    {"seat": "joiner", "kind": "agent", "name": "bravo", "role": "navigator", "you": False}],
        "bundle": {"sessions": [{"name": "bravo"}], "dirs": ["/w/proj"], "leases": False, "elevated": {"requested": [], "granted": []}},
        "approval": {"state": "allowed"}, "handoff": None}
CODE = {"ok": True, "code": "ABCD-EFGH", "phrase": "amber river", "expires": 1893456000, "pair": "pr-1"}


@pytest.fixture
def calls(monkeypatch):
    seen, state = [], {"reply": {}}

    def fake(method, path, body=None, timeout=10.0):
        seen.append((method, path, body))
        r = state["reply"]
        return r(method, path) if callable(r) else r
    monkeypatch.setattr(pair_cli, "_call", fake)
    return seen, state


def test_start_creates_then_issues_a_code(calls, capsys):
    seen, st = calls
    st["reply"] = lambda m_, p: (201, {"ok": True, "pair": PAIR}) if p == "/api/pairs" else (200, CODE)
    assert pair_cli.main(["start", "--session", "bravo", "--dir", "~/proj", "--lease", "--elevated", "raw_input,relay", "--role", "navigator"]) == 0
    assert seen[0][:2] == ("POST", "/api/pairs")
    body = seen[0][2]
    assert body["sessions"] == ["bravo"] and body["dirs"] == [os.path.expanduser("~/proj")] and body["leases"] is True
    assert body["elevated"] == ["raw_input", "relay"] and body["role"] == "navigator"
    assert seen[1][:2] == ("POST", "/api/pairs/pr-1/code")
    out = capsys.readouterr().out
    assert "ABCD-EFGH" in out and "amber river" in out and "5분" in out


def test_join_status_handoff_accept_end(calls, capsys):
    seen, st = calls
    st["reply"] = (200, {"ok": True, "pair": {**PAIR, "state": "joined", "phrase": "amber river", "role": "navigator"}})
    assert pair_cli.main(["join", "abcd-efgh"]) == 0
    assert seen[-1] == ("POST", "/api/pairs/join", {"code": "abcd-efgh"})
    out = capsys.readouterr().out
    assert "pr-1" in out and "amber river" in out and "navigator" in out
    st["reply"] = (200, {"ok": True, "pair": PAIR})
    assert pair_cli.main(["status", "pr-1"]) == 0 and seen[-1][:2] == ("GET", "/api/pairs/pr-1/status")
    assert "운전 중" in capsys.readouterr().out
    assert pair_cli.main(["handoff", "pr-1"]) == 0 and seen[-1][:2] == ("POST", "/api/pairs/pr-1/handoff")
    assert pair_cli.main(["accept", "pr-1"]) == 0 and seen[-1][:2] == ("POST", "/api/pairs/pr-1/handoff/accept")
    assert pair_cli.main(["end", "pr-1"]) == 0 and seen[-1][:2] == ("DELETE", "/api/pairs/pr-1")
    st["reply"] = (200, {"pairs": [PAIR]})
    assert pair_cli.main(["ls"]) == 0 and "pr-1" in capsys.readouterr().out


def test_failures_are_explained(calls, capsys):
    seen, st = calls
    st["reply"] = (409, {"error": "pair_disabled", "reason": "x"})
    assert pair_cli.main(["status"]) == 1
    assert "VT_AUTHZ_ENFORCE" in capsys.readouterr().err
    st["reply"] = (403, {"error": "forbidden", "reason": "권한 없음", "need": {"resource": "pair:*", "scopes": ["pair.manage"]}})
    assert pair_cli.main(["start", "--session", "x"]) == 1
    assert "pair.manage" in capsys.readouterr().err
    assert pair_cli.main(["bogus"]) == 2


def test_revoke_verify_waits_for_cleanup(calls, capsys, monkeypatch):
    seen, st = calls
    monkeypatch.setattr(pair_cli.time, "sleep", lambda s: None)
    state = {"n": 0}

    def reply(method, path):
        if method == "DELETE":
            return 200, {"ok": True, "pair": PAIR}
        state["n"] += 1
        c = {"done": state["n"] >= 3, "grants": 0 if state["n"] >= 3 else 2, "leases": 0, "saga": state["n"] >= 3}
        return 200, {"pairs": [{**PAIR, "state": "ended", "cleanup": c}]}
    st["reply"] = reply
    assert pair_cli.main(["revoke", "pr-1", "--verify"]) == 0
    assert "확인 완료" in capsys.readouterr().out and state["n"] == 3
    st["reply"] = lambda m_, p: (200, {"ok": True, "pair": PAIR}) if m_ == "DELETE" else (200, {"pairs": [{**PAIR, "state": "ended", "cleanup": {"done": False, "grants": 1, "leases": 0}}]})
    assert pair_cli.main(["revoke", "pr-1", "--verify"]) == 1
    assert "정리가 끝나지 않았습니다" in capsys.readouterr().err


def test_revoke_verify_from_a_non_member_terminal_still_verifies(calls, capsys):
    seen, st = calls

    def reply(method, path):
        if method == "DELETE":
            return 404, {"error": "not_found"}
        return 200, {"pairs": [{**PAIR, "state": "revoked", "cleanup": {"done": True, "grants": 0, "leases": 0}}]}
    st["reply"] = reply
    assert pair_cli.main(["revoke", "pr-1", "--verify"]) == 0
    assert pair_cli.main(["revoke", "pr-1"]) == 1                      # --verify 없이는 404 가 그대로 실패


def test_cli_without_a_server_reports(monkeypatch, capsys):
    def boom(*a, **k):
        raise pair_cli.Unreachable("x")
    monkeypatch.setattr(pair_cli, "_call", boom)
    for argv in (["start", "--session", "x"], ["join", "AAAA-AAAA"], ["status"], ["handoff", "p"], ["end", "p"]):
        assert pair_cli.main(argv) == 1
    assert "unix socket" in capsys.readouterr().err


def test_doctor_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "pair")
    assert pair_cli.cmd_doctor([]) == 0
    assert capsys.readouterr().out.strip() == "1|0|0"
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    pair_cli.cmd_doctor([])
    assert capsys.readouterr().out.strip() == "0|0|0"


# --- MCP ----------------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    seen, st = [], {"reply": (200, {})}
    monkeypatch.setattr(m, "_api", lambda method, path, body=None, headers=None: (seen.append((method, path, body)), st["reply"](method, path) if callable(st["reply"]) else st["reply"])[1])
    return seen, st


def test_mcp_pair_tools(api):
    seen, st = api
    st["reply"] = lambda mm, p: (201, {"pair": PAIR}) if p == "/api/pairs" else (200, CODE)
    text, err = m.tool_pair_create({"sessions": ["bravo"], "dirs": ["/w/proj"], "elevated": ["raw_input"]})
    assert not err and "ABCD-EFGH" in text and "amber river" in text and seen[0][2] == {"sessions": ["bravo"], "dirs": ["/w/proj"], "elevated": ["raw_input"]}
    st["reply"] = (200, {"pair": {**PAIR, "phrase": "amber river", "state": "joined"}})
    text, err = m.tool_pair_join({"code": "ABCD-EFGH"})
    assert not err and seen[-1] == ("POST", "/api/pairs/join", {"code": "ABCD-EFGH"}) and "amber river" in text and "pair_status" in text
    text, err = m.tool_pair_status({"id": "pr-1"})
    assert not err and "pr-1" in text and "운전 중" in text and seen[-1][:2] == ("GET", "/api/pairs/pr-1/status")
    st["reply"] = (200, {"pairs": []})
    assert m.tool_pair_status({}) == ("pair 가 없습니다", False)
    st["reply"] = (200, {"pair": PAIR})
    assert not m.tool_pair_handoff({"id": "pr-1"})[1] and seen[-1][:2] == ("POST", "/api/pairs/pr-1/handoff")
    assert not m.tool_pair_accept({"id": "pr-1"})[1] and seen[-1][:2] == ("POST", "/api/pairs/pr-1/handoff/accept")
    st["reply"] = (409, {"error": "pair_disabled"})
    text, err = m.tool_pair_join({"code": "x"})
    assert err and "꺼져" in text
    st["reply"] = (403, {"error": "forbidden", "need": {"resource": "pair:*", "scopes": ["pair.manage"]}})
    text, err = m.tool_pair_create({"sessions": ["x"]})
    assert err and "pair.manage" in text and "request_access" in text


def test_pair_tools_are_filtered_by_whoami(monkeypatch):
    state = {"who": {"scopes": ["session.read", "pair.manage"], "features": {"pair": True}}}

    def fake(method, path, body=None, headers=None):
        if path == "/api/authz/whoami":
            return (200, state["who"]) if state["who"] is not None else (403, {})
        return 200, {"is_child": False, "can_spawn": False}
    monkeypatch.setattr(m, "_api", fake)
    names = lambda: {t["name"] for t in m.tools_for_caller()}      # noqa: E731
    pair = set(m.PAIR_TOOL_NAMES)
    assert pair <= names()                                          # 켜져 있고 pair.manage 가 있으면 전부
    state["who"] = {"scopes": ["session.read"], "features": {"pair": True}}
    n = names()
    assert "pair_create" not in n and pair - {"pair_create"} <= n   # 만들기는 pair.manage 가 있어야 한다
    state["who"] = {"scopes": ["session.read", "pair.manage"]}      # 서버가 pair 를 켜지 않았다
    assert not (pair & names())
    state["who"] = None                                             # whoami 불가(TCP) — pair 도구는 숨긴다
    assert not (pair & names())


def test_whoami_announces_pair_only_when_enabled(tmp_path, monkeypatch):
    from authz import inbox
    from authz import principals as P
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "")
    assert "features" not in inbox.whoami(P.agent("$1:1"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "pair")
    assert inbox.whoami(P.agent("$1:1"))["features"] == {"pair": True}
