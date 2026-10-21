"""Real native-file operations, plan conflicts and host/session isolation."""
import concurrent.futures
import json
from pathlib import Path

import pytest

import agent_config as manager
import agent_config_context as contexts
import agent_config_maintenance as maintenance
import agent_config_restart as restart
import mcp_adapters
import mcp_plans
_real_identify = contexts.identify


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    repo = home / "sample-repo"
    repo.mkdir()
    state = tmp_path / "state"
    monkeypatch.setenv("VT_MCP_HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(state))
    monkeypatch.delenv("VT_CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(manager, "tmux_runner_panes", lambda: [])
    tree = {"id": "tree", "path": str(repo), "repoName": "sample-repo", "branch": "main"}
    monkeypatch.setattr("worktree.list_worktrees", lambda: [tree])
    monkeypatch.setattr("codex_cli.run_json", lambda *a, **k: ({"installed": []}, None))
    for root in (home / ".codex", home / "profile"):
        root.mkdir()
        (root / "config.toml").write_text('# preserve me\n[mcp_servers.docs]\ncommand = "sample-mcp"\nenabled = true\n')
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"notes": {"command": "sample", "env": {"TOKEN": "private-secret"}}}}))
    ctx = {"tool": "codex", "root": str(home / "profile"), "home": str(home), "project": str(repo),
           "worktree": "tree", "session": "sample", "pane": "%1", "pid": 123, "cwd": str(repo),
           "identity": "identity-1", "env": {"HOME": str(home)}, "argv": ["codex"]}
    monkeypatch.setattr(contexts, "identify", lambda session: dict(ctx))
    return home, repo, state, ctx


def test_profile_paths_are_thread_local(setup):
    home, repo, state, ctx = setup
    def read(root):
        with contexts.use({**ctx, "root": str(root)}):
            return mcp_adapters.codex_paths()["global"]
    with concurrent.futures.ThreadPoolExecutor() as pool:
        paths = list(pool.map(read, [home / "profile", home / ".codex"] * 20))
    assert paths == [home / "profile" / "config.toml", home / ".codex" / "config.toml"] * 20
    assert mcp_adapters.codex_paths()["global"] == home / ".codex" / "config.toml"


def test_plugin_cache_is_separate_for_each_profile(setup, monkeypatch):
    import codex_cli
    home, repo, state, ctx = setup
    calls = []
    def query(*args, **kwargs):
        name = codex_cli.codex_home().name
        calls.append(name)
        return {"installed": [{"pluginId": name}]}, None
    monkeypatch.setattr(codex_cli, "run_json", query)
    for name in ("profile", ".codex", "profile"):
        with contexts.use({**ctx, "root": str(home / name)}):
            result, error = codex_cli.plugin_list()
            assert result["installed"][0]["pluginId"] == name
    assert calls == ["profile", ".codex"]


def test_inventory_pages_include_later_projects_and_refuse_changed_contexts(setup, monkeypatch):
    home, repo, state, ctx = setup
    trees = []
    for i in range(24):
        project = home / f"repo-{i:02}"
        project.mkdir()
        trees.append({"id": f"tree-{i}", "path": str(project), "repoName": project.name})
    last = Path(trees[-1]["path"]) / ".codex" / "config.toml"
    last.parent.mkdir()
    last.write_text('[mcp_servers.last_project]\ncommand = "sample"\n')
    monkeypatch.setattr("worktree.list_worktrees", lambda: list(trees))
    first = manager.inventory(None, "all")
    assert first["next_offset"] == 32
    complete = manager.full_inventory(None, "all")
    assert any(row["name"] == "last_project" for row in complete["items"])
    trees.pop()
    with pytest.raises(ValueError):
        manager.inventory(None, "all", offset=32, inventory_revision=first["inventory_revision"])


def test_item_ids_are_stable_between_whole_and_session_views(setup):
    ctx = setup[3]
    selected = next(row for row in manager.inventory("sample", "session")["items"] if row["name"] == "docs")
    whole = manager.full_inventory(None, "all")
    same = next(row for row in whole["items"] if row["name"] == "docs" and row["profile"] == ctx["root"])
    assert same["id"] == selected["id"]


