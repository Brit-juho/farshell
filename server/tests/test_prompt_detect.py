"""A3 — 승인 대기(waiting) 감지: 패턴 히트/소멸/오탐/해제 경로.

이 감지는 **화면에 계속 남을 수 있는 상태**를 만든다. 그래서 테스트의 무게가
"뜨는가"보다 "잘 풀리는가"와 "안 떠야 할 때 안 뜨는가"에 있다.
"""

import time

import pytest

import agent_prompt_detect as D
import auto_responder


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    D._patterns = None
    D._global_detector = None
    monkeypatch.setattr(D, "FLAP_GUARD_SEC", 0.0)  # 테스트에서 sleep하지 않기 위해
    yield
    D._patterns = None
    D._global_detector = None


@pytest.fixture
def det():
    events = []
    d = D.PromptDetector(lambda sid, waiting: events.append((sid, waiting)))
    return d, events


def test_patterns_load_from_toml():
    pats = D.load_patterns(force=True)
    assert "claude" in pats
    assert any(b"Do you want to proceed?" == p for p in pats["claude"]["enter"])
    assert any(b"esc to interrupt" == p for p in pats["claude"]["exit"])


def test_broken_toml_is_skipped_not_fatal(monkeypatch, tmp_path):
    (tmp_path / "broken.toml").write_text("this is [not valid toml")
    (tmp_path / "ok.toml").write_text('name = "x"\nenter = ["APPROVE ME PLEASE"]\nexit = []\n')
    monkeypatch.setattr(D, "DETECT_DIR", tmp_path)
    pats = D.load_patterns(force=True)
    assert "x" in pats, "깨진 파일 하나가 나머지를 막으면 안 된다"
    assert "broken" not in pats


def test_empty_pattern_file_is_ignored(monkeypatch, tmp_path):
    (tmp_path / "codex.toml").write_text('name = "codex"\nenter = []\nexit = []\n')
    monkeypatch.setattr(D, "DETECT_DIR", tmp_path)
    assert D.load_patterns(force=True) == {}


# ── 감지 ──────────────────────────────────────────────────────────────────
def test_enter_pattern_sets_waiting(det):
    d, events = det
    d.feed("s1", b"\x1b[32m Do you want to proceed?\r\n 1. Yes\r\n")
    assert d.is_waiting("s1") is True
    assert events == [("s1", True)]


def test_exit_pattern_clears_waiting(det):
    d, events = det
    d.feed("s1", b"Do you want to proceed?")
    d.feed("s1", b"\r\n... (esc to interrupt)")
    assert d.is_waiting("s1") is False
    assert events == [("s1", True), ("s1", False)]


def test_exit_wins_when_both_in_window(det):
    """프롬프트가 떴다가 방금 사라진 윈도우 — 현재 화면은 '사라진 뒤'다."""
    d, _ = det
    d.feed("s1", b"Do you want to proceed? ... answered ... esc to interrupt")
    assert d.is_waiting("s1") is False


def test_no_duplicate_events_while_still_waiting(det):
    d, events = det
    d.feed("s1", b"Do you want to proceed?")
    d.feed("s1", b"Do you want to proceed?")
    assert events == [("s1", True)], "상태가 안 바뀌면 통지하지 않는다(WS 폭주 방지)"


def test_late_options_refresh_waiting_metadata_once(det):
    """실측 회귀: Codex TUI는 질문을 먼저, y/p/esc 옵션을 뒤 청크에 그린다.

    첫 청크에서 waiting 콜백이 끝났다는 이유로 뒤 옵션을 통지하지 않으면
    detector 내부에는 옵션이 있어도 agent_status/API에는 영원히 None이 남는다.
    """
    d, events = det
    d.feed("s1", b"Would you like to run the following command?\r\n")
    assert d.get_prompt("s1")[1] is None
    d.feed("s1", (
        "› 1. Yes, proceed (y)\r\n"
        "  2. Yes, and don't ask again (p)\r\n"
        "  3. No, and tell Codex what to do differently (esc)\r\n"
    ).encode())
    assert d.get_prompt("s1")[1] == [
        {"key": "y", "label": "Yes, proceed"},
        {"key": "p", "label": "Yes, and don't ask again"},
        {"key": "esc", "label": "No, and tell Codex what to do differently"},
    ]
    assert events == [("s1", True), ("s1", True)]


