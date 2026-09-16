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


# --------------------------------------------------------------------- agy

# agy(Antigravity CLI)는 구 Gemini CLI와 다른 제품이다 — 2026-09-16 격리된
# HOME에서 agy 1.1.27을 직접 돌려 확인한 구조만 여기에 고정한다.

def _write_agy(home, data):
    d = home / ".gemini" / "config"
    d.mkdir(parents=True, exist_ok=True)
    (d / "mcp_config.json").write_text(json.dumps(data))


def test_agy_reads_global_config(home):
    _write_agy(home, {"mcpServers": {"notion": {"command": "npx", "type": "stdio"}}})
    s = mcp_adapters.scan_agy()["servers"]
    assert [(x["name"], x["scope"]) for x in s] == [("notion", "global")]


def test_agy_missing_disabled_key_means_enabled(home):
    """`agy mcp enable`은 키를 false로 두지 않고 **지운다** — 없으면 켜진 것."""
    _write_agy(home, {"mcpServers": {"n": {"command": "x"}}})
    assert mcp_adapters.scan_agy()["servers"][0]["enabled"] is True


def test_agy_disabled_true_means_off(home):
    _write_agy(home, {"mcpServers": {"n": {"command": "x", "disabled": True}}})
    assert mcp_adapters.scan_agy()["servers"][0]["enabled"] is False


def test_agy_disabled_false_means_on(home):
    """`agy mcp add`가 처음 쓸 때는 disabled:false를 넣는다(실측)."""
    _write_agy(home, {"mcpServers": {"n": {"command": "x", "disabled": False}}})
    assert mcp_adapters.scan_agy()["servers"][0]["enabled"] is True


def test_agy_ignores_worktree_because_it_has_no_project_scope(home, tmp_path):
    """프로젝트 디렉토리에 설정을 둬도 agy는 읽지 않는다(실측 확인)."""
    _write_agy(home, {"mcpServers": {"g": {"command": "x"}}})
    wt = tmp_path / "wt"
    (wt / ".gemini" / "config").mkdir(parents=True)
    (wt / ".gemini" / "config" / "mcp_config.json").write_text(
        json.dumps({"mcpServers": {"projonly": {"command": "y"}}}))
    names = [s["name"] for s in mcp_adapters.scan_agy(str(wt), "wt1")["servers"]]
    assert names == ["g"]


def test_agy_reference_is_marked_unverified(home):
    """agy가 `${VAR}`를 확장하는지는 확인하지 못했다 — 확장된다고도,
    안 된다고도 단정하지 않는다."""
    _write_agy(home, {"mcpServers": {"n": {"command": "x", "env": {"T": "${TOK}"}}}})
    s = mcp_adapters.scan_agy()["servers"][0]
    assert s["env"][0]["ref"] == "TOK"
    assert s["env"][0]["ref_unverified"] is True
    assert any("확인되지 않았다" in n for n in s["notes"])


def test_agy_broken_json_is_reported(home):
    d = home / ".gemini" / "config"
    d.mkdir(parents=True, exist_ok=True)
    (d / "mcp_config.json").write_text("{broken")
    assert mcp_adapters.scan_agy()["errors"][0]["reason"] == "JSON 파싱 실패"


# -------------------------------------------------------------------- 공통

def test_transport_is_classified(home):
    _write_claude(home, {"mcpServers": {
        "s": {"command": "x"}, "h": {"url": "https://e.example"}, "u": {}}})
    kinds = {s["name"]: s["transport"] for s in mcp_adapters.scan_claude()["servers"]}
    assert kinds == {"s": "stdio", "h": "http", "u": "unknown"}


# ── 참조 쓰기 (97번 3단계) ─────────────────────────────────────────────────
#
# §0-3의 핵심: **문법을 통일하면 Codex에서 조용히 깨진다.** Codex는 값 자리의
# `${VAR}`를 확장하지 않고 리터럴 문자열 그대로 전달한다. 그래서 이 테스트들이
# 지키는 것은 "각 도구의 문법이 서로 다르게 유지되는 것" 자체다.

from pathlib import Path as _Path

import mcp_adapters as _a


def test_claude_writes_dollar_brace_in_the_value_slot():
    out = _a.apply_refs("claude", {"command": "npx", "env": {"TOKEN": "sk-real"}},
                        {"env": {"TOKEN": "FSH_MCP_N_TOKEN"}})
    assert out["env"]["TOKEN"] == "${FSH_MCP_N_TOKEN}"


def test_codex_removes_the_value_slot_and_lists_the_name():
    """값 자리를 남겨두면 그게 곧 평문 유출이다 — Codex는 확장하지 않으므로."""
    out = _a.apply_refs("codex", {"command": "npx", "env": {"TOKEN": "sk-real"}},
                        {"env": {"TOKEN": "FSH_MCP_N_TOKEN"}})
    assert "env" not in out, "codex 정의에 env 값 칸이 남았다"
    assert out["env_vars"] == ["FSH_MCP_N_TOKEN"]


def test_codex_never_gets_dollar_brace():
    out = _a.apply_refs("codex", {"env": {"A": "x"}}, {"env": {"A": "V"}})
    assert "${" not in json.dumps(out), "codex에 확장되지 않는 문법이 들어갔다"


