"""agents.py — 워크트리 생성 시 바로 붙일 수 있는 AI CLI 표(N44).

2026-09-23: gemini를 agy(Antigravity, Gemini CLI 후계자)로 교체하고 qwen을
추가했다 — 이 표가 WorktreeDialog.tsx의 AGENT_OPTIONS·bin/fsh의 cmd_agent()와
셋이 같은 이름을 들고 있어야 한다(문서화된 계약, 단일 정의 없음).
"""

import agents


def test_known_agents_match_the_documented_set():
    assert set(agents.AGENTS) == {"claude", "codex", "aider", "agy", "qwen"}


def test_gemini_is_no_longer_supported():
    assert agents.get("gemini") is None
    assert agents.is_supported("gemini") is False


def test_agy_and_qwen_are_supported():
    assert agents.is_supported("agy") is True
    assert agents.is_supported("qwen") is True
    assert agents.get("agy").label == "Antigravity"
    assert agents.get("qwen").label == "Qwen"


def test_get_and_is_supported_reject_none_and_unknown():
    assert agents.get(None) is None
    assert agents.get("made-up") is None
    assert agents.is_supported(None) is False
    assert agents.is_supported("made-up") is False