def test_codex_full_screen_redraw_keeps_prompt_until_late_options(det):
    """0.155.0 실측: 144×38 전체화면 재렌더가 질문과 옵션 사이 2KB를 넘는다."""
    d, events = det
    d.feed("s1", b"Would you like to run the following command?\r\n")
    # 실제 출력은 ANSI 커서 이동/지우기지만, 여기서는 창 용량 계약만 정확히
    # 검증하도록 무해한 화면 채움 줄로 8KiB를 만든다.
    d.feed("s1", ("screen repaint\r\n" * 550).encode())
    d.feed("s1", (
        "› 1. Yes, proceed (y)\r\n"
        "  2. Yes, and don't ask again (p)\r\n"
        "  3. No, and tell Codex what to do differently (esc)\r\n"
    ).encode())
    assert d.get_prompt("s1")[1] == [
        {"key": "y", "label": "Yes, proceed"},
        {"key": "p", "label": "Yes, and don't ask again"},
        {"key": "esc", "label": "No, and tell Codex what to do differently"},
    ]
    assert events[-1] == ("s1", True)

    # 같은 화면 재렌더는 다시 알리지 않는다.
    d.feed("s1", b"\r\n")
    assert events == [("s1", True), ("s1", True)]


def test_pattern_split_across_chunks_is_caught(det):
    """PTY는 문자열을 임의 위치에서 쪼갠다 — 슬라이딩 윈도우가 그걸 잇는다."""
    d, _ = det
    d.feed("s1", b"Do you want ")
    d.feed("s1", b"to proceed?")
    assert d.is_waiting("s1") is True


def test_build_log_containing_yes_does_not_trigger(det):
    """오탐 방지 — 짧은 'Yes'/'y/n'은 패턴이 아니다."""
    d, events = det
    d.feed("s1", b"ok 12 - yes it works\nYes: 3 passed\n")
    assert d.is_waiting("s1") is False
    assert events == []


def test_user_input_clears_waiting(det):
    """해제 판정 중 가장 확실한 신호 — 사람이 실제로 답했다."""
    d, events = det
    d.feed("s1", b"Do you want to proceed?")
    d.on_user_input("s1")
    assert d.is_waiting("s1") is False
    assert events[-1] == ("s1", False)


def test_user_input_when_not_waiting_is_noop(det):
    d, events = det
    d.on_user_input("s1")
    assert events == []


def test_old_prompt_does_not_retrigger_after_user_input(det):
    """입력으로 해제한 뒤 윈도우를 비운다 — 지나간 프롬프트가 다시 히트하면 안 된다."""
    d, _ = det
    d.feed("s1", b"Do you want to proceed?")
    d.on_user_input("s1")
    d.feed("s1", b"\r\nrunning...\r\n")
    assert d.is_waiting("s1") is False


def test_sessions_are_independent(det):
    d, _ = det
    d.feed("s1", b"Do you want to proceed?")
    d.feed("s2", b"just some output")
    assert d.is_waiting("s1") is True
    assert d.is_waiting("s2") is False


def test_remove_clears_session_state(det):
    d, _ = det
    d.feed("s1", b"Do you want to proceed?")
    d.remove("s1")
    assert d.is_waiting("s1") is False
    assert "s1" not in d._windows


def test_on_change_exception_does_not_break_feed(monkeypatch):
    """감지가 서버를 죽이지 않는다."""
    def boom(sid, waiting):
        raise RuntimeError("consumer exploded")
    d = D.PromptDetector(boom)
    d.feed("s1", b"Do you want to proceed?")  # 예외가 새어 나오면 실패


