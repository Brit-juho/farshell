"""질문 상태가 아닌데 선택지·「선택 요청」이 뜨던 오탐 회귀.

원인 둘:
  1. PTY 윈도우는 **출력 이력**이라, 작업 중인 에이전트가 docs·toml·tests를 읽으며 찍는
     `Enter to select · ↑/↓ to navigate`, `Do you want to proceed?`, Codex `unanswered)`가 그대로
     waiting이 됐다. 지금은 waiting으로 올리기 전에 tmux **현재 화면**(`screen_state`: 마지막
     SCREEN_TAIL_LINES줄, 뒤에 작업 중 표시가 있으면 지나간 것)에 같은 프롬프트가 있는지 묻는다.
  2. `_extract_prompt`가 윈도우 전체의 번호 줄을 모아, 앞선 번호 목록이 가짜 버튼이 됐다.
     이제 프롬프트 줄 아래(푸터형은 바로 위)의 마지막 번호 묶음만 쓴다.
"""

import asyncio
from pathlib import Path

import pytest

import agent_prompt_detect as D
import codex_0160_frames as F

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    D._patterns = None
    D._global_detector = None
    monkeypatch.setattr(D, "FLAP_GUARD_SEC", 0.0)
    yield
    D._patterns = None
    D._global_detector = None


class Screen:
    """tmux 화면 한 장을 흉내 낸다 — 시험이 `text`를 바꿔 가며 지금 화면을 정한다."""

    def __init__(self, text=None):
        self.text = text
        self.calls = 0

    def __call__(self, sid, agent):
        self.calls += 1
        if self.text is None:
            return None
        return D.screen_state(agent, self.text)


def _det(agent="claude", screen=None):
    events = []
    d = D.PromptDetector(lambda sid, w: events.append((sid, w)), agent_for=lambda sid: agent,
                         screen_for=screen)
    return d, events


def _as_screen(raw: bytes) -> str:
    return D._strip_ansi(raw.decode("utf-8", errors="ignore"))


# ── 시험 데이터: 이 저장소를 Claude로 개발할 때 실제로 찍히는 문구 ────────────────────
def _repo_doc_output() -> str:
    """docs/ref/agents.md 본문(우선순위·waiting 규칙)을 읽는 Read 도구 출력 — 프롬프트 문구가 그대로 들어 있다."""
    p = REPO / "docs" / "ref" / "agents.md"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if "Enter to select" not in text:  # 문서가 바뀌어도 문구는 보장한다
        text += "\nchoice = [\"Enter to select · ↑/↓ to navigate\"]\n"
    return text


WORKING_FOOTER = "✻ Reading docs… (12s · esc to interrupt)\n\n────\n❯ \n────\n  ? for shortcuts\n"


def _tool_output() -> bytes:
    body = "\n".join(
        [
            "⏺ Read(docs/ref/agents.md)",
            *["  ⎿  " + ln for ln in _repo_doc_output().splitlines()[:80]],
            "  choice = [\"Enter to select · ↑/↓ to navigate\"]",
            "  Do you want to proceed?",
            "  unanswered) enter to submit answer",
        ]
    )
    return (body + "\r\n").encode()


# ── (가) 작업 중 출력의 문구는 이력으로만 남는다 ───────────────────────────────────
def test_quoted_prompt_literals_in_tool_output_do_not_raise_waiting_when_screen_is_busy():
    screen = Screen(text="  Do you want to proceed?\n  Enter to select · ↑/↓ to navigate\n"
                         + WORKING_FOOTER)
    d, events = _det("claude", screen)
    d.feed("s1", _tool_output())
    assert not d.is_waiting("s1") and events == []
    assert screen.calls == 1


def test_quoted_literal_scrolled_off_the_screen_is_history_only():
    """문구가 화면 마지막 14줄 밖으로 밀렸으면(스피너도 없음) 지금 질문이 아니다."""
    screen = Screen(text="\n".join(f"결과 줄 {i}" for i in range(30)) + "\n❯ \n")
    d, events = _det("claude", screen)
    d.feed("s1", _tool_output())
    assert not d.is_waiting("s1") and events == []


def test_codex_quoted_literals_in_output_do_not_raise_waiting():
    screen = Screen(text="• Ran rg -n unanswered docs\n\n"
                         "• Working (3s • esc to interrupt)\n\n› Ask Codex to do anything\n")
    d, events = _det("codex", screen)
    d.feed("s1", b"detect/codex.toml: choice = [\"unanswered)\", \"enter to submit answer\"]\r\n")
    assert not d.is_waiting("s1") and events == []


