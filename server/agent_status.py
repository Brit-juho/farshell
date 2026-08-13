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
    # Claude Code 훅 JSON은 이벤트 종류와 무관하게 항상 cwd를 담고 있다.
    # 이 세션이 "어느 tmux 세션(pane)의 작업인지"는 서버에 별도로 없는데,
    # /api/tmux/sessions 가 이미 pane_current_path를 cwd로 내려주므로
    # 프론트가 cwd로 매칭해 그리드 카드를 특정한다(P7: 라이브 프리뷰 작업 상태 표시).
    cwd = payload.get("cwd")

    if event == "pre":
        tool = payload.get("tool_name") or payload.get("tool", "?")
        prev = _state.get(sid, {})
        _state[sid] = {
            "tool": tool,
            "since": time.time(),
            "count": prev.get("count", 0) + 1,
            "input": payload.get("tool_input", {}),
            "cwd": cwd or prev.get("cwd"),
        }
    elif event == "post":
        cur = _state.get(sid, {})
        _state[sid] = {
            **cur,
            "tool": None,
            "last_tool": cur.get("tool"),
            "last_done": time.time(),
            "cwd": cwd or cur.get("cwd"),
        }
    elif event == "stop":
        # 상태를 지우기 전에 cwd만은 건져서 돌려준다 — 그래야 "방금 끝난 게
        # 어느 카드인지"를 프론트가 알 수 있다. pop 후엔 이 세션 정보가 사라진다.
        cur = _state.pop(sid, None) or {}
        result_cwd = cwd or cur.get("cwd")
        return {"cwd": result_cwd} if result_cwd else None

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