# ── auto_responder 상호배제 ───────────────────────────────────────────────
def test_auto_trust_cooldown_suppresses_waiting(monkeypatch, det):
    """VT_AUTO_TRUST=1이면 auto_responder가 프롬프트를 먼저 삼킨다.

    그 짧은 창에 waiting을 띄우면 화면이 깜빡이고, 더 나쁘게는 큐가 그 pane을
    막힌 것으로 오해한다.
    """
    d, events = det
    monkeypatch.setenv("VT_AUTO_TRUST", "1")
    responder = auto_responder.AutoResponder(write_fn=lambda sid, data: None)
    responder._last_response_time["s1"] = time.monotonic()
    monkeypatch.setattr(auto_responder, "_global_responder", responder)

    d.feed("s1", b"Yes, I trust this folder")
    assert d.is_waiting("s1") is False
    assert events == []


def test_no_suppression_when_auto_trust_is_off(monkeypatch, det):
    d, _ = det
    monkeypatch.delenv("VT_AUTO_TRUST", raising=False)
    responder = auto_responder.AutoResponder(write_fn=lambda sid, data: None)
    responder._last_response_time["s1"] = time.monotonic()
    monkeypatch.setattr(auto_responder, "_global_responder", responder)

    d.feed("s1", b"Yes, I trust this folder")
    assert d.is_waiting("s1") is True


def test_suppression_expires_with_cooldown(monkeypatch, det):
    d, _ = det
    monkeypatch.setenv("VT_AUTO_TRUST", "1")
    responder = auto_responder.AutoResponder(write_fn=lambda sid, data: None)
    responder._last_response_time["s1"] = time.monotonic() - auto_responder.COOLDOWN_SECONDS - 1
    monkeypatch.setattr(auto_responder, "_global_responder", responder)

    d.feed("s1", b"Yes, I trust this folder")
    assert d.is_waiting("s1") is True, "cooldown이 지났으면 더는 억제하지 않는다"


# ── N38(70-mobile.md §2) — 질문·번호 선택지 캡처 ────────────────────────────
def test_patterns_load_options_regex():
    pats = D.load_patterns(force=True)
    assert pats["claude"]["options"], "claude.toml에 options 캡처가 있어야 한다"


def test_options_extracted_on_enter_hit(det):
    d, _ = det
    d.feed("s1", b"Do you want to proceed?\r\n\xe2\x9d\xaf 1. Yes\r\n  2. Yes, and don't ask again\r\n  3. No\r\n")
    question, options = d.get_prompt("s1")
    assert question == "Do you want to proceed?"
    assert options == [
        {"key": "1", "label": "Yes"},
        {"key": "2", "label": "Yes, and don't ask again"},
        {"key": "3", "label": "No"},
    ]


def test_options_extracted_when_output_has_ansi_noise(det):
    """실기기 검증(2.1.0)에서 재현 — 실제 CLI 출력은 옵션 줄 사이에 커서 이동
    (`\\x1b[3S\\x1b[46;2H`)·줄지우기(`\\x1b[K`)·캐리지리턴 단독(`\\r`)이 낀다.
    이걸 안 벗기면 1번 선택지가 통째로 누락되고 2·3번 라벨 끝에 `\\x1b[K`가
    그대로 붙는다."""
    d, _ = det
    raw = (
        b"\n\x1b[ADo you want to make this edit to legacy.css?\r\n"
        b"\x1b[K\xe2\x9d\xaf\r\x1b[3S\x1b[46;2H 1. Yes\r\n"
        b"  2. Yes, and don't ask again this session\x1b[K\r\n"
        b"  3. No\x1b[K\r\n"
    )
    d.feed("s1", raw)
    question, options = d.get_prompt("s1")
    assert question == "Do you want to make this edit to legacy.css?"
    assert options == [
        {"key": "1", "label": "Yes"},
        {"key": "2", "label": "Yes, and don't ask again this session"},
        {"key": "3", "label": "No"},
    ]


