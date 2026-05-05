"""AI CLI 자동 감지 — tmux pane의 현재 실행 명령으로 Claude/Codex/Aider/Gemini 식별."""

import os
import subprocess

TMUX_SOCKET = os.environ.get("VT_TMUX_SOCKET", "vt")
TMUX_BASE = ["tmux", "-L", TMUX_SOCKET]

KNOWN_AGENTS = {
    "claude": {"icon": "🟣", "label": "Claude", "color": "#A855F7"},
    "codex":  {"icon": "🟢", "label": "Codex",  "color": "#22C55E"},
    "aider":  {"icon": "🔵", "label": "Aider",  "color": "#3B82F6"},
    "gemini": {"icon": "🟡", "label": "Gemini", "color": "#EAB308"},
}


def detect(session_name: str) -> dict | None:
    """tmux 세션의 모든 pane을 검사해 실행 중인 AI CLI를 반환.

    Returns:
        {"agent": "claude", "icon": "🟣", "label": "Claude", "color": "#..."} 또는 None
    """
    if not session_name:
        return None
    try:
        out = subprocess.check_output(
            TMUX_BASE + ["list-panes", "-t", session_name, "-F", "#{pane_current_command}"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None

    for cmd in out.splitlines():
        cmd_lc = cmd.strip().lower()
        for key, info in KNOWN_AGENTS.items():
            if key in cmd_lc:
                return {"agent": key, **info}
    return None


def detect_all() -> dict[str, dict]:
    """모든 tmux 세션의 agent 상태를 dict로 반환."""
    try:
        out = subprocess.check_output(
            TMUX_BASE + ["list-sessions", "-F", "#{session_name}"],
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {}

    result = {}
    for name in out.splitlines():
        if not name:
            continue
        agent = detect(name)
        if agent:
            result[name] = agent
    return result
