"""A2 — "이 훅 이벤트가 어느 tmux 세션의 것인가"를 3단으로 판정한다.

ADR-4: **자기보고가 1차, cwd는 폴백.**

    1. pane 자기보고 — 훅이 실어 보낸 $TMUX_PANE을 서버의 pane 목록과 정확 매칭
    2. cwd 일치 — 자기보고가 없거나 못 믿을 때. 후보가 **정확히 하나일 때만**
    3. 포기 — None. 아무것도 강조하지 않는다

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


def session_for_cwd(cwd: Optional[str]) -> Optional[str]:
    """cwd → tmux 세션 이름. 후보가 정확히 하나일 때만(모호하면 None).

    `tmux_target.session_for_cwd`와 같은 규칙이다. 그쪽은 큐 전용 모듈이라
    의존 방향(pane_resolve → tmux_target)을 만들지 않으려고 여기서 같은
    판정을 직접 한다 — 규칙이 갈라지지 않도록 두 곳 모두 테스트로 고정돼 있다.
    """
    if not cwd:
        return None
    matches = {p.session for p in tmux_runner.get_all_panes() if p.path == cwd}
    return next(iter(matches)) if len(matches) == 1 else None


def resolve(pane: Optional[str], tmux_env: Optional[str], cwd: Optional[str]) -> tuple[Optional[str], str]:
    """3단 해석. (세션 이름 또는 None, 근거) 를 반환한다.

    근거 문자열은 진단용이다 — `fsh pane report`와 로그가 "왜 이 세션으로
    판정했는지"를 보여줘야 사용자가 오탐을 신고할 수 있다.
      pane          — 자기보고 pane id로 정확 매칭
      cwd           — cwd가 유일해서 매칭
      foreign-tmux  — 우리 소켓이 아니라 pane id를 버렸고 cwd로도 못 찾음
      ambiguous     — cwd 후보가 둘 이상 → 포기(아무것도 강조하지 않는다)
      none          — 단서 자체가 없음
    """
    trusted_pane = is_our_socket(tmux_env)
    if pane and trusted_pane:
        session = session_for_pane(pane)
        if session:
            return session, "pane"
    session = session_for_cwd(cwd)
    if session:
        return session, "cwd"
    if pane and not trusted_pane:
        return None, "foreign-tmux"
    if cwd:
        # cwd는 있는데 못 찾았다 — 후보가 여럿이거나(모호) 아예 없거나.
        matches = {p.session for p in tmux_runner.get_all_panes() if p.path == cwd}
        return None, "ambiguous" if len(matches) > 1 else "none"
    return None, "none"