def test_options_survive_many_small_writes_before_enter_hit(det):
    """실기기 검증(2.1.0)에서 재현 — 실제 터미널은 커서 깜빡임·tmux 상태줄
    갱신 같은 잡음 청크가 초 단위로 끼어들어, 프롬프트 한 번 렌더가 4개
    넘는 write로 쪼개지는 일이 흔하다. 예전 `deque(maxlen=4)`는 **청크 개수**로
    잘라서 앞쪽(1번 선택지) 청크가 바이트 창 안에 여전히 들어가는데도
    밀려났다 — bytearray 기반 바이트 수 상한이면 살아남아야 한다."""
    d, _ = det
    d.feed("s1", b"Do you want to make this edit to legacy.css?\r\n")
    d.feed("s1", b"\xe2\x9d\xaf 1. Yes\r\n")
    # 4청크 넘는 잡음(cursor show/hide 토글류) — 예전 구현이면 위 두 청크를 밀어낸다.
    for _ in range(8):
        d.feed("s1", b"\x1b[?25l\x1b[?12l\x1b[?25h")
    d.feed("s1", b"  2. Yes, and don't ask again\r\n")
    d.feed("s1", b"  3. No\r\n")
    question, options = d.get_prompt("s1")
    assert question == "Do you want to make this edit to legacy.css?"
    assert options == [
        {"key": "1", "label": "Yes"},
        {"key": "2", "label": "Yes, and don't ask again"},
        {"key": "3", "label": "No"},
    ]


def test_no_options_when_prompt_has_no_numbered_choices(det):
    """선택지 형태가 아니면 (질문, None) — 프런트는 이때 「터미널로」로 폴백한다."""
    d, _ = det
    d.feed("s1", b"Do you want to make this edit to foo.py?\r\n")
    question, options = d.get_prompt("s1")
    assert question == "Do you want to make this edit to foo.py?"
    assert options is None


def test_get_prompt_is_none_when_not_waiting(det):
    d, _ = det
    assert d.get_prompt("s1") == (None, None)


def test_get_prompt_clears_after_exit(det):
    d, _ = det
    d.feed("s1", b"Do you want to proceed?\r\n 1. Yes\r\n 2. No\r\n")
    assert d.get_prompt("s1")[1] is not None
    d.feed("s1", b"\r\n... (esc to interrupt)")
    assert d.get_prompt("s1") == (None, None)


def test_get_prompt_clears_after_user_input(det):
    d, _ = det
    d.feed("s1", b"Do you want to proceed?\r\n 1. Yes\r\n 2. No\r\n")
    d.on_user_input("s1")
    assert d.get_prompt("s1") == (None, None)


# ── N9/N45 — codex/gemini/aider 실제 프롬프트 문구 회귀 테스트 ──────────────
# 문구 출처는 server/detect/{codex,gemini,aider}.toml 상단 주석 참고
# (openai/codex, google-gemini/gemini-cli, Aider-AI/aider 공식 저장소 소스 확인).

def test_codex_patterns_load_from_toml():
    pats = D.load_patterns(force=True)
    assert "codex" in pats
    assert any(
        b"Would you like to run the following command?" == p
        for p in pats["codex"]["enter"]
    )
    assert any(b"esc to interrupt" == p for p in pats["codex"]["exit"])


def test_codex_exec_prompt_sets_waiting(det):
    d, events = det
    d.feed("s1", b"Would you like to run the following command?\r\n> npm test\r\n")
    assert d.is_waiting("s1") is True
    assert events == [("s1", True)]


def test_codex_status_line_clears_waiting(det):
    """codex 상태줄 `Working (Ns • esc to interrupt)` — 작업 재개 신호."""
    d, _ = det
    d.feed("s1", b"Would you like to make the following edits?")
    d.feed("s1", b"\r\nWorking (2s \xe2\x80\xa2 esc to interrupt)")
    assert d.is_waiting("s1") is False


def test_codex_newer_approval_wins_over_old_working_line(det):
    """실측 회귀: 같은 2KB에 이전 working 상태줄과 새 승인 질문이 함께 있다."""
    d, _ = det
    d.feed("s1", b"Working (2s \xe2\x80\xa2 esc to interrupt)\r\n")
    d.feed("s1", b"Would you like to run the following command?\r\n")
    assert d.is_waiting("s1") is True


