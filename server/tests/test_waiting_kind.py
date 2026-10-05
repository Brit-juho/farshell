"""waiting의 유형(kind) — 훅 payload / 화면 문구 / 폴백 / CLI별 spec 격리 / 보조 badge.

우선순위: 훅 kind > 화면 패턴 kind(그 CLI의 spec으로만) > 폴백. 대기인데 kind 미정이면
`question`, PermissionRequest인데 도구를 모르면 `approval`. 실제 ~/.claude 설정은 건드리지
않는다(훅 등록 테스트는 CLAUDE_CONFIG_DIR을 tmp로 격리).
"""

import json

import pytest
from starlette.testclient import TestClient

import agent_prompt_detect as D
import agent_status as A
import claude_hooks as ch


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    A._state.clear()
    D._patterns = None
    D._global_detector = None
    monkeypatch.setattr(D, "FLAP_GUARD_SEC", 0.0)
    yield
    A._state.clear()
    D._patterns = None
    D._global_detector = None


def _perm(tool, tool_input=None, sid="s1", **kw):
    p = {"session_id": sid, "tool_name": tool, "tool_input": tool_input or {}}
    return A.on_event("permission", p, **kw)


ASK = {"questions": [{"question": "어느 쪽으로 갈까요?", "options": [
    {"label": "A안", "description": "x"}, {"label": "B안"}]}]}


# ── 훅 payload -> kind ───────────────────────────────────────────────────
def test_permission_ordinary_tool_is_approval():
    ent = _perm("Bash", {"command": "ls"})
    assert ent["status"] == A.WAITING and ent["kind"] == "approval"
    assert ent["question"] == "Bash 실행 승인이 필요합니다"


def test_permission_unknown_or_missing_tool_is_approval():
    assert _perm("SomeNewTool", sid="a")["kind"] == "approval"
    ent = A.on_event("permission", {"session_id": "b"})
    assert ent["kind"] == "approval" and ent["question"] == "실행 승인이 필요합니다"


def test_ask_user_question_is_choice_with_options_and_no_approval_text():
    ent = _perm("AskUserQuestion", ASK)
    assert ent["kind"] == "choice"
    assert ent["options"] == [{"key": "1", "label": "A안"}, {"key": "2", "label": "B안"}]
    assert "승인" not in ent["question"] and ent["question"] == "어느 쪽으로 갈까요?"


def test_ask_user_question_malformed_input_still_choice():
    for bad in ({}, {"questions": []}, {"questions": [{"options": "x"}]}, {"questions": [None]}):
        ent = _perm("AskUserQuestion", bad, sid="m")
        assert ent["kind"] == "choice" and ent["options"] is None
        assert "승인" not in ent["question"]


def test_exit_plan_mode_is_plan():
    ent = _perm("ExitPlanMode", {"plan": "..."})
    assert ent["kind"] == "plan" and ent["options"] is None
    assert "승인" not in ent["question"]


def test_codex_permission_event_is_approval():
    ent = _perm("shell", agent="codex")
    assert ent["kind"] == "approval" and ent["agent"] == "codex"


def test_codex_pre_request_user_input_is_waiting_choice():
    ent = A.on_event("pre", {"session_id": "c", "tool_name": "request_user_input",
                             "tool_input": ASK}, agent="codex")
    assert ent["status"] == A.WAITING and ent["kind"] == "choice"
    assert [o["label"] for o in ent["options"]] == ["A안", "B안"]


def test_request_user_input_from_other_cli_does_not_wait():
    ent = A.on_event("pre", {"session_id": "c", "tool_name": "request_user_input"}, agent="claude")
    assert ent["status"] == A.WORKING and "kind" not in ent


def test_codex_ordinary_pre_stays_working():
    ent = A.on_event("pre", {"session_id": "c", "tool_name": "shell"}, agent="codex")
    assert ent["status"] == A.WORKING and "kind" not in ent


def test_kind_removed_when_waiting_ends():
    for ender in (("post", {"session_id": "s1"}), ("pre", {"session_id": "s1", "tool_name": "Bash"}),
                  ("stop", {"session_id": "s1"}), ("prompt", {"session_id": "s1"})):
        _perm("ExitPlanMode")
        assert A.get_state("s1")["kind"] == "plan"
        ent = A.on_event(ender[0], ender[1])
        assert "kind" not in ent and "kind" not in A.public_entry(ent), ender[0]


def test_kind_removed_on_ttl_sweep(monkeypatch):
    _perm("AskUserQuestion", ASK)
    monkeypatch.setenv("VT_AGENT_TTL_WAITING", "0")
    A.sweep(now=A.get_state("s1")["updated_at"] + 5)
    ent = A.get_state("s1")
    assert ent["status"] == A.WORKING and "kind" not in ent


def test_kind_is_part_of_prompt_fingerprint():
    ent = _perm("Bash")
    first = ent["prompt_id"]
    # 같은 질문/선택지여도 kind가 다르면 다른 프롬프트다.
    ent["kind"] = "choice"
    ent["question"] = "Bash 실행 승인이 필요합니다"
    A._set_status(ent, A.WAITING)
    assert ent["prompt_id"] != first


