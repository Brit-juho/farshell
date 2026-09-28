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
import sys

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
    monkeypatch.setattr(
        "codex_cli.run_json",
        lambda args, **kwargs: (([], None) if args[:2] == ["mcp", "list"]
                                else ({"installed": []}, None)),
    )

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
    assert set(body["facts"]) == {"claude", "codex", "agy", "opencode"}   # 4단계에서 opencode 추가


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


# --------------------------------------------------- 토글 배치 (P3-1, ADR-37)

def test_toggle_batch_requires_elevation(client):
    _login(client)
    r = client.post("/api/mcp/toggle-batch", json={
        "tool": "claude", "worktree": "wt1",
        "changes": [{"name": "g", "enabled": False}],
    })
    assert r.status_code == 401


def test_toggle_batch_writes_after_elevation(client):
    _elevate(client)
    _write_claude(client, {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}},
                          "projects": {}})
    r = client.post("/api/mcp/toggle-batch", json={
        "tool": "claude", "worktree": "wt1",
        "changes": [{"name": "a", "enabled": False}, {"name": "b", "enabled": False}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert {res["name"] for res in body["results"]} == {"a", "b"}
    entry = _read_claude(client)["projects"][str(client.vt_repo)]
    assert sorted(entry["disabledMcpServers"]) == ["a", "b"]


def test_toggle_batch_reports_partial_ok_false(client):
    _elevate(client)
    _write_claude(client, {"projects": {}})
    r = client.post("/api/mcp/toggle-batch", json={
        "tool": "claude", "worktree": None,  # 워크트리 없음 → claude는 이 항목만 실패
        "changes": [{"name": "a", "enabled": False}],
    })
    body = r.json()
    assert body["ok"] is False
    assert body["results"][0]["status"] == "failed"


def test_toggle_batch_invalid_tool_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/toggle-batch",
                    json={"tool": "nope", "changes": [{"name": "a", "enabled": False}]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_tool"


def test_toggle_batch_missing_changes_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/toggle-batch", json={"tool": "claude"})
    assert r.status_code == 400 and r.json()["error"] == "missing_changes"


def test_toggle_batch_empty_changes_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/toggle-batch", json={"tool": "claude", "changes": []})
    assert r.status_code == 400 and r.json()["error"] == "missing_changes"


def test_toggle_batch_over_the_cap_is_400(client):
    _elevate(client)
    changes = [{"name": f"s{i}", "enabled": False} for i in range(65)]
    r = client.post("/api/mcp/toggle-batch", json={"tool": "claude", "changes": changes})
    assert r.status_code == 400 and r.json()["error"] == "batch_too_large"


def test_toggle_batch_non_boolean_enabled_is_rejected(client):
    _elevate(client)
    r = client.post("/api/mcp/toggle-batch", json={
        "tool": "claude", "changes": [{"name": "a", "enabled": "false"}],
    })
    assert r.status_code == 400 and r.json()["error"] == "invalid_enabled"


def test_toggle_batch_missing_change_name_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/toggle-batch", json={
        "tool": "claude", "changes": [{"enabled": False}],
    })
    assert r.status_code == 400 and r.json()["error"] == "missing_name"


# ------------------------------------------------------------ 그룹 태그 (2단계)

def test_tags_do_not_require_elevation(client):
    """태그는 FarShell 화면 안의 라벨이고 CLI 설정 파일을 전혀 안 건드린다.
    여기에 승격을 걸면 칩 하나 붙일 때마다 비밀번호를 묻게 된다."""
    _login(client)
    r = client.post("/api/mcp/tags", json={"name": "g", "tags": ["검증용"]})
    assert r.status_code == 200 and r.json()["ok"]


def test_tags_never_touch_the_cli_file(client):
    _login(client)
    _write_claude(client, {"mcpServers": {"g": {"command": "x"}}})
    before = (client.vt_home / ".claude.json").read_text()
    client.post("/api/mcp/tags", json={"name": "g", "tags": ["검증용"]})
    assert (client.vt_home / ".claude.json").read_text() == before


def test_get_carries_tags_separately_from_servers(client):
    """`servers[].tags`로 섞지 않는다 — "CLI 파일에서 읽은 사실"과 "우리가
    붙인 라벨"이 한 덩어리로 보이면 안 된다."""
    _login(client)
    _write_claude(client, {"mcpServers": {"g": {"command": "x"}}})
    client.post("/api/mcp/tags", json={"name": "g", "tags": ["검증용"]})
    body = client.get("/api/mcp").json()
    assert body["tags"] == {"g": ["검증용"]}
    assert body["allTags"] == ["검증용"]
    assert "tags" not in body["servers"][0]


def test_group_requires_elevation(client):
    """그룹 켜기는 토글을 여러 번 하는 것과 정확히 같은 일이다."""
    _login(client)
    client.post("/api/mcp/tags", json={"name": "g", "tags": ["검증용"]})
    r = client.post("/api/mcp/group", json={"tag": "검증용", "enabled": False, "worktree": "wt1"})
    assert r.status_code == 401
    assert r.json()["error"] == "elevation_required"


def test_group_off_turns_every_member_off(client):
    _elevate(client)
    _write_claude(client, {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"},
                                          "keep": {"command": "z"}}, "projects": {}})
    client.post("/api/mcp/tags", json={"name": "a", "tags": ["검증용"]})
    client.post("/api/mcp/tags", json={"name": "b", "tags": ["검증용"]})
    r = client.post("/api/mcp/group",
                    json={"tag": "검증용", "enabled": False, "worktree": "wt1"})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    disabled = _read_claude(client)["projects"][str(client.vt_repo)]["disabledMcpServers"]
    assert sorted(disabled) == ["a", "b"]   # 태그 없는 keep은 그대로 켜져 있다


def test_group_target_tool_never_changes_other_tool(client, monkeypatch):
    _elevate(client)
    import mcp_scan, mcp_write
    from mcp_catalog import set_tags
    set_tags("shared", ["검증용"])
    entries = [
        {"name": "shared", "tool": tool, "scope": "global", "enabled": True}
        for tool in ("claude", "codex")
    ]
    monkeypatch.setattr(mcp_scan, "scan", lambda _: {"servers": entries})
    changed = []
    monkeypatch.setattr(mcp_write, "set_enabled", lambda tool, name, enabled, **kwargs:
                        changed.append(tool) or {"status": "ok", "changed": True})
    r = client.post("/api/mcp/group", json={"tag": "검증용", "enabled": False,
                                             "tool": "codex", "worktree": "wt1"})
    assert r.status_code == 200
    assert changed == ["codex"]
    assert [x["tool"] for x in r.json()["results"]] == ["codex"]


def test_group_on_from_a_mixed_state_turns_all_on(client):
    """섞인 상태(일부만 켜짐)에서 눌러도 결과가 결정적이어야 한다 — §1-3."""
    _elevate(client)
    _write_claude(client, {
        "mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}},
        "projects": {str(client.vt_repo): {"disabledMcpServers": ["a"]}},
    })
    client.post("/api/mcp/tags", json={"name": "a", "tags": ["검증용"]})
    client.post("/api/mcp/tags", json={"name": "b", "tags": ["검증용"]})
    r = client.post("/api/mcp/group",
                    json={"tag": "검증용", "enabled": True, "worktree": "wt1"})
    assert r.json()["status"] == "ok"
    assert _read_claude(client)["projects"][str(client.vt_repo)]["disabledMcpServers"] == []


def test_group_is_idempotent(client):
    """멱등성은 별도 장치가 아니라 "목표 상태 지정"이라는 정의에서 따라 나온다."""
    _elevate(client)
    _write_claude(client, {"mcpServers": {"a": {"command": "x"}}, "projects": {}})
    client.post("/api/mcp/tags", json={"name": "a", "tags": ["검증용"]})
    body = {"tag": "검증용", "enabled": False, "worktree": "wt1"}
    first = client.post("/api/mcp/group", json=body).json()
    second = client.post("/api/mcp/group", json=body).json()
    assert first["changed"] == 1
    assert second["changed"] == 0 and second["status"] == "ok"
    # 두 번째는 파일을 아예 열지 않는다 — 남의 설정 파일을 건드리는 횟수를 줄인다.
    assert all(r["skipped"] for r in second["results"])


def test_group_reports_which_member_failed(client, monkeypatch):
    """부분 실패를 성공으로 뭉개면 사용자가 "다 껐다"고 믿는다."""
    _elevate(client)
    _write_claude(client, {"mcpServers": {"a": {"command": "x"}, "b": {"command": "y"}},
                           "projects": {}})
    client.post("/api/mcp/tags", json={"name": "a", "tags": ["검증용"]})
    client.post("/api/mcp/tags", json={"name": "b", "tags": ["검증용"]})

    real = __import__("mcp_write").set_enabled

    def flaky(tool, name, enabled, **kw):
        if name == "b":
            return {"status": "failed", "reason": "일부러 실패", "changed": False}
        return real(tool, name, enabled, **kw)

    monkeypatch.setattr("mcp_write.set_enabled", flaky)
    body = client.post("/api/mcp/group",
                       json={"tag": "검증용", "enabled": False, "worktree": "wt1"}).json()
    assert body["status"] == "partial"
    failed = [r for r in body["results"] if r["status"] == "failed"]
    assert [r["name"] for r in failed] == ["b"]


def test_group_with_unknown_tag_is_409(client):
    _elevate(client)
    r = client.post("/api/mcp/group",
                    json={"tag": "없는태그", "enabled": False, "worktree": "wt1"})
    assert r.status_code == 409 and r.json()["status"] == "failed"


def test_group_non_boolean_enabled_is_rejected(client):
    _elevate(client)
    client.post("/api/mcp/tags", json={"name": "a", "tags": ["검증용"]})
    r = client.post("/api/mcp/group", json={"tag": "검증용", "enabled": "false"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_enabled"


def test_tag_rename_and_delete_over_http(client):
    _login(client)
    client.post("/api/mcp/tags", json={"name": "a", "tags": ["검증용"]})
    client.post("/api/mcp/tags", json={"name": "b", "tags": ["검증용"]})
    assert client.post("/api/mcp/tags/rename",
                       json={"from": "검증용", "to": "테스트용"}).json()["renamed"] == 2
    assert client.get("/api/mcp").json()["allTags"] == ["테스트용"]
    assert client.post("/api/mcp/tags/delete", json={"tag": "테스트용"}).json()["removed"] == 2
    assert client.get("/api/mcp").json()["allTags"] == []


# ──────────────────────────────────────────────── 자격증명·가져오기 (3단계)

def test_cred_write_requires_elevation(client):
    """시크릿을 받는 경로다."""
    _login(client)
    r = client.post("/api/mcp/creds",
                    json={"server": "notion", "key": "token", "secret": "sk-x"})
    assert r.status_code == 401 and r.json()["error"] == "elevation_required"


def test_cred_list_never_carries_the_secret(client):
    _elevate(client)
    client.post("/api/mcp/creds",
                json={"server": "notion", "key": "token", "secret": "sk-super-secret"})
    r = client.get("/api/mcp/creds")
    assert r.status_code == 200
    assert "sk-super-secret" not in r.text, "원문이 응답에 실렸다"
    assert r.json()["creds"][0]["masked"] == "sk-s…cret"


def test_cred_write_response_never_echoes_the_secret(client):
    _elevate(client)
    r = client.post("/api/mcp/creds",
                    json={"server": "notion", "key": "token", "secret": "sk-super-secret"})
    assert "sk-super-secret" not in r.text


def test_blank_env_gets_the_generated_default(client):
    _elevate(client)
    r = client.post("/api/mcp/creds",
                    json={"server": "notion", "key": "token", "secret": "x", "env": ""})
    assert r.json()["cred"]["env"] == "FSH_MCP_NOTION_TOKEN"


def test_bad_env_name_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/creds",
                    json={"server": "n", "key": "k", "secret": "x", "env": "bad name"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_env_name"


def test_cred_delete_requires_elevation_and_works(client):
    _elevate(client)
    cid = client.post("/api/mcp/creds",
                      json={"server": "n", "key": "k", "secret": "x"}).json()["cred"]["id"]
    assert client.post("/api/mcp/creds/delete", json={"id": cid}).status_code == 200
    assert client.get("/api/mcp/creds").json()["creds"] == []
    assert client.post("/api/mcp/creds/delete", json={"id": cid}).status_code == 404


def test_deploy_requires_elevation(client):
    _login(client)
    r = client.post("/api/mcp/deploy",
                    json={"name": "n", "defn": {"command": "x"}, "tool": "claude"})
    assert r.status_code == 401 and r.json()["error"] == "elevation_required"


def test_deploy_writes_a_reference_and_records_it(client):
    _elevate(client)
    _write_claude(client, {})
    r = client.post("/api/mcp/deploy", json={
        "name": "notion", "defn": {"command": "npx", "env": {"TOKEN": "sk-real-secret"}},
        "tool": "claude", "scope": "global",
        "env_map": {"env": {"TOKEN": "FSH_MCP_NOTION_TOKEN"}},
    })
    assert r.status_code == 200 and r.json()["status"] == "ok"
    text = (client.vt_home / ".claude.json").read_text()
    assert "sk-real-secret" not in text
    assert "${FSH_MCP_NOTION_TOKEN}" in text
    # 회수용 기록이 남는다(§2-5).
    assert client.get("/api/mcp/creds").json()["refs"][0]["server"] == "notion"


def test_deploy_refuses_to_leave_a_value_behind(client):
    """`.mcp.json`은 저장소에 커밋되는 파일이다 — 409로 막고 파일은 안 건드린다."""
    _elevate(client)
    _write_claude(client, {})
    before = (client.vt_home / ".claude.json").read_text()
    r = client.post("/api/mcp/deploy", json={
        "name": "notion", "defn": {"command": "npx", "env": {"A": "sk-a", "B": "sk-b"}},
        "tool": "claude", "scope": "global", "env_map": {"env": {"A": "V_A"}},
    })
    assert r.status_code == 409
    assert "env.B" in r.json()["reason"]
    assert (client.vt_home / ".claude.json").read_text() == before


def test_deploy_to_codex_preserves_existing_toml(client):
    _elevate(client)
    path = client.vt_home / ".codex" / "config.toml"
    path.write_text("# keep this comment\n[model_providers.demo]\nname = 'demo'\n")
    r = client.post("/api/mcp/deploy",
                    json={"name": "n", "defn": {"command": "x"}, "tool": "codex"})
    assert r.status_code == 200
    assert "# keep this comment" in path.read_text()
    assert '[mcp_servers."n"]' in path.read_text()
    assert client.get("/api/mcp").json()["servers"][-1]["name"] == "n"


def test_skill_scan_and_codex_toggle(client):
    _elevate(client)
    root = client.vt_home / ".agents" / "skills" / "review"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("# review")
    rows = client.get("/api/mcp/skills").json()["skills"]
    skill = next(x for x in rows if x["name"] == "review")
    assert skill["tool"] == "codex" and skill["enabled"] is True
    r = client.post("/api/mcp/skills/toggle", json={"id": skill["id"], "enabled": False})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert client.get("/api/mcp/skills").json()["skills"][0]["enabled"] is False


def test_skill_import_to_project_never_overwrites_existing(client):
    _elevate(client)
    source = client.vt_home / ".agents" / "skills" / "review"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# original")
    skill = next(x for x in client.get("/api/mcp/skills?worktree=wt1").json()["skills"]
                 if x["name"] == "review")
    body = {"id": skill["id"], "tool": "claude", "scope": "local", "worktree": "wt1"}
    first = client.post("/api/mcp/skills/import", json=body)
    assert first.status_code == 200
    target = client.vt_repo / ".claude" / "skills" / "review" / "SKILL.md"
    assert target.read_text() == "# original"
    target.write_text("# changed")
    second = client.post("/api/mcp/skills/import", json=body)
    assert second.status_code == 409
    assert target.read_text() == "# changed"


def test_mcp_library_save_preview_apply_and_conflict(client):
    _elevate(client)
    _write_claude(client, {"mcpServers": {"docs": {"command": "npx", "args": ["docs"],
                                               "env": {"TOKEN": "${DOCS_TOKEN}"}}}})
    save = client.post("/api/mcp/library/save", json={"tool": "claude", "scope": "global", "name": "docs"})
    assert save.status_code == 200
    item_id = save.json()["id"]
    listing = client.get("/api/mcp/library").json()
    assert listing["servers"][0]["name"] == "docs"
    assert "npx" not in str(listing)
    preview = client.post("/api/mcp/library/preview",
                          json={"id": item_id, "tool": "codex", "scope": "global"})
    assert preview.status_code == 200
    config = client.vt_home / ".codex" / "config.toml"
    config.write_text("# preserve\n")
    stale = client.post("/api/mcp/library/apply", json={"id": item_id, "tool": "codex",
                                                        "scope": "global", "token": preview.json()["token"]})
    assert stale.status_code == 409
    fresh = client.post("/api/mcp/library/preview",
                        json={"id": item_id, "tool": "codex", "scope": "global"}).json()
    applied = client.post("/api/mcp/library/apply", json={"id": item_id, "tool": "codex",
                                                          "scope": "global", "token": fresh["token"]})
    assert applied.status_code == 200
    assert "DOCS_TOKEN" in config.read_text()
    assert "# preserve" in config.read_text()
    again = client.post("/api/mcp/library/preview",
                        json={"id": item_id, "tool": "codex", "scope": "global"})
    assert again.status_code == 409 and again.json()["conflict"] is True


def test_mcp_library_never_returns_definition_or_literal_secret(client):
    _elevate(client)
    _write_claude(client, {"mcpServers": {
        "safe": {"command": "npx", "env": {"TOKEN": "${SAFE_TOKEN}"}},
        "unsafe": {"command": "npx", "env": {"TOKEN": "sk-private-value"}},
    }})
    rejected = client.post("/api/mcp/library/save", json={"tool": "claude", "scope": "global", "name": "unsafe"})
    assert rejected.status_code == 409
    saved = client.post("/api/mcp/library/save", json={"tool": "claude", "scope": "global", "name": "safe"})
    assert saved.status_code == 200
    listing = client.get("/api/mcp/library")
    preview = client.post("/api/mcp/library/preview", json={"id": saved.json()["id"], "tool": "codex", "scope": "global"})
    assert preview.status_code == 200
    for response in (listing, preview):
        assert "sk-private-value" not in response.text
        assert "npx" not in response.text
        assert "${SAFE_TOKEN}" not in response.text


def test_deploy_validates_its_input(client):
    _elevate(client)
    for body, err in (
        ({"defn": {"command": "x"}, "tool": "claude"}, "missing_name"),
        ({"name": "n", "tool": "claude"}, "invalid_defn"),
        ({"name": "n", "defn": {"command": "x"}, "tool": "nope"}, "invalid_tool"),
        ({"name": "n", "defn": {"command": "x"}, "tool": "claude", "scope": "weird"}, "invalid_scope"),
        ({"name": "n", "defn": {"command": "x"}, "tool": "claude", "env_map": []}, "invalid_env_map"),
    ):
        r = client.post("/api/mcp/deploy", json=body)
        assert r.status_code == 400 and r.json()["error"] == err, body


# ──────────────────────────────────────────────────────── 플러그인 (4단계)

def _cc_install(client, pid, scope="user", project=None):
    d = client.vt_home / ".claude" / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "installed_plugins.json"
    data = json.loads(f.read_text()) if f.exists() else {"version": 2, "plugins": {}}
    row = {"scope": scope, "installPath": str(client.vt_home / "nowhere"), "version": "2.0.0"}
    if project:
        row["projectPath"] = project
    data["plugins"].setdefault(pid, []).append(row)
    f.write_text(json.dumps(data))


def _cc_settings(client, data, where=None):
    path = (where or client.vt_home) / ".claude" / ("settings.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_plugins_list_distinguishes_absent_from_disabled(client):
    """"목록에 없음"과 "false"는 다르다 — 뭉개면 "끈 적 없는데 꺼져 보인다"."""
    _login(client)
    for pid in ("on@m", "off@m", "never@m"):
        _cc_install(client, pid)
    _cc_settings(client, {"enabledPlugins": {"on@m": True, "off@m": False}})
    body = client.get("/api/mcp/plugins").json()
    by = {p["name"]: p for p in body["plugins"] if p["tool"] == "claude"}
    assert by["on@m"]["enabled"] is True and by["on@m"]["explicit"] is True
    assert by["off@m"]["enabled"] is False
    assert by["never@m"]["enabled"] is None and by["never@m"]["explicit"] is False
    assert by["on@m"]["marketplace"] == "m" and by["on@m"]["plugin"] == "on"
    assert by["on@m"]["level"] == "user" and by["on@m"]["version"] == "2.0.0"


def test_plugins_list_shows_three_levels_with_project_read_only(client):
    _login(client)
    _cc_install(client, "p@m")
    _cc_settings(client, {"enabledPlugins": {"p@m": True}})
    _cc_settings(client, {"enabledPlugins": {"p@m": False}}, where=client.vt_repo)
    rows = [p for p in client.get("/api/mcp/plugins?worktree=wt1").json()["plugins"]
            if p["name"] == "p@m"]
    by = {p["level"]: p for p in rows}
    assert set(by) == {"user", "project", "local"}
    assert by["project"]["writable"] is False and by["project"]["enabled"] is False
    assert by["local"]["writable"] is True and by["local"]["enabled"] is None
    # local 미지정 → project가 user를 이긴다
    assert all(p["effective"] is False for p in rows)


def test_plugins_list_reports_enabled_but_uninstalled(client):
    _login(client)
    _cc_settings(client, {"enabledPlugins": {"ghost@m": True}})
    by = {p["name"]: p for p in client.get("/api/mcp/plugins").json()["plugins"]}
    assert by["ghost@m"]["installed"] is False and by["ghost@m"]["writable"] is False


def test_plugins_list_includes_codex_skills_and_mcp_bundle(client, monkeypatch):
    _login(client)
    plugin = client.vt_home / "plugin-a"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / "skills" / "review").mkdir(parents=True)
    (plugin / "skills" / "review" / "SKILL.md").write_text("# review")
    (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "skills": "./skills/", "mcpServers": "./.mcp.json",
    }))
    (client.vt_home / ".codex" / "config.toml").write_text(
        '[plugins."bundle@market"]\nenabled = false\n')
    monkeypatch.setattr("codex_cli.run_json", lambda args, **kwargs: ({"installed": [{
        "pluginId": "bundle@market", "name": "bundle", "marketplaceName": "market",
        "version": "1.2.3", "installed": True, "enabled": False,
        "source": {"source": "local", "path": str(plugin)}, "authPolicy": "ON_USE",
    }]}, None))

    by = {p["name"]: p for p in client.get("/api/mcp/plugins").json()["plugins"]}
    codex = by["bundle@market"]
    assert codex["tool"] == "codex" and codex["scope"] == "global"
    assert codex["skills"] == ["review"] and codex["skill_count"] == 1
    assert codex["bundles_mcp"] is True and codex["version"] == "1.2.3"


def test_plugin_toggle_requires_elevation(client):
    _login(client)
    r = client.post("/api/mcp/plugins/toggle", json={"name": "p@m", "enabled": False})
    assert r.status_code == 401 and r.json()["error"] == "elevation_required"


def test_plugin_toggle_writes_after_elevation(client):
    _elevate(client)
    _cc_install(client, "p@m")
    path = _cc_settings(client, {"enabledPlugins": {"p@m": True}})
    r = client.post("/api/mcp/plugins/toggle", json={"name": "p@m", "enabled": False})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert json.loads(path.read_text())["enabledPlugins"]["p@m"] is False


def test_plugin_toggle_project_scope_is_409(client):
    _elevate(client)
    _cc_install(client, "p@m")
    r = client.post("/api/mcp/plugins/toggle", json={
        "name": "p@m", "enabled": False, "scope": "project", "worktree": "wt1"})
    assert r.status_code == 409


def test_codex_plugin_toggle_writes_only_enabled(client, monkeypatch):
    _elevate(client)
    config = client.vt_home / ".codex" / "config.toml"
    config.write_text('model = "keep"\n\n[plugins."p@m"]\n# keep\nenabled = true\n')
    monkeypatch.setattr("codex_cli.installed_plugin_ids", lambda: ({"p@m"}, None))
    r = client.post("/api/mcp/plugins/toggle", json={
        "name": "p@m", "enabled": False, "tool": "codex", "scope": "global",
    })
    assert r.status_code == 200 and r.json()["changed"] is True
    assert config.read_text() == 'model = "keep"\n\n[plugins."p@m"]\n# keep\nenabled = false\n'


def test_plugin_toggle_of_an_uninstalled_plugin_is_409(client):
    _elevate(client)
    r = client.post("/api/mcp/plugins/toggle", json={"name": "nope@m", "enabled": True})
    assert r.status_code == 409 and "설치되지 않은" in r.json()["reason"]


def test_plugin_toggle_validates_input(client):
    _elevate(client)
    assert client.post("/api/mcp/plugins/toggle",
                       json={"enabled": True}).json()["error"] == "missing_name"
    assert client.post("/api/mcp/plugins/toggle",
                       json={"name": "p@m", "enabled": "true"}).json()["error"] == "invalid_enabled"


# ------------------------------------------------------------- 검증(P3-2)

_STUB_MINIMAL = """
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        resp = {"jsonrpc": "2.0", "id": msg["id"],
                "result": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "serverInfo": {"name": "stub", "version": "0"}}}
        sys.stdout.write(json.dumps(resp) + "\\n"); sys.stdout.flush()
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        resp = {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{"name": "a"}]}}
        sys.stdout.write(json.dumps(resp) + "\\n"); sys.stdout.flush()
"""


def test_verify_requires_elevation(client):
    _login(client)
    r = client.post("/api/mcp/servers/global/n/verify", json={"tool": "claude"})
    assert r.status_code == 401 and r.json()["error"] == "elevation_required"


def test_verify_stdio_reports_connected(client):
    _elevate(client)
    _write_claude(client, {"mcpServers": {"n": {
        "command": sys.executable, "args": ["-c", _STUB_MINIMAL]}}})
    r = client.post("/api/mcp/servers/global/n/verify", json={"tool": "claude"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "connected"
    assert body["tool_count"] == 1


def test_verify_unknown_server_is_404(client):
    _elevate(client)
    _write_claude(client, {"mcpServers": {}})
    r = client.post("/api/mcp/servers/global/nope/verify", json={"tool": "claude"})
    assert r.status_code == 404


def test_verify_invalid_scope_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/servers/weird/n/verify", json={"tool": "claude"})
    assert r.status_code == 400


def test_verify_invalid_tool_is_400(client):
    _elevate(client)
    r = client.post("/api/mcp/servers/global/n/verify", json={"tool": "not-a-tool"})
    assert r.status_code == 400


def test_verify_http_without_confirmation_is_rejected(client):
    _elevate(client)
    _write_claude(client, {"mcpServers": {"n": {"url": "https://e.example/mcp"}}})
    r = client.post("/api/mcp/servers/global/n/verify", json={"tool": "claude"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "error"
    assert "확인" in body["error"]


def test_verify_never_leaks_env_values(client):
    _elevate(client)
    _write_claude(client, {"mcpServers": {"n": {
        "command": sys.executable,
        "args": ["-c", "import sys; sys.stderr.write('sk-should-never-appear'); sys.exit(1)"],
        "env": {"TOKEN": "sk-should-never-appear"},
    }}})
    r = client.post("/api/mcp/servers/global/n/verify", json={"tool": "claude"})
    assert "sk-should-never-appear" not in r.text


def test_verify_missing_env_is_unverified_without_spawning(client, monkeypatch):
    """ADR-46 — 값을 모르면 띄우지 않고 이름만 알려준다. 실패가 아니다."""
    import mcp_verify
    _elevate(client)
    monkeypatch.delenv("ABSENT_ROUTE_TOK", raising=False)
    monkeypatch.setattr("mcp_catalog.env_for", lambda servers: {})
    spawned = []

    async def _no_spawn(*a, **k):
        spawned.append(a)
        return {"state": "connected"}
    monkeypatch.setattr(mcp_verify, "verify_stdio", _no_spawn)
    _write_claude(client, {"mcpServers": {"n": {
        "command": sys.executable, "env": {"T": "${ABSENT_ROUTE_TOK}", "L": "sk-literal-secret"}}}})
    r = client.post("/api/mcp/servers/global/n/verify", json={"tool": "claude"})
    body = r.json()
    assert body["state"] == "unverified"
    assert body["missing"] == ["ABSENT_ROUTE_TOK"]
    assert spawned == []
    assert "sk-literal-secret" not in r.text


def test_codex_plugin_state_comes_from_config_not_the_cached_list(client, monkeypatch):
    """ADR-48 — 설치 목록은 캐시지만 켬/끔은 매번 config.toml에서 읽는다(파일이 진실)."""
    _login(client)
    monkeypatch.setattr("codex_cli.run_json", lambda args, **kwargs: ({"installed": [{
        "pluginId": "p@m", "name": "p", "enabled": True, "source": {}}]}, None))
    config = client.vt_home / ".codex" / "config.toml"
    config.write_text("")
    by = {p["name"]: p for p in client.get("/api/mcp/plugins").json()["plugins"]}
    assert by["p@m"]["enabled"] is True
    config.write_text('[plugins."p@m"]\nenabled = false\n')
    by = {p["name"]: p for p in client.get("/api/mcp/plugins").json()["plugins"]}
    assert by["p@m"]["enabled"] is False, "캐시된 옛 enabled가 이기면 안 된다"


def test_codex_plugin_toggle_does_not_hold_the_lock_while_asking_the_cli(client, monkeypatch):
    """설치 확인(느린 CLI)은 config.toml 락 밖에서 해야 한다."""
    import mcp_write
    _elevate(client)
    (client.vt_home / ".codex" / "config.toml").write_text('[plugins."p@m"]\nenabled = true\n')
    held = []
    real_locked = mcp_write._locked

    def spy_ids():
        held.append(getattr(mcp_write, "_spy_inside", False))
        return {"p@m"}, None

    def spy_locked(path):
        cm = real_locked(path)

        class _Wrap:
            def __enter__(self):
                mcp_write._spy_inside = True
                return cm.__enter__()

            def __exit__(self, *a):
                mcp_write._spy_inside = False
                return cm.__exit__(*a)
        return _Wrap()
    monkeypatch.setattr("codex_cli.installed_plugin_ids", spy_ids)
    monkeypatch.setattr(mcp_write, "_locked", spy_locked)
    r = client.post("/api/mcp/plugins/toggle", json={
        "name": "p@m", "enabled": False, "tool": "codex", "scope": "global"})
    assert r.status_code == 200 and held == [False]
