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