def test_repeated_chunks_with_same_quote_do_not_hammer_tmux():
    screen = Screen(text=WORKING_FOOTER)
    d, events = _det("claude", screen)
    for _ in range(20):
        d.feed("s1", _tool_output())
    # 같은 위치의 문구는 SCREEN_RECHECK_SEC 안에 다시 묻지 않는다(청크마다 새 위치가 생기면 묻는다).
    assert events == []
    assert screen.calls <= 20


# ── (바) 스피너 재그림 직후에도 flap 하지 않는다 ───────────────────────────────────
def test_spinner_redraw_after_quote_never_flaps(monkeypatch):
    monkeypatch.setattr(D, "FLAP_GUARD_SEC", 1.0)  # 실제 값 — 예전엔 가짜 waiting이 이 1초만큼 유지됐다
    screen = Screen(text=WORKING_FOOTER)
    d, events = _det("claude", screen)
    d.feed("s1", _tool_output())
    d.feed("s1", "\x1b[24;3H✻ Reading docs… (13s · esc to interrupt)".encode())
    d.feed("s1", _tool_output())
    assert events == []


# ── 진짜 프롬프트는 그대로 잡힌다 (라) ─────────────────────────────────────────────
CLAUDE_BASH = (
    "⏺ Bash(npm test)\r\n\r\n"
    " Bash command\r\n\r\n   npm test\r\n   Run the tests\r\n\r\n"
    " Do you want to proceed?\r\n"
    " ❯ 1. Yes\r\n   2. Yes, and don't ask again for npm test commands\r\n   3. No, and tell Claude what to do differently\r\n"
    "\r\n Esc to cancel · Tab to amend\r\n"
)
CLAUDE_BASH_OPTIONS = [
    {"key": "1", "label": "Yes"},
    {"key": "2", "label": "Yes, and don't ask again for npm test commands"},
    {"key": "3", "label": "No, and tell Claude what to do differently"},
]
CLAUDE_ASK = (
    "←  ☐ MCP  ☐ 설계 승인  ✔ Submit  →\r\n\r\nMCP 방식과 범위는 위 설계안대로 할까요?\r\n\r\n"
    "❯ 1. 로컬 stdio (추천)\r\n  2. 원격 HTTP MCP로\r\n  3. Type something.\r\n"
    "────────────────────────────────────────\r\n  4. Chat about this\r\n\r\n"
    "Enter to select · Tab/Arrow keys to navigate · Esc to cancel\r\n"
)
CLAUDE_PLAN = (
    "Here is Claude's plan:\r\n"
    "\r\n Would you like to proceed?\r\n"
    " ❯ 1. Yes, and use auto mode\r\n   2. Yes, manually approve edits\r\n   3. No, keep planning\r\n"
)


@pytest.mark.parametrize("name,raw,kind,options", [
    ("bash", CLAUDE_BASH, "approval", CLAUDE_BASH_OPTIONS),
    ("ask", CLAUDE_ASK, "choice", [
        {"key": "1", "label": "로컬 stdio (추천)"}, {"key": "2", "label": "원격 HTTP MCP로"},
        {"key": "3", "label": "Type something."}, {"key": "4", "label": "Chat about this"}]),
    ("plan", CLAUDE_PLAN, "plan", [
        {"key": "1", "label": "Yes, and use auto mode"}, {"key": "2", "label": "Yes, manually approve edits"},
        {"key": "3", "label": "No, keep planning"}]),
])
def test_real_claude_prompts_still_raise_waiting_with_same_kind_and_options(name, raw, kind, options):
    screen = Screen(text=raw)
    d, events = _det("claude", screen)
    d.feed("s1", raw.encode())
    assert d.is_waiting("s1") and events == [("s1", True)]
    assert d.get_kind("s1") == kind
    assert d.get_prompt("s1")[1] == options


def test_real_claude_approval_fixture_from_tmux_diffs_still_raises_waiting():
    """tmux가 바뀐 줄만 보내는 실측 바이트(절대 커서 이동)에서도 같은 선택지."""
    import test_waiting_kind as W
    screen = Screen(text=_as_screen(W.CLAUDE_APPROVAL_REAL))
    d, events = _det("claude", screen)
    d.feed("s1", W.CLAUDE_APPROVAL_REAL)
    assert d.is_waiting("s1") and d.get_kind("s1") == "approval"
    assert [o["key"] for o in d.get_prompt("s1")[1]] == ["1", "2", "3", "4"]
    assert events == [("s1", True)]


def test_real_claude_narrow_plan_fixture_still_raises_waiting():
    import test_waiting_kind as W
    screen = Screen(text=_as_screen(W.CLAUDE_PLAN_NARROW_REAL))
    d, _ = _det("claude", screen)
    d.feed("s1", W.CLAUDE_PLAN_NARROW_REAL)
    assert d.is_waiting("s1") and d.get_kind("s1") == "plan"