def test_kind_in_public_api_entry():
    _perm("ExitPlanMode")
    assert A.public_entry(A.get_state("s1"))["kind"] == "plan"
    assert "_kind_src" not in A.public_entry(A.get_state("s1"))


# ── 우선순위: 훅 > 화면 > 폴백 ───────────────────────────────────────────
def test_screen_kind_used_without_hook():
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"})
    ent = A.on_waiting("s1", True, question="Q", options=None, kind="plan")
    assert ent["kind"] == "plan"


def test_hook_kind_beats_screen_kind():
    _perm("AskUserQuestion", ASK)
    ent = A.on_waiting("s1", True, question="Do you want to proceed?", options=None, kind="approval")
    assert ent["kind"] == "choice"
    assert ent["options"] and ent["options"][0]["label"] == "A안", "화면의 빈 값이 훅 선택지를 지우면 안 된다"


def test_codex_hook_question_survives_screen_footer():
    ent = A.on_event("pre", {"session_id": "s1", "tool_name": "request_user_input",
                             "tool_input": ASK}, agent="codex")
    original_id = ent["prompt_id"]
    ent = A.on_waiting("s1", True, question="Question 1/1 (1 unanswered)",
                       options=None, kind="choice")
    assert ent["question"] == "어느 쪽으로 갈까요?"
    assert ent["prompt_id"] == original_id
    assert [o["label"] for o in ent["options"]] == ["A안", "B안"]


def test_codex_hook_choice_options_survive_stale_approval_screen_options():
    ent = A.on_event("pre", {"session_id": "s1", "tool_name": "request_user_input",
                             "tool_input": ASK}, agent="codex")
    ent = A.on_waiting("s1", True, question="Would you like to run the following command?",
                       options=[{"key": "y", "label": "Yes, proceed"}], kind="approval")
    assert ent["kind"] == "choice"
    assert ent["question"] == "어느 쪽으로 갈까요?"
    assert [o["label"] for o in ent["options"]] == ["A안", "B안"]


def test_waiting_without_any_kind_falls_back_to_question():
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"})
    assert A.on_waiting("s1", True, question="Q")["kind"] == "question"
    # 모르는 kind 문자열도 폴백
    A.on_waiting("s1", False)
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"})
    assert A.on_waiting("s1", True, question="Q", kind="bogus")["kind"] == "question"


def test_screen_kind_refresh_changes_prompt_id():
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"})
    a = A.on_waiting("s1", True, question="Q", kind="approval")["prompt_id"]
    b = A.on_waiting("s1", True, question="Q", kind="choice")["prompt_id"]
    assert a != b


def test_screen_waiting_released_removes_kind():
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"})
    A.on_waiting("s1", True, question="Q", kind="plan")
    ent = A.on_waiting("s1", False)
    assert "kind" not in ent


# ── Notification: 보조 기록만 ────────────────────────────────────────────
def test_notification_is_auxiliary_only():
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"})
    ent = A.on_event("notification", {"session_id": "s1", "notification_type": "permission_prompt"})
    assert ent["status"] == A.WORKING and "kind" not in ent
    assert ent["last_notification"]["type"] == "permission_prompt"
    # 엔트리가 없으면 만들지 않는다
    assert A.on_event("notification", {"session_id": "none"}) is None
    assert "none" not in A._state


def test_notification_does_not_override_hook_kind():
    _perm("ExitPlanMode")
    ent = A.on_event("notification", {"session_id": "s1", "notification_type": "permission_prompt"})
    assert ent["kind"] == "plan" and ent["status"] == A.WAITING


# ── Stop 의 `?` -> 보조 badge, waiting 아님 ──────────────────────────────
@pytest.mark.parametrize("msg", ["진행할까요?", "ok?  \n", "계속할까요？"])
def test_stop_ending_with_question_mark_sets_badge_not_waiting(msg):
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"})
    ent = A.on_event("stop", {"session_id": "s1", "last_assistant_message": msg})
    assert ent["status"] == A.DONE and ent["badge"] == "question"
    assert "kind" not in ent and ent.get("prompt_id") is None


@pytest.mark.parametrize("msg", ["끝났습니다.", "물어보자면? 아니다", "", None, 5])
def test_stop_without_trailing_question_has_no_badge(msg):
    ent = A.on_event("stop", {"session_id": "s1", "last_assistant_message": msg})
    assert ent["status"] == A.DONE and "badge" not in ent


def test_badge_cleared_by_next_turn():
    A.on_event("stop", {"session_id": "s1", "last_assistant_message": "?"})
    assert A.get_state("s1")["badge"] == "question"
    A.on_event("prompt", {"session_id": "s1"})
    assert "badge" not in A.get_state("s1")
    A.on_event("stop", {"session_id": "s1", "last_assistant_message": "?"})
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"})
    assert "badge" not in A.get_state("s1")


