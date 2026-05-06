"""tmux pane 라이브 프리뷰 (Phase 7 #7-3).

다른 세션의 화면을 ANSI escape 보존한 채 캡처.
1초 TTL 캐시로 다중 클라이언트 폴링 비용 감소.

lunemis/mux의 tmux/capture.go + ui/preview.go를 웹용으로 변형.
"""

from __future__ import annotations

import logging
from typing import Optional

import tmux_runner
from ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# 1초 TTL — 1초 폴링하는 N개 클라이언트가 있어도 tmux 호출은 초당 1회
_preview_cache: TTLCache[str] = TTLCache(ttl=1.0)


def capture_pane(session_name: str, lines: int = 20, ansi: bool = True) -> Optional[str]:
    """tmux 세션의 활성 pane을 캡처.

    Args:
        session_name: 대상 세션 이름
        lines: 마지막 N줄 (default 20). -S -<lines>로 scrollback 포함
        ansi: True면 -e 옵션으로 ANSI escape 보존 (색깔/굵기 유지)

    Returns:
        캡처된 텍스트 (실패 시 None)
    """
    if not session_name:
        return None

    cache_key = f"{session_name}:{lines}:{ansi}"
    cached = _preview_cache.get(cache_key)
    if cached is not None:
        return cached

    args = ["capture-pane", "-t", session_name, "-p"]
    if ansi:
        args.append("-e")
    if lines > 0:
        args.extend(["-S", f"-{lines}"])

    text = tmux_runner.run_text(args, timeout=1.5)
    if text is None:
        return None

    # 마지막 N줄로 제한 (capture-pane이 정확히 N줄 보장 안 할 수 있음)
    if lines > 0:
        all_lines = text.split("\n")
        if len(all_lines) > lines:
            text = "\n".join(all_lines[-lines:])

    _preview_cache.set(cache_key, text)
    return text


def invalidate(session_name: Optional[str] = None) -> None:
    """캐시 무효화. send-keys 직후 호출 가능."""
    if session_name is None:
        _preview_cache.clear()
        return
    # prefix 매치 무효화
    with _preview_cache._lock:
        keys = [k for k in _preview_cache._store if k.startswith(f"{session_name}:")]
        for k in keys:
            _preview_cache._store.pop(k, None)