def test_inactive_profile_private_project_is_discovered_and_written_in_place(setup, monkeypatch):
    home, repo, state, ctx = setup
    profile = home / "claude-profile"
    profile.mkdir()
    path = profile / ".claude.json"
    path.write_text(json.dumps({"projects": {str(repo): {"mcpServers": {"private": {"command": "sample"}}}}}))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    monkeypatch.setattr("worktree.list_worktrees", lambda: [])
    with manager.transaction() as data:
        data["profiles"] = {"known": {"tool": "claude", "root": str(profile)}}
    row = next(r for r in manager.full_inventory(None, "all")["items"] if r["name"] == "private")
    assert row["context"]["worktree"].startswith("config:")
    plan = manager.preview([{"id": row["id"], "enabled": False}], None, "all")
    result = manager.apply(plan["token"])
    assert result["status"] == "ok", result
    assert "private" in json.loads(path.read_text())["projects"][str(repo)]["disabledMcpServers"]
    assert "projects" not in json.loads((home / ".claude.json").read_text())


def test_resume_confirmation_requires_private_verified_session_start_evidence(monkeypatch):
    import agent_status
    monkeypatch.setattr(agent_status, "_state", {})
    monkeypatch.setattr(agent_status, "_owners", {})
    entry = agent_status.on_event("session_start", {"session_id": "a" * 32, "source": "resume"},
                                  session="sample", agent="codex", verified=True)
    assert entry["_session_start_source"] == "resume" and entry["_session_start_verified"] is True
    assert "_session_start_at" not in agent_status.public_entry(entry)


def test_session_inventory_uses_real_profile_and_never_returns_values(setup):
    home, repo, state, ctx = setup
    scan = manager.inventory("sample", "session", plugins=True)
    assert scan["selected"]["root"] == str(home / "profile")
    row = next(r for r in scan["items"] if r["kind"] == "mcp")
    assert row["source"] == str(home / "profile" / "config.toml")
    assert "env" not in scan["selected"] and "argv" not in scan["selected"]
    whole = json.dumps(manager.inventory(None, "all", plugins=True))
    assert "private-secret" not in whole


def test_profile_toggle_preserves_default_profile_and_comments(setup):
    home, repo, state, ctx = setup
    row = next(r for r in manager.inventory("sample", "session")["items"] if r["kind"] == "mcp")
    plan = manager.preview([{"id": row["id"], "enabled": False}], "sample", "session")
    result = manager.apply(plan["token"])
    assert result["status"] == "ok", result
    assert 'enabled = false' in (home / "profile" / "config.toml").read_text()
    assert '# preserve me' in (home / "profile" / "config.toml").read_text()
    assert 'enabled = true' in (home / ".codex" / "config.toml").read_text()
    assert list((state / "agent-config-backups").glob("*.bak"))
    with pytest.raises(ValueError):
        manager.apply(plan["token"])


def test_plan_refuses_changed_source_before_any_write(setup):
    home, repo, state, ctx = setup
    row = manager.inventory("sample", "session")["items"][0]
    plan = manager.preview([{"id": row["id"], "enabled": False}], "sample", "session")
    path = Path(row["source"])
    path.write_text(path.read_text() + '# changed elsewhere\n')
    with pytest.raises(ValueError):
        manager.apply(plan["token"])
    assert 'enabled = true' in path.read_text()


def test_plan_refuses_changed_session(setup, monkeypatch):
    home, repo, state, ctx = setup
    row = manager.inventory("sample", "session")["items"][0]
    plan = manager.preview([{"id": row["id"], "enabled": False}], "sample", "session")
    monkeypatch.setattr(contexts, "identify", lambda session: {**ctx, "identity": "new-process"})
    with pytest.raises(ValueError):
        manager.apply(plan["token"])