def test_stop_badge_replaced_when_next_stop_has_no_question():
    A.on_event("stop", {"session_id": "s1", "last_assistant_message": "?"})
    A.on_event("stop", {"session_id": "s1", "last_assistant_message": "done."})
    assert "badge" not in A.get_state("s1")


# ── 화면 문구 -> kind (CLI x kind), 그 CLI의 spec으로만 ────────────────────
def _det(agent):
    events, badges = [], []
    d = D.PromptDetector(lambda sid, w: events.append((sid, w)),
                         agent_for=lambda sid: agent,
                         on_badge=lambda sid, b: badges.append((sid, b)))
    return d, events, badges


SCREENS = [
    # (agent, 화면 문자열, 기대 kind)
    ("claude", "Do you want to proceed?\r\n ❯ 1. Yes\r\n", "approval"),
    ("claude", "Would you like to proceed?\r\n ❯ 1. Yes, clear context\r\n", "plan"),
    ("claude", "Here is Claude's plan:\r\n", "plan"),
    ("claude", "Which one?\r\n Enter to select · ↑/↓ to navigate · Esc to cancel", "choice"),
    ("codex", "Would you like to run the following command?\r\n", "approval"),
    ("codex", "Implement this plan?\r\n", "plan"),
    ("codex", "Question 1/1 (1 unanswered)\r\n", "choice"),
    ("codex", "tab to add notes | enter to submit answer\r\n", "choice"),
    ("agy", "Requesting permission for: rm -rf x\r\n", "approval"),
    ("agy", "Run this command?\r\n", "approval"),
    ("agy", "Pick\r\n ↑/↓ Navigate · enter Select · esc Skip\r\n", "choice"),
]


@pytest.mark.parametrize("agent,screen,kind", SCREENS)
def test_screen_kind_per_cli(agent, screen, kind):
    d, events, _ = _det(agent)
    d.feed("s1", screen.encode())
    assert d.is_waiting("s1") and d.get_kind("s1") == kind
    d.feed("s1", b"")  # 재호출해도 유지
    assert d.get_kind("s1") == kind


def test_kind_cleared_when_not_waiting():
    d, _, _ = _det("claude")
    d.feed("s1", b"Would you like to proceed?")
    d.on_user_input("s1")
    assert d.get_kind("s1") is None


def test_unscoped_detector_still_matches_all_specs():
    """resolver가 없으면(옛 호출부/테스트) 옛 동작 그대로."""
    d = D.PromptDetector(lambda *a: None)
    d.feed("s1", b"Would you like to proceed?")
    assert d.get_kind("s1") == "plan"


def test_agent_unknown_to_resolver_matches_all_specs():
    d, _, _ = _det(None)
    d.feed("s1", b"Do you want to proceed?")
    assert d.is_waiting("s1") and d.get_kind("s1") == "approval"


def test_agy_does_not_hit_claude_patterns():
    """agy가 우연히 claude.toml에 걸리던 동작을 정리했다."""
    d, events, _ = _det("agy")
    d.feed("s1", b"Do you want to proceed?\r\n Would you like to proceed?\r\n")
    assert not d.is_waiting("s1") and events == []


def test_claude_does_not_hit_agy_or_codex_patterns():
    d, events, _ = _det("claude")
    d.feed("s1", b"Requesting permission for: x\r\n Run this command?\r\n Implement this plan?\r\n"
                 b" 1 unanswered)\r\n")
    assert not d.is_waiting("s1")


def test_codex_does_not_hit_claude_choice_footer():
    d, _, _ = _det("codex")
    d.feed("s1", "Enter to select · ↑/↓ to navigate".encode())
    assert not d.is_waiting("s1")


def test_cli_without_toml_never_waits():
    d, _, _ = _det("opencode")
    d.feed("s1", b"Do you want to proceed?")
    assert not d.is_waiting("s1")


def test_resolver_exception_falls_back_to_unscoped():
    def boom(sid):
        raise RuntimeError("x")
    d = D.PromptDetector(lambda *a: None, agent_for=boom)
    d.feed("s1", b"Do you want to proceed?")
    assert d.is_waiting("s1")


def test_resolver_called_only_on_hit_and_cached():
    calls = []
    d = D.PromptDetector(lambda *a: None, agent_for=lambda sid: calls.append(sid) or "claude")
    d.feed("s1", b"plain output\r\n")
    assert calls == []
    d.feed("s1", b"Do you want to proceed?")
    d.feed("s1", b" more")
    assert len(calls) == 1


# ── Codex exit 예외 ──────────────────────────────────────────────────────
def test_codex_choice_ignores_esc_to_interrupt_in_same_window():
    d, events, _ = _det("codex")
    d.feed("s1", b"Question 1/1 (1 unanswered)\r\n enter to submit answer\r\n"
                 b"Working (0s \xe2\x80\xa2 esc to interrupt)\r\n")
    assert d.is_waiting("s1") and d.get_kind("s1") == "choice"
    d.feed("s1", b"\x1b[2K esc to interrupt")
    assert d.is_waiting("s1")