def test_screen_busy_after_prompt_means_prompt_is_gone():
    """승인에 답한 뒤 작업이 이어져 상태줄이 그려졌다면 윈도우의 옛 문구는 지나간 것이다."""
    screen = Screen(text=CLAUDE_BASH + "\r\n" + WORKING_FOOTER)
    d, events = _det("claude", screen)
    d.feed("s1", CLAUDE_BASH.encode())
    assert events == []


def test_unreadable_screen_falls_back_to_history_judgement():
    """tmux를 못 읽으면 예전처럼 이력으로 판정한다 — 진짜 프롬프트를 놓치는 쪽이 더 나쁘다."""
    d, events = _det("claude", Screen(text=None))
    d.feed("s1", CLAUDE_BASH.encode())
    assert d.is_waiting("s1") and events == [("s1", True)]


def test_screen_probe_failure_falls_back_to_history_judgement():
    def boom(sid, agent):
        raise RuntimeError("tmux gone")
    d, _ = _det("claude", boom)
    d.feed("s1", CLAUDE_BASH.encode())
    assert d.is_waiting("s1")


def test_no_screen_probe_means_old_behaviour():
    d, _ = _det("claude", None)
    d.feed("s1", _tool_output())
    assert d.is_waiting("s1")  # 화면 확인이 없으면(구성 안 됨) 이력만 본다


def test_new_prompt_after_rejected_quote_is_checked_again():
    screen = Screen(text=WORKING_FOOTER)
    d, events = _det("claude", screen)
    d.feed("s1", _tool_output())
    assert events == []
    screen.text = CLAUDE_BASH          # 이제 진짜 승인 화면이 그려졌다
    d.feed("s1", CLAUDE_BASH.encode())
    assert events == [("s1", True)]
    assert d.get_prompt("s1")[1] == CLAUDE_BASH_OPTIONS


def test_already_waiting_does_not_query_screen_on_redraw():
    screen = Screen(text=CLAUDE_BASH)
    d, _ = _det("claude", screen)
    d.feed("s1", CLAUDE_BASH.encode())
    calls = screen.calls
    d.feed("s1", b"\x1b[H")
    d.feed("s1", CLAUDE_BASH.encode())
    assert screen.calls == calls


# ── Codex 실화면 (마) ───────────────────────────────────────────────────────────
def test_real_codex_patch_approval_still_raises_waiting():
    screen = Screen(text=_as_screen(F.PATCH_APPROVAL))
    d, events = _det("codex", screen)
    d.feed("s1", F.PATCH_APPROVAL)
    assert d.is_waiting("s1") and d.get_kind("s1") == "approval"
    q, opts = d.get_prompt("s1")
    assert q == "Would you like to make the following edits?"
    assert [o["key"] for o in opts] == ["y", "a", "esc"]
    assert events == [("s1", True)]


def test_real_codex_choice_overlay_still_raises_waiting_despite_status_line():
    screen = Screen(text=_as_screen(F.CHOICE_OVERLAY))
    d, _ = _det("codex", screen)
    d.feed("s1", F.CHOICE_OVERLAY)
    assert d.is_waiting("s1") and d.get_kind("s1") == "choice"


def test_codex_approval_after_quoted_numbered_list_keeps_only_real_options():
    d, _ = _det("codex", None)
    d.feed("s1", (
        "Steps:\r\n1. install (y)\r\n2. build (p)\r\n"
        "Would you like to run the following command?\r\n"
        "› 1. Yes, proceed (y)\r\n  2. Yes, and don't ask again (p)\r\n"
        "  3. No, and tell Codex what to do differently (esc)\r\n").encode())
    assert [o["key"] for o in d.get_prompt("s1")[1]] == ["y", "p", "esc"]


# ── (나) 앞선 번호 목록 + 진짜 Bash 승인 ────────────────────────────────────────────
def test_numbered_output_before_bash_approval_is_not_offered_as_buttons():
    prev = ("⏺ Read(docs/plan.md)\r\n  ⎿  1. 설치\r\n     2. 설정\r\n     3. 빌드\r\n     4. 배포\r\n     5. 확인\r\n\r\n")
    d, _ = _det("claude", None)
    d.feed("s1", (prev + CLAUDE_BASH).encode())
    assert d.get_prompt("s1")[1] == CLAUDE_BASH_OPTIONS


def test_numbered_output_before_bash_approval_with_screen_check():
    prev = "  1. 설치\r\n  2. 설정\r\n  3. 빌드\r\n  4. 배포\r\n  5. 확인\r\n"
    screen = Screen(text=CLAUDE_BASH)
    d, _ = _det("claude", screen)
    d.feed("s1", (prev + CLAUDE_BASH).encode())
    assert d.is_waiting("s1")
    assert d.get_prompt("s1")[1] == CLAUDE_BASH_OPTIONS