def test_apply_refs_does_not_mutate_the_input():
    src = {"command": "npx", "env": {"TOKEN": "sk-real"}}
    _a.apply_refs("codex", src, {"env": {"TOKEN": "V"}})
    assert src["env"]["TOKEN"] == "sk-real"


def test_headers_are_covered_too():
    out = _a.apply_refs("claude", {"url": "https://x", "headers": {"Authorization": "Bearer sk"}},
                        {"headers": {"Authorization": "FSH_MCP_X_AUTH"}})
    assert out["headers"]["Authorization"] == "${FSH_MCP_X_AUTH}"


def test_codex_merges_into_existing_env_vars_without_duplicates():
    out = _a.apply_refs("codex", {"env_vars": ["OLD", "FSH_MCP_N_TOKEN"], "env": {"T": "x"}},
                        {"env": {"T": "FSH_MCP_N_TOKEN"}})
    assert out["env_vars"] == ["FSH_MCP_N_TOKEN", "OLD"]


def test_has_literal_secret_is_the_last_gate():
    assert _a.has_literal_secret({"env": {"TOKEN": "sk-real"}}) == ["env.TOKEN"]
    assert _a.has_literal_secret({"env": {"TOKEN": "${V}"}}) == []
    assert _a.has_literal_secret({"env": {"TOKEN": "$V"}}) == []


def test_unmapped_fields_keep_their_literal_value_and_are_caught():
    """매핑에서 빠진 칸은 값이 그대로 남는다 — 그걸 잡는 게 has_literal_secret다."""
    out = _a.apply_refs("claude", {"env": {"A": "sk-a", "B": "sk-b"}},
                        {"env": {"A": "V_A"}})
    assert _a.has_literal_secret(out) == ["env.B"]


# ── opencode (97번 4단계) ──────────────────────────────────────────────────
#
# opencode는 **고유 문법**을 쓴다: `{env:VAR}`. `${VAR}`는 동작하지 않는다.
# 문법을 하나로 뭉치면 opencode의 정상 참조가 "값이 파일에 박혔다"는 거짓
# 경고로 바뀌고, 반대로 안 되는 문법을 심어 조용히 깨진다.

def test_opencode_reads_its_own_reference_syntax():
    assert _a._classify_ref("{env:TOK}", style="brace_env") == ("TOK", False)


def test_dollar_brace_is_a_literal_for_opencode():
    """opencode에서 `${VAR}`는 확장되지 않는다 — 참조로 인정하면 거짓말이다."""
    assert _a._classify_ref("${TOK}", style="brace_env") == (None, True)


def test_opencode_writes_brace_env():
    out = _a.apply_refs("opencode", {"env": {"T": "sk-real"}}, {"env": {"T": "V"}})
    assert out["env"]["T"] == "{env:V}"
    assert "${" not in json.dumps(out)


def test_has_literal_secret_respects_the_tool_syntax():
    """도구를 안 넘기면 opencode의 정상 참조가 유출로 잡혀 배포가 영원히 막힌다."""
    defn = {"env": {"T": "{env:V}"}}
    assert _a.has_literal_secret(defn, tool="opencode") == []
    assert _a.has_literal_secret(defn, tool="claude") == ["env.T"]


def test_ref_syntax_per_tool():
    assert _a.ref_syntax("claude") == "dollar_brace"
    assert _a.ref_syntax("agy") == "dollar_brace"
    assert _a.ref_syntax("codex") == "name_only"
    assert _a.ref_syntax("opencode") == "brace_env"


def test_opencode_scan_reads_global_and_project(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_OPENCODE_HOME", str(tmp_path / "cfg"))
    g = tmp_path / "cfg" / "opencode" / "opencode.json"
    g.parent.mkdir(parents=True)
    g.write_text(json.dumps({"mcp": {"a": {"command": "x"},
                                     "off": {"command": "y", "enabled": False}}}))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "opencode.json").write_text(json.dumps({"mcp": {"b": {"command": "z"}}}))

    out = _a.scan_opencode(str(repo), "wt1")
    by = {s["name"]: s for s in out["servers"]}
    assert set(by) == {"a", "off", "b"}
    assert by["a"]["enabled"] is True and by["off"]["enabled"] is False
    assert by["b"]["scope"] == "local" and by["b"]["shared"] is True
    assert by["a"]["scope"] == "global"


def test_opencode_scan_never_leaks_values(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_OPENCODE_HOME", str(tmp_path / "cfg"))
    g = tmp_path / "cfg" / "opencode" / "opencode.json"
    g.parent.mkdir(parents=True)
    g.write_text(json.dumps({"mcp": {"a": {"command": "x",
                                           "env": {"T": "sk-should-never-appear"}}}}))
    out = _a.scan_opencode(None, None)
    assert "sk-should-never-appear" not in json.dumps(out, ensure_ascii=False)
    assert out["servers"][0]["env"][0]["key"] == "T"


def test_opencode_project_file_warns_about_committing_with_its_own_syntax():
    """공유 파일 경고가 `${VAR}`를 예시로 들면 opencode 사용자에겐 틀린 조언이다."""
    entry = _a._entry(name="n", tool="opencode", scope="local",
                      path=_Path("/repo/opencode.json"), enabled=True,
                      defn={"env": {"T": "sk-literal"}}, expands=True, shared=True)
    assert any("{env:VAR}" in n for n in entry["notes"])