def test_codex_approval_still_released_by_esc_to_interrupt():
    d, _, _ = _det("codex")
    d.feed("s1", b"Would you like to run the following command?\r\n")
    assert d.is_waiting("s1")
    d.feed("s1", b"Working (3s \xe2\x80\xa2 esc to interrupt)")
    assert not d.is_waiting("s1")


def test_claude_choice_not_exempt_from_exit():
    d, _, _ = _det("claude")
    d.feed("s1", "Enter to select · ↑/↓ to navigate".encode())
    assert d.is_waiting("s1")
    d.feed("s1", b" esc to interrupt")
    assert not d.is_waiting("s1")


def test_codex_choice_released_by_user_input():
    d, _, _ = _det("codex")
    d.feed("s1", b"enter to submit answer esc to interrupt")
    assert d.is_waiting("s1")
    d.on_user_input("s1")
    assert not d.is_waiting("s1")


def test_agy_exit_is_empty_so_esc_to_cancel_never_clears():
    pats = D.load_patterns(force=True)
    assert pats["agy"]["exit"] == []
    d, _, _ = _det("agy")
    d.feed("s1", b"Run this command?\r\n esc to cancel")
    assert d.is_waiting("s1")


# ── agy badge (done + plan, waiting 아님) ────────────────────────────────
def test_agy_artifact_review_is_badge_not_waiting():
    d, events, badges = _det("agy")
    d.feed("s1", b"Plan ready. /artifact to review\r\n")
    assert not d.is_waiting("s1") and events == []
    assert badges == [("s1", "plan")]
    d.feed("s1", b" more output")
    assert badges == [("s1", "plan")], "같은 값은 다시 알리지 않는다"
    d.on_user_input("s1")
    assert badges[-1] == ("s1", None)


def test_badge_literal_ignored_for_other_cli():
    d, _, badges = _det("claude")
    d.feed("s1", b"/artifact to review")
    assert badges == []


def test_badge_set_on_entry_without_changing_status():
    A.report("pane:dev", "done", session="dev", agent="agy")
    assert A.set_badge_for_session("dev", "plan") == 1
    ent = A.get_state("pane:dev")
    assert ent["status"] == A.DONE and ent["badge"] == "plan"
    A.set_badge_for_session("dev", None)
    assert "badge" not in A.get_state("pane:dev")
    A.set_badge_for_session("dev", "plan")
    A.report("pane:dev", "working", session="dev")
    assert "badge" not in A.get_state("pane:dev")


# ── deps 연결: 화면 kind 가 엔트리까지 간다 ──────────────────────────────
def test_deps_wires_agent_scope_and_kind(monkeypatch):
    import deps

    class _Info:
        tmux_name = "dev"
    monkeypatch.setattr(deps.session_store, "get", lambda sid: _Info())
    A.report("pane:dev", "working", session="dev", agent="codex")
    deps._prompt_detector._windows.pop("pty1", None)
    deps._prompt_detector.feed("pty1", b"Implement this plan?\r\n")
    ent = A.get_state("pane:dev")
    assert ent["status"] == A.WAITING and ent["kind"] == "plan"
    # codex로 판별된 세션은 Claude 문구에 반응하지 않는다.
    deps._prompt_detector.remove("pty1")
    A.report("pane:dev", "working", session="dev", agent="codex")
    deps._prompt_detector.feed("pty1", b"Do you want to proceed?\r\n")
    assert A.get_state("pane:dev")["status"] == A.WORKING
    deps._prompt_detector.remove("pty1")


# ── coverage: CLI별 kind 목록 ────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("VT_CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    import main
    with TestClient(main.app) as c:
        yield c


def test_coverage_reports_kinds_per_cli(client):
    rows = {r["cli"]: r for r in client.get("/api/agents/coverage").json()}
    assert rows["claude"]["kinds"] == ["approval", "choice", "plan", "question"]
    assert rows["claude"]["kindSources"] == {
        "approval": "screen", "choice": "screen", "plan": "screen", "question": "fallback"}
    assert rows["codex"]["kinds"] == ["approval", "choice", "plan", "question"]
    assert rows["agy"]["kinds"] == ["approval", "choice", "question"]
    assert "plan" not in rows["agy"]["kinds"], "agy plan은 대기가 아니라 badge"
    assert rows["agy"]["badges"] == ["plan"]
    assert rows["aider"]["kinds"] == ["approval", "question"]


def test_coverage_claude_hook_kinds_win_over_screen_once_permission_hook_registered(client, tmp_path):
    import claude_hooks
    path = tmp_path / "claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(claude_hooks.apply_install({})[0]))
    rows = {r["cli"]: r for r in client.get("/api/agents/coverage").json()}
    assert rows["claude"]["path"] == "hook"
    assert rows["claude"]["kindSources"] == {
        "approval": "hook", "choice": "hook", "plan": "hook", "question": "fallback"}


def test_coverage_claude_without_permission_hook_stays_screen(client, tmp_path):
    import claude_hooks
    settings, _ = claude_hooks.apply_install({})
    settings["hooks"].pop("PermissionRequest")
    path = tmp_path / "claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings))
    rows = {r["cli"]: r for r in client.get("/api/agents/coverage").json()}
    assert rows["claude"]["path"] == "pty"
    assert rows["claude"]["kindSources"]["approval"] == "screen"


