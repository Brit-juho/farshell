"""Agent 상태 머신 — Pre/Post/Stop 훅 + waiting 감지(A3)로 갱신.

A1 이전에는 여기에 **상태라는 개념이 없었다.** 이벤트 3종을 그대로 기록하고
`working`/`done` 판정은 프론트가 `state.tool` 유무로 파생시켰다(grid.js). 그래서
소비자(탭·카드·파비콘·푸시·큐)마다 파생 규칙이 흩어졌고, `favicon.js`가 허용
상태 화이트리스트를 따로 들고 있는 것도 그 결과였다. 이제 서버가 `status`
문자열 하나를 판정해 내려주고, 소비자는 그대로 쓴다.

    idle ──pre──▶ working ──stop──▶ done ──ack/새 pre──▶ ...
                    │  ▲                │
        패턴 감지 ──┘  └── 패턴 소멸/사용자 입력    TTL 만료 ──▶ idle
                   waiting

규칙:
  - **`post`는 상태를 바꾸지 않는다.** 다음 도구가 곧바로 이어질 수 있어서다
    (grid.js:324의 "의도적 무시" 주석을 서버로 옮겨 명문화한 것). tool 필드와
    last_done만 갱신한다.
  - **`stop`은 엔트리를 지우지 않는다.** A1 이전에는 `_state.pop`이라 "done이라는
    상태"가 서버에 남지 않았고, 그래서 새로고침하면 done 배지가 사라졌다.
    이제 엔트리를 유지하고 `status`만 바꾼다 — 새로고침해도 done이 살아남는다
    (`A5` 복원의 전제).
  - **알 수 없는 이벤트는 무시한다** — 상태를 건드리지 않고 현재 값을 돌려준다.
  - `error`는 토큰만 정의하고 2.0에서 진입 경로를 만들지 않는다.

TTL(2-4): working 15분(훅 유실 의심) · waiting 2분(감지 오탐 가정) · done 30분.
만료는 **삭제가 아니라 전이**다. sweeper는 백그라운드 태스크가 아니라 읽기·쓰기
진입점에서 지연 실행(lazy)한다 — 이벤트 루프가 없는 컨텍스트(단위 테스트, CLI)
에서도 같은 규칙이 그대로 성립하고, 태스크 수명 관리라는 실패 지점이 안 생긴다.

In-memory only — 서버 재시작 시 초기화된다(영속화는 2.0 범위 밖).
"""

import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

IDLE = "idle"
WORKING = "working"
WAITING = "waiting"
DONE = "done"
ERROR = "error"  # 예약 — 2.0에서는 진입 경로 없음

STATUSES = (IDLE, WORKING, WAITING, DONE, ERROR)

# 2-6 정렬 우선순위 — "내 개입이 필요한 것이 항상 맨 위".
_URGENCY = {WAITING: 0, DONE: 1, WORKING: 2, ERROR: 3, IDLE: 4}

# session_id (또는 transcript_path 등 고유 키) → 현재 상태
_state: dict[str, dict] = {}


def _ttl(name: str, default: float) -> float:
    """TTL은 환경변수로 덮어쓸 수 있다 — 검증(A6)에서 15분을 기다릴 수 없기 때문."""
    try:
        return float(os.environ.get(f"VT_AGENT_TTL_{name.upper()}", default))
    except ValueError:
        return default


def _ttls() -> dict[str, float]:
    return {
        WORKING: _ttl("working", 900.0),   # 15분 — 훅 유실 의심
        WAITING: _ttl("waiting", 120.0),   # 2분 — 감지 오탐 가정
        DONE: _ttl("done", 1800.0),        # 30분 — ack 없이 방치
    }


def _sid(payload: dict) -> str:
    return payload.get("session_id") or payload.get("transcript_path", "default")


