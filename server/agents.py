"""지원 AI CLI 에이전트 표 — 단일 정의 (N44, 30-worktree.md §3-5).

`claude|codex|aider|agy|qwen` 표. 예전엔 `bin/fsh`의 `cmd_agent()`(`fsh claude` /
`fsh agent`)가 같은 표를 따로 갖고 있었으나 2026-10-02에 그 명령을 제거해 이 파일이
유일한 정의다. 워크트리 만들기(§3 5단계: 만들자마자 붙일 에이전트)와 `fsh worktree
add --agent`가 이 표를 그대로 참조한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    name: str          # 'claude' | 'codex' | 'aider' | 'agy' | 'qwen'
    bin_name: str       # PATH에서 찾을 실행 파일명
    command: str        # tmux send-keys로 넣을 기동 커맨드
    label: str          # 사람이 읽을 이름


AGENTS: dict[str, AgentSpec] = {
    "claude": AgentSpec("claude", "claude", "claude", "Claude"),
    "codex": AgentSpec("codex", "codex", "codex", "Codex"),
    "aider": AgentSpec("aider", "aider", "aider", "Aider"),
    # 2026-09-23 — 구글의 이전 CLI가 2026-06-18로 종료되고 Antigravity CLI(agy)로
    # 통합돼 그 자리를 agy로 바꿨다(사용자 지시, agent_detector.py 쪽과 같은
    # 결정). qwen-code도 codex/aider급 독립 에이전트로 추가.
    "agy": AgentSpec("agy", "agy", "agy", "Antigravity"),
    "qwen": AgentSpec("qwen", "qwen", "qwen", "Qwen"),
}


def get(name: str | None) -> AgentSpec | None:
    if not name:
        return None
    return AGENTS.get(name)


def is_supported(name: str | None) -> bool:
    return bool(name) and name in AGENTS
