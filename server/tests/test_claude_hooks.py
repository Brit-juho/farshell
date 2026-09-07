"""A0 — server/claude_hooks.py (Claude Code 훅 멱등 등록기).

이 모듈은 **사용자의 전역 설정 파일**을 고친다. 그래서 테스트의 초점이
"등록이 되는가"보다 "남의 것을 안 망가뜨리는가"에 맞춰져 있다:
멱등성 · 남의 훅 보존 · 경로 이동 시 갱신(중복 아님) · 깨진 JSON에서 중단.
"""

import json

import pytest

import claude_hooks as ch


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """실제 ~/.claude/settings.json을 절대 건드리지 않도록 격리."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    return tmp_path / "settings.json"


def _cmds(data, event):
    out = []
    for g in data.get("hooks", {}).get(event, []):
        out += [h["command"] for h in g.get("hooks", [])]
    return out


def test_install_on_empty_config_registers_three_events(cfg):
    assert ch._cmd_install() == 0
    data = json.loads(cfg.read_text())
    for event, arg in ch.EVENTS.items():
        cmds = _cmds(data, event)
        assert len(cmds) == 1, f"{event}에 항목이 정확히 하나여야 한다: {cmds}"
        assert cmds[0].endswith(f"agent_hook.sh {arg}")
    # 도구 훅은 모든 도구를 봐야 하므로 matcher "*", Stop은 matcher 자체가 없다.
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "*"
    assert "matcher" not in data["hooks"]["Stop"][0]


def test_install_is_idempotent(cfg):
    ch._cmd_install()
    first = cfg.read_text()
    assert ch._cmd_install() == 0
    assert cfg.read_text() == first, "두 번째 실행이 파일을 바꾸면 안 된다"


def test_install_preserves_foreign_hooks(cfg):
    cfg.write_text(json.dumps({
        "model": "opus",
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "/home/x/other_stop.sh", "timeout": 10}]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/home/x/coach.sh"}]}],
        },
    }))
    ch._cmd_install()
    data = json.loads(cfg.read_text())

    assert data["model"] == "opus", "훅과 무관한 키는 그대로여야 한다"
    assert "/home/x/other_stop.sh" in _cmds(data, "Stop")
    assert _cmds(data, "UserPromptSubmit") == ["/home/x/coach.sh"]
    assert any("agent_hook.sh stop" in c for c in _cmds(data, "Stop"))
    # 남의 훅에 붙어 있던 timeout 같은 필드도 그대로 보존돼야 한다
    assert any(
        h.get("timeout") == 10
        for g in data["hooks"]["Stop"]
        for h in g["hooks"]
        if "other_stop" in h["command"]
    )


def test_direct_tts_hook_entry_is_replaced_not_duplicated(cfg):
    """`Stop → tts_hook.sh` 직접 등록은 `agent_hook.sh stop`으로 교체한다.

    agent_hook.sh stop이 내부에서 tts_hook.sh에 stdin을 위임하므로, 둘 다
    남겨두면 TTS가 두 번 재생된다(이 저장소 개발자의 실제 설정이 그 상태였다).
    """
    cfg.write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "/old/repo/server/tts_hook.sh"}]}
    ]}}))
    ch._cmd_install()
    stop = _cmds(json.loads(cfg.read_text()), "Stop")
    assert len(stop) == 1, f"항목이 늘어나면 안 된다: {stop}"
    assert stop[0].endswith("agent_hook.sh stop")


def test_moved_repo_path_is_updated_not_appended(cfg):
    cfg.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": "/old/path/server/agent_hook.sh pre"}
        ]}],
    }}))
    ch._cmd_install()
    pre = _cmds(json.loads(cfg.read_text()), "PreToolUse")
    assert len(pre) == 1
    assert "/old/path/" not in pre[0], "옛 경로를 가리키는 죽은 훅이 남으면 안 된다"


def test_our_hook_sharing_a_group_with_foreign_hook_keeps_both(cfg):
    cfg.write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [
            {"type": "command", "command": "/old/repo/server/tts_hook.sh"},
            {"type": "command", "command": "/home/x/other.sh"},
        ]}
    ]}}))
    ch._cmd_install()
    stop = _cmds(json.loads(cfg.read_text()), "Stop")
    assert "/home/x/other.sh" in stop
    assert any(c.endswith("agent_hook.sh stop") for c in stop)
    assert len(stop) == 2


def test_status_reports_add_update_ok(cfg):
    settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/old/server/tts_hook.sh"}]}]}}
    st = ch.plan(settings)
    assert st["Stop"][0] == "update"
    assert st["PreToolUse"][0] == "add"

    ch._cmd_install()
    st2 = ch.plan(json.loads(cfg.read_text()))
    assert all(state == "ok" for state, _ in st2.values())


def test_broken_json_aborts_without_overwriting(cfg):
    cfg.write_text("{ this is not json")
    assert ch._cmd_install() == 2
    assert cfg.read_text() == "{ this is not json", "깨진 설정을 덮어쓰면 안 된다"


def test_install_writes_backup(cfg):
    cfg.write_text(json.dumps({"hooks": {}}))
    ch._cmd_install()
    assert cfg.with_suffix(".json.vtbak").exists()


def test_uninstall_removes_only_ours(cfg):
    cfg.write_text(json.dumps({"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "/home/x/other_stop.sh"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/home/x/coach.sh"}]}],
    }}))
    ch._cmd_install()
    assert ch._cmd_uninstall() == 0

    data = json.loads(cfg.read_text())
    assert _cmds(data, "Stop") == ["/home/x/other_stop.sh"]
    assert _cmds(data, "UserPromptSubmit") == ["/home/x/coach.sh"]
    # 우리 것만 있던 이벤트는 키 자체가 사라진다(빈 배열을 남기지 않는다)
    assert "PreToolUse" not in data["hooks"]


def test_uninstall_on_clean_config_is_noop(cfg):
    cfg.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/x.sh"}]}]}}))
    before = cfg.read_text()
    assert ch._cmd_uninstall() == 0
    assert cfg.read_text() == before
