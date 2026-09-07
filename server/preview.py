"""tmux pane 라이브 프리뷰 (Phase 7 #7-3 → Phase 9 #1: ws push 전환).

다른 세션의 화면을 ANSI escape 보존한 채 캡처. v1.2.x까지는 1초 폴링 + 1초 TTL
캐시였지만, Phase 9 #1에서 server-side watcher가 변화 감지 시에만 구독자에게
push하는 모델로 전환. 폴링 트래픽 99% 감소.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Awaitable, Callable, Optional

import tmux_runner
from ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# frontend/js/lib/ansilex.js의 ANSI_STRIP_RE와 같은 목적(줄이 "글자 없이 escape만"인지
# 판정) — 여긴 서버 쪽 트렁케이션(_capture_no_cache)에서 "진짜로 빈 줄인지" 볼 때만 쓴다.
_ANSI_STRIP_RE = re.compile(r"\x1b\[[\d;]*m|\x1b\[[?\d;]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _is_blank_line(line: str) -> bool:
    return _ANSI_STRIP_RE.sub("", line).strip() == ""

# 1초 TTL — REST 호환 (capture_pane을 직접 호출하는 구식 클라이언트도 보호)
_preview_cache: TTLCache[str] = TTLCache(ttl=1.0)

# Phase 9 #1: 세션별 구독자 + 마지막 hash. watcher가 변화만 push.
_subscribers: dict[str, set[Callable[[str], Awaitable[None]]]] = {}
_last_hash: dict[str, str] = {}
_watcher_task: Optional[asyncio.Task] = None
WATCH_INTERVAL = 0.25  # 250ms — 변화 감지 latency 상한


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
    text = _capture_no_cache(session_name, lines=lines, ansi=ansi)
    if text is not None:
        _preview_cache.set(cache_key, text)
    return text


def _capture_no_cache(session_name: str, lines: int = 20, ansi: bool = True) -> Optional[str]:
    """캐시를 우회한 직접 캡처. watcher가 변화 감지에 사용한다."""
    args = ["capture-pane", "-t", session_name, "-p"]
    if ansi:
        args.append("-e")
    if lines > 0:
        args.extend(["-S", f"-{lines}"])
    text = tmux_runner.run_text(args, timeout=1.5)
    if text is None:
        return None
    if lines > 0:
        all_lines = text.split("\n")
        # 실측 버그(2026-09-07, L6 검증 중 발견): `-S -{lines}`는 캡처의 시작점만
        # 정하고 끝은 항상 현재 화면 맨 아래다 — 창이 `lines`보다 크고(예: 53행)
        # 방금 만든 세션처럼 실제 내용이 위쪽 몇 줄뿐이면 총 줄 수가 `lines`를
        # 넘고 뒤쪽은 전부 빈 줄로 채워진다. 그 상태에서 "끝에서부터 lines줄"을
        # 그대로 자르면 내용이 아니라 뒤쪽 빈 줄들만 남아 프리뷰가 통째로
        # 비어 보인다(pane-picker.js·quickopen.js의 라이브 썸네일에서 재현 확인).
        # 뒤쪽 빈 줄부터 걷어낸 뒤에 끝에서부터 잘라야 실제 최신 내용이 남는다 —
        # 화면을 꽉 채우는 진짜 긴 출력(빈 줄 없음)에서는 이 정리가 아무 것도
        # 안 걷어내므로 기존 동작과 동일하다.
        while all_lines and _is_blank_line(all_lines[-1]):
            all_lines.pop()
        if len(all_lines) > lines:
            all_lines = all_lines[-lines:]
        text = "\n".join(all_lines)
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


# ── Phase 9 #1: ws push 인프라 ─────────────────────────────────────

async def subscribe(session_name: str, callback: Callable[[str], Awaitable[None]]) -> None:
    """세션의 변화를 구독한다. 즉시 현재 상태 1회 푸시.

    callback은 (text: str) -> Awaitable[None] 시그니처.
    """
    _subscribers.setdefault(session_name, set()).add(callback)
    # P1: capture-pane은 blocking subprocess(최대 1.5s)라 이벤트 루프에서 직접 돌리면
    # 그동안 모든 WS(터미널 포함) I/O가 멈춘다. executor로 offload한다.
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, _capture_no_cache, session_name)
    if text is not None:
        try:
            await callback(text)
            _last_hash[session_name] = hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()
        except Exception:
            pass
    _ensure_watcher()


def unsubscribe(session_name: str, callback: Callable[[str], Awaitable[None]]) -> None:
    subs = _subscribers.get(session_name)
    if subs:
        subs.discard(callback)
        if not subs:
            _subscribers.pop(session_name, None)
            _last_hash.pop(session_name, None)


def _ensure_watcher() -> None:
    global _watcher_task
    if _watcher_task and not _watcher_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _watcher_task = loop.create_task(_watch_loop())


async def _watch_loop() -> None:
    """모든 구독 세션을 WATCH_INTERVAL마다 캡처 → 변화 시 push.

    구독자가 0이면 idle 모드(1초 sleep)로 들어가 CPU 절약.
    """
    loop = asyncio.get_running_loop()
    while True:
        if not _subscribers:
            await asyncio.sleep(1.0)
            continue
        for name in list(_subscribers.keys()):
            # P1: blocking capture-pane을 executor로 — 이벤트 루프 정지 방지.
            text = await loop.run_in_executor(None, _capture_no_cache, name)
            if text is None:
                continue
            h = hashlib.md5(text.encode("utf-8", "ignore")).hexdigest()
            if _last_hash.get(name) == h:
                continue
            _last_hash[name] = h
            for cb in list(_subscribers.get(name, ())):
                try:
                    await cb(text)
                except Exception:
                    _subscribers.get(name, set()).discard(cb)
        await asyncio.sleep(WATCH_INTERVAL)
