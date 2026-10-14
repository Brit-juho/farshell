"""훅을 보낸 프로세스가 정말 그 pane의 대화형 에이전트인가 (2026-10-13, docs/plan-2.4/60 P2).

`$TMUX_PANE`은 상속값이다. 같은 pane에서 시작된 프로세스는 전부 그 값을 물려받는다 —
Bash 도구로 띄운 `claude -p`, MCP 서버가 띄운 하위 claude, 데몬이 된 Codex app-server.
그 훅이 pane을 주장하면 엉뚱한 상태가 그 세션에 붙었다. herdr가 같은 사고를 겪고 포그라운드
프로세스 대조(`tcgetpgrp`)로 막았다.

훅은 자기 pid(`hook_pid`, agent_hook.sh를 돌리는 셸)를 실어 보내고, 서버는 그 요청을 처리하는
동안(훅은 응답을 기다린다) 프로세스 표를 한 번 읽어 판정한다:

  1. 훅 pid에서 위로 올라가며 셸(sh/bash/zsh/dash/fish)을 건너뛴 첫 프로세스 = **에이전트**.
  2. 에이전트의 조상에 그 pane의 `pane_pid`가 없다 → `foreign`(pane 주장이 거짓 — 데몬 등).
  3. `~/.claude/sessions/<에이전트 pid>.json`이 터미널 대화형(`entrypoint: "cli"`)이고 그
     sessionId가 훅의 session_id와 같다 → `ok` (Claude Code가 세션마다 쓰는 파일. 문서화되지
     않은 내부 파일이라 없거나 형식이 다르면 조용히 다음 규칙으로 넘어간다).
  4. 에이전트에서 프로세스 그룹 리더까지 가는 길에 셸이 아닌 프로세스가 있으면 `child`
     (MCP 서버가 띄운 claude는 부모 claude의 그룹을 물려받는다). 그룹이 그 tty의 포그라운드가
     아니면 `child`(Bash 도구·백그라운드 작업은 tty 없는 별도 그룹이다). 아니면 `ok`.

판정할 수 없으면(옛 훅이 pid를 안 보냄, ps 실패) `unknown` — 호출부는 예전 규칙을 쓴다.

`child`는 이벤트를 버리라는 뜻이 아니라 **그 pane의 소유권을 가져갈 수 없다**는 뜻이다
(agent_status `verified=False`). 래퍼 프로그램으로 띄운 진짜 에이전트를 자식으로 오판해도
소유자가 없는 세션에서는 그대로 동작하게 하려는 것이다. `ok`는 소유자를 바로 넘겨받는다.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

import tmux_runner

logger = logging.getLogger(__name__)

OK = "ok"
CHILD = "child"
FOREIGN = "foreign"
UNKNOWN = "unknown"

_SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh", "tcsh", "csh"}
_MAX_DEPTH = 32


def _ps_table() -> Optional[dict[int, tuple[int, int, int, str]]]:
    """pid → (ppid, pgid, tpgid, comm). 실패하면 None."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,tpgid=,comm="],
                             capture_output=True, text=True, timeout=2.0)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    table: dict[int, tuple[int, int, int, str]] = {}
    for line in out.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid, ppid, pgid, tpgid = (int(x) for x in parts[:4])
        except ValueError:
            continue
        table[pid] = (ppid, pgid, tpgid, parts[4].strip())
    return table


def _is_shell(comm: str) -> bool:
    # macOS는 경로째(`/bin/zsh`), 로그인 셸은 `-zsh`로 보인다.
    return os.path.basename(comm).lstrip("-") in _SHELLS


def _claude_sessions_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "sessions"


def claude_session_id_for(pid: int) -> Optional[str]:
    """`~/.claude/sessions/<pid>.json`의 sessionId — 터미널 대화형 세션일 때만. 아니면 None.

    `claude -p`도 실행 중에는 같은 파일을 `kind: "interactive"`로 쓴다(2026-10-13 실측,
    2.1.296). 구별되는 값은 `entrypoint`다: 터미널 대화형은 `cli`, `-p`는 `sdk-cli`. 이걸
    안 보면 Bash 도구로 띄운 `claude -p`가 pane 소유권을 가져갔다(격리 실측).
    """
    try:
        data = json.loads((_claude_sessions_dir() / f"{pid}.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("pid") not in (pid, str(pid)):
        return None
    if data.get("entrypoint") != "cli":
        return None
    sid = data.get("sessionId")
    return sid if isinstance(sid, str) and sid else None


def _pane_pid(pane_id: str) -> Optional[int]:
    for p in tmux_runner.get_all_panes():
        if p.pane_id == pane_id:
            return p.pid or None
    return None


def classify(hook_pid, pane_id: Optional[str], session_id: Optional[str] = None,
             table: Optional[dict] = None, pane_pid: Optional[int] = None) -> tuple[str, Optional[int]]:
    """(판정, 에이전트 pid). 모듈 주석의 규칙. table/pane_pid는 테스트용 주입."""
    if not isinstance(hook_pid, int) or isinstance(hook_pid, bool) or hook_pid <= 1 or not pane_id:
        return UNKNOWN, None
    if pane_pid is None:
        pane_pid = _pane_pid(pane_id)
    if not pane_pid:
        return UNKNOWN, None
    if table is None:
        table = _ps_table()
    if not table or hook_pid not in table:
        return UNKNOWN, None

    # 1. 셸을 건너뛴 첫 프로세스.
    agent = hook_pid
    for _ in range(_MAX_DEPTH):
        row = table.get(agent)
        if row is None:
            return UNKNOWN, None
        if not _is_shell(row[3]):
            break
        agent = row[0]
    else:
        return UNKNOWN, None
    if agent <= 1:
        return FOREIGN, None

    # 2. 에이전트가 그 pane 아래에 있는가.
    cur, inside = agent, False
    for _ in range(_MAX_DEPTH):
        if cur == pane_pid:
            inside = True
            break
        row = table.get(cur)
        if row is None or row[0] in (0, cur):
            break
        cur = row[0]
    if not inside:
        return FOREIGN, agent

    # 3. Claude가 직접 쓴 세션 파일.
    if session_id and claude_session_id_for(agent) == session_id:
        return OK, agent

    # 4. 그룹 리더까지 셸만 거치고, 그 그룹이 포그라운드인가.
    _, pgid, tpgid, _ = table[agent]
    cur = agent
    for _ in range(_MAX_DEPTH):
        if cur == pgid:
            break
        row = table.get(cur)
        if row is None:
            return CHILD, agent
        cur = row[0]
        if cur != pgid and cur in table and not _is_shell(table[cur][3]):
            return CHILD, agent
        if cur in (0, 1):
            return CHILD, agent
    if pgid != agent and pgid in table and not _is_shell(table[pgid][3]):
        return CHILD, agent   # 다른 프로그램이 이끄는 그룹 안에서 뜬 자식(예: claude가 직접 띄운 claude -p)
    if pgid <= 0 or pgid != tpgid:
        return CHILD, agent
    return OK, agent
