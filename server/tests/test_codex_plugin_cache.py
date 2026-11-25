"""codex 플러그인 목록 조회: 목록 한 번을 계산하는 동안(failure_scope)에만 실패를 기억한다."""

import codex_cli


def _fail(calls):
    def fake(args, timeout=10.0):
        calls.append(args)
        return None, "Codex CLI 응답 시간이 초과됐습니다"
    return fake


def test_outside_scope_failure_is_not_remembered(monkeypatch):
    """범위 밖은 예전 그대로 — 실패를 굳히지 않는다(test_mcp_scan 의 계약)."""
    codex_cli.reset_plugin_cache()
    calls = []
    monkeypatch.setattr(codex_cli, "run_json", _fail(calls))
    codex_cli.plugin_list()
    codex_cli.plugin_list()
    assert len(calls) == 2


def test_inside_scope_failure_is_not_repeated(monkeypatch):
    """작업 폴더 25곳이 같은 실패를 30초씩 25번 기다리던 문제."""
    codex_cli.reset_plugin_cache()
    calls = []
    monkeypatch.setattr(codex_cli, "run_json", _fail(calls))
    with codex_cli.failure_scope():
        results = [codex_cli.plugin_list() for _ in range(25)]
    assert len(calls) == 1
    assert all(err == "Codex CLI 응답 시간이 초과됐습니다" for _, err in results)


def test_scope_memory_is_shared_across_separate_scopes_until_ttl(monkeypatch):
    """목록은 여러 페이지 요청으로 오므로 범위가 달라도 TTL 안이면 같은 실패를 쓴다."""
    codex_cli.reset_plugin_cache()
    calls = []
    monkeypatch.setattr(codex_cli, "run_json", _fail(calls))
    with codex_cli.failure_scope():
        codex_cli.plugin_list()
    with codex_cli.failure_scope():
        codex_cli.plugin_list()
    assert len(calls) == 1
    monkeypatch.setattr(codex_cli, "SCOPED_FAIL_TTL_SEC", 0.0)
    with codex_cli.failure_scope():
        codex_cli.plugin_list()
    assert len(calls) == 2


def test_refresh_and_success_bypass_the_memory(monkeypatch):
    codex_cli.reset_plugin_cache()
    answers = [(None, "boom"), ({"installed": []}, None)]
    calls = []

    def fake(args, timeout=10.0):
        calls.append(args)
        return answers.pop(0)

    monkeypatch.setattr(codex_cli, "run_json", fake)
    with codex_cli.failure_scope():
        assert codex_cli.plugin_list()[1] == "boom"
        data, err = codex_cli.plugin_list(refresh=True)
        assert err is None and data == {"installed": []}
        assert codex_cli.plugin_list() == ({"installed": []}, None)
    assert len(calls) == 2
    codex_cli.reset_plugin_cache()
