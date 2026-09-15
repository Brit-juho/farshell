"""97번 1단계 3/n — MCP 조회·토글 API.

라우트까지 왕복하는 형태에서 확인해야 하는 것들:
1. **값이 응답에 실리지 않는다** — 모듈 단위로 막았어도 라우트가 다른 경로로
   내보내면 소용없다(96번에서 같은 방식으로 credits 누출을 막았다).
2. **토글은 승격이 필요하다** — MCP를 켜고 끄는 건 에이전트가 부를 수 있는
   도구를 바꾸는 일이고, 이 UI는 터널 너머에 노출된다.
3. **세 가지 결과가 그대로 전달된다** — 특히 `unknown`을 성공으로 뭉개지 않는다.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import auth as _auth

    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password("s3cret-pw"))
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".gemini" / "config").mkdir(parents=True)
    monkeypatch.setenv("VT_MCP_HOME", str(home))
    monkeypatch.delenv("VT_CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        "worktree.list_worktrees",
        lambda force=False: [{"id": "wt1", "path": str(repo),
                              "repoName": "repo", "branch": "main"}],
    )

    import main
    with TestClient(main.app) as c:
        c.vt_home = home
        c.vt_repo = repo
        yield c


def _login(client, password="s3cret-pw"):
    assert client.post("/api/auth", json={"token": password}).status_code == 200


def _elevate(client, password="s3cret-pw"):
    _login(client, password)
    assert client.post("/api/auth/elevate", json={"password": password}).status_code == 200


def _write_claude(client, data):
    (client.vt_home / ".claude.json").write_text(json.dumps(data))


def _read_claude(client):
    return json.loads((client.vt_home / ".claude.json").read_text())


# ------------------------------------------------------------------ 조회

def test_get_lists_servers_and_facts(client):
    _login(client)
    _write_claude(client, {"mcpServers": {"github": {"command": "x"}}})
    r = client.get("/api/mcp")
    assert r.status_code == 200
    body = r.json()
    assert [s["name"] for s in body["servers"]] == ["github"]
    assert set(body["facts"]) == {"claude", "codex", "agy"}


def test_get_never_leaks_values(client):
    """`~/.claude.json` 하나에 다른 모든 서버의 키가 같이 들어 있다."""
    _login(client)
    _write_claude(client, {"mcpServers": {"n": {
        "command": "x", "env": {"TOKEN": "sk-should-never-appear"}}}})
    r = client.get("/api/mcp")
    assert "sk-should-never-appear" not in r.text
    assert r.json()["servers"][0]["env"][0]["key"] == "TOKEN"


def test_get_with_worktree_resolves_label(client):
    _login(client)
    _write_claude(client, {"mcpServers": {"g": {"command": "x"}}})
    body = client.get("/api/mcp?worktree=wt1").json()
    assert body["worktree"]["label"] == "repo/main"


def test_get_groups_by_name(client):
    _login(client)
    _write_claude(client, {"mcpServers": {"b": {"command": "x"}, "a": {"command": "y"}}})
    groups = client.get("/api/mcp").json()["groups"]
    assert [g["name"] for g in groups] == ["a", "b"]


def test_get_reports_parse_errors_without_failing(client):
    _login(client)
    (client.vt_home / ".claude.json").write_text("{broken")
    body = client.get("/api/mcp").json()
    assert body["servers"] == []
    assert body["errors"][0]["reason"] == "JSON 파싱 실패"


# ------------------------------------------------------------------ 토글

def test_toggle_requires_elevation(client):
    _login(client)  # 로그인만 하고 승격은 안 한다
    r = client.post("/api/mcp/toggle",
                    json={"tool": "claude", "name": "g", "enabled": False, "worktree": "wt1"})
    assert r.status_code == 401
    assert r.json()["error"] == "elevation_required"


def test_toggle_writes_after_elevation(client):
    _elevate(client)
    _write_claude(client, {"mcpServers": {"g": {"command": "x"}}, "projects": {}})
    r = client.post("/api/mcp/toggle",
                    json={"tool": "claude", "name": "g", "enabled": False, "worktree": "wt1"})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    entry = _read_claude(client)["projects"][str(client.vt_repo)]
    assert entry["disabledMcpServers"] == ["g"]


def test_toggle_is_idempotent_over_http(client):
    _elevate(client)
    _write_claude(client, {"projects": {}})
    body = {"tool": "claude", "name": "g", "enabled": False, "worktree": "wt1"}
    assert client.post("/api/mcp/toggle", json=body).json()["changed"] is True
    assert client.post("/api/mcp/toggle", json=body).json()["changed"] is False


def test_refusal_is_409_and_file_untouched(client):
    _elevate(client)
    (client.vt_home / ".claude.json").write_text("{broken")
    r = client.post("/api/mcp/toggle",
                    json={"tool": "claude", "name": "g", "enabled": False, "worktree": "wt1"})
    assert r.status_code == 409
    assert r.json()["status"] == "failed"
    assert (client.vt_home / ".claude.json").read_text() == "{broken"


def test_unknown_result_is_200_not_masked_as_success(client, monkeypatch):
    """썼는데 확인하지 못한 경우 — 성공으로 뭉개면 사용자가 껐다고 믿는다."""
    _elevate(client)
    monkeypatch.setattr(
        "mcp_write.set_enabled",
        lambda *a, **k: {"status": "unknown", "reason": "확인 실패", "changed": None},
    )
    r = client.post("/api/mcp/toggle",
                    json={"tool": "claude", "name": "g", "enabled": False, "worktree": "wt1"})
    assert r.status_code == 200
    assert r.json()["status"] == "unknown"


def test_invalid_tool_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/toggle", json={"tool": "nope", "name": "g", "enabled": False})
    assert r.status_code == 400 and r.json()["error"] == "invalid_tool"


def test_missing_name_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/toggle", json={"tool": "claude", "enabled": False})
    assert r.status_code == 400 and r.json()["error"] == "missing_name"


def test_non_boolean_enabled_is_rejected(client):
    """문자열 "false"를 참으로 읽어 서버를 켜버리는 사고를 막는다."""
    _elevate(client)
    r = client.post("/api/mcp/toggle",
                    json={"tool": "claude", "name": "g", "enabled": "false", "worktree": "wt1"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_enabled"


def test_invalid_scope_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/toggle",
                    json={"tool": "codex", "name": "g", "enabled": False, "scope": "sideways"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_scope"


def test_agy_toggle_round_trip(client):
    _elevate(client)
    cfg = client.vt_home / ".gemini" / "config" / "mcp_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"n": {"command": "x"}}}))
    r = client.post("/api/mcp/toggle", json={"tool": "agy", "name": "n", "enabled": False})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert json.loads(cfg.read_text())["mcpServers"]["n"]["disabled"] is True
    # 조회에도 그대로 반영된다 — 저장된 상태가 아니라 파일을 다시 읽기 때문.
    servers = client.get("/api/mcp").json()["servers"]
    assert [s["enabled"] for s in servers if s["tool"] == "agy"] == [False]