def test_coverage_codex_hook_kinds_win_over_screen(client, tmp_path):
    import codex_hooks
    text, _ = codex_hooks.apply_install("")
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "config.toml").write_text(text)
    rows = {r["cli"]: r for r in client.get("/api/agents/coverage").json()}
    assert rows["codex"]["path"] == "hook"
    assert rows["codex"]["kindSources"]["approval"] == "hook"
    assert rows["codex"]["kindSources"]["choice"] == "hook"
    assert rows["codex"]["kindSources"]["plan"] == "screen"
    assert rows["codex"]["badges"] == ["question"]


def test_coverage_no_waiting_means_no_kinds(client, monkeypatch, tmp_path):
    detect_dir = tmp_path / "empty-detect"
    detect_dir.mkdir()
    monkeypatch.setattr(D, "DETECT_DIR", detect_dir)
    rows = {r["cli"]: r for r in client.get("/api/agents/coverage").json()}
    assert rows["claude"]["kinds"] == [] and rows["claude"]["kindSources"] == {}


# ── API: 훅 이벤트 -> /api/agent/status 의 kind ──────────────────────────
def test_api_event_exposes_kind_and_badge(client):
    r = client.post("/api/agent/event", json={
        "event": "permission", "agent": "codex",
        "payload": {"session_id": "api1", "tool_name": "ExitPlanMode"}})
    assert r.json()["state"]["kind"] == "plan"
    allst = client.get("/api/agent/status").json()["all"]
    assert allst["api1"]["kind"] == "plan"
    client.post("/api/agent/event", json={
        "event": "stop", "payload": {"session_id": "api1", "last_assistant_message": "ok?"}})
    ent = client.get("/api/agent/status").json()["all"]["api1"]
    assert ent["status"] == "done" and ent["badge"] == "question" and "kind" not in ent


# ── Notification 훅 등록 (임시 경로 격리) ────────────────────────────────
@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    return tmp_path / "claude-home" / "settings.json"


def test_notification_hook_registered_idempotent_with_matcher(cfg):
    assert ch.settings_path() == cfg and "claude-home" in str(cfg)
    assert ch._cmd_install() == 0
    data = json.loads(cfg.read_text())
    group = data["hooks"]["Notification"]
    assert len(group) == 1
    assert group[0]["matcher"] == "permission_prompt|idle_prompt"
    assert group[0]["hooks"][0]["command"].endswith("agent_hook.sh notification claude")
    first = cfg.read_text()
    assert ch._cmd_install() == 0 and cfg.read_text() == first


def test_notification_hook_status_and_foreign_hooks_preserved(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"hooks": {"Notification": [
        {"hooks": [{"type": "command", "command": "/home/x/notify.sh"}]}]}}))
    st = ch.plan(ch.load_settings(cfg))
    assert st["Notification"] == ("add", None)
    ch._cmd_install()
    data = json.loads(cfg.read_text())
    cmds = [h["command"] for g in data["hooks"]["Notification"] for h in g["hooks"]]
    assert "/home/x/notify.sh" in cmds and sum("agent_hook.sh" in c for c in cmds) == 1
    assert ch.plan(ch.load_settings(cfg))["Notification"][0] == "ok"
    ch._cmd_uninstall()
    data = json.loads(cfg.read_text())
    assert [h["command"] for g in data["hooks"]["Notification"] for h in g["hooks"]] == ["/home/x/notify.sh"]


# ── 실화면 발췌(2026-10-05, Claude Code 2.1.289, 격리 tmux 소켓에서 서버 PTY로 받은 바이트) ──
# 선택지 줄 사이가 `\r\n`이 아니라 절대 커서 이동(`\x1b[19;1H`)이고, 단어마다 색상 코드가 낀다.
CLAUDE_APPROVAL_REAL = (
    b'\x1b[16;1H Do you want to proceed?\x1b[K\r\n \x1b[38;5;153m\xe2\x9d\xaf\x1b[39m '
    b'\x1b[38;5;246m1.\x1b[39m \x1b[38;5;153mYes\x1b[39m\x1b[K\r\n   \x1b[38;5;246m2.\x1b[39m '
    b'Yes, and always allow access to \x1b[1m/private/tmp/probe3/repo\x1b(B\x1b[m from this project'
    b'\x1b[19;1H   \x1b[38;5;246m3.\x1b[39m Yes, and switch to auto mode\x1b[38;5;246m \xc2\xb7\x1b[39m '
    b'\x1b[38;5;246mauto\x1b[39m \x1b[38;5;246mmode\x1b[39m    \x1b[20;1H   \x1b[38;5;246m4.\x1b[39m No'
    b'\x1b[K\r\n\x1b[K\r\n \x1b[38;5;246mEsc\x1b[39m \x1b[38;5;246mto\x1b[39m \x1b[38;5;246mcancel\x1b[39m'
)
CLAUDE_INTERRUPTED_REAL = (
    b'\x1b[H  \xe2\x8e\xbf \xc2\xa0\x1b[38;5;246mInterrupted\x1b[39m \x1b[38;5;246m\xc2\xb7\x1b[39m '
    b'\x1b[38;5;246mWhat\x1b[39m \x1b[38;5;246mshould\x1b[39m \x1b[38;5;246mClaude\x1b[39m '
    b'\x1b[38;5;246mdo\x1b[39m \x1b[38;5;246minstead?\x1b[39m\x1b[K\r\n'
)
CLAUDE_WORKING_STATUS_REAL = (
    b'\x1b[24;3H\xe2\x8f\xb8 manual mode on \xc2\xb7 esc to interrupt \xc2\xb7 \xe2\x86\x90 for agents'
)
# 80열 PTY에서 ExitPlanMode 대화상자 제목이 줄바꿈된다.
CLAUDE_PLAN_NARROW_REAL = (
    b' Claude has written up a plan and is ready to execute. Would you like to\r\n proceed?\r\n'
    b' \xe2\x9d\xaf 1. Yes, and use auto mode\r\n   2. Yes, manually approve edits\r\n'
)