def test_codex_options_use_real_shortcut_keys(det):
    d, _ = det
    d.feed("s1", (
        "Would you like to run the following command?\r\n"
        "› 1. Yes, proceed (y)\r\n"
        "  2. Yes, and don't ask again (p)\r\n"
        "  3. No, and tell Codex what to do differently (esc)\r\n"
    ).encode())
    assert d.get_prompt("s1")[1] == [
        {"key": "y", "label": "Yes, proceed"},
        {"key": "p", "label": "Yes, and don't ask again"},
        {"key": "esc", "label": "No, and tell Codex what to do differently"},
    ]


def test_codex_footer_does_not_trigger_gemini_exit(det):
    """0.155.0 실측: Codex footer도 Gemini exit와 같은 `esc to cancel`을 쓴다."""
    d, events = det
    d.feed("s1", (
        "Would you like to run the following command?\r\n"
        "› 1. Yes, proceed (y)\r\n"
        "  2. Yes, and don't ask again (p)\r\n"
        "  3. No, and tell Codex what to do differently (esc)\r\n"
        "Press enter to confirm or esc to cancel\r\n"
    ).encode())
    assert d.is_waiting("s1") is True
    assert d.get_prompt("s1")[1][0] == {"key": "y", "label": "Yes, proceed"}
    assert events == [("s1", True)]


def test_gemini_patterns_load_from_toml():
    pats = D.load_patterns(force=True)
    assert "gemini" in pats
    assert any(b"Allow execution of" == p for p in pats["gemini"]["enter"])
    assert any(b"esc to cancel" == p for p in pats["gemini"]["exit"])


def test_gemini_shell_confirmation_sets_waiting(det):
    d, events = det
    d.feed("s1", b"Allow execution of [Shell]?\r\n\xe2\x9d\xaf 1. Allow once\r\n")
    assert d.is_waiting("s1") is True
    assert events == [("s1", True)]


def test_gemini_esc_to_cancel_clears_waiting(det):
    d, _ = det
    d.feed("s1", b"Apply this change?")
    d.feed("s1", b"\r\nGenerating\xe2\x80\xa6 (esc to cancel, 3s)")
    assert d.is_waiting("s1") is False


def test_aider_patterns_load_from_toml():
    pats = D.load_patterns(force=True)
    assert "aider" in pats
    assert any(b"(Y)es/(N)o" == p for p in pats["aider"]["enter"])
    assert any(
        b"Allow edits to file that has not been added to the chat?" == p
        for p in pats["aider"]["enter"]
    )
    assert pats["aider"]["exit"] == [], "aider는 확인된 exit 문구가 없다 — 추측해서 채우지 않았다"


def test_aider_confirm_ask_suffix_sets_waiting(det):
    """aider의 모든 confirm_ask()는 예외 없이 이 접미사를 붙인다(aider/io.py)."""
    d, events = det
    d.feed("s1", b"Run shell command? (Y)es/(N)o [Yes]: ")
    assert d.is_waiting("s1") is True
    assert events == [("s1", True)]


def test_aider_waiting_only_clears_via_user_input_or_ttl(det):
    """aider는 exit 리터럴이 없다 — 풀스크린 상태줄이 없어서다(토큰 주석 참고).

    빌드 로그가 아무리 흘러도 waiting은 저절로 안 풀린다. 실제 해제는
    on_user_input(사람이 답함) 또는 TTL(agent_status 쪽 책임)로만 일어난다 —
    이 감지기 레벨에서는 그게 맞는 동작이다.
    """
    d, _ = det
    d.feed("s1", b"Allow edits to file that has not been added to the chat? (Y)es/(N)o [Yes]: ")
    d.feed("s1", b"\r\nRunning tests...\r\nAll 42 tests passed\r\n")
    assert d.is_waiting("s1") is True
    d.on_user_input("s1")
    assert d.is_waiting("s1") is False
