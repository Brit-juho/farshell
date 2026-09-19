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