def test_real_claude_approval_options_are_split_by_cursor_moves():
    d, _, _ = _det("claude")
    d.feed("s1", CLAUDE_APPROVAL_REAL)
    assert d.get_kind("s1") == "approval"
    _, options = d.get_prompt("s1")
    labels = {o["key"]: o["label"] for o in options}
    assert labels["1"] == "Yes"
    assert labels["2"].startswith("Yes, and always allow access to")
    assert "3." not in labels["2"] and "4. No" not in labels["2"]
    assert labels["3"].startswith("Yes, and switch to auto mode")
    assert labels["4"] == "No"


def test_real_claude_narrow_plan_dialog_is_plan():
    d, _, _ = _det("claude")
    d.feed("s1", CLAUDE_PLAN_NARROW_REAL)
    assert d.is_waiting("s1") and d.get_kind("s1") == "plan"


def _int_det(agent="claude"):
    ints = []
    d = D.PromptDetector(lambda *a: None, agent_for=lambda sid: agent,
                         on_interrupt=lambda sid: ints.append(sid))
    return d, ints


def test_real_claude_interrupted_line_fires_despite_color_codes_and_nbsp():
    d, ints = _int_det()
    d.feed("s1", CLAUDE_INTERRUPTED_REAL)
    assert ints == ["s1"]


def test_interrupt_split_across_chunks_still_fires():
    d, ints = _int_det()
    cut = len(CLAUDE_INTERRUPTED_REAL) // 2
    d.feed("s1", CLAUDE_INTERRUPTED_REAL[:cut])
    d.feed("s1", CLAUDE_INTERRUPTED_REAL[cut:])
    assert ints == ["s1"]


def test_interrupt_ignored_when_working_status_line_redrawn_after_it():
    """작업이 다시 시작돼 상태줄(`esc to interrupt`)이 같은 구간에 그려지면 옛 줄의 재그리기다."""
    d, ints = _int_det()
    d.feed("s1", CLAUDE_INTERRUPTED_REAL + CLAUDE_WORKING_STATUS_REAL)
    assert ints == []


def test_interrupt_not_scoped_to_other_cli_or_agy_without_literal():
    for agent in ("codex", "agy"):
        d, ints = _int_det(agent)
        d.feed("s1", CLAUDE_INTERRUPTED_REAL)
        assert ints == [], agent


def test_interrupt_clears_detector_waiting_even_if_prompt_text_still_in_window():
    events = []
    ints = []
    d = D.PromptDetector(lambda sid, w: events.append((sid, w)), agent_for=lambda sid: "claude",
                         on_interrupt=lambda sid: ints.append(sid))
    d.feed("s1", CLAUDE_APPROVAL_REAL)
    assert d.is_waiting("s1")
    d.feed("s1", CLAUDE_INTERRUPTED_REAL)  # 사용자가 FarShell 밖(실제 터미널)에서 거절
    assert ints == ["s1"] and not d.is_waiting("s1")
    assert events[-1] == ("s1", False)


def test_rejecting_approval_ends_hook_working_entry(monkeypatch):
    """거절(`4. No`)·ESC 중단은 Stop 훅이 없다 — 화면 문구가 훅 엔트리를 done으로 마친다.

    실측: 거절 뒤 화면은 빈 입력줄인데 엔트리가 working(tool=Bash)으로 TTL까지 남았다."""
    import deps

    class _Info:
        tmux_name = "dev"

    monkeypatch.setattr(deps.session_store, "get", lambda sid: _Info())
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"}, session="dev")
    A.on_event("permission", {"session_id": "s1", "tool_name": "Bash"}, session="dev")
    A.on_event("pre", {"session_id": "s2", "tool_name": "Read"}, session="other")
    deps.on_terminal_input("p1", "4")           # 거절 번호 입력 -> waiting 해제, working으로
    assert A.get_status("s1") == A.WORKING
    deps._on_interrupt("p1")                     # 화면에 `Interrupted ·` 가 그려졌다
    assert A.get_status("s1") == A.DONE
    assert A.get_state("s1")["tool"] is None
    assert A.get_status("s2") == A.WORKING       # 다른 세션은 건드리지 않는다


