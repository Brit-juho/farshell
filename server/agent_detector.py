"""AI CLI 자동 감지 (Phase 7 #7-1 + Phase 8 G6 + G8).

개선 사항:
- 자식 프로세스 스캔: pane_current_command가 'bash'여도 자식 'claude'를 잡음 (lunemis/mux 차용)
- 정확 매치: filepath.basename으로 false positive 방지 (substring → exact)
- 5초 TTL 캐시: 동일 panePID 반복 조회 비용 감소
- batch 패턴: list-panes -a 1회 호출로 모든 세션 처리 (purplemux 차용)
- macOS 호환: pgrep 실패 시 ps -eo pid,ppid fallback
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Optional

import tmux_runner
from ttl_cache import TTLCache

logger = logging.getLogger(__name__)

KNOWN_AGENTS = {
    "claude": {"icon": "🟣", "label": "Claude", "color": "#A855F7"},
    "codex":  {"icon": "🟢", "label": "Codex",  "color": "#22C55E"},
    "aider":  {"icon": "🔵", "label": "Aider",  "color": "#3B82F6"},
    "gemini": {"icon": "🟡", "label": "Gemini", "color": "#EAB308"},
}

# panePID → resolved command (5초 TTL)
_cmd_cache: TTLCache[str] = TTLCache(ttl=5.0)


def _find_child_pids(parent_pid: int) -> list[int]:
    """자식 PID 목록. pgrep 우선, 실패 시 ps -eo fallback (macOS 호환)."""
    if parent_pid <= 0:
        return []

    # 1차: pgrep -P (Linux/macOS 모두 견고)
    if shutil.which("pgrep"):
        try:
            out = subprocess.check_output(
                ["pgrep", "-P", str(parent_pid)],
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            ).decode()
            pids = [int(p) for p in out.split() if p.isdigit()]
            if pids:
                return pids
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # 2차 fallback: ps -eo pid,ppid
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,ppid"],
            stderr=subprocess.DEVNULL,
            timeout=1.0,
        ).decode()
        result = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) == parent_pid:
                if parts[0].isdigit():
                    result.append(int(parts[0]))
        return result
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _scan_child_processes(pane_pid: int, raw_cmd: str) -> str:
    """자식 프로세스 args 스캔해서 AI CLI 발견."""
    children = _find_child_pids(pane_pid)
    for pid in children:
        try:
            args_out = subprocess.check_output(
                ["ps", "-o", "args=", "-p", str(pid)],
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            ).decode()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            continue
        for part in args_out.split():
            base = os.path.basename(part)
            if base in KNOWN_AGENTS:
                return base
        # 재귀 1단계 — bash → bash → claude 같은 케이스 잡기 (limit 1)
        for grandchild in _find_child_pids(pid)[:5]:
            try:
                gc_args = subprocess.check_output(
                    ["ps", "-o", "args=", "-p", str(grandchild)],
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                ).decode()
            except Exception:
                continue
            for part in gc_args.split():
                base = os.path.basename(part)
                if base in KNOWN_AGENTS:
                    return base
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
    """단일 세션의 AI CLI 감지.

    Returns:
        {"agent": "claude", "icon": "🟣", "label": "Claude", "color": "#..."} 또는 None
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
    return None


def detect_all() -> dict[str, dict]:
    """모든 tmux 세션의 agent 상태 (batch 패턴 — 1회 호출)."""
    panes = tmux_runner.get_all_panes()
    if not panes:
        return {}

    # 세션별 첫 번째 매칭 AI 반환
    seen: set[str] = set()
    result: dict[str, dict] = {}
    for pane in panes:
        if pane.session in seen:
            continue
        resolved = _resolve_command(pane.pid, pane.command)
        if resolved in KNOWN_AGENTS:
            info = KNOWN_AGENTS[resolved]
            result[pane.session] = {"agent": resolved, **info}
            seen.add(pane.session)
    return result


def invalidate_cache(pane_pid: Optional[int] = None) -> None:
    """캐시 무효화. None이면 전체."""
    if pane_pid is None:
        _cmd_cache.clear()
    else:
        # prefix 매치 invalidate
        with _cmd_cache._lock:
            keys_to_remove = [k for k in _cmd_cache._store if k.startswith(f"{pane_pid}:")]
            for k in keys_to_remove:
                _cmd_cache._store.pop(k, None)
