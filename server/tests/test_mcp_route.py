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


def test_deploy_to_codex_is_refused_over_http(client):
    _elevate(client)
    r = client.post("/api/mcp/deploy",
                    json={"name": "n", "defn": {"command": "x"}, "tool": "codex"})
    assert r.status_code == 409 and "codex mcp add" in r.json()["reason"]


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

def test_plugins_list_distinguishes_absent_from_disabled(client):
    """"목록에 없음"과 "false"는 다르다 — 뭉개면 "끈 적 없는데 꺼져 보인다"."""
    _login(client)
    _write_claude(client, {"enabledPlugins": {"on@m": True, "off@m": False}})
    body = client.get("/api/mcp/plugins").json()
    by = {p["name"]: p for p in body["plugins"]}
    assert by["on@m"]["enabled"] is True and by["on@m"]["explicit"] is True
    assert by["off@m"]["enabled"] is False
    assert by["on@m"]["marketplace"] == "m" and by["on@m"]["plugin"] == "on"


def test_plugins_list_includes_codex_skills_and_mcp_bundle(client, monkeypatch):
    _login(client)
    _write_claude(client, {"enabledPlugins": {}})
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
    _write_claude(client, {"enabledPlugins": {"p@m": True}})
    r = client.post("/api/mcp/plugins/toggle", json={"name": "p@m", "enabled": False})
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert _read_claude(client)["enabledPlugins"]["p@m"] is False


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
    _write_claude(client, {"enabledPlugins": {}})
    r = client.post("/api/mcp/plugins/toggle", json={"name": "nope@m", "enabled": True})
    assert r.status_code == 409 and "설치되지 않은" in r.json()["reason"]


def test_plugin_toggle_validates_input(client):
    _elevate(client)
    assert client.post("/api/mcp/plugins/toggle",
                       json={"enabled": True}).json()["error"] == "missing_name"
    assert client.post("/api/mcp/plugins/toggle",
                       json={"name": "p@m", "enabled": "true"}).json()["error"] == "invalid_enabled"