def test_mixed_preset_retains_omitted_items(setup):
    home, repo, state, ctx = setup
    path = home / "profile" / "config.toml"
    path.write_text(path.read_text() + '\n[mcp_servers.other]\ncommand = "other"\nenabled = true\n')
    skill = home / ".agents" / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: review\n---\nReview the changes.\n')
    scan = manager.inventory("sample", "session", plugins=True)
    chosen = [r for r in scan["items"] if r["name"] in ("docs", "review")]
    assert {r["kind"] for r in chosen} == {"mcp", "skill"}
    saved = manager.save_preset("Review", [{"id": r["id"], "enabled": False} for r in chosen], "sample", "session")
    plan = manager.preview(None, "sample", "session", saved["id"])
    result = manager.apply(plan["token"])
    assert result["status"] == "ok", result
    with contexts.use(ctx):
        config, error = mcp_adapters._read_toml(path)
    assert config["mcp_servers"]["other"]["enabled"] is True
    assert config["mcp_servers"]["docs"]["enabled"] is False
    assert config["skills"]["config"][0]["enabled"] is False


def test_skill_plan_checks_actual_config_destination(setup):
    home, repo, state, ctx = setup
    skill = home / ".agents" / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text('Review.\n')
    row = next(r for r in manager.inventory("sample", "session")["items"] if r["kind"] == "skill")
    plan = manager.preview([{"id": row["id"], "enabled": False}], "sample", "session")
    path = home / "profile" / "config.toml"
    path.write_text(path.read_text() + '# external\n')
    with pytest.raises(ValueError):
        manager.apply(plan["token"])
    assert 'skills.config' not in path.read_text()


def test_environment_values_are_write_only_and_scope_precedence(setup):
    home, repo, state, ctx = setup
    manager.set_environment("sample", "machine", "API_KEY", "global-secret", ctx["identity"])
    manager.set_environment("sample", "profile", "API_KEY", "profile-secret", ctx["identity"])
    manager.set_environment("sample", "project", "API_KEY", "project-secret", ctx["identity"])
    assert manager.launch_environment(ctx)["API_KEY"] == "project-secret"
    assert "secret" not in json.dumps(manager.environment("sample"))
    assert manager._path().stat().st_mode & 0o777 == 0o600
    for name in ("HOME", "PATH", "VT_TOKEN", "NODE_OPTIONS", "DYLD_INSERT_LIBRARIES"):
        with pytest.raises(ValueError):
            manager.set_environment("sample", "machine", name, "x", ctx["identity"])


def test_invalid_changes_and_corrupt_store_refuse_writes(setup):
    for changes in ([], [{"id": "unknown", "enabled": False}], [{"id": "unknown", "enabled": "false"}]):
        with pytest.raises(ValueError):
            manager.preview(changes, "sample", "session")
    manager._path().parent.mkdir(parents=True, exist_ok=True)
    manager._path().write_text('{broken')
    with pytest.raises(ValueError):
        manager.inventory("sample", "session")
    assert manager._path().read_text() == '{broken'


def test_skill_edit_round_trip_checks_review_revision(setup):
    home, repo, state, ctx = setup
    path = home / ".agents" / "skills" / "review" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text('Original instructions.\n')
    row = next(r for r in manager.inventory("sample", "session")["items"] if r["kind"] == "skill")
    editor = maintenance.editor(row["id"], "sample", "session")
    plan = maintenance.preview_edit(row["id"], "sample", "session", editor["revision"], 'Updated instructions.\n')
    assert maintenance.apply_edit(plan["token"])["status"] == "ok"
    assert path.read_text() == 'Updated instructions.\n'
    with pytest.raises(ValueError):
        maintenance.preview_edit(row["id"], "sample", "session", editor["revision"], 'Stale change.\n')


def test_remote_request_never_writes_before_target_approval(setup, monkeypatch):
    from routes import agent_config as routes
    row = manager.inventory("sample", "session")["items"][0]
    body = {"session": "sample", "view": "session", "changes": [{"id": row["id"], "enabled": False}]}
    plan = routes._remote("preview", body, "peer-a")
    with pytest.raises(ValueError):
        routes._remote("request", {"action": "apply", "payload": {"token": plan["token"]}}, "peer-b")
    pending = routes._remote("request", {"action": "apply", "payload": {"token": plan["token"]}}, "peer-a")
    assert pending["status"] == "pending"
    assert 'enabled = true' in Path(row["source"]).read_text()
    with pytest.raises(ValueError):
        routes._remote("apply", {"token": plan["token"]}, "peer-a")
    assert routes.pending_requests()


