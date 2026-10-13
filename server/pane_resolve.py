"""A2 — "이 훅 이벤트가 어느 tmux 세션의 것인가"를 3단으로 판정한다.

ADR-4: **자기보고가 1차, cwd는 폴백.**

    1. pane 자기보고 — 훅이 실어 보낸 $TMUX_PANE을 서버의 pane 목록과 정확 매칭
    2. cwd 일치 — 자기보고가 없거나 못 믿을 때. 후보가 **정확히 하나일 때만**
    3. 포기 — None. 아무것도 강조하지 않는다

Codex 훅은 예외다. 공유 app-server 데몬이 처음 연결된 TUI의 `$TMUX_PANE`을
다른 세션의 훅에도 전달할 수 있으므로, Codex 훅은 pane을 버리고 유일한 cwd로
처음 연결한다. 같은 Codex session_id의 후속 이벤트는 agent_status의 연결을
유지한다. `fsh pane report`처럼 호출자 셸이 직접 보낸 값은 기존 pane 우선이다.

3번이 이 파일에서 가장 중요한 규칙이다. 같은 cwd에 세션이 둘 이상이면(둘 다
`$HOME`에서 띄우는 건 흔하다) 확신 있게 엉뚱한 세션을 켜는 것보다 아무것도 안
켜는 게 안전하다 — 기존 `tmux_target.session_for_cwd`와 `grid.js`가 이미 같은
판단을 하고 있고 회귀 테스트도 있다. A2는 그 규칙을 **없애는 게 아니라**,
1단계를 앞에 붙여 "모호해서 포기하는" 경우 자체를 줄인다.

**`TMUX` 소켓 검증이 필요한 이유**: FarShell은 `-L fsh` 격리 소켓을 쓴다(Phase 6
원칙). 사용자가 자기 개인 tmux(`default` 소켓)에서 claude를 띄웠다면 거기서 온
`%12`라는 pane id가 **우리 소켓의 전혀 다른 pane과 우연히 같을 수 있다.**
그러면 엉뚱한 세션에 배지가 뜨고, 최악의 경우 큐가 거기로 투입된다. 그래서
`TMUX` 환경변수의 첫 필드(소켓 경로)가 우리 것일 때만 pane id를 신뢰하고,
아니면 버리고 cwd 폴백으로 내려간다.
"""

from __future__ import annotations

import os
from typing import Optional

import tmux_runner


def _our_socket_names() -> set[str]:
    # tmux는 `-L <name>`을 `<tmpdir>/tmux-<uid>/<name>` 소켓 파일로 만든다.
    # 이름만 비교하면 충분하다(경로 앞부분은 tmux/OS가 정한다).
    return {tmux_runner.VT_TMUX_SOCKET, os.environ.get("VT_TMUX_SOCKET", "")} - {""}


def is_our_socket(tmux_env: Optional[str]) -> bool:
    """`$TMUX`(형식: `<소켓경로>,<pid>,<세션번호>`)가 우리 소켓인지.

    값이 없으면(=tmux 밖에서 실행) False. 판정 불가도 False로 떨어뜨린다 —
    "확실히 우리 것"일 때만 pane id를 신뢰한다.
    """
    if not tmux_env:
        return False
    socket_path = tmux_env.split(",", 1)[0]
    if not socket_path:
        return False
    return os.path.basename(socket_path) in _our_socket_names()


def session_for_pane(pane_id: Optional[str]) -> Optional[str]:
    """pane id → tmux 세션 이름. 우리 소켓에 그 pane이 없으면 None."""
    if not pane_id:
        return None
    for p in tmux_runner.get_all_panes():
        if p.pane_id and p.pane_id == pane_id:
            return p.session
    return None