def sweep(now: Optional[float] = None) -> int:
    """만료된 상태를 전이시킨다. 전이한 엔트리 수를 반환.

    working → idle (훅이 유실됐다고 본다. 로그 경고 — 이 경로로 자주 오면
              훅 배선이나 네트워크를 의심해야 한다)
    waiting → working (승인 프롬프트 감지가 오탐이었다고 가정. idle이 아니라
              working으로 돌리는 이유: 그 사이 stop이 안 왔다면 아직 도는 중이다)
    done    → idle (사용자가 확인하지 않고 방치)
    idle    → 엔트리 삭제 (done TTL만큼 더 지나면 들고 있을 이유가 없다)
    """
    now = time.time() if now is None else now
    ttls = _ttls()
    changed = 0
    for sid, ent in list(_state.items()):
        status = ent.get("status", IDLE)
        age = now - ent.get("updated_at", now)
        if status == WORKING and age > ttls[WORKING]:
            logger.warning(
                f"agent {sid}: working이 {age:.0f}초째 — 훅 유실로 보고 idle로 만료"
            )
            _set_status(ent, IDLE, now)
            ent["tool"] = None
            changed += 1
        elif status == WAITING and age > ttls[WAITING]:
            _set_status(ent, WORKING, now)
            changed += 1
        elif status == DONE and age > ttls[DONE]:
            _set_status(ent, IDLE, now)
            changed += 1
        elif status == IDLE and age > ttls[DONE]:
            del _state[sid]
            changed += 1
    return changed


def _set_status(ent: dict, status: str, now: Optional[float] = None) -> None:
    ent["status"] = status
    ent["updated_at"] = time.time() if now is None else now


def _entry(sid: str, cwd: Optional[str] = None) -> dict:
    ent = _state.get(sid)
    if ent is None:
        ent = {
            "status": IDLE,
            "tool": None,
            "since": time.time(),
            "count": 0,
            "input": {},
            "cwd": cwd,
            "updated_at": time.time(),
        }
        _state[sid] = ent
    if cwd:
        ent["cwd"] = cwd
    return ent


def on_event(event: str, payload: dict, session: Optional[str] = None) -> Optional[dict]:
    """훅 이벤트 처리.

    Args:
        event: "pre" | "post" | "stop" (그 외는 무시)
        payload: Claude Code hook stdin JSON
        session: A2의 3단 해석(pane_resolve.resolve)이 특정한 tmux 세션 이름.
                 None이면 이번 이벤트로는 특정하지 못했다는 뜻이고, **엔트리에
                 이미 있던 값을 지우지 않는다** — pre에서 pane id로 확실히
                 특정해뒀는데 stop 페이로드가 어쩌다 단서를 못 실어 왔다고
                 해서 그 정보를 잃을 이유가 없다.

    Returns:
        해당 세션의 갱신된 상태(엔트리 전체). 알 수 없는 세션의 알 수 없는
        이벤트면 None.
    """
    sweep()
    sid = _sid(payload)
    # Claude Code 훅 JSON은 이벤트 종류와 무관하게 항상 cwd를 담고 있다.
    # 이 세션이 "어느 tmux 세션(pane)의 작업인지"는 서버에 별도로 없는데,
    # /api/tmux/sessions 가 이미 pane_current_path를 cwd로 내려주므로
    # cwd로 매칭해 카드/큐 타깃을 특정한다(pane 자기보고는 A2에서 추가된다).
    cwd = payload.get("cwd")

    if event == "pre":
        ent = _entry(sid, cwd)
        if session:
            ent["tmux_session"] = session
        ent["tool"] = payload.get("tool_name") or payload.get("tool", "?")
        ent["since"] = time.time()
        ent["count"] = ent.get("count", 0) + 1
        ent["input"] = payload.get("tool_input", {})
        # waiting/done에서 pre가 오면 그것도 working으로 — 새 도구가 시작됐다는
        # 뜻이므로 이전 상태(승인 대기/완료 표시)는 더 이상 유효하지 않다.
        _set_status(ent, WORKING)
        return ent

    if event == "post":
        # 상태는 그대로 둔다(파일 상단 규칙). 도구가 끝났을 뿐 다음 도구가
        # 곧바로 이어질 수 있어서다.
        ent = _entry(sid, cwd)
        if session:
            ent["tmux_session"] = session
        ent["last_tool"] = ent.get("tool")
        ent["tool"] = None
        ent["last_done"] = time.time()
        return ent

    if event == "stop":
        ent = _entry(sid, cwd)
        if session:
            ent["tmux_session"] = session
        ent["tool"] = None
        _set_status(ent, DONE)
        return ent

    # 알 수 없는 이벤트 — 상태를 건드리지 않는다.
    return _state.get(sid)


