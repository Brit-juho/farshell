"""97번 1단계 1/n — 도구별 MCP 설정 읽기 어댑터.

이 테스트의 핵심 불변식 두 개:
1. **값은 어떤 경로로도 결과에 담기지 않는다**(97번 §2-3). env/헤더의 실제
   값이 정규화 결과에 나타나면 그건 곧 터널 너머로 키가 새는 것이다.
2. **모르면 꺼졌다고 말하지 않는다** — 파싱 실패·미지 스키마를 "없음/꺼짐"으로
   뭉개면 다음 단계(쓰기)가 설정을 통째로 날린다(agent-deck #1956).
"""

from __future__ import annotations

import json

import pytest

import mcp_adapters


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_MCP_HOME", str(tmp_path))
    monkeypatch.delenv("VT_CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".gemini").mkdir()
    return tmp_path


def _write_claude(home, data):
    (home / ".claude.json").write_text(json.dumps(data))


# ------------------------------------------------------------- 값 유출 방지

def test_claude_env_values_never_appear_in_output(home):
    _write_claude(home, {"mcpServers": {"notion": {
        "command": "npx", "env": {"NOTION_TOKEN": "sk-super-secret-value"}}}})
    result = mcp_adapters.scan_claude()
    blob = json.dumps(result)
    assert "sk-super-secret-value" not in blob
    field = result["servers"][0]["env"][0]
    assert field["key"] == "NOTION_TOKEN"      # 이름은 보여도 된다
    assert field["literal"] is True            # 값이 박혀 있다는 사실만
    assert "value" not in field


def test_claude_header_values_never_appear(home):
    _write_claude(home, {"mcpServers": {"x": {
        "url": "https://e.example", "headers": {"Authorization": "Bearer leak-me"}}}})
    assert "leak-me" not in json.dumps(mcp_adapters.scan_claude())


def test_reference_reports_variable_name_not_value(home):
    _write_claude(home, {"mcpServers": {"n": {
        "command": "x", "env": {"TOKEN": "${NOTION_TOKEN}"}}}})
    field = mcp_adapters.scan_claude()["servers"][0]["env"][0]
    assert field["ref"] == "NOTION_TOKEN"
    assert field["literal"] is False


def test_reference_with_default_is_still_a_reference(home):
    _write_claude(home, {"mcpServers": {"n": {"command": "x", "env": {"T": "${VAR:-fallback}"}}}})
    assert mcp_adapters.scan_claude()["servers"][0]["env"][0]["ref"] == "VAR"


# ------------------------------------------------------ 파싱 실패는 삼키지 않는다

def test_broken_json_is_reported_not_swallowed(home):
    (home / ".claude.json").write_text("{ this is not json")
    result = mcp_adapters.scan_claude()
    assert result["servers"] == []
    assert result["errors"][0]["reason"] == "JSON 파싱 실패"


def test_null_root_is_rejected(home):
    """json.loads("null")은 조용히 성공한다 — 빈 설정으로 오해하면 안 된다."""
    (home / ".claude.json").write_text("null")
    assert mcp_adapters.scan_claude()["errors"][0]["reason"] == "최상위가 객체가 아님"


def test_missing_file_is_not_an_error(home):
    result = mcp_adapters.scan_claude()
    assert result["servers"] == [] and result["errors"] == []


def test_mcpservers_as_empty_list_is_tolerated(home):
    """실측: 비어 있을 때 {} 가 아니라 [] 로 들어 있는 경우가 있다."""
    _write_claude(home, {"projects": {"/w": {"mcpServers": []}}, "mcpServers": []})
    assert mcp_adapters.scan_claude("/w", "wt1")["servers"] == []


# ----------------------------------------------------------- claude 스코프/토글

def test_claude_disabled_list_turns_server_off_for_that_project(home):
    _write_claude(home, {
        "mcpServers": {"github": {"command": "x"}},
        "projects": {"/w": {"disabledMcpServers": ["github"]}},
    })
    assert mcp_adapters.scan_claude("/w", "wt1")["servers"][0]["enabled"] is False
    # 다른 워크트리에선 그대로 켜져 있다 — 토글이 프로젝트별이라는 사실 그대로.
    assert mcp_adapters.scan_claude("/other", "wt2")["servers"][0]["enabled"] is True


def test_claude_global_without_worktree_says_state_is_unknown(home):
    _write_claude(home, {"mcpServers": {"github": {"command": "x"}}})
    s = mcp_adapters.scan_claude()["servers"][0]
    assert s["enabled_basis"] == "unknown"
    assert any("프로젝트마다" in n for n in s["notes"])


def test_mcp_json_needs_project_approval(home, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".mcp.json").write_text(json.dumps({"mcpServers": {"shared": {"command": "x"}}}))
    _write_claude(home, {"projects": {str(wt): {}}})

    s = [x for x in mcp_adapters.scan_claude(str(wt), "wt1")["servers"] if x["shared"]][0]
    assert s["pending_approval"] is True
    assert s["enabled"] is False
    assert any("승인" in n for n in s["notes"])


def test_mcp_json_approved_is_enabled(home, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".mcp.json").write_text(json.dumps({"mcpServers": {"shared": {"command": "x"}}}))
    _write_claude(home, {"projects": {str(wt): {"enabledMcpjsonServers": ["shared"]}}})
    s = [x for x in mcp_adapters.scan_claude(str(wt), "wt1")["servers"] if x["shared"]][0]
    assert s["enabled"] is True and s["pending_approval"] is False


def test_literal_secret_in_committed_file_is_warned(home, tmp_path):
    """`.mcp.json`은 저장소에 커밋되는 파일 — 평문 키가 있으면 경고해야 한다."""
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".mcp.json").write_text(json.dumps({"mcpServers": {"s": {
        "command": "x", "env": {"KEY": "literal-secret"}}}}))
    _write_claude(home, {})
    s = [x for x in mcp_adapters.scan_claude(str(wt), "wt1")["servers"] if x["shared"]][0]
    assert any("커밋" in n for n in s["notes"])
    assert "literal-secret" not in json.dumps(s)


