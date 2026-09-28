"""지원 AI CLI 에이전트 표 — 단일 정의 (N44, 30-worktree.md §3-5).

`bin/fsh`의 `cmd_agent()`가 갖고 있던 `claude|codex|aider|agy|qwen` 표를 여기로
옮겼다. 워크트리 만들기(§3 5단계: 만들자마자 붙일 에이전트)와 `fsh worktree
add --agent`가 이 표를 그대로 참조한다 — 두 곳에 같은 매핑을 다시 적지 않는다.

`bin/fsh`의 `cmd_agent()`는 `--disallowedTools` 자동 주입처럼 셸 환경에서만
뜻이 있는 로직이 섞여 있어 그대로 옮기지 않았다. 여기 있는 건 "실행 파일명 +
기동 커맨드"라는 순수 매핑뿐이다 — bin/fsh를 이 파일을 호출하도록 리팩터링하는
건 이번 범위(N8/N44) 밖이라 하지 않았다(bin/fsh의 tmux 소켓 로직은 90-verification.md
§4-3에서 "바꾸지 않는 것"으로 못박혀 있고, 셸→파이썬 호출 경계를 새로 만드는 건
별도 검토가 필요하다).
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