def on_waiting(sid: str, waiting: bool, cwd: Optional[str] = None) -> Optional[dict]:
    """A3(승인 프롬프트 감지)이 부르는 진입점.

    waiting=True는 **working일 때만** 받아들인다 — done인 세션에 뒤늦게 도착한
    패턴 감지가 완료 표시를 승인 대기로 되돌리면 안 된다. waiting=False는
    working으로 되돌린다(패턴 소멸 / 사용자 입력 / exit 패턴).
    """
    sweep()
    ent = _state.get(sid)
    if ent is None:
        if not waiting:
            return None
        ent = _entry(sid, cwd)
        _set_status(ent, WORKING)
    if cwd:
        ent["cwd"] = cwd
    if waiting:
        if ent["status"] == WORKING:
            _set_status(ent, WAITING)
    elif ent["status"] == WAITING:
        _set_status(ent, WORKING)
    return ent


def ack(sid: str) -> Optional[dict]:
    """사용자가 완료를 확인했다(탭/카드 클릭) → done을 idle로 내린다.

    done이 아닌 상태에는 아무 것도 하지 않는다 — 작업 중인 세션을 클릭했다고
    working이 지워지면 안 된다.
    """
    sweep()
    ent = _state.get(sid)
    if ent and ent.get("status") == DONE:
        _set_status(ent, IDLE)
    return ent


def report(sid: str, status: str, session: Optional[str] = None,
           cwd: Optional[str] = None, agent: Optional[str] = None) -> dict:
    """pane 자기보고(`fsh pane report`)로 상태를 직접 세팅한다.

    훅이 없는 에이전트(codex/aider/gemini)를 위한 경로다 — 그쪽은 Claude Code
    훅 같은 게 없으니 사용자가 래퍼/스크립트에서 직접 알려주는 수밖에 없다.
    알 수 없는 status 문자열은 거부한다(오타가 조용히 새 상태를 만들면 안 된다).
    """
    if status not in STATUSES:
        raise ValueError(f"알 수 없는 상태: {status} (가능: {', '.join(STATUSES)})")
    sweep()
    ent = _entry(sid, cwd)
    if session:
        ent["tmux_session"] = session
    if agent:
        ent["agent"] = agent
    if status != WORKING:
        ent["tool"] = None
    _set_status(ent, status)
    return ent


def status_for_session(name: Optional[str]) -> str:
    """tmux 세션 이름으로 상태를 찾는다 — A2 이후의 **정확한** 경로.

    `status_for_cwd`와 같은 우선순위 규칙(가장 개입이 필요한 상태)을 쓰되,
    키가 cwd 문자열이 아니라 3단 해석이 특정한 세션 이름이라 같은 디렉토리에
    세션이 둘이어도 서로 안 섞인다.
    """
    if not name:
        return IDLE
    sweep()
    found = [e.get("status", IDLE) for e in _state.values() if e.get("tmux_session") == name]
    if not found:
        return IDLE
    return min(found, key=lambda s: _URGENCY.get(s, 9))


def get_status(sid: str) -> str:
    """세션의 현재 상태 문자열. 모르는 세션은 idle."""
    sweep()
    ent = _state.get(sid)
    return ent.get("status", IDLE) if ent else IDLE


def status_for_cwd(cwd: Optional[str]) -> str:
    """cwd로 상태를 찾는다 — 큐 5번 관문(A4)과 UI가 쓴다.

    같은 cwd가 여럿이면 "가장 개입이 필요한" 상태를 돌려준다(2-6의 정렬
    우선순위와 같은 순서). 여기서 아무것도 안 돌려주면 큐가 waiting인 pane에
    투입해버릴 수 있어, 모호할 때는 **더 보수적인 쪽**을 고른다.
    """
    if not cwd:
        return IDLE
    sweep()
    found = [e.get("status", IDLE) for e in _state.values() if e.get("cwd") == cwd]
    if not found:
        return IDLE
    return min(found, key=lambda s: _URGENCY.get(s, 9))


def get_state(sid: Optional[str] = None) -> dict:
    """전체 또는 특정 세션 상태 반환."""
    sweep()
    if sid:
        return _state.get(sid, {})
    return dict(_state)


def all_active() -> list[dict]:
    """현재 도구 실행 중(working이고 tool이 붙어 있는) 세션 목록.

    기존 소비자(grid.js 등)가 `tool` 필드를 보고 working을 파생시키던 계약을
    그대로 유지한다 — status를 함께 실어 보내되 필드는 하나도 빼지 않는다.
    """
    sweep()
    now = time.time()
    return [
        {"session_id": sid, **data, "elapsed": now - data.get("since", now)}
        for sid, data in _state.items()
        if data.get("tool")
    ]