def test_interrupt_does_not_touch_done_or_idle_entries():
    A.on_event("stop", {"session_id": "s1"}, session="dev")
    before = A.get_state("s1")["updated_at"]
    assert A.finish_interrupted_for_session("dev") == 0
    assert A.get_state("s1")["updated_at"] == before


# agy 실화면 발췌(1.2.14): 거절 뒤 줄, 그리고 이어서 뜬 선택 화면
AGY_INTERRUPTED_REAL = (
    b"  \xe2\x8e\xbf  Interrupted \xc2\xb7 What should Antigravity CLI do instead?\r\n"
)
AGY_CHOICE_REAL = (
    b"Question 1/1: Pick a color\r\n> 1. Red\r\n  2. Blue\r\n  3. Write-in...\r\n"
    b"  \xe2\x86\x91/\xe2\x86\x93 Navigate \xc2\xb7 enter Select \xc2\xb7 esc Skip\r\n"
)
AGY_APPROVAL_REAL = (
    b"Requesting permission for:\r\n   touch agy_probe.txt\r\nRun this command?\r\n"
    b"> 1. Yes, run command\r\n  4. No, cancel\r\n"
)


def test_agy_interrupted_line_ends_waiting_without_exit_pattern():
    d, ints = _int_det("agy")
    events = []
    d._on_change = lambda sid, w: events.append(w)
    d.feed("s1", AGY_APPROVAL_REAL)
    assert d.get_kind("s1") == "approval"
    d.feed("s1", AGY_INTERRUPTED_REAL)
    assert ints == ["s1"] and not d.is_waiting("s1")
    d.feed("s1", b"\x1b[24;1H? for shortcuts")  # 이후 잡음이 옛 프롬프트를 되살리지 않는다
    assert not d.is_waiting("s1")


def test_agy_choice_after_cancel_is_waiting_again_on_pane_entry(monkeypatch):
    """훅이 없는 agy는 취소로 done이 된 뒤 새 턴의 선택 화면도 받아야 한다(2026-10-05 실측:
    done에 막혀 선택 화면이 떠 있는데도 엔트리가 done이었다)."""
    import deps

    class _Info:
        tmux_name = "ag"

    monkeypatch.setattr(deps.session_store, "get", lambda sid: _Info())
    det = deps._prompt_detector  # _on_waiting_change가 kind/question을 이 인스턴스에서 읽는다
    monkeypatch.setattr(det, "_agent_for", lambda sid: "agy")
    det._agent_cache.clear()
    det.remove("p1")
    det.feed("p1", AGY_APPROVAL_REAL)
    assert A.get_status("pane:ag") == A.WAITING
    det.feed("p1", AGY_INTERRUPTED_REAL)
    assert A.get_status("pane:ag") == A.DONE
    det.feed("p1", AGY_CHOICE_REAL)
    ent = A.get_state("pane:ag")
    assert ent["status"] == A.WAITING and ent["kind"] == "choice"
    det.remove("p1")


def test_done_by_stop_still_rejects_late_screen_waiting():
    """`_reopen`은 거절/ESC로 끝난 엔트리에만 붙는다 — 정상 완료(Stop)는 기존 규칙 그대로."""
    A.on_event("stop", {"session_id": "s"})
    A.on_waiting("s", True)
    assert A.get_status("s") == A.DONE


# agy 1.2.14 실화면: 선택 화면 안내줄이 단어마다 색상 코드로 쪼개져 온다(바이트 리터럴이 안 맞던 화면).
AGY_CHOICE_STYLED_REAL = (
    b'\x1b[1mQuestion 1/1: Pick a color\x1b(B\x1b[m\x1b[K\r\n\x1b[K\x1b[94m\r\n> 1. Red\x1b[39m\x1b[K\r\n'
    b'  2. Blue\x1b[K\r\n  3. Write-in...\x1b[K\r\n\x1b[K\r\n  \x1b[94m\x1b[1m\xe2\x86\x91/\xe2\x86\x93'
    b'\x1b(B\x1b[m\x1b[90m Navigate \xc2\xb7 \x1b[94m\x1b[1menter\x1b(B\x1b[m\x1b[90m Select \xc2\xb7 '
    b'\x1b[94m\x1b[1mesc\x1b(B\x1b[m\x1b[90m Skip\x1b[39m\x1b[K\x1b[90m\r\nesc to cancel\x1b[39m'
)


def test_real_agy_styled_choice_screen_is_choice():
    d, _, _ = _det("agy")
    d.feed("s1", AGY_CHOICE_STYLED_REAL)
    assert d.is_waiting("s1") and d.get_kind("s1") == "choice"