def test_http_requires_human_and_elevation_and_applies_reviewed_plan(setup, monkeypatch):
    import auth
    import time
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from routes import agent_config as routes
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", "isolated-signing-key")
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", "configured-password")
    monkeypatch.setattr(auth, "VT_AUTH_TOKEN", "machine-only")
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(routes.elevated_router)
    with TestClient(app) as client:
        body = {"session": "sample", "view": "session"}
        assert client.post('/api/agent-config/read/inventory', json=body).status_code == 403
        assert client.post('/api/agent-config/read/inventory', json=body, headers={"Authorization": "Bearer machine-only"}).status_code == 403
        client.cookies.set('vt_session', auth.make_session())
        scan = client.post('/api/agent-config/read/inventory', json=body)
        assert scan.status_code == 200
        row = next(r for r in scan.json()["items"] if r["kind"] == "mcp")
        preview = client.post('/api/agent-config/read/preview', json={**body, "changes": [{"id": row["id"], "enabled": False}]})
        assert preview.status_code == 200
        payload = {"token": preview.json()["token"]}
        assert client.post('/api/agent-config/write/apply', json=payload).status_code == 401
        client.cookies.set('vt_session', auth.make_session(elev_exp=int(time.time()) + 60))
        result = client.post('/api/agent-config/write/apply', json=payload)
        assert result.status_code == 200 and result.json()["status"] == "ok", result.text
        assert 'enabled = false' in Path(row["source"]).read_text()
        assert client.post('/api/agent-config/read/inventory', json={"view": []}).status_code == 409
        assert client.post('/api/agent-config/write/metadata', json=[]).status_code == 409


def test_restart_refuses_unknown_flags_and_wrapper(setup):
    home, repo, state, ctx = setup
    with pytest.raises(ValueError):
        restart._argv({**ctx, "argv": ["node", "cli.js"]}, "a" * 32)
    binary = home / "codex"
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o700)
    with pytest.raises(ValueError):
        restart._argv({**ctx, "argv": [str(binary), "--config", "unknown=true"]}, "a" * 32)
    args = restart._argv({**ctx, "argv": [str(binary), "--model", "sample-model"]}, "a" * 32)
    assert args == [str(binary), "--model", "sample-model", "resume", "a" * 32]


def test_composer_draft_blocks_restart(setup, monkeypatch):
    ctx = setup[3]
    monkeypatch.setattr("tmux_runner.run_text", lambda *a, **k: 'Some answer\n› unsent text\n  hints\n')
    assert not restart._empty_prompt(ctx)

    monkeypatch.setattr("tmux_runner.run_text", lambda *a, **k: 'Some answer\n› \n  ? for shortcuts\n')
    assert restart._empty_prompt(ctx)
    monkeypatch.setattr("tmux_runner.run_text", lambda *a, **k: 'Some answer\n› \n  multiline draft\n')
    assert not restart._empty_prompt(ctx)


def test_codex_edit_preserves_unknown_settings_and_unedited_secrets(setup):
    home, repo, state, ctx = setup
    path = home / "profile" / "config.toml"
    path.write_text('# global comment\n[other]\nsetting = "keep"\n\n[mcp_servers.docs]\n# server comment\ncommand = "sample"\nargs = [\n "old",\n]\nenabled = true\nstartup_timeout_sec = 30\n[mcp_servers.docs.env]\nTOKEN = "private-secret"\n')
    row = next(r for r in manager.inventory("sample", "session")["items"] if r["kind"] == "mcp")
    plan = maintenance.preview_edit(row["id"], "sample", "session", row["revision"], json.dumps({"args": ["new"]}))
    assert 'private-secret' not in json.dumps(plan)
    assert maintenance.apply_edit(plan["token"])["status"] == "ok"
    assert '# global comment' in path.read_text() and '# server comment' in path.read_text()
    config, error = mcp_adapters._read_toml(path)
    assert config["other"]["setting"] == "keep"
    assert config["mcp_servers"]["docs"]["args"] == ["new"]
    assert config["mcp_servers"]["docs"]["env"]["TOKEN"] == "private-secret"
    assert config["mcp_servers"]["docs"]["startup_timeout_sec"] == 30