# ── (다) 6단계 계획 + ExitPlanMode ────────────────────────────────────────────────
PLAN_STEPS = (
    "Here is Claude's plan:\r\n"
    " 1. 조사\r\n 2. 설계\r\n 3. 구현\r\n 4. 시험\r\n 5. 문서\r\n 6. 배포\r\n"
)


def test_six_step_plan_before_exit_plan_mode_yields_only_the_three_real_buttons():
    d, _ = _det("claude", None)
    d.feed("s1", (PLAN_STEPS + "\r\n Would you like to proceed?\r\n"
                  " ❯ 1. Yes, and use auto mode\r\n   2. Yes, manually approve edits\r\n"
                  "   3. No, keep planning\r\n").encode())
    assert d.get_kind("s1") == "plan"
    assert [o["label"] for o in d.get_prompt("s1")[1]] == [
        "Yes, and use auto mode", "Yes, manually approve edits", "No, keep planning"]


def test_plan_title_before_options_still_waiting_with_no_options_yet():
    d, _ = _det("claude", None)
    d.feed("s1", (PLAN_STEPS + "\r\n Would you like to proceed?\r\n").encode())
    assert d.is_waiting("s1")
    assert d.get_prompt("s1")[1] is None   # 옵션이 아직 안 왔다 — 계획 단계를 버튼으로 내지 않는다


def test_choice_footer_uses_run_above_footer_not_older_lists():
    d, _ = _det("claude", None)
    d.feed("s1", ("  1. 오래된 목록 A\r\n  2. 오래된 목록 B\r\n  3. C\r\n  4. D\r\n\r\n"
                  + CLAUDE_ASK).encode())
    assert [o["key"] for o in d.get_prompt("s1")[1]] == ["1", "2", "3", "4"]
    assert d.get_prompt("s1")[1][0]["label"] == "로컬 stdio (추천)"


def test_partial_redraw_after_arrow_key_keeps_all_options():
    """방향키로 ❯가 옮겨 가면 tmux는 바뀐 1·2번 줄만 다시 보낸다 — 3번 버튼이 사라지면 안 된다."""
    d, _ = _det("claude", None)
    d.feed("s1", CLAUDE_BASH.encode())
    d.feed("s1", ("\x1b[10;1H   1. Yes\x1b[11;1H ❯ 2. Yes, and don't ask again for npm test commands").encode())
    assert d.get_prompt("s1")[1] == CLAUDE_BASH_OPTIONS


def test_question_is_the_latest_prompt_line():
    d, _ = _det("claude", None)
    d.feed("s1", b"Do you want to proceed?\r\nold text\r\n"
                 b"Do you want to proceed?\r\n\xe2\x9d\xaf 1. Yes\r\n  2. No\r\n")
    assert d.get_prompt("s1")[0] == "Do you want to proceed?"
    assert [o["key"] for o in d.get_prompt("s1")[1]] == ["1", "2"]


# ── 비동기 경로(서버의 실제 경로: PTY 콜백은 이벤트 루프 위) ─────────────────────────
def test_async_screen_check_raises_waiting_only_after_screen_confirms():
    async def go():
        screen = Screen(text=CLAUDE_BASH)
        d, events = _det("claude", screen)
        d._agent_cache["s1"] = ("claude", 1e18)
        d.feed("s1", CLAUDE_BASH.encode())
        assert events == []                      # 화면 확인이 끝나기 전엔 올리지 않는다
        for _ in range(50):
            await asyncio.sleep(0.02)
            if events:
                break
        assert events == [("s1", True)]
        assert d.get_prompt("s1")[1] == CLAUDE_BASH_OPTIONS
        return screen.calls

    assert asyncio.run(go()) == 1


def test_async_screen_check_rejects_quote_when_screen_is_busy():
    async def go():
        screen = Screen(text=WORKING_FOOTER)
        d, events = _det("claude", screen)
        d._agent_cache["s1"] = ("claude", 1e18)
        d.feed("s1", _tool_output())
        await asyncio.sleep(0.3)
        assert events == [] and not d.is_waiting("s1")

    asyncio.run(go())


# ── screen_state: Codex 질문 화면의 같은 줄 `esc to interrupt` ──────────────────────
def test_screen_state_keeps_codex_choice_overlay_prompt_despite_same_line_interrupt_hint():
    shot = D.screen_state("codex", _as_screen(F.CHOICE_OVERLAY))
    assert shot["prompt"] is True and shot["kind"] == "choice"


def test_screen_state_codex_quote_above_working_line_is_not_a_prompt():
    text = ("• Ran rg unanswered docs/ref/agents.md\n  Question 1/1 (1 unanswered)\n\n"
            "• Working (3s • esc to interrupt)\n\n› Ask Codex to do anything\n")
    assert D.screen_state("codex", text)["prompt"] is False
