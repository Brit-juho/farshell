"""Agent 도구 사용 상태 추적 — Pre/Post/Stop 훅에서 갱신.

In-memory 상태 (dict) — 서버 재시작 시 초기화. 영속화 불필요.
"""

import time
from typing import Optional

# session_id (또는 transcript_path 등 고유 키) → 현재 상태
_state: dict[str, dict] = {}


def on_event(event: str, payload: dict) -> Optional[dict]:
    """훅 이벤트 처리.

    Args:
        event: "pre" | "post" | "stop"
        payload: Claude Code hook stdin JSON

    Returns:
        해당 세션의 갱신된 상태 (없으면 None)
    """
    sid = (
        payload.get("session_id")
        or payload.get("transcript_path", "default")
    )

    if event == "pre":
        tool = payload.get("tool_name") or payload.get("tool", "?")
        prev = _state.get(sid, {})
        _state[sid] = {
            "tool": tool,
            "since": time.time(),
            "count": prev.get("count", 0) + 1,
            "input": payload.get("tool_input", {}),
        }
    elif event == "post":
        cur = _state.get(sid, {})
        _state[sid] = {
            **cur,
            "tool": None,
            "last_tool": cur.get("tool"),
            "last_done": time.time(),
        }
    elif event == "stop":
        _state.pop(sid, None)
        return None

    return _state.get(sid)


def get_state(sid: Optional[str] = None) -> dict:
    """전체 또는 특정 세션 상태 반환."""
    if sid:
        return _state.get(sid, {})
    return dict(_state)


def all_active() -> list[dict]:
    """현재 도구 사용 중인 세션 목록."""
    now = time.time()
    return [
        {"session_id": sid, **data, "elapsed": now - data.get("since", now)}
        for sid, data in _state.items()
        if data.get("tool")
    ]
