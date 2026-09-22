"""agent_detector.py — DESIGN.md §4가 정의해둔 agent-ssh/agent-shell 마크가
실제로 채워지는지. AI CLI가 없으면 예전엔 `None`(=프런트가 배지를 통째로
숨김)이었는데, 이제 raw pane_current_command만으로 ssh/shell을 구분해 항상
무언가를 반환한다(사용자 결정 — 항상 배지, ssh는 자식 프로세스 스캔 없이
raw 명령만).
"""

import pytest

import agent_detector
import tmux_runner
from tmux_runner import PaneInfo


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # 알려진 CLI가 아닌 명령은 자식 프로세스까지 스캔하는데(bash 뒤에 숨은
    # claude를 잡으려고), 테스트에서는 그 subprocess 호출 자체를 없앤다 —
    # 없다고 답하면 _scan_child_processes가 raw_cmd를 그대로 돌려준다.
    monkeypatch.setattr(agent_detector, "_find_child_pids", lambda pid: [])
    agent_detector.invalidate_cache()
    yield
    agent_detector.invalidate_cache()


def _patch_list_panes(monkeypatch, text: str):
    monkeypatch.setattr(tmux_runner, "run_text", lambda args, timeout=None: text)


def test_detect_finds_known_agent(monkeypatch):
    _patch_list_panes(monkeypatch, "claude\t111")
    info = agent_detector.detect("dev")
    assert info["agent"] == "claude"


def test_detect_falls_back_to_ssh(monkeypatch):
    _patch_list_panes(monkeypatch, "ssh\t222")
    info = agent_detector.detect("dev")
    assert info["agent"] == "ssh"
    assert info["label"] == "SSH"


def test_detect_falls_back_to_shell_for_plain_zsh(monkeypatch):
    _patch_list_panes(monkeypatch, "zsh\t333")
    info = agent_detector.detect("dev")
    assert info["agent"] == "shell"
    assert info["label"] == "Shell"


def test_detect_prefers_agent_over_ssh_across_panes(monkeypatch):
    """같은 세션에 ssh 창과 claude 창이 둘 다 있으면 AI가 이긴다."""
    _patch_list_panes(monkeypatch, "ssh\t1\nclaude\t2")
    info = agent_detector.detect("dev")
    assert info["agent"] == "claude"


def test_detect_finds_agy(monkeypatch):
    """Gemini CLI 종료(2026-06-18) 후 후계자 Antigravity — 바이너리 agy."""
    _patch_list_panes(monkeypatch, "agy\t777")
    info = agent_detector.detect("dev")
    assert info["agent"] == "agy"
    assert info["label"] == "Antigravity"


def test_detect_no_longer_knows_gemini(monkeypatch):
    """옛 gemini 바이너리는 더 이상 AI CLI로 안 잡힌다(사용자 지시로 제거) — shell로 떨어진다."""
    _patch_list_panes(monkeypatch, "gemini\t778")
    info = agent_detector.detect("dev")
    assert info["agent"] == "shell"


def test_detect_finds_qwen(monkeypatch):
    """qwen-code — codex/aider와 같은 급의 독립 코딩 에이전트(로컬 LLM 칩이 아니다)."""
    _patch_list_panes(monkeypatch, "qwen\t888")
    info = agent_detector.detect("dev")
    assert info["agent"] == "qwen"
    assert info["label"] == "Qwen"


@pytest.mark.parametrize("raw_cmd,expected_agent,expected_label", [
    ("ollama", "ollama", "Ollama"),
    ("lms", "lms", "LM Studio"),
    ("llama-cli", "llamacpp", "llama.cpp"),
])
def test_detect_finds_local_llm_runners(monkeypatch, raw_cmd, expected_agent, expected_label):
    _patch_list_panes(monkeypatch, f"{raw_cmd}\t999")
    info = agent_detector.detect("dev")
    assert info["agent"] == expected_agent
    assert info["label"] == expected_label


@pytest.mark.parametrize("raw_cmd", ["python", "python3", "Python"])
def test_detect_finds_python_by_raw_command(monkeypatch, raw_cmd):
    """homebrew/framework 빌드는 comm이 대문자 'Python'으로 온다 — 셋 다 잡아야 한다."""
    _patch_list_panes(monkeypatch, f"{raw_cmd}\t444")
    info = agent_detector.detect("dev")
    assert info["agent"] == "python"
    assert info["label"] == "Python"


def test_detect_finds_node_by_raw_command(monkeypatch):
    _patch_list_panes(monkeypatch, "node\t555")
    info = agent_detector.detect("dev")
    assert info["agent"] == "node"
    assert info["label"] == "Node"


def test_detect_finds_docker_by_raw_command(monkeypatch):
    _patch_list_panes(monkeypatch, "docker\t666")
    info = agent_detector.detect("dev")
    assert info["agent"] == "docker"
    assert info["label"] == "Docker"


def test_detect_prefers_ssh_over_tool_across_panes(monkeypatch):
    _patch_list_panes(monkeypatch, "python\t1\nssh\t2")
    info = agent_detector.detect("dev")
    assert info["agent"] == "ssh"


def test_detect_prefers_agent_over_tool_across_panes(monkeypatch):
    _patch_list_panes(monkeypatch, "node\t1\nclaude\t2")
    info = agent_detector.detect("dev")
    assert info["agent"] == "claude"


def test_detect_no_panes_stays_none(monkeypatch):
    """세션 자체가 없어 판정 불가 — "shell"과는 다른, 진짜 모름."""
    _patch_list_panes(monkeypatch, "")
    assert agent_detector.detect("dev") is None


def test_detect_empty_name_is_none():
    assert agent_detector.detect("") is None


def test_detect_all_labels_every_session(monkeypatch):
    panes = [
        PaneInfo(session="ai", command="claude", pid=1, pane_id="%1"),
        PaneInfo(session="remote", command="ssh", pid=2, pane_id="%2"),
        PaneInfo(session="plain", command="zsh", pid=3, pane_id="%3"),
        PaneInfo(session="py", command="Python", pid=4, pane_id="%4"),
        PaneInfo(session="js", command="node", pid=5, pane_id="%5"),
        PaneInfo(session="ctr", command="docker", pid=6, pane_id="%6"),
        PaneInfo(session="antigrav", command="agy", pid=7, pane_id="%7"),
        PaneInfo(session="qwencoder", command="qwen", pid=8, pane_id="%8"),
        PaneInfo(session="localllm", command="ollama", pid=9, pane_id="%9"),
    ]
    monkeypatch.setattr(tmux_runner, "get_all_panes", lambda: panes)
    result = agent_detector.detect_all()
    assert result["ai"]["agent"] == "claude"
    assert result["remote"]["agent"] == "ssh"
    assert result["plain"]["agent"] == "shell"
    assert result["py"]["agent"] == "python"
    assert result["js"]["agent"] == "node"
    assert result["ctr"]["agent"] == "docker"
    assert result["antigrav"]["agent"] == "agy"
    assert result["qwencoder"]["agent"] == "qwen"
    assert result["localllm"]["agent"] == "ollama"


def test_detect_all_no_panes_is_empty(monkeypatch):
    monkeypatch.setattr(tmux_runner, "get_all_panes", lambda: [])
    assert agent_detector.detect_all() == {}