def test_removed_environment_overlay_does_not_survive_resume(setup):
    ctx = setup[3]
    manager.set_environment("sample", "project", "SAMPLE_VALUE", "overlay", ctx["identity"])
    first = manager.prepare_launch_environment(ctx)
    assert first["SAMPLE_VALUE"] == "overlay"
    manager.set_environment("sample", "project", "SAMPLE_VALUE", None, ctx["identity"])
    second = manager.prepare_launch_environment({**ctx, "env": first})
    assert "SAMPLE_VALUE" not in second
    original = {**ctx, "env": {**ctx["env"], "SAMPLE_VALUE": "inherited"}}
    manager.set_environment("sample", "project", "SAMPLE_VALUE", "overlay", ctx["identity"])
    first = manager.prepare_launch_environment(original)
    manager.set_environment("sample", "project", "SAMPLE_VALUE", None, ctx["identity"])
    assert manager.prepare_launch_environment({**ctx, "env": first})["SAMPLE_VALUE"] == "inherited"


def test_real_isolated_tmux_normal_exit_and_same_conversation_resume(setup, monkeypatch):
    import os
    import shutil
    import subprocess
    import time
    import shlex
    import tmux_runner
    if not shutil.which("tmux") or not shutil.which("cc"):
        pytest.skip("isolated native process fixture requires tmux and a C compiler")
    home, repo, state, ctx = setup
    source = home / "fixture.c"
    source.write_text(r'''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
  char *out = getenv("FSH_FIXTURE_OUT");
  FILE *f = out ? fopen(out, "w") : NULL;
  if(f) { fprintf(f, "%s\n%s\n", argc > 2 ? argv[2] : "initial", getenv("SAMPLE_VALUE") ? getenv("SAMPLE_VALUE") : "unset"); fclose(f); }
  printf("\342\200\272 "); fflush(stdout);
  char line[1024];
  while(fgets(line, sizeof(line), stdin)) { if(strncmp(line, "/exit", 5) == 0) return 0; }
  return 0;
}
''')
    binary = home / "codex"
    subprocess.run(["cc", str(source), "-o", str(binary)], check=True, capture_output=True, timeout=20)
    socket = "fsh-config-resume-" + str(os.getpid())
    monkeypatch.setenv("VT_CONFIG", str(home / "empty.env"))
    monkeypatch.setenv("VT_WORKSPACE_PATH", str(home / "workspace.json"))
    monkeypatch.setenv("VT_TMUX_SOCKET", socket)
    monkeypatch.delenv("VT_TOKEN", raising=False)
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", socket)
    monkeypatch.setattr(contexts, "identify", _real_identify)
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    monkeypatch.setattr("agent_status.visible_state", lambda: {"owner": {"host": "local", "tmux_session": "config-resume", "agent": "codex", "session_id": sid, "status": "idle", "updated_at": time.time(), "_session_start_at": time.time(), "_session_start_verified": True, "_session_start_source": "resume"}})
    out = home / "resumed.txt"
    command = shlex.join(["env", "HOME=" + str(home), "CODEX_HOME=" + str(home / "profile"), "FSH_FIXTURE_OUT=" + str(out), str(binary)])
    try:
        rc, _, error = tmux_runner.run(["new-session", "-d", "-s", "config-resume", "-c", str(repo), command])
        assert rc == 0, error
        for _ in range(40):
            try:
                actual = contexts.identify("config-resume")
                break
            except (OSError, ValueError):
                time.sleep(.05)
        else:
            pytest.fail("fixture process could not be identified")
        manager.set_environment("config-resume", "project", "SAMPLE_VALUE", "sample-value", actual["identity"])
        plan = restart.preview("config-resume")
        job = restart.start(plan["token"])
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            result = restart.status(job["id"])
            if result["state"] not in ("waiting", "restarting"):
                break
            time.sleep(.1)
        assert result["state"] == "resumed", result
        assert out.read_text().splitlines() == [sid, "sample-value"]
        assert not list((state / "agent-launch").glob("*.json"))
    finally:
        tmux_runner.run(["kill-server"])