# ------------------------------------------------------------------- codex

def _write_codex(home, text):
    (home / ".codex" / "config.toml").write_text(text)


def test_codex_enabled_false_is_read(home):
    _write_codex(home, '[mcp_servers.a]\ncommand = "x"\nenabled = false\n')
    assert mcp_adapters.scan_codex()["servers"][0]["enabled"] is False


def test_codex_default_is_enabled(home):
    _write_codex(home, '[mcp_servers.a]\ncommand = "x"\n')
    assert mcp_adapters.scan_codex()["servers"][0]["enabled"] is True


def test_codex_env_vars_names_are_surfaced(home):
    """`env_vars`는 이름만 적는 공식 방식 — 시크릿이 파일에 안 남는다."""
    _write_codex(home, '[mcp_servers.a]\ncommand = "x"\nenv_vars = ["MY_TOKEN"]\n')
    assert mcp_adapters.scan_codex()["servers"][0]["env_vars"] == ["MY_TOKEN"]


def test_codex_dollar_brace_is_flagged_as_dead_reference(home):
    """codex는 확장하지 않는다 — `${VAR}`를 쓰면 리터럴로 전달돼 조용히 깨진다."""
    _write_codex(home, '[mcp_servers.a]\ncommand = "x"\n[mcp_servers.a.env]\nT = "${MY_TOKEN}"\n')
    s = mcp_adapters.scan_codex()["servers"][0]
    assert s["env"][0]["literal"] is True
    assert any("확장하지 않는다" in n for n in s["notes"])


def test_codex_untrusted_project_is_flagged(home, tmp_path):
    wt = tmp_path / "wt"
    (wt / ".codex").mkdir(parents=True)
    (wt / ".codex" / "config.toml").write_text('[mcp_servers.local]\ncommand = "x"\n')
    _write_codex(home, "")
    s = [x for x in mcp_adapters.scan_codex(str(wt), "wt1")["servers"] if x["scope"] == "local"][0]
    assert any("신뢰 등록" in n for n in s["notes"])


def test_codex_trusted_project_has_no_trust_warning(home, tmp_path):
    wt = tmp_path / "wt"
    (wt / ".codex").mkdir(parents=True)
    (wt / ".codex" / "config.toml").write_text('[mcp_servers.local]\ncommand = "x"\n')
    _write_codex(home, f'[projects."{wt}"]\ntrust_level = "trusted"\n')
    s = [x for x in mcp_adapters.scan_codex(str(wt), "wt1")["servers"] if x["scope"] == "local"][0]
    assert not any("신뢰 등록" in n for n in s["notes"])


def test_broken_toml_is_reported(home):
    _write_codex(home, "[[[not toml")
    assert mcp_adapters.scan_codex()["errors"][0]["reason"] == "TOML 파싱 실패"


# ------------------------------------------------------------------ gemini

def test_gemini_reads_global_and_local(home, tmp_path):
    (home / ".gemini" / "settings.json").write_text(
        json.dumps({"mcpServers": {"g": {"command": "x"}}}))
    wt = tmp_path / "wt"
    (wt / ".gemini").mkdir(parents=True)
    (wt / ".gemini" / "settings.json").write_text(
        json.dumps({"mcpServers": {"l": {"command": "y"}}}))
    scopes = {s["name"]: s["scope"] for s in mcp_adapters.scan_gemini(str(wt), "wt1")["servers"]}
    assert scopes == {"g": "global", "l": "local"}


def test_gemini_enablement_file_turns_server_off(home):
    (home / ".gemini" / "settings.json").write_text(
        json.dumps({"mcpServers": {"g": {"command": "x"}}}))
    (home / ".gemini" / "mcp-server-enablement.json").write_text(json.dumps({"g": False}))
    assert mcp_adapters.scan_gemini()["servers"][0]["enabled"] is False


def test_gemini_unknown_enablement_schema_defaults_to_enabled(home):
    """스키마를 확인하지 못한 파일이다 — 못 알아보면 '꺼짐'으로 단정하지 않는다."""
    (home / ".gemini" / "settings.json").write_text(
        json.dumps({"mcpServers": {"g": {"command": "x"}}}))
    (home / ".gemini" / "mcp-server-enablement.json").write_text(
        json.dumps({"g": {"something": "else"}}))
    assert mcp_adapters.scan_gemini()["servers"][0]["enabled"] is True


# -------------------------------------------------------------------- 공통

def test_transport_is_classified(home):
    _write_claude(home, {"mcpServers": {
        "s": {"command": "x"}, "h": {"url": "https://e.example"}, "u": {}}})
    kinds = {s["name"]: s["transport"] for s in mcp_adapters.scan_claude()["servers"]}
    assert kinds == {"s": "stdio", "h": "http", "u": "unknown"}
