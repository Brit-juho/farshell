"""AI CLI 자동 감지 (Phase 7 #7-1 + Phase 8 G6 + G8).

개선 사항:
- 자식 프로세스 스캔: pane_current_command가 'bash'여도 자식 'claude'를 잡음 (lunemis/mux 차용)
- 정확 매치: filepath.basename으로 false positive 방지 (substring → exact)
- 5초 TTL 캐시: 동일 panePID 반복 조회 비용 감소
- batch 패턴: list-panes -a 1회 호출로 모든 세션 처리 (purplemux 차용)
- 프로세스 표: `ps -A -o pid=,ppid=,args=` 한 번을 짧게 캐시해 모든 칸이 나눠 쓴다
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Optional

import tmux_runner
from ttl_cache import TTLCache

logger = logging.getLogger(__name__)

KNOWN_AGENTS = {
    "claude": {"icon": "🟣", "label": "Claude", "color": "#A855F7"},
    "codex":  {"icon": "🟢", "label": "Codex",  "color": "#22C55E"},
    "aider":  {"icon": "🔵", "label": "Aider",  "color": "#3B82F6"},
    # 2026-09-23(사용자 승인) — 구글이 2026-06-18로 이전 CLI를 종료하고
    # Antigravity CLI(바이너리 `agy`)로 통합했다. 이전 바이너리 감지는 제거했다
    # (사용자 지시). 프런트 마크(겹친 마름모)는 같은 제품 계보라 그대로 쓴다.
    "agy":    {"icon": "🟡", "label": "Antigravity", "color": "#EAB308"},
    # qwen-code(Alibaba, npm @qwen-code/qwen-code, 바이너리 `qwen`) — codex/aider와
    # 같은 급의 독립 코딩 에이전트 CLI라 로컬 LLM 칩이 아니라 여기(전용 마크)로
    # 넣는다(2026-09-23 설계 승인, A안).
    "qwen":   {"icon": "🟣", "label": "Qwen", "color": "#7C3AED"},
    # 2026-09-29(사용자 요청) — 브랜드 로고가 있는 코딩 에이전트 CLI 추가. 키는
    # 실행 바이너리 이름이다(자식 프로세스 args의 basename과 맞춘다).
    "opencode":     {"icon": "", "label": "opencode", "color": "#94A3B8"},
    "cursor-agent": {"icon": "", "label": "Cursor", "color": "#94A3B8"},
    "copilot":      {"icon": "", "label": "Copilot", "color": "#94A3B8"},
    "amp":          {"icon": "", "label": "Amp", "color": "#94A3B8"},
    "goose":        {"icon": "", "label": "Goose", "color": "#94A3B8"},
    "kimi":         {"icon": "", "label": "Kimi", "color": "#94A3B8"},
}

# AI CLI가 아닐 때도 "무엇이 떠 있는가"를 구분한다 — DESIGN.md §4가 처음부터
# `agent-ssh`/`agent-shell` 마크를 정의해뒀는데(N39, 2.1.2), 여기가 항상 None을
# 돌려주는 바람에 프런트에서 그 마크가 죽은 코드로 남아 있었다. `icon`은 다른
# 항목들과 형태를 맞추려고 둔 필드일 뿐 프런트는 안 읽는다(SVG 마크는
# frontend/js/ui/icons.js가 `agent` 문자열만 보고 자체적으로 고른다) — 그래서
# 이모지를 새로 추가하지 않는다.
SSH_INFO = {"icon": "", "label": "SSH", "color": "#64748B"}
SHELL_INFO = {"icon": "", "label": "Shell", "color": "#94A3B8"}

# AI CLI도 ssh도 아니지만 "뭘 돌리는지" 구분할 가치가 있는 것들 — python/node/
# docker. ssh와 같은 원칙(§164 주석 참고)으로 raw pane_current_command만
# 본다: 서버냐 스크립트냐(uvicorn/flask 등 argv 패턴)까지 구분하려면 child
# scan급 비용과 계속 늘어나는 패턴 목록이 필요해서 범위 밖으로 뒀다(2026-09-22
# 설계 승인 — "python이면 뭘 하든 그냥 agent-python"). homebrew/framework
# 빌드의 python3는 comm이 "Python"(대문자)으로 온다 — 실측 확인.
TOOL_INFO = {
    "python": {"icon": "", "label": "Python", "color": "#3776AB"},
    "node": {"icon": "", "label": "Node", "color": "#5FA04E"},
    "docker": {"icon": "", "label": "Docker", "color": "#2496ED"},
    # 2026-09-23(사용자 승인) — 로컬 LLM 러너 3종. "터미널에서 지금 보고 있는
    # CLI"라는 이 기능 취지와 맞는 것만 골랐다(값싼 신호 원칙은 위와 같음) —
    # vLLM/Docker Model Runner는 서버로 띄워두는 용도라 뺐다. 프런트에서 셋 다
    # agent-local(칩) 마크를 공유하고 라벨만 다르다 — DESIGN.md §5가 원래도
    # "로컬 LLM 하나"로 뭉뚱그린 설계였다.
    "ollama": {"icon": "", "label": "Ollama", "color": "#94A3B8"},
    "lms": {"icon": "", "label": "LM Studio", "color": "#94A3B8"},
    "llamacpp": {"icon": "", "label": "llama.cpp", "color": "#94A3B8"},
}
_RAW_TOOL_CMDS = {
    "python": "python", "python3": "python", "Python": "python",
    "node": "node",
    "docker": "docker",
    "ollama": "ollama",
    "lms": "lms",
    "llama-cli": "llamacpp",
}

# panePID → resolved command (5초 TTL)
_cmd_cache: TTLCache[str] = TTLCache(ttl=5.0)

# 프로세스 표 한 장(pid → (ppid, args)). 예전엔 칸마다 `pgrep -P`·`ps -o args= -p`를 따로 띄워
# 5초 루프 한 번에 칸 수 × (1 + 자식 수 + 손자 수)개의 프로세스를 만들었다(2026-11-26 실측: 서버가
# 30초에 `ps` 20개). 이제 짧은 창 안의 조회는 `ps` 한 번을 나눠 쓴다. 창은 루프 간격(5초)보다
# 짧게 둬서, 루프마다 새 표를 읽는다(판정이 예전보다 늦어지지 않게).
PROC_TABLE_TTL = 2.0
_proc_lock = threading.Lock()
_proc_table: Optional[dict[int, tuple[int, str]]] = None
_proc_table_at = 0.0


def parse_ps_table(text: str) -> dict[int, tuple[int, str]]:
    """`ps -A -o pid=,ppid=,args=` 출력 → {pid: (ppid, args)}. 순수 함수."""
    table: dict[int, tuple[int, str]] = {}
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def _process_table() -> Optional[dict[int, tuple[int, str]]]:
    """짧게 캐시한 프로세스 표. 못 읽으면 None(호출자는 '자식 없음'으로 본다)."""
    global _proc_table, _proc_table_at
    with _proc_lock:
        now = time.monotonic()
        if _proc_table is not None and now - _proc_table_at < PROC_TABLE_TTL:
            return _proc_table
        try:
            out = subprocess.check_output(
                ["ps", "-ww", "-A", "-o", "pid=,ppid=,args="],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", errors="replace")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return None
        _proc_table, _proc_table_at = parse_ps_table(out), now
        return _proc_table


def _find_child_pids(parent_pid: int) -> list[int]:
    """자식 PID 목록(프로세스 표 한 장에서)."""
    if parent_pid <= 0:
        return []
    table = _process_table()
    if not table:
        return []
    return [pid for pid, (ppid, _) in table.items() if ppid == parent_pid]


def _proc_args(pid: int) -> str:
    table = _process_table()
    entry = table.get(pid) if table else None
    return entry[1] if entry else ""


def _has_known_agent(args: str) -> Optional[str]:
    for part in args.split():
        base = os.path.basename(part)
        if base in KNOWN_AGENTS:
            return base
    return None


def _scan_child_processes(pane_pid: int, raw_cmd: str) -> str:
    """자식 프로세스 args 스캔해서 AI CLI 발견."""
    for pid in _find_child_pids(pane_pid):
        found = _has_known_agent(_proc_args(pid))
        if found:
            return found
        # 재귀 1단계 — bash → bash → claude 같은 케이스 잡기 (limit 1)
        for grandchild in _find_child_pids(pid)[:5]:
            found = _has_known_agent(_proc_args(grandchild))
            if found:
                return found
    return raw_cmd


def _resolve_command(pane_pid: int, raw_cmd: str) -> str:
    """raw_cmd → 실제 AI CLI 이름 (캐시 + 자식 스캔)."""
    # fast path: raw가 정확 매치
    if raw_cmd in KNOWN_AGENTS:
        return raw_cmd

    cache_key = f"{pane_pid}:{raw_cmd}"
    cached = _cmd_cache.get(cache_key)
    if cached is not None:
        return cached

    resolved = _scan_child_processes(pane_pid, raw_cmd)
    _cmd_cache.set(cache_key, resolved)
    return resolved


def detect(session_name: str) -> Optional[dict]:
    """단일 세션의 AI CLI 감지 — 없으면 ssh/shell로 폴백(그래도 그냥 None은 아니다).

    Returns:
        {"agent": "claude"|"codex"|"aider"|"agy"|"qwen"|"opencode"|"cursor-agent"|"copilot"|"amp"|"goose"|"kimi"|"ssh"
                  |"python"|"node"|"docker"|"ollama"|"lms"|"llamacpp"|"shell",
         "icon": ..., "label": ..., "color": "#..."}
        세션 자체가 없거나(no panes) session_name이 비어 있으면 None(=판정 불가, "shell"과는 다르다).
    """
    if not session_name:
        return None

    fmt = "#{pane_current_command}\t#{pane_pid}"
    text = tmux_runner.run_text(
        ["list-panes", "-t", session_name, "-F", fmt],
        timeout=2.0,
    )
    if not text:
        return None

    found_ssh = False
    found_tool: Optional[str] = None
    for line in text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        cmd = parts[0]
        try:
            pid = int(parts[1])
        except ValueError:
            pid = 0
        resolved = _resolve_command(pid, cmd)
        if resolved in KNOWN_AGENTS:
            info = KNOWN_AGENTS[resolved]
            return {"agent": resolved, **info}
        # raw pane_current_command만 본다(자식 프로세스까지 스캔하지 않는다) —
        # zsh 안에서 alias/스크립트로 실행된 ssh까지 잡으려면 AI 판정과 같은
        # 무게의 child scan이 매 세션마다 하나씩 더 붙는다. 값싼 신호만 쓴다.
        if cmd == "ssh":
            found_ssh = True
        elif found_tool is None and cmd in _RAW_TOOL_CMDS:
            found_tool = _RAW_TOOL_CMDS[cmd]
    if found_ssh:
        return {"agent": "ssh", **SSH_INFO}
    if found_tool:
        return {"agent": found_tool, **TOOL_INFO[found_tool]}
    return {"agent": "shell", **SHELL_INFO}


def detect_all() -> dict[str, dict]:
    """모든 tmux 세션의 agent 상태 (batch 패턴 — 1회 호출).

    AI를 못 찾은 세션도 이제 결과에 들어간다(ssh/shell) — `detect()`와 같은
    폴백. 세션당 판정은 하나뿐이라 먼저 세션별로 pane을 모아 확정 짓는다.
    """
    panes = tmux_runner.get_all_panes()
    if not panes:
        return {}

    by_session: dict[str, list] = {}
    for pane in panes:
        by_session.setdefault(pane.session, []).append(pane)

    result: dict[str, dict] = {}
    for session, session_panes in by_session.items():
        found_ssh = False
        found_tool: Optional[str] = None
        matched = None
        for pane in session_panes:
            resolved = _resolve_command(pane.pid, pane.command)
            if resolved in KNOWN_AGENTS:
                matched = resolved
                break
            if pane.command == "ssh":
                found_ssh = True
            elif found_tool is None and pane.command in _RAW_TOOL_CMDS:
                found_tool = _RAW_TOOL_CMDS[pane.command]
        if matched:
            result[session] = {"agent": matched, **KNOWN_AGENTS[matched]}
        elif found_ssh:
            result[session] = {"agent": "ssh", **SSH_INFO}
        elif found_tool:
            result[session] = {"agent": found_tool, **TOOL_INFO[found_tool]}
        else:
            result[session] = {"agent": "shell", **SHELL_INFO}
    return result


def invalidate_cache(pane_pid: Optional[int] = None) -> None:
    """캐시 무효화. None이면 전체."""
    global _proc_table
    if pane_pid is None:
        _cmd_cache.clear()
        with _proc_lock:
            _proc_table = None
    else:
        # prefix 매치 invalidate
        with _cmd_cache._lock:
            keys_to_remove = [k for k in _cmd_cache._store if k.startswith(f"{pane_pid}:")]
            for k in keys_to_remove:
                _cmd_cache._store.pop(k, None)
