"""97번 1단계 2/n — MCP 서버 on/off 쓰기(mcp_write).

남의 설정 파일을 고치는 모듈이라 테스트 대부분이 "안 고치는 것"에 관한
것이다. 지켜야 할 불변식:

1. **못 읽은 파일엔 쓰지 않는다** — 파싱 실패를 빈 설정으로 간주하고 쓰면
   그 순간 사용자 설정이 통째로 날아간다(agent-deck #1956).
2. **손대기로 한 곳 말고는 한 글자도 안 바뀐다** — TOML 주석·정렬까지.
3. **멱등** — 목표 상태를 지정하므로 같은 호출을 반복해도 안전하다.
4. **썼는데 확인 못 했으면 성공이라 말하지 않는다**(`unknown`).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import mcp_write


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vtstate"))
    monkeypatch.delenv("VT_CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".gemini" / "config").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def wt(tmp_path, monkeypatch):
    path = tmp_path / "repo"
    path.mkdir(exist_ok=True)
    entry = {"id": "wt1", "path": str(path), "repoName": "repo", "branch": "main"}
    monkeypatch.setattr("worktree.list_worktrees", lambda force=False: [entry])
    return entry


def _claude(home, data):
    (home / ".claude.json").write_text(json.dumps(data, indent=2))


def _read_claude(home):
    return json.loads((home / ".claude.json").read_text())


# --------------------------------------------------------------- claude

def test_claude_disable_adds_to_project_list(home, wt):
    _claude(home, {"mcpServers": {"github": {"command": "x"}}, "projects": {}})
    r = mcp_write.set_enabled("claude", "github", False, worktree_id="wt1")
    assert r["status"] == "ok" and r["changed"] is True
    entry = _read_claude(home)["projects"][wt["path"]]
    assert entry["disabledMcpServers"] == ["github"]


def test_claude_enable_removes_from_project_list(home, wt):
    _claude(home, {"projects": {wt["path"]: {"disabledMcpServers": ["github", "other"]}}})
    mcp_write.set_enabled("claude", "github", True, worktree_id="wt1")
    assert _read_claude(home)["projects"][wt["path"]]["disabledMcpServers"] == ["other"]


def test_claude_is_idempotent(home, wt):
    _claude(home, {"projects": {}})
    first = mcp_write.set_enabled("claude", "g", False, worktree_id="wt1")
    second = mcp_write.set_enabled("claude", "g", False, worktree_id="wt1")
    assert first["changed"] is True
    assert second["status"] == "ok" and second["changed"] is False
    assert _read_claude(home)["projects"][wt["path"]]["disabledMcpServers"] == ["g"]


def test_claude_without_worktree_is_refused(home):
    """켜짐/꺼짐이 프로젝트별이라 워크트리 없이는 토글 자체가 성립하지 않는다."""
    _claude(home, {"mcpServers": {"g": {"command": "x"}}})
    r = mcp_write.set_enabled("claude", "g", False)
    assert r["status"] == "failed" and "프로젝트별" in r["reason"]


def test_claude_shared_uses_approval_lists(home, wt):
    _claude(home, {"projects": {}})
    mcp_write.set_enabled("claude", "s", True, worktree_id="wt1", shared=True)
    entry = _read_claude(home)["projects"][wt["path"]]
    assert entry["enabledMcpjsonServers"] == ["s"]
    # 없던 목록을 빈 배열로 새로 만들지는 않는다 — 최소 수정.
    assert entry.get("disabledMcpjsonServers", []) == []


def test_claude_shared_disable_flips_both_lists(home, wt):
    _claude(home, {"projects": {wt["path"]: {"enabledMcpjsonServers": ["s"]}}})
    mcp_write.set_enabled("claude", "s", False, worktree_id="wt1", shared=True)
    entry = _read_claude(home)["projects"][wt["path"]]
    assert entry["enabledMcpjsonServers"] == []
    assert entry["disabledMcpjsonServers"] == ["s"]


def test_claude_other_settings_survive(home, wt):
    """이 파일엔 MCP 말고도 사용자 설정 전체가 들어 있다."""
    _claude(home, {
        "anonymousId": "keep-me", "autoUpdates": True,
        "projects": {"/elsewhere": {"disabledMcpServers": ["z"]}},
    })
    mcp_write.set_enabled("claude", "g", False, worktree_id="wt1")
    after = _read_claude(home)
    assert after["anonymousId"] == "keep-me"
    assert after["autoUpdates"] is True
    assert after["projects"]["/elsewhere"]["disabledMcpServers"] == ["z"]


def test_broken_claude_json_is_never_written(home, wt):
    (home / ".claude.json").write_text("{ broken json")
    r = mcp_write.set_enabled("claude", "g", False, worktree_id="wt1")
    assert r["status"] == "failed" and "쓰기를 중단" in r["reason"]
    assert (home / ".claude.json").read_text() == "{ broken json"


def test_null_root_is_never_written(home, wt):
    (home / ".claude.json").write_text("null")
    r = mcp_write.set_enabled("claude", "g", False, worktree_id="wt1")
    assert r["status"] == "failed"
    assert (home / ".claude.json").read_text() == "null"


def test_unknown_worktree_is_refused(home, wt):
    _claude(home, {"projects": {}})
    r = mcp_write.set_enabled("claude", "g", False, worktree_id="nope")
    assert r["status"] == "failed" and "워크트리" in r["reason"]


# ------------------------------------------------------------------ agy

def _agy(home, data):
    (home / ".gemini" / "config" / "mcp_config.json").write_text(json.dumps(data, indent=2))


def _read_agy(home):
    return json.loads((home / ".gemini" / "config" / "mcp_config.json").read_text())


def test_agy_disable_sets_disabled_true(home):
    _agy(home, {"mcpServers": {"n": {"command": "x"}}})
    r = mcp_write.set_enabled("agy", "n", False)
    assert r["status"] == "ok"
    assert _read_agy(home)["mcpServers"]["n"]["disabled"] is True


def test_agy_enable_removes_the_key(home):
    """`agy mcp enable`은 false로 두지 않고 키를 지운다(실측) — 그대로 맞춘다."""
    _agy(home, {"mcpServers": {"n": {"command": "x", "disabled": True}}})
    mcp_write.set_enabled("agy", "n", True)
    assert "disabled" not in _read_agy(home)["mcpServers"]["n"]


def test_agy_unknown_server_is_refused(home):
    _agy(home, {"mcpServers": {}})
    r = mcp_write.set_enabled("agy", "nope", False)
    assert r["status"] == "failed" and "정의가 없다" in r["reason"]


def test_agy_has_no_local_scope(home):
    _agy(home, {"mcpServers": {"n": {"command": "x"}}})
    r = mcp_write.set_enabled("agy", "n", False, scope="local", worktree_id=None)
    assert r["status"] == "failed" and "로컬 스코프가 없다" in r["reason"]


def test_agy_preserves_other_servers(home):
    _agy(home, {"mcpServers": {
        "a": {"command": "x", "env": {"T": "keep"}}, "b": {"url": "https://e.example"}}})
    mcp_write.set_enabled("agy", "a", False)
    after = _read_agy(home)
    assert after["mcpServers"]["a"]["env"] == {"T": "keep"}
    assert after["mcpServers"]["b"] == {"url": "https://e.example"}


# ---------------------------------------------------------------- codex

def _codex(home, text):
    (home / ".codex" / "config.toml").write_text(text)


def _read_codex(home):
    return (home / ".codex" / "config.toml").read_text()


def test_codex_sets_enabled_false(home):
    _codex(home, '[mcp_servers.a]\ncommand = "x"\n')
    r = mcp_write.set_enabled("codex", "a", False)
    assert r["status"] == "ok"
    assert "enabled = false" in _read_codex(home)


def test_codex_replaces_existing_enabled(home):
    _codex(home, '[mcp_servers.a]\ncommand = "x"\nenabled = false\n')
    mcp_write.set_enabled("codex", "a", True)
    text = _read_codex(home)
    assert "enabled = true" in text and "enabled = false" not in text


def test_codex_preserves_comments_and_formatting(home):
    """구조체 재직렬화가 아니라 텍스트 수술이라는 것의 실제 의미."""
    original = (
        "# 내가 손으로 적은 주석\n"
        "model   =   'gpt-5'\n"
        "\n"
        "[mcp_servers.a]\n"
        "# 이 서버는 중요하다\n"
        'command = "x"\n'
        "\n"
        "[mcp_servers.a.env]\n"
        'FOO = "bar"\n'
    )
    _codex(home, original)
    mcp_write.set_enabled("codex", "a", False)
    after = _read_codex(home)
    assert "# 내가 손으로 적은 주석" in after
    assert "# 이 서버는 중요하다" in after
    assert "model   =   'gpt-5'" in after
    assert 'FOO = "bar"' in after


def test_codex_enabled_goes_into_the_server_table_not_a_subtable(home):
    """`enabled`가 `[mcp_servers.a.env]` 안으로 들어가면 env 변수가 돼버린다."""
    _codex(home, '[mcp_servers.a]\ncommand = "x"\n\n[mcp_servers.a.env]\nFOO = "bar"\n')
    mcp_write.set_enabled("codex", "a", False)
    parsed = tomllib.loads(_read_codex(home))
    assert parsed["mcp_servers"]["a"]["enabled"] is False
    assert parsed["mcp_servers"]["a"]["env"] == {"FOO": "bar"}


def test_codex_quoted_table_name_is_matched(home):
    _codex(home, '[mcp_servers."my-server"]\ncommand = "x"\n')
    r = mcp_write.set_enabled("codex", "my-server", False)
    assert r["status"] == "ok"
    assert tomllib.loads(_read_codex(home))["mcp_servers"]["my-server"]["enabled"] is False


def test_codex_only_the_named_server_changes(home):
    _codex(home, '[mcp_servers.a]\ncommand = "x"\n\n[mcp_servers.b]\ncommand = "y"\n')
    mcp_write.set_enabled("codex", "a", False)
    parsed = tomllib.loads(_read_codex(home))
    assert parsed["mcp_servers"]["a"]["enabled"] is False
    assert "enabled" not in parsed["mcp_servers"]["b"]


def test_codex_unknown_server_is_refused(home):
    _codex(home, '[mcp_servers.a]\ncommand = "x"\n')
    r = mcp_write.set_enabled("codex", "zzz", False)
    assert r["status"] == "failed" and "정의가 없다" in r["reason"]


def test_codex_broken_toml_is_never_written(home):
    _codex(home, "[[[broken")
    r = mcp_write.set_enabled("codex", "a", False)
    assert r["status"] == "failed"
    assert _read_codex(home) == "[[[broken"


def test_codex_is_idempotent(home):
    _codex(home, '[mcp_servers.a]\ncommand = "x"\n')
    mcp_write.set_enabled("codex", "a", False)
    snapshot = _read_codex(home)
    second = mcp_write.set_enabled("codex", "a", False)
    assert second["changed"] is False
    assert _read_codex(home) == snapshot


def test_codex_local_scope_writes_project_file(home, wt):
    local = Path(wt["path"]) / ".codex"
    local.mkdir(parents=True, exist_ok=True)
    (local / "config.toml").write_text('[mcp_servers.p]\ncommand = "x"\n')
    _codex(home, "")
    r = mcp_write.set_enabled("codex", "p", False, scope="local", worktree_id="wt1")
    assert r["status"] == "ok"
    assert "enabled = false" in (local / "config.toml").read_text()
    # 전역 파일은 손대지 않았다.
    assert _read_codex(home) == ""


def test_codex_local_without_worktree_is_refused(home):
    _codex(home, '[mcp_servers.a]\ncommand = "x"\n')
    r = mcp_write.set_enabled("codex", "a", False, scope="local")
    assert r["status"] == "failed"


# ------------------------------------------------------------- 텍스트 수술 단위

def test_toml_surgery_reports_when_server_absent():
    text = '[mcp_servers.a]\ncommand = "x"\n'
    out, found = mcp_write.toml_set_enabled(text, "zzz", False)
    assert found is False and out == text


def test_toml_surgery_keeps_trailing_content():
    text = '[mcp_servers.a]\ncommand = "x"\n\n[other]\nkeep = 1\n'
    out, _ = mcp_write.toml_set_enabled(text, "a", False)
    assert out.endswith("[other]\nkeep = 1\n")


def test_unknown_tool_is_rejected(home):
    r = mcp_write.set_enabled("nope", "x", False)
    assert r["status"] == "failed" and "모르는 도구" in r["reason"]


# ── 가져오기 (97번 3단계, §2) ──────────────────────────────────────────────
#
# 이 블록이 지키는 단 하나: **값이 CLI 설정 파일에 절대 안 들어간다.**
# `.mcp.json`은 git에 커밋되는 파일이고, 거기 평문 키가 박히는 것이
# 레퍼런스(agent-deck)가 실제로 밟고 있는 사고 경로다.

def test_deploy_writes_a_reference_not_the_value(home, tmp_path):
    import mcp_write
    (home / ".claude.json").write_text("{}")
    out = mcp_write.deploy(
        "notion", {"command": "npx", "env": {"TOKEN": "sk-real-secret"}},
        tool="claude", scope="global",
        env_map={"env": {"TOKEN": "FSH_MCP_NOTION_TOKEN"}},
    )
    assert out["status"] == "ok"
    text = (home / ".claude.json").read_text()
    assert "sk-real-secret" not in text, "값이 설정 파일에 들어갔다"
    assert "${FSH_MCP_NOTION_TOKEN}" in text


def test_deploy_refuses_when_a_value_would_remain(home):
    """매핑에서 빠진 칸이 있으면 **파일을 열지도 않는다.**"""
    import mcp_write
    (home / ".claude.json").write_text("{}")
    before = (home / ".claude.json").read_text()
    out = mcp_write.deploy(
        "notion", {"command": "npx", "env": {"A": "sk-a", "B": "sk-b"}},
        tool="claude", scope="global", env_map={"env": {"A": "V_A"}},
    )
    assert out["status"] == "failed"
    assert "env.B" in out["reason"]
    assert (home / ".claude.json").read_text() == before


def test_deploy_to_codex_is_refused_with_the_official_command(home):
    """config.toml에 새 테이블을 텍스트 수술로 만드는 건 위험이 다르다."""
    import mcp_write
    out = mcp_write.deploy("n", {"command": "x"}, tool="codex", scope="global")
    assert out["status"] == "failed"
    assert "codex mcp add" in out["reason"]


def test_deploy_into_shared_mcp_json_still_carries_no_value(home, tmp_path):
    """저장소에 커밋되는 파일이라 여기가 가장 위험하다."""
    import mcp_write
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    out = mcp_write.deploy(
        "notion", {"command": "npx", "env": {"TOKEN": "sk-real-secret"}},
        tool="claude", scope="local", worktree_path=str(repo), shared=True,
        env_map={"env": {"TOKEN": "FSH_MCP_NOTION_TOKEN"}},
    )
    assert out["status"] == "ok"
    text = (repo / ".mcp.json").read_text()
    assert "sk-real-secret" not in text
    assert "${FSH_MCP_NOTION_TOKEN}" in text


def test_deploy_records_a_ref_for_later_revocation(home, tmp_path):
    import importlib

    import mcp_catalog
    importlib.reload(mcp_catalog)   # home 픽스처가 세운 VT_STATE_DIR를 집게 한다
    import mcp_write
    (home / ".claude.json").write_text("{}")
    mcp_write.deploy("notion", {"command": "npx", "env": {"TOKEN": "sk"}},
                     tool="claude", scope="global",
                     env_map={"env": {"TOKEN": "FSH_MCP_NOTION_TOKEN"}})
    refs = mcp_catalog.list_refs()
    assert [r["env"] for r in refs] == ["FSH_MCP_NOTION_TOKEN"]
    assert refs[0]["server"] == "notion"


def test_deploy_is_idempotent(home):
    import mcp_write
    (home / ".claude.json").write_text("{}")
    args = dict(tool="claude", scope="global",
                env_map={"env": {"TOKEN": "FSH_MCP_NOTION_TOKEN"}})
    defn = {"command": "npx", "env": {"TOKEN": "sk"}}
    assert mcp_write.deploy("notion", defn, **args)["changed"] is True
    assert mcp_write.deploy("notion", defn, **args)["changed"] is False


def test_deploy_does_not_disturb_other_keys(home):
    import mcp_write
    (home / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"keep": {"command": "old"}}, "somethingElse": {"a": 1}}))
    mcp_write.deploy("notion", {"command": "npx"}, tool="claude", scope="global")
    data = json.loads((home / ".claude.json").read_text())
    assert data["somethingElse"] == {"a": 1}
    assert data["mcpServers"]["keep"] == {"command": "old"}
    assert data["mcpServers"]["notion"] == {"command": "npx"}


# ── opencode 쓰기 (97번 4단계) ─────────────────────────────────────────────

@pytest.fixture
def oc_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_OPENCODE_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vtstate"))
    p = tmp_path / "cfg" / "opencode"
    p.mkdir(parents=True)
    return p / "opencode.json"


def test_opencode_toggle_sets_enabled_false(oc_home):
    import mcp_write
    oc_home.write_text(json.dumps({"mcp": {"a": {"command": "x"}}}))
    out = mcp_write.set_enabled("opencode", "a", False)
    assert out["status"] == "ok" and out["changed"] is True
    assert json.loads(oc_home.read_text())["mcp"]["a"]["enabled"] is False


def test_opencode_toggle_is_idempotent(oc_home):
    import mcp_write
    oc_home.write_text(json.dumps({"mcp": {"a": {"command": "x"}}}))
    assert mcp_write.set_enabled("opencode", "a", False)["changed"] is True
    assert mcp_write.set_enabled("opencode", "a", False)["changed"] is False


def test_opencode_toggle_refuses_an_unknown_server(oc_home):
    import mcp_write
    oc_home.write_text(json.dumps({"mcp": {}}))
    out = mcp_write.set_enabled("opencode", "missing", False)
    assert out["status"] == "failed" and "정의가 없다" in out["reason"]


def test_opencode_toggle_leaves_other_settings_alone(oc_home):
    """opencode.json에는 MCP 말고도 사용자의 설정이 들어 있다."""
    import mcp_write
    oc_home.write_text(json.dumps({"theme": "dark", "mcp": {"a": {"command": "x"}}}))
    mcp_write.set_enabled("opencode", "a", False)
    assert json.loads(oc_home.read_text())["theme"] == "dark"


def test_opencode_deploy_uses_its_own_reference_syntax(oc_home):
    """`${VAR}`를 심으면 opencode에서 **조용히 안 먹는다**(§0-3)."""
    import mcp_write
    out = mcp_write.deploy("notion", {"command": "npx", "env": {"TOKEN": "sk-real-secret"}},
                           tool="opencode", scope="global",
                           env_map={"env": {"TOKEN": "FSH_MCP_NOTION_TOKEN"}})
    assert out["status"] == "ok"
    text = oc_home.read_text()
    assert "sk-real-secret" not in text
    assert "{env:FSH_MCP_NOTION_TOKEN}" in text
    assert "${" not in text


def test_oauth_servers_are_never_deployed(home):
    """§2-5 — 만료·갱신·audience 제약이 있어 옮겨봐야 조용히 깨진다."""
    import mcp_write
    (home / ".claude.json").write_text("{}")
    for defn in ({"url": "https://x", "oauth": {"client_id": "c"}},
                 {"url": "https://x", "auth": {"oauth": {}}}):
        out = mcp_write.deploy("n", defn, tool="claude", scope="global")
        assert out["status"] == "failed"
        assert "OAuth" in out["reason"]
    assert (home / ".claude.json").read_text() == "{}"


# ── 플러그인 (97번 4단계) ──────────────────────────────────────────────────

def test_plugin_toggle_flips_an_installed_plugin(home, wt):
    import mcp_write
    _claude(home, {"enabledPlugins": {"p@market": True}})
    out = mcp_write.set_plugin_enabled("p@market", False)
    assert out["status"] == "ok" and out["changed"] is True
    assert _read_claude(home)["enabledPlugins"]["p@market"] is False


def test_plugin_toggle_refuses_an_uninstalled_plugin(home, wt):
    """미설치 플러그인은 값만 바꿔도 안 켜진다 — 켠 척하면 안 된다(§0-2)."""
    import mcp_write
    _claude(home, {"enabledPlugins": {}})
    out = mcp_write.set_plugin_enabled("nope@market", True)
    assert out["status"] == "failed"
    assert "설치되지 않은" in out["reason"]


def test_plugin_toggle_is_idempotent(home, wt):
    import mcp_write
    _claude(home, {"enabledPlugins": {"p@market": True}})
    assert mcp_write.set_plugin_enabled("p@market", False)["changed"] is True
    assert mcp_write.set_plugin_enabled("p@market", False)["changed"] is False


def test_plugin_local_scope_writes_into_the_project_block(home, wt):
    import mcp_write
    _claude(home, {"projects": {wt["path"]: {"enabledPlugins": {"p@m": True}}}})
    out = mcp_write.set_plugin_enabled("p@m", False, scope="local", worktree_id="wt1")
    assert out["status"] == "ok"
    assert _read_claude(home)["projects"][wt["path"]]["enabledPlugins"]["p@m"] is False


def test_plugin_toggle_does_not_disturb_mcp_settings(home, wt):
    import mcp_write
    _claude(home, {"enabledPlugins": {"p@m": True},
                   "mcpServers": {"keep": {"command": "x"}}})
    mcp_write.set_plugin_enabled("p@m", False)
    assert _read_claude(home)["mcpServers"]["keep"] == {"command": "x"}


def test_plugin_toggle_for_other_tools_is_refused(home):
    import mcp_write
    out = mcp_write.set_plugin_enabled("p", True, tool="codex")
    assert out["status"] == "failed"
