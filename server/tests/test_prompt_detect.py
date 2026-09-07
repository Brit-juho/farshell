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
