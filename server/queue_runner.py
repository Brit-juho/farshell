"""큐 드레인 — 쌓아둔 프롬프트를 tmux pane 에 순차 투입 (P4).

**언제 도는가**
  - 자동: Claude Code 의 stop 훅(`POST /api/agent/event`, event="stop") 직후.
    작업이 끝났다는 가장 정확한 신호다.
  - 수동: `vt queue run` / 웹 UI "지금 실행".

**자동 드레인의 한계를 분명히 해둔다.** stop 훅은 Claude Code 만 보낸다.
codex/aider/gemini 는 훅이 없어서 자동 드레인이 걸리지 않는다 — 그 경우 수동 실행이
유일한 경로다. 출력 유휴(idle)로 추측해서 투입하는 방식은 검토했다가 뺐다:
빌드 로그가 잠깐 끊긴 것과 작업 완료를 구분할 수 없어서, 남의 입력 중간에
프롬프트를 끼워 넣는 사고가 난다. 조용히 틀리느니 안 하는 편이 낫다.

**투입 전 통과해야 하는 관문 4개**
  1. 유예 시간 — stop 직후 바로 넣지 않는다. 사용자가 직접 타이핑을 시작했을 수 있다.
  2. safe_mode — 위험 명령이면 투입하지 않고 큐에 blocked 로 남긴다.
  3. 타깃 pane 생존 확인 — 없으면 큐를 유지한 채 중단한다(항목을 버리지 않는다).
  4. 한 번에 한 건 — 연속 투입하면 에이전트가 두 지시를 한 입력으로 붙여 읽는다.
"""

from __future__ import annotations

import asyncio
import logging
import os

import queue_store
import tmux_target

logger = logging.getLogger(__name__)


def _grace_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("VT_QUEUE_GRACE_SEC", "3")))
    except ValueError:
        return 3.0


def autodrain_enabled() -> bool:
    return os.environ.get("VT_QUEUE_AUTODRAIN", "1").strip() not in ("0", "false", "no", "off")


# 동시에 두 드레인이 돌면 순서가 깨진다. 프로세스 내 단일 소유자로 만든다.
_drain_lock = asyncio.Lock()
_pending_task: asyncio.Task | None = None


def _resolve_pane(item: dict) -> tuple[str | None, str]:
    """항목이 타깃을 명시했으면 그 세션, 아니면 음성과 같은 규칙으로 결정."""
    target = item.get("target")
    if target:
        pane = tmux_target.session_pane(target)
        return pane, (f"session:{target}" if pane else "none")
    return tmux_target.resolve_voice_target_pane()


def _check_safe(text: str) -> tuple[bool, str]:
    try:
        import safe_mode
    except ImportError:
        return True, ""
    # tmux 로 보내는 전체 텍스트가 여러 줄일 수 있다 — 첫 줄만 검사하면
    # "echo hi\nsudo rm -rf /" 처럼 뒤 줄의 위험 명령이 통과해버린다.
    # pty_manager.py 의 Enter 단위 라인 검사와 같은 기준으로 모든 줄을 검사한다.
    for line in (text or "").splitlines():
        ok, reason = safe_mode.check(line)
        if not ok:
            return ok, (reason or "")
    return True, ""


def drain_once() -> dict:
    """pending 한 건을 투입한다. blocking — 호출부가 to_thread 로 감싼다."""
    item = queue_store.pop_next()
    if item is None:
        return {"ok": True, "drained": 0, "reason": "큐가 비었습니다"}

    ok, reason = _check_safe(item["text"])
    if not ok:
        queue_store.mark_blocked(item, reason)
        logger.warning(f"큐 항목이 safe_mode 에 막힘: {reason}")
        return {"ok": False, "drained": 0, "error": "blocked",
                "reason": f"위험 명령으로 차단됨: {reason}", "item": item}

    pane, mode = _resolve_pane(item)
    if not pane:
        # 항목을 버리지 않는다 — 세션이 돌아오면 그대로 실행돼야 한다.
        queue_store.mark_blocked(item, "타깃 tmux 세션 없음")
        return {"ok": False, "drained": 0, "error": "no_target",
                "reason": "보낼 tmux 세션이 없습니다 (큐에 유지됨)", "item": item}

    if not tmux_target.send_to_tmux(pane, item["text"]):
        queue_store.mark_blocked(item, "tmux 전송 실패")
        return {"ok": False, "drained": 0, "error": "send_failed",
                "reason": "tmux 전송에 실패했습니다 (큐에 유지됨)", "item": item}

    logger.info(f"큐 투입 → {pane} ({mode}): {item['text'][:60]}")
    return {"ok": True, "drained": 1, "pane": pane, "mode": mode, "item": item,
            "remaining": queue_store.pending_count()}


async def drain_after_grace(delay: float | None = None) -> dict:
    """유예 시간을 두고 한 건 투입. stop 훅이 호출한다."""
    wait = _grace_sec() if delay is None else delay
    if wait > 0:
        await asyncio.sleep(wait)
    async with _drain_lock:
        return await asyncio.to_thread(drain_once)


async def drain_now() -> dict:
    """즉시 한 건 투입 (수동 실행)."""
    async with _drain_lock:
        return await asyncio.to_thread(drain_once)


def schedule_drain() -> bool:
    """stop 이벤트에서 호출. 이미 예약된 드레인이 있으면 중복 예약하지 않는다."""
    global _pending_task
    if not autodrain_enabled():
        return False
    if queue_store.pending_count() == 0:
        return False
    if _pending_task and not _pending_task.done():
        return False
    try:
        _pending_task = asyncio.create_task(drain_after_grace())
    except RuntimeError:
        # 이벤트 루프 밖에서 불린 경우 — 무시한다(수동 실행 경로가 있다).
        return False
    return True
