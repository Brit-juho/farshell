"""97번 1단계 1/n — 워크트리 기준 MCP 설정 수집(mcp_scan).

`scan()`은 아무것도 저장하지 않고 매번 실제 파일을 읽는다 — 그래서 "우리
기록과 실제 파일이 어긋난다"는 상태가 존재할 수 없다는 게 설계의 전제다
(97번 §1-1). 이 테스트는 그 전제와, 도구별 사실(`facts`)이 화면까지
전달되는지를 고정한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mcp_scan


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_MCP_HOME", str(tmp_path))
    monkeypatch.delenv("VT_CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr("codex_cli.run_json", lambda args, **kwargs: ([], None))
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".gemini").mkdir()
    return tmp_path


@pytest.fixture
def wt(tmp_path, monkeypatch):
    """worktree.list_worktrees를 가짜로 — 실제 git 탐색에 의존하지 않는다."""
    path = tmp_path / "repo"
    path.mkdir()
    entry = {"id": "wt1", "path": str(path), "repoName": "repo", "branch": "feat/x"}
    monkeypatch.setattr("worktree.list_worktrees", lambda force=False: [entry])
    return entry


def test_scan_without_worktree_reads_global_only(home, wt):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"g": {"command": "x"}}}))
    result = mcp_scan.scan()
    assert result["worktree"] is None
    assert [s["scope"] for s in result["servers"]] == ["global"]


def test_scan_with_worktree_includes_local(home, wt):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"g": {"command": "x"}}}))
    (Path(wt["path"]) / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"l": {"command": "y"}}}))
    result = mcp_scan.scan("wt1")
    assert result["worktree"]["label"] == "repo/feat/x"
    assert {s["name"] for s in result["servers"]} == {"g", "l"}


def test_unknown_worktree_id_falls_back_to_global(home, wt):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"g": {"command": "x"}}}))
    result = mcp_scan.scan("nope")
    assert result["worktree"] is None


def test_facts_are_included_for_each_tool(home, wt):
    facts = mcp_scan.scan()["facts"]
    assert set(facts) == {"claude", "codex", "agy", "opencode"}
    assert facts["claude"]["hot_reload"] is False
    assert facts["codex"]["hot_reload"] is False


def test_agy_fact_admits_what_is_unverified(home, wt):
    """agy는 반영 시점을 확인하지 못했다 — False로 단정하면 "즉시 안 된다"는
    거짓 정보를, True로 단정하면 "껐으니 안전하다"는 더 나쁜 거짓을 준다."""
    g = mcp_scan.scan()["facts"]["agy"]
    assert g["hot_reload"] is None
    assert g["off_is_immediate"] is None
    assert g["scopes"] == ["global"]


def test_unverified_tools_report_unknown_not_false(home, wt):
    """claude/codex는 on→off 즉시성이 공식 문서에 없다 — 모르는 걸 False로
    단정하면 '안전하다'는 잘못된 인상을 준다."""
    facts = mcp_scan.scan()["facts"]
    assert facts["claude"]["off_is_immediate"] is None
    assert facts["codex"]["off_is_immediate"] is None


def test_errors_from_multiple_tools_are_collected(home, wt):
    (home / ".claude.json").write_text("{broken")
    (home / ".codex" / "config.toml").write_text("[[[broken")
    sources = {e["reason"] for e in mcp_scan.scan()["errors"]}
    assert sources == {"JSON 파싱 실패", "TOML 파싱 실패"}


def test_tools_filter_limits_scan(home, wt):
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"g": {"command": "x"}}}))
    result = mcp_scan.scan(tools=("codex",))
    assert result["servers"] == []
    assert set(result["facts"]) == {"codex"}


def test_codex_auth_status_is_merged_without_credentials(home, monkeypatch):
    (home / ".codex" / "config.toml").write_text(
        '[mcp_servers.needs_login]\nurl = "https://example.invalid/mcp"\n')
    monkeypatch.setattr("codex_cli.run_json", lambda args, **kwargs: ([{
        "name": "needs_login", "enabled": True, "auth_status": "not_logged_in",
    }], None))
    server = mcp_scan.scan(tools=("codex",))["servers"][0]
    assert server["auth_status"] == "not_logged_in"
    assert any("codex mcp login needs_login" in note for note in server["notes"])


def test_codex_auth_probe_failure_keeps_server_visible(home, monkeypatch):
    (home / ".codex" / "config.toml").write_text(
        '[mcp_servers.keep]\ncommand = "x"\n')
    monkeypatch.setattr("codex_cli.run_json", lambda args, **kwargs: (None, "timeout"))
    result = mcp_scan.scan(tools=("codex",))
    assert [s["name"] for s in result["servers"]] == ["keep"]
    assert result["errors"][-1]["reason"] == "timeout"


def test_group_by_name_keeps_scopes_separate(home):
    servers = [
        {"name": "ctx", "scope": "global", "tool": "claude"},
        {"name": "ctx", "scope": "local", "tool": "claude"},
        {"name": "abc", "scope": "global", "tool": "codex"},
    ]
    groups = mcp_scan.group_by_name(servers)
    assert [g["name"] for g in groups] == ["abc", "ctx"]
    assert len(groups[1]["entries"]) == 2


def test_scan_never_leaks_values_end_to_end(home, wt):
    """어댑터 단위로도 막지만, 라우트가 그대로 내보내는 최종 형태에서도 확인한다."""
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"n": {
        "command": "x", "env": {"TOKEN": "top-secret-xyz"}}}}))
    assert "top-secret-xyz" not in json.dumps(mcp_scan.scan("wt1"))


# ADR-48 — `codex plugin list --json`은 원격 조회로 19초가 걸려 10초 제한에 매번 걸렸다.
def test_codex_plugin_list_is_cached_and_single_flight(monkeypatch):
    import codex_cli
    calls = []

    def fake(args, **kwargs):
        calls.append(kwargs.get("timeout"))
        return {"installed": [{"pluginId": "p@m", "enabled": True}]}, None
    monkeypatch.setattr("codex_cli.run_json", fake)
    a, _ = codex_cli.plugin_list()
    b, _ = codex_cli.plugin_list()
    assert a is b and len(calls) == 1, "두 번째는 캐시에서"
    assert calls[0] >= 30, "원격 조회가 끝날 시간을 준다"
    codex_cli.plugin_list(refresh=True)
    assert len(calls) == 2, "새로고침은 캐시를 버린다"


def test_codex_plugin_list_keeps_stale_result_on_failure(monkeypatch):
    import codex_cli
    monkeypatch.setattr("codex_cli.run_json", lambda args, **k: ({"installed": [{"pluginId": "p@m"}]}, None))
    codex_cli.plugin_list()
    monkeypatch.setattr("codex_cli.run_json", lambda args, **k: (None, "Codex CLI 응답 시간이 초과됐습니다"))
    data, err = codex_cli.plugin_list(refresh=True)
    assert err is None and data["installed"][0]["pluginId"] == "p@m"


def test_codex_plugin_failure_is_not_cached(monkeypatch):
    import codex_cli
    monkeypatch.setattr("codex_cli.run_json", lambda args, **k: (None, "timeout"))
    assert codex_cli.plugin_list()[1] == "timeout"
    monkeypatch.setattr("codex_cli.run_json", lambda args, **k: ({"installed": []}, None))
    assert codex_cli.plugin_list() == ({"installed": []}, None)


# ── 포커스된 칸 기준(session:<이름>) ───────────────────────────────────────

def _tmux_cwd(monkeypatch, cwd, want="=dev:"):
    def fake(args, timeout=2.0):
        assert args[:4] == ["display-message", "-p", "-t", want]
        return f"{cwd}\n" if cwd else None
    monkeypatch.setattr("tmux_runner.run_text", fake)


def test_session_context_climbs_to_git_root_and_reuses_known_worktree(home, wt, monkeypatch):
    import subprocess
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    subprocess.run(["git", "init", "-q", wt["path"]], check=True)
    sub = Path(wt["path"]) / "a" / "b"
    sub.mkdir(parents=True)
    _tmux_cwd(monkeypatch, str(sub))
    got = mcp_scan.find_worktree("session:dev")
    assert got["id"] == "wt1" and got["session"] == "dev"


def test_session_context_outside_roots_falls_back_to_global(home, wt, monkeypatch, tmp_path_factory):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home / "repo"))
    outside = tmp_path_factory.mktemp("outside")
    _tmux_cwd(monkeypatch, str(outside))
    assert mcp_scan.find_worktree("session:dev") is None


def test_session_context_unknown_dir_gets_session_id(home, wt, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    plain = home / "plain"
    plain.mkdir()
    _tmux_cwd(monkeypatch, str(plain))
    got = mcp_scan.find_worktree("session:dev")
    assert got["id"] == "session:dev" and got["path"] == str(plain.resolve())
    (home / ".claude.json").write_text("{}")
    (plain / ".mcp.json").write_text(json.dumps({"mcpServers": {"p": {"command": "x"}}}))
    result = mcp_scan.scan("session:dev", tools=("claude",))
    assert result["worktree"]["session"] == "dev"
    assert [s["level"] for s in result["servers"]] == ["project"]


def test_session_missing_pane_is_global_only(home, wt, monkeypatch):
    _tmux_cwd(monkeypatch, None)
    assert mcp_scan.find_worktree("session:dev") is None


def test_level_separates_claude_local_from_project(home, wt):
    (home / ".claude.json").write_text(json.dumps({
        "mcpServers": {"u": {"command": "x"}},
        "projects": {wt["path"]: {"mcpServers": {"l": {"command": "y"}}}},
    }))
    (Path(wt["path"]) / ".mcp.json").write_text(json.dumps({"mcpServers": {"p": {"command": "z"}}}))
    by = {s["name"]: s for s in mcp_scan.scan("wt1", tools=("claude",))["servers"]}
    assert (by["u"]["level"], by["l"]["level"], by["p"]["level"]) == ("user", "local", "project")
    assert by["l"]["scope"] == by["p"]["scope"] == "local"  # 쓰기 기준은 그대로


def test_session_id_is_mapped_to_its_tmux_name(home, wt, monkeypatch):
    """화면은 FarShell 세션 id를 보낸다 — tmux 이름은 서버의 세션 저장소가 안다."""
    from deps import session_store
    session_store.add("sid-1", name="x")
    session_store.update_tmux_name("sid-1", "dev")
    try:
        monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
        plain = home / "plain2"
        plain.mkdir()
        _tmux_cwd(monkeypatch, str(plain))
        got = mcp_scan.find_worktree("session:sid-1")
        assert got["id"] == "session:sid-1" and got["session"] == "dev"
    finally:
        session_store.remove("sid-1")