def session_for_cwd(cwd: Optional[str], prefer_command: Optional[str] = None) -> Optional[str]:
    """cwd → tmux 세션 이름. 후보가 정확히 하나일 때만(모호하면 None).

    `tmux_target.session_for_cwd`와 같은 규칙이다. 그쪽은 큐 전용 모듈이라
    의존 방향(pane_resolve → tmux_target)을 만들지 않으려고 여기서 같은
    판정을 직접 한다 — 규칙이 갈라지지 않도록 두 곳 모두 테스트로 고정돼 있다.

    `prefer_command`가 있으면 그 cwd의 pane 중 그 명령이 떠 있는 pane만 후보로 본다(그런
    pane이 하나도 없으면 cwd 후보 전체). Codex 훅은 pane을 믿을 수 없어 cwd로 찾는데, 한
    저장소에 세션이 여럿이어도 Codex가 떠 있는 세션은 대개 하나다(2026-10-13).
    """
    if not cwd:
        return None
    panes = [p for p in tmux_runner.get_all_panes() if p.path == cwd]
    if prefer_command:
        running = [p for p in panes if os.path.basename(p.command or "") == prefer_command]
        if running:
            panes = running
    matches = {p.session for p in panes}
    return next(iter(matches)) if len(matches) == 1 else None


def resolve(pane: Optional[str], tmux_env: Optional[str], cwd: Optional[str],
            *, agent: Optional[str] = None, pane_verified: bool = False,
            cwd_fallback: bool = True) -> tuple[Optional[str], str]:
    """3단 해석. (세션 이름 또는 None, 근거) 를 반환한다.

    근거 문자열은 진단용이다 — `fsh pane report`와 로그가 "왜 이 세션으로
    판정했는지"를 보여줘야 사용자가 오탐을 신고할 수 있다.
      pane          — 자기보고 pane id로 정확 매칭
      cwd           — cwd가 유일해서 매칭
      foreign-tmux  — 우리 소켓이 아니라 pane id를 버렸고 cwd로도 못 찾음
      ambiguous     — cwd 후보가 둘 이상 → 포기(아무것도 강조하지 않는다)
      pane-gone     — 우리 소켓의 pane id인데 그 pane이 이미 없음 → cwd로 추측하지 않는다
      no-pane       — pane이 없고 cwd 폴백을 끈 호출(Claude 훅: tmux 밖에서 도는 Claude)
      none          — 단서 자체가 없음

    `pane_verified` — hook_identity가 훅 프로세스를 그 pane의 포그라운드 에이전트로 확인했다.
    Codex라도 그때는 pane을 믿는다(데몬이 아니라 TUI 프로세스 트리에서 돈 훅이다).
    `cwd_fallback=False` — pane 단서가 없으면 cwd로 찾지 않는다. Claude 훅은 pane 셸의 자식이라
    `TMUX_PANE`이 없다면 tmux 밖(데스크톱 앱·IDE·다른 터미널)에서 도는 Claude다. 예전엔 그
    상태가 같은 cwd의 FarShell 세션에 붙었다(2026-10-13).
    """
    # Codex 0.160의 훅은 공유 app-server 데몬에서 실행될 수 있다. 데몬이 처음
    # 시작된 pane의 TMUX_PANE을 다른 Codex 세션 훅에도 물려주므로 이 값은
    # Codex 훅의 출처 증명이 아니다. cwd가 유일할 때만 처음 연결하고, 뒤따르는
    # cwd 없는 이벤트는 agent_status가 session_id의 기존 연결을 유지한다.
    if agent == "codex" and not pane_verified:
        pane = None
    trusted_pane = is_our_socket(tmux_env)
    if pane and trusted_pane:
        session = session_for_pane(pane)
        if session:
            return session, "pane"
        # 우리 소켓의 pane이 이미 없다 — 그 세션이 닫히면서 에이전트가 마지막 훅(Stop·SessionEnd)을
        # 보낸 것이다. cwd로 추측하면 **같은 폴더에 남은 다른 세션**이 그 상태를 받는다(2026-10-13
        # 실측: 하위 세션을 닫자 부모가 done이 돼 큐가 부모의 셸에 입력됐다). 출처가 확실한 단서가
        # 사라졌다는 사실을 존중한다.
        return None, "pane-gone"
    if not cwd_fallback:
        return None, "foreign-tmux" if pane else "no-pane"
    session = session_for_cwd(cwd, prefer_command="codex" if agent == "codex" else None)
    if session:
        return session, "cwd"
    if pane and not trusted_pane:
        return None, "foreign-tmux"
    if cwd:
        # cwd는 있는데 못 찾았다 — 후보가 여럿이거나(모호) 아예 없거나.
        matches = {p.session for p in tmux_runner.get_all_panes() if p.path == cwd}
        return None, "ambiguous" if len(matches) > 1 else "none"
    return None, "none"