def test_styled_screen_split_mid_escape_and_mid_utf8_still_matches():
    for cut in range(1, len(AGY_CHOICE_STYLED_REAL)):
        d, _, _ = _det("agy")
        d.feed("s1", AGY_CHOICE_STYLED_REAL[:cut])
        d.feed("s1", AGY_CHOICE_STYLED_REAL[cut:])
        assert d.get_kind("s1") == "choice", cut


def test_agy_artifact_badge_matches_through_color_codes():
    d, _, badges = _det("agy")
    d.feed("s1", b"\x1b[90m/artifact\x1b[39m to review\r\n")
    assert badges == [("s1", "plan")]


def test_permission_hook_after_screen_keeps_screen_options():
    """Claude Code는 대화상자를 그린 뒤 PermissionRequest 훅을 부르기도 한다(2.1.289 실측) —
    화면이 뽑은 선택지를 승인 훅이 None으로 덮으면 모바일 인라인 버튼이 사라진다."""
    A.on_event("pre", {"session_id": "s", "tool_name": "Bash"}, session="dev")
    opts = [{"key": "1", "label": "Yes"}, {"key": "4", "label": "No"}]
    A.on_waiting("s", True, question="Do you want to proceed?", options=opts, kind="approval")
    ent = _perm("Bash", sid="s", session="dev")
    assert ent["status"] == A.WAITING and ent["kind"] == "approval"
    assert ent["options"] == opts


def test_permission_hook_first_then_screen_still_fills_options():
    ent = _perm("Bash", sid="s", session="dev")
    assert ent["options"] is None
    opts = [{"key": "1", "label": "Yes"}]
    A.on_waiting("s", True, question="Do you want to proceed?", options=opts, kind="approval")
    assert A.get_state("s")["options"] == opts and A.get_state("s")["kind"] == "approval"


# ── attach/새로고침이 waiting을 풀던 문제(2026-10-06) ─────────────────────────
# tmux 3.7c가 attach 직후 클라이언트 터미널에 보낸 질의(실측 바이트 발췌)와, 그에 xterm.js가
# onData로 내보내는 응답. 응답이 WS 바이너리로 올라와 「사람이 입력했다」로 읽혔다.
TMUX_ATTACH_QUERIES = b"\x1b[c\x1b[>c\x1b[>q\x1b]10;?\x1b\\\x1b]11;?\x1b\\"
XTERM_REPLIES_TO_ATTACH = [
    b"\x1b[?1;2c", b"\x1b[>0;276;0c", b"\x1bP>|xterm.js(5.5.0)\x1b\\",
    b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\", b"\x1b]11;rgb:0000/0000/0000\x1b\\",
    b"\x1b[?997;1n", b"\x1b[I", b"\x1b[24;80R",
]


def _waiting_hook_entry(monkeypatch):
    import deps

    class _Info:
        tmux_name = "dev"

    monkeypatch.setattr(deps.session_store, "get", lambda sid: _Info())
    A.on_event("pre", {"session_id": "s1", "tool_name": "Bash"}, session="dev")
    A.on_event("permission", {"session_id": "s1", "tool_name": "Bash"}, session="dev")
    assert A.get_status("s1") == A.WAITING
    return deps


@pytest.mark.parametrize("reply", XTERM_REPLIES_TO_ATTACH)
def test_terminal_protocol_reply_does_not_release_waiting(monkeypatch, reply):
    deps = _waiting_hook_entry(monkeypatch)
    deps.on_terminal_input("p1", reply)
    deps.on_terminal_input("p1", reply.decode("latin-1"))   # /keys 경로는 str
    assert A.get_status("s1") == A.WAITING


def test_attach_reply_burst_keeps_waiting_and_detector_state(monkeypatch):
    deps = _waiting_hook_entry(monkeypatch)
    deps.on_terminal_input("p1", b"".join(XTERM_REPLIES_TO_ATTACH))
    assert A.get_status("s1") == A.WAITING


def test_attach_queries_are_what_the_replies_answer():
    # 질의 목록이 바뀌면(tmux 업그레이드) 이 상수를 다시 채취해야 한다는 표식.
    for q in (b"\x1b[c", b"\x1b[>c", b"\x1b[>q", b"\x1b]10;?", b"\x1b]11;?"):
        assert q in TMUX_ATTACH_QUERIES


@pytest.mark.parametrize("key,status", [
    (b"4", A.WORKING), (b"\r", A.WORKING), (b"\x1b[A", A.WORKING),
    (b"\x1b[1;5R", A.WORKING),            # Ctrl+F3 — CPR와 문법이 같아도 사람 키
    (b"\x1b[I" + b"y", A.WORKING),        # 응답에 사람 키가 섞이면 사람 입력
    (b"\x1b", A.DONE),
])
def test_real_keys_still_release_waiting(monkeypatch, key, status):
    deps = _waiting_hook_entry(monkeypatch)
    deps.on_terminal_input("p1", key)
    assert A.get_status("s1") == status


def test_is_terminal_report_edges():
    from terminal_reports import is_terminal_report as r
    assert not r(None) and not r(b"") and not r("")
    assert r(b"\x1b[?1;2c") and r("\x1b[?1;2c")
    assert not r(b"\x1b[?1;2cy")
