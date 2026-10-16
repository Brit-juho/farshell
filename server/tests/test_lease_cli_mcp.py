"""`fsh lease …` 명령과 MCP lease 도구의 입출력 — 서버 호출은 가짜로 막고, 보내는 요청과 사람이 읽는 출력을 본다."""

from __future__ import annotations

import pytest

import mcp_farshell as m
from authz import lease_cli

LEASE = {"id": "ls-1", "holder": "alpha", "glob": "/w/proj", "mode": "write", "exclusive": True, "remaining": 1700,
         "reason": "r", "derivedFrom": None}


@pytest.fixture
def calls(monkeypatch):
    seen = []
    state = {"reply": (200, {})}

    def fake(method, path, body=None, timeout=8.0):
        seen.append((method, path, body))
        return state["reply"]
    monkeypatch.setattr(lease_cli, "_call", fake)
    return seen, state


def test_acquire_sends_mode_exclusive_and_ttl(calls, capsys):
    seen, st = calls
    st["reply"] = (201, {"ok": True, "lease": LEASE})
    assert lease_cli.main(["acquire", "/w/proj/*", "--read", "--exclusive", "--ttl", "2h", "--reason", "x"]) == 0
    assert seen[-1] == ("POST", "/api/leases", {"glob": "/w/proj/*", "mode": "read", "exclusive": True, "reason": "x", "ttl": 7200})
    assert "ls-1" in capsys.readouterr().out
    lease_cli.main(["acquire", "/w/proj"])
    assert seen[-1][2]["mode"] == "write" and "ttl" not in seen[-1][2]                 # 기본 쓰기·서버 기본 ttl
    assert lease_cli.main(["acquire", "/w/proj", "--ttl", "9x"]) == 2


def test_acquire_conflict_and_missing_permission_are_explained(calls, capsys):
    seen, st = calls
    st["reply"] = (409, {"error": "lease_conflict", "reason": "다른 세션이 이 경로를 쓰는 중입니다", "holder": "alpha", "expires": 1893456000})
    assert lease_cli.main(["acquire", "/w/proj"]) == 1
    err = capsys.readouterr().err
    assert "alpha" in err and "쓰는 중" in err
    st["reply"] = (403, {"error": "forbidden", "reason": "권한 없음", "need": {"resource": "fs:local/w/proj/*", "scopes": ["fs.write"]}})
    assert lease_cli.main(["acquire", "/w/proj"]) == 1
    assert "fs.write" in capsys.readouterr().err


def test_ls_renew_release(calls, capsys):
    seen, st = calls
    st["reply"] = (200, {"leases": [LEASE], "enforced": False})
    assert lease_cli.main(["ls"]) == 0
    out = capsys.readouterr().out
    assert "ls-1" in out and "alpha" in out and "/w/proj" in out and "yes" in out and "VT_AUTHZ_ENFORCE" in out     # 권고 모드 안내
    st["reply"] = (200, {"leases": [], "enforced": True})
    lease_cli.main(["ls"])
    assert "없습니다" in capsys.readouterr().out
    st["reply"] = (200, {"ok": True, "lease": {**LEASE, "capped": True}})
    assert lease_cli.main(["renew", "ls-1", "--ttl", "30m"]) == 0
    assert seen[-1] == ("POST", "/api/leases/ls-1/renew", {"ttl": 1800})
    assert "최대" in capsys.readouterr().out
    st["reply"] = (200, {"ok": True, "released": 1})
    assert lease_cli.main(["release", "ls-1"]) == 0 and seen[-1][:2] == ("DELETE", "/api/leases/ls-1")


def test_cli_without_a_server_reports_instead_of_crashing(monkeypatch, capsys):
    def boom(*a, **k):
        raise lease_cli.Unreachable("x")
    monkeypatch.setattr(lease_cli, "_call", boom)
    for argv in (["ls"], ["acquire", "/w"], ["renew", "x"], ["release", "x"]):
        assert lease_cli.main(argv) == 1
    assert "unix socket" in capsys.readouterr().err
    assert lease_cli.main(["nope"]) == 2


def test_doctor_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "lease")
    assert lease_cli.cmd_doctor([], str(tmp_path)) == 0
    n, guard, skipped, enf, sagas = capsys.readouterr().out.strip().split("|")
    assert (n, guard, skipped, enf, sagas) == ("0", "no-repo", "0", "1", "0")


# --- MCP ----------------------------------------------------------------------------------


@pytest.fixture
def api(monkeypatch):
    seen, st = [], {"reply": (200, {})}
    monkeypatch.setattr(m, "_api", lambda method, path, body=None, headers=None: (seen.append((method, path, body)), st["reply"])[1])
    return seen, st


def test_mcp_lease_tools(api):
    seen, st = api
    st["reply"] = (201, {"lease": LEASE})
    text, err = m.tool_lease_acquire({"path": "/w/proj", "exclusive": True, "ttl": 600})
    assert not err and "ls-1" in text and seen[-1] == ("POST", "/api/leases", {"glob": "/w/proj", "mode": "write", "exclusive": True, "ttl": 600})
    st["reply"] = (409, {"error": "lease_conflict", "holder": "alpha", "expires": 1893456000})
    text, err = m.tool_lease_acquire({"path": "/w/proj"})
    assert err and "alpha" in text and "충돌" in text
    st["reply"] = (403, {"error": "forbidden", "need": {"resource": "fs:local/w/proj/*", "scopes": ["fs.write"]}})
    text, err = m.tool_lease_acquire({"path": "/w/proj"})
    assert err and "request_access" in text and "fs.write" in text
    st["reply"] = (200, {"lease": {**LEASE, "capped": True}})
    text, err = m.tool_lease_renew({"id": "ls-1", "ttl": 60})
    assert not err and seen[-1] == ("POST", "/api/leases/ls-1/renew", {"ttl": 60}) and "최대" in text
    st["reply"] = (200, {"ok": True})
    assert m.tool_lease_release({"id": "ls-1"})[1] is False and seen[-1][:2] == ("DELETE", "/api/leases/ls-1")
    assert seen and m.tool_lease_release({"id": "../x"}) and seen[-1][1] == "/api/leases/..%2Fx"      # id 로 경로를 바꿀 수 없다
    st["reply"] = (200, {"leases": [LEASE, {**LEASE, "id": "ls-2", "derivedFrom": "ls-1"}], "enforced": False})
    text, err = m.tool_lease_list({})
    assert not err and "ls-1" in text and "[상속]" in text and "권고 모드" in text
    st["reply"] = (200, {"leases": [], "enforced": True})
    assert m.tool_lease_list({})[0] == "lease 가 없습니다"


def test_mcp_registry_has_the_four_tools_with_scope_filters():
    names = {t["name"] for t in m.TOOLS}
    assert {"lease_acquire", "lease_renew", "lease_release", "lease_list"} <= names
    assert set(m.HANDLERS) >= names - {t["name"] for t in m.TOOLS if t["name"] not in m.HANDLERS}
    for n in ("lease_acquire", "lease_renew", "lease_release", "lease_list"):
        assert set(m.TOOL_SCOPES[n]) == {"fs.write", "fs.read"} and n in m.HANDLERS
    req = next(t for t in m.TOOLS if t["name"] == "lease_acquire")["inputSchema"]
    assert req["required"] == ["path"] and req["properties"]["ttl"]["maximum"] == 28800
