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

## 호스트 차원 (N7/N39 2026-09-13)

멀티호스트가 들어오면서 **모든 조회에 host가 붙는다.** 그전에는 상태를 tmux 세션
이름 문자열 하나로 찾았는데(`status_for_session("dev")`), 맥 두 대에 똑같이 `dev`
세션이 있으면(기본 이름이라 거의 확실히 그렇다) 원격 `dev`의 승인 대기가 로컬
`dev` 탭 배지에 뜨고, 더 나쁘게는 **큐가 로컬 pane을 원격 상태 때문에 차단**한다.
cwd도 같은 문제를 갖는다 — 두 맥의 홈 경로(`/Users/<같은이름>/proj`)는 흔히 똑같다.

그래서 엔트리마다 `host`를 갖고(로컬은 `"local"`), 조회 함수 전부가 host를 받는다.
기본값이 `"local"`이라 기존 호출부는 그대로 동작하고, 원격 엔트리는 로컬 조회에
**절대 매칭되지 않는다.** `_state`의 키도 호스트별로 갈라둔다(`_key`) — 두 맥의
`transcript_path`는 사용자 이름이 같으면 문자열까지 동일할 수 있어서, sid만으로는
서로 덮어쓴다.
"""

import logging
import os
import re
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

# "이 서버가 직접 돌리는 세션"을 뜻하는 예약 호스트 이름. host_store.RESERVED_IDS가
# 원격 호스트에게 이 id를 못 쓰게 막는다(tree.js leaf의 host 기본값도 같은 값).
LOCAL_HOST = "local"

# _key(sid, host) → 현재 상태. 키를 호스트별로 가르는 이유는 모듈 주석 참고.
_state: dict[str, dict] = {}


def _key(sid: str, host: str = LOCAL_HOST) -> str:
    """상태 저장 키. 로컬은 sid 그대로 — 기존 키 공간을 바꾸지 않는다.

    반환값을 다시 파싱하지 않는다(불투명 키). 호스트/세션을 알아야 하는 쪽은
    엔트리의 `host`/`session_id` 필드를 읽는다.
    """
    return sid if host == LOCAL_HOST else f"{host}@{sid}"


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
            ent["question"] = None
            ent["options"] = None
            changed += 1
        elif status == DONE and age > ttls[DONE]:
            _set_status(ent, IDLE, now)
            changed += 1
        elif status == IDLE and age > ttls[DONE]:
            del _state[sid]
            changed += 1
    return changed


# P1-1(ADR-33) — 승인 응답 경쟁. 화면 둘이 같은 선택지를 거의 동시에 눌러도
# 첫 요청만 통과시키기 위한 토큰. 모듈 전역 단조 증가 — 서버 재시작 시 0으로
# 돌아가도 안전하다(`_state`도 함께 비므로 옛 토큰을 든 엔트리가 없다, E8).
_prompt_seq = 0

# waiting의 유형(kind). 프런트는 이 값으로 「승인 요청 / 선택 요청 / 계획 확인 / 질문」을
# 고른다. 우선순위: 훅 kind > 화면 패턴 kind > 폴백(`question`).
KIND_APPROVAL = "approval"
KIND_CHOICE = "choice"
KIND_PLAN = "plan"
KIND_QUESTION = "question"
KINDS = (KIND_APPROVAL, KIND_CHOICE, KIND_PLAN, KIND_QUESTION)

# 훅 tool_name -> kind. 목록에 없는 도구는 approval(PermissionRequest인데 도구를 모르면
# 승인 요청이다).
_TOOL_KINDS = {"AskUserQuestion": KIND_CHOICE, "ExitPlanMode": KIND_PLAN}
# Codex는 질문을 PermissionRequest가 아니라 PreToolUse로 알린다.
_CODEX_CHOICE_TOOLS = ("request_user_input",)

_KIND_QUESTIONS = {
    KIND_CHOICE: "선택이 필요합니다",
    KIND_PLAN: "계획 확인이 필요합니다",
    KIND_QUESTION: "입력이 필요합니다",
}


def _tool_options(tool_input) -> Optional[list]:
    """AskUserQuestion/request_user_input의 `questions[0].options[].label` -> 선택지."""
    try:
        opts = tool_input["questions"][0]["options"]
    except (KeyError, IndexError, TypeError):
        return None
    out = []
    for i, o in enumerate(opts if isinstance(opts, list) else [], 1):
        label = o.get("label") if isinstance(o, dict) else o if isinstance(o, str) else None
        if isinstance(label, str) and label.strip():
            out.append({"key": str(i), "label": label.strip()})
    return out[:6] or None


def _tool_question(tool_input) -> Optional[str]:
    try:
        q = tool_input["questions"][0]["question"]
    except (KeyError, IndexError, TypeError):
        return None
    return " ".join(q.split()) if isinstance(q, str) and q.strip() else None


def _prompt_fingerprint(ent: dict) -> str:
    """'같은 질문인가'의 판정 기준.

    질문 원문을 그대로 비교하면 안 된다 — PTY 감지는 화면이 다시 그려질 때마다
    같은 프롬프트를 재추출하는데, ANSI 잔여·말줄임·후행 공백 때문에 문자열이
    미세하게 달라질 수 있다. 그때마다 토큰을 새로 내면 사용자가 보고 있는
    토큰이 탭하기 전에 만료된다(docs/plan-2.3/10-approval-race.md §4 E5).

    그래서 공백을 접어 정규화하고, 선택지는 **키만** 본다(라벨은 잘릴 수 있다).
    """
    q = " ".join((ent.get("question") or "").split())
    keys = ",".join(o.get("key", "") for o in (ent.get("options") or []))
    return f"{q}\x00{keys}\x00{ent.get('kind') or ''}"


def _set_status(ent: dict, status: str, now: Optional[float] = None) -> None:
    global _prompt_seq
    prev = ent.get("status")
    ent["status"] = status
    ent["updated_at"] = time.time() if now is None else now
    if status != WAITING:
        # waiting이 풀리면 유형도 같이 사라진다(다음 waiting은 새로 판정).
        ent.pop("kind", None)
        ent.pop("_kind_src", None)
        # 해소된 프롬프트가 "아직 활성"으로 남지 않게 한다(crush 「소비 뒤
        # 활성 표식을 지운다」 규율의 우리 번역).
        ent["prompt_id"] = None
        ent["_prompt_fp"] = None
        return
    fp = _prompt_fingerprint(ent)
    if prev != WAITING or ent.get("_prompt_fp") != fp:
        _prompt_seq += 1
        ent["prompt_id"] = str(_prompt_seq)
        ent["_prompt_fp"] = fp


def _entry(sid: str, cwd: Optional[str] = None, host: str = LOCAL_HOST) -> dict:
    key = _key(sid, host)
    ent = _state.get(key)
    if ent is None:
        ent = {
            "status": IDLE,
            "tool": None,
            "since": time.time(),
            "count": 0,
            "input": {},
            "cwd": cwd,
            "updated_at": time.time(),
            # host/session_id는 엔트리를 보고 "어디의 무엇인지" 알 수 있게 하는
            # 유일한 경로다 — _state의 키는 불투명하게 두고 여기서만 읽는다.
            "host": host,
            "session_id": sid,
        }
        _state[key] = ent
    if cwd:
        ent["cwd"] = cwd
    return ent


# P2-1(ADR-34) — 에이전트 이름은 클라이언트로 나가고 report_seen 파일명 근처로도
# 간다(device_settings._SAFE_ID와 같은 규율). 길이 상한 + 문자 제한을 여기 하나에
# 모은다 — _apply_agent가 이 값의 유일한 진입점이 되므로 여기 하나면 된다(E11).
_AGENT_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


def _apply_agent(ent: dict, agent: Optional[str]) -> None:
    """1차 신호(자기보고·훅)로 받은 에이전트 이름을 엔트리에 박는다.

    - 값이 없거나(None/빈 문자열) 형식이 이상하면 아무것도 안 한다 —
      **기존 값을 지우지 않는다.** 훅이 agent를 안 실어 보내는 구버전
      설치가 있고, 그 요청이 이미 박힌 이름을 날리면 안 된다(E1/E6/E11).
    - 이미 같은 값이면 건드리지 않는다 — `since` 같은 인접 필드를 덩달아
      갱신하지 않는다(crush `reportLocked`, E2).
    - 아직 없으면 무조건 채운다(crush `registerInitial`, E3).
    - **1차 신호가 2차 추측(`agent_detector`)을 이긴다**는 게 이 항목
      전체의 요점이다(§2.4) — 탐지기와 값이 달라도 여기서 지우지 않는다.
      TTL이 옛 값을 자연 만료시킨다(§3.4).
    """
    if not agent or not _AGENT_NAME_RE.match(agent):
        return
    if ent.get("agent") == agent:
        return
    ent["agent"] = agent


def _set_hook_prompt(ent: dict, kind: str, tool, tool_input) -> None:
    """훅이 알려준 waiting의 kind/질문/선택지를 엔트리에 싣는다(`_set_status` 전에 —
    지문이 kind·질문·선택지를 보므로 순서가 바뀌면 토큰이 안 갱신된다)."""
    ent["kind"] = kind
    ent["_kind_src"] = "hook"
    if kind == KIND_APPROVAL:
        ent["question"] = f"{tool} 실행 승인이 필요합니다" if tool else "실행 승인이 필요합니다"
        ent["options"] = None
    else:
        ent["question"] = _tool_question(tool_input) or _KIND_QUESTIONS[kind]
        ent["options"] = _tool_options(tool_input) if kind == KIND_CHOICE else None


def _ends_with_question(text) -> bool:
    return isinstance(text, str) and text.rstrip().endswith(("?", "？"))


def on_event(event: str, payload: dict, session: Optional[str] = None,
             host: str = LOCAL_HOST, agent: Optional[str] = None) -> Optional[dict]:
    """훅 이벤트 처리.

    Args:
        event: "pre" | "post" | "stop" (그 외는 무시)
        payload: Claude Code hook stdin JSON
        host: 이 이벤트를 낸 호스트. 로컬 훅은 항상 "local"이고, 원격 호스트가
              중계한 이벤트만 다른 값을 갖는다(모듈 주석 「호스트 차원」).
        session: A2의 3단 해석(pane_resolve.resolve)이 특정한 tmux 세션 이름.
                 None이면 이번 이벤트로는 특정하지 못했다는 뜻이고, **엔트리에
                 이미 있던 값을 지우지 않는다** — pre에서 pane id로 확실히
                 특정해뒀는데 stop 페이로드가 어쩌다 단서를 못 실어 왔다고
                 해서 그 정보를 잃을 이유가 없다.
        agent: P2-1(ADR-34) — 훅이 자기 이름을 실어 보내면(`--agent codex` 등)
               `_apply_agent`로 엔트리에 박는다. 세션(컨테이너)에서 추측하는
               `agent_detector`보다 우선하는 1차 신호다(§2.4).

    Returns:
        해당 세션의 갱신된 상태(엔트리 전체). 알 수 없는 세션의 알 수 없는
        이벤트면 None.
    """
    sweep()
    sid = _sid(payload)
    key = _key(sid, host)
    # Claude Code 훅 JSON은 이벤트 종류와 무관하게 항상 cwd를 담고 있다.
    # 이 세션이 "어느 tmux 세션(pane)의 작업인지"는 서버에 별도로 없는데,
    # /api/tmux/sessions 가 이미 pane_current_path를 cwd로 내려주므로
    # cwd로 매칭해 카드/큐 타깃을 특정한다(pane 자기보고는 A2에서 추가된다).
    cwd = payload.get("cwd")

    if event in ("prompt", "user_prompt_submit"):
        ent = _entry(sid, cwd, host)
        _apply_agent(ent, agent)
        if session:
            ent["tmux_session"] = session
        ent["tool"] = None
        ent["since"] = time.time()
        _set_status(ent, WORKING)
        ent["question"] = None
        ent["options"] = None
        ent.pop("badge", None)
        return ent

    if event in ("permission", "permission_request"):
        ent = _entry(sid, cwd, host)
        _apply_agent(ent, agent)
        if session:
            ent["tmux_session"] = session
        tool = payload.get("tool_name") or payload.get("tool")
        ent["tool"] = tool
        ent["since"] = time.time()
        ent["count"] = ent.get("count", 0) + 1
        ent["input"] = payload.get("tool_input", {})
        # P1-1 — question/options를 _set_status보다 먼저 채운다. _set_status가
        # 그 자리에서 지문(_prompt_fingerprint)을 계산하므로, 순서가 바뀌면
        # 항상 빈 질문으로 계산돼 토큰이 매번 같아진다(docs/plan-2.3/
        # 10-approval-race.md §3.1의 경고).
        # kind는 훅이 아는 tool_name으로 정한다(훅 > 화면 > 폴백). 도구를 모르면 approval.
        kind = _TOOL_KINDS.get(tool, KIND_APPROVAL)
        _set_hook_prompt(ent, kind, tool, ent["input"])
        _set_status(ent, WAITING)
        return ent

    if event == "notification":
        # Claude Code Notification(permission_prompt/idle_prompt) — **보조 기록만 한다.**
        # kind 판정에도 상태 전이에도 쓰지 않는다(문구가 버전마다 달라 근거로 약하다).
        ent = _state.get(key)
        if ent is not None:
            ntype = payload.get("notification_type")
            ent["last_notification"] = {
                "type": ntype if isinstance(ntype, str) else None,
                "at": time.time(),
            }
        return ent

    if event == "pre":
        ent = _entry(sid, cwd, host)
        _apply_agent(ent, agent)
        if session:
            ent["tmux_session"] = session
        ent["tool"] = payload.get("tool_name") or payload.get("tool", "?")
        ent["since"] = time.time()
        ent["count"] = ent.get("count", 0) + 1
        ent["input"] = payload.get("tool_input", {})
        if ((agent or ent.get("agent")) == "codex"
                and ent["tool"] in _CODEX_CHOICE_TOOLS):
            # Codex Plan 모드의 질문 — PermissionRequest 없이 PreToolUse로만 온다.
            ent.pop("badge", None)
            _set_hook_prompt(ent, KIND_CHOICE, ent["tool"], ent["input"])
            _set_status(ent, WAITING)
            return ent
        ent.pop("badge", None)
        # waiting/done에서 pre가 오면 그것도 working으로 — 새 도구가 시작됐다는
        # 뜻이므로 이전 상태(승인 대기/완료 표시)는 더 이상 유효하지 않다.
        _set_status(ent, WORKING)
        # N38: 이전 승인 질문(있었다면)도 함께 폐기 — 새 도구가 시작됐으니
        # 그 질문은 더 이상 답할 대상이 아니다.
        ent["question"] = None
        ent["options"] = None
        return ent

    if event == "post":
        # 상태는 그대로 둔다(파일 상단 규칙). 도구가 끝났을 뿐 다음 도구가
        # 곧바로 이어질 수 있어서다.
        ent = _entry(sid, cwd, host)
        _apply_agent(ent, agent)
        if session:
            ent["tmux_session"] = session
        ent["last_tool"] = ent.get("tool")
        ent["tool"] = None
        ent["last_done"] = time.time()
        if ent.get("status") == WAITING:
            _set_status(ent, WORKING)
            ent["question"] = None
            ent["options"] = None
        return ent

    if event == "session_start":
        # 에이전트가 막 열렸다 — 아직 아무 일도 안 했으니 idle. 압축(compact) 뒤에도
        # 이 훅이 오는데 그건 작업 도중일 수 있어 무시한다(claude_hooks.MATCHERS와
        # 같은 규칙을 여기서도 건다 — matcher 없이 등록된 옛 설정 대비).
        if payload.get("source") == "compact":
            return _state.get(key)
        ent = _entry(sid, cwd, host)
        _apply_agent(ent, agent)
        if session:
            ent["tmux_session"] = session
        ent["tool"] = None
        _set_status(ent, IDLE)
        ent["question"] = None
        ent["options"] = None
        ent.pop("badge", None)
        return ent

    if event in ("stop", "session_end"):
        ent = _entry(sid, cwd, host)
        _apply_agent(ent, agent)
        if session:
            ent["tmux_session"] = session
        ent["tool"] = None
        _set_status(ent, DONE)
        ent["question"] = None
        ent["options"] = None
        # 마지막 응답이 `?`로 끝나면 보조 표시만 붙인다. 대기로 올리지 않는다 —
        # 수사적 질문·인용 때문에 오탐 위험이 커서 큐/푸시를 막을 근거가 못 된다.
        if _ends_with_question(payload.get("last_assistant_message")):
            ent["badge"] = KIND_QUESTION
        else:
            ent.pop("badge", None)
        return ent

    # 알 수 없는 이벤트 — 상태를 건드리지 않는다.
    return _state.get(key)


def on_waiting(
    sid: str,
    waiting: bool,
    cwd: Optional[str] = None,
    question: Optional[str] = None,
    options: Optional[list] = None,
    host: str = LOCAL_HOST,
    kind: Optional[str] = None,
) -> Optional[dict]:
    """A3(승인 프롬프트 감지)이 부르는 진입점.

    waiting=True는 **working일 때만** 받아들인다 — done인 세션에 뒤늦게 도착한
    패턴 감지가 완료 표시를 승인 대기로 되돌리면 안 된다. waiting=False는
    working으로 되돌린다(패턴 소멸 / 사용자 입력 / exit 패턴).

    N38(모바일 인라인 승인): waiting=True일 때 question/options가 있으면
    엔트리에 실어둔다 — `agent_prompt_detect`가 `detect/*.toml`의 `options`
    캡처 그룹으로 뽑은 번호 선택지다. 캡처에 실패하면 둘 다 None으로 남고,
    프런트는 그 경우 인라인 버튼 대신 「터미널로」를 보여준다(70-mobile.md §2).
    """
    sweep()
    ent = _state.get(_key(sid, host))
    if ent is None:
        if not waiting:
            return None
        ent = _entry(sid, cwd, host)
        _set_status(ent, WORKING)
    if cwd:
        ent["cwd"] = cwd
    if waiting:
        # waiting은 working일 때만 받아들인다(done/idle에 뒤늦게 도착한 감지가
        # 완료 표시를 되돌리면 안 된다는 기존 규칙). 이미 waiting이면 "같은
        # 프롬프트 재확인"이거나 "다음 질문으로 넘어감"일 수 있는데 둘 다
        # 여기로 들어와야 한다 — question/options를 먼저 갱신한 뒤 매번
        # _set_status를 불러 지문을 다시 판정한다. 그래야 같은 질문이면
        # 토큰이 그대로 있고(E4), 선택지가 바뀌면 새 토큰이 나간다(E6) —
        # 두 if로 나눠 앞쪽만 상태를 바꾸던 예전 코드는 이미 waiting인 채
        # 질문만 바뀌는 경우 _set_status를 안 태워 토큰이 갱신되지 않았다.
        if ent["status"] in (WORKING, WAITING):
            hook_kind = ent["status"] == WAITING and ent.get("_kind_src") == "hook"
            ent["question"] = question
            if hook_kind and options is None:
                pass  # 훅이 준 선택지를 화면 쪽 빈 값으로 지우지 않는다
            else:
                ent["options"] = options
            if not hook_kind:
                # 화면 패턴 kind, 없으면 폴백 `question`. 훅 kind가 있으면 그것이 이긴다.
                ent["kind"] = kind if kind in KINDS else KIND_QUESTION
                ent["_kind_src"] = "screen"
            _set_status(ent, WAITING)
    elif ent["status"] == WAITING:
        _set_status(ent, WORKING)
        ent["question"] = None
        ent["options"] = None
    return ent


def ack(sid: str, host: str = LOCAL_HOST) -> Optional[dict]:
    """사용자가 완료를 확인했다(탭/카드 클릭) → done을 idle로 내린다.

    done이 아닌 상태에는 아무 것도 하지 않는다 — 작업 중인 세션을 클릭했다고
    working이 지워지면 안 된다.
    """
    sweep()
    ent = _state.get(_key(sid, host))
    if ent and ent.get("status") == DONE:
        _set_status(ent, IDLE)
    return ent


def _entries_for(tmux_name: str, host: str = LOCAL_HOST) -> list[dict]:
    """tmux 세션 이름으로 엔트리를 찾는다.

    `deps.py`의 `_on_waiting_change`가 targets를 계산하던 것과 **같은 규칙**
    (tmux_session == tmux_name 이고 host == 주어진 host)이다. 두 곳이 각자
    조건을 들고 있으면 하나만 고쳐질 위험이 있어 여기 하나로 모았다 —
    `deps.py`도 이 헬퍼를 쓴다. host 기본값이 LOCAL_HOST인 이유: 두 맥의
    기본 tmux 세션 이름(`dev`)이 흔히 같아서, host를 안 거르면 원격의 같은
    이름 세션이 로컬 조회에 걸려든다.
    """
    return [e for e in _state.values()
            if e.get("tmux_session") == tmux_name and e.get("host", LOCAL_HOST) == host]


def consume_prompt(tmux_name: str, prompt_id: str, host: str = LOCAL_HOST) -> bool:
    """토큰이 맞으면 **즉시 비우고** True. 어긋나면 아무것도 안 하고 False.

    P1-1(ADR-33) — 승인 응답 경쟁의 핵심. 화면 둘이 같은 선택지를 거의 동시에
    눌러도 먼저 도착한 요청만 여기를 통과하고, 나머지는 이미 비워진 토큰을
    보고 False를 받는다.

    ⚠ 이 함수는 동기이고, 검사와 대입 사이에 **절대 `await`를 넣지 않는다.**
    asyncio 단일 이벤트 루프에서 await 없는 구간은 원자적으로 실행되므로,
    crush가 뮤텍스로 얻는 것(csync.Map.Take)을 우리는 이 규칙으로 얻는다.
    await가 하나라도 들어가면 두 요청이 같은 토큰을 통과한다
    (docs/plan-2.3/10-approval-race.md §4 E13).
    """
    sweep()
    for ent in _entries_for(tmux_name, host):
        if ent.get("status") == WAITING and ent.get("prompt_id") == prompt_id:
            ent["prompt_id"] = None       # 검사와 같은 스텝에서 소비한다.
            return True
    return False


def public_entry(ent: dict) -> dict:
    """클라이언트로 내려가는 형태. `_` 접두 키(내부용, 예: `_prompt_fp`)는 뺀다."""
    return {k: v for k, v in ent.items() if not k.startswith("_")}


def report(sid: str, status: str, session: Optional[str] = None,
           cwd: Optional[str] = None, agent: Optional[str] = None,
           host: str = LOCAL_HOST) -> dict:
    """pane 자기보고(`fsh pane report`)로 상태를 직접 세팅한다.

    훅이 없는 에이전트(codex/aider/agy)를 위한 경로다 — 그쪽은 Claude Code
    훅 같은 게 없으니 사용자가 래퍼/스크립트에서 직접 알려주는 수밖에 없다.
    알 수 없는 status 문자열은 거부한다(오타가 조용히 새 상태를 만들면 안 된다).
    """
    if status not in STATUSES:
        raise ValueError(f"알 수 없는 상태: {status} (가능: {', '.join(STATUSES)})")
    sweep()
    ent = _entry(sid, cwd, host)
    if session:
        ent["tmux_session"] = session
    # P2-1(ADR-34) — `_apply_agent`가 유일한 진입점이 되도록 자기보고도
    # 여길 거친다. 예전엔 여기서 직접 대입해 길이/문자 제한(E11)이 훅
    # 경로에만 걸리고 `fsh pane report --agent`에는 안 걸렸다.
    _apply_agent(ent, agent)
    if status != WORKING:
        ent["tool"] = None
    else:
        ent.pop("badge", None)
    _set_status(ent, status)
    return ent


def set_badge_for_session(name: Optional[str], badge: Optional[str],
                          host: str = LOCAL_HOST) -> int:
    """화면 문구가 알려주는 보조 표시(`badge`)를 그 tmux 세션의 엔트리에 붙이거나(값)
    뗀다(None). 상태는 바꾸지 않는다 — 대기로 올리지 않는 신호용(agy `/artifact to
    review` 등). 붙은 엔트리 수를 반환."""
    if not name:
        return 0
    n = 0
    for ent in _entries_for(name, host):
        if badge:
            ent["badge"] = badge
        else:
            ent.pop("badge", None)
        n += 1
    return n


def known_status_for_session(name: Optional[str], host: str = LOCAL_HOST) -> Optional[str]:
    """`status_for_session`과 같지만 엔트리가 없으면 IDLE이 아니라 None —
    "쉬고 있다"와 "모른다"를 가려야 하는 호출부용(에이전트 메시지의 즉시 전달)."""
    if not name:
        return None
    sweep()
    found = [e.get("status", IDLE) for e in _state.values()
             if e.get("tmux_session") == name and e.get("host", LOCAL_HOST) == host]
    if not found:
        return None
    return min(found, key=lambda s: _URGENCY.get(s, 9))


def status_for_session(name: Optional[str], host: str = LOCAL_HOST) -> str:
    """tmux 세션 이름으로 상태를 찾는다 — A2 이후의 **정확한** 경로.

    `status_for_cwd`와 같은 우선순위 규칙(가장 개입이 필요한 상태)을 쓰되,
    키가 cwd 문자열이 아니라 3단 해석이 특정한 세션 이름이라 같은 디렉토리에
    세션이 둘이어도 서로 안 섞인다.
    """
    if not name:
        return IDLE
    sweep()
    found = [e.get("status", IDLE) for e in _state.values()
             if e.get("tmux_session") == name and e.get("host", LOCAL_HOST) == host]
    if not found:
        return IDLE
    return min(found, key=lambda s: _URGENCY.get(s, 9))


def finish_waiting_for_session(name: Optional[str], host: str = LOCAL_HOST) -> int:
    """승인 화면에서 취소(Escape)한 세션을 `done`으로 마친다.

    Codex는 PermissionRequest 훅은 보내지만 승인 화면을 Escape로 취소할 때
    Stop/SessionEnd 훅을 보내지 않는다(0.155.0 실측). 입력 감지만으로 waiting을
    풀면 working으로 돌아가 15분간 남는다. Escape는 실행 승인이 아니라 현재
    요청 중단이므로, 그 순간 waiting이던 **해당 tmux 세션의 로컬 엔트리만**
    완료 처리한다. 다른 상태·호스트는 건드리지 않는다.
    """
    if not name:
        return 0
    sweep()
    now = time.time()
    changed = 0
    for ent in _state.values():
        if (ent.get("tmux_session") != name
                or ent.get("host", LOCAL_HOST) != host
                or ent.get("status") != WAITING):
            continue
        _set_status(ent, DONE, now)
        ent["tool"] = None
        ent["question"] = None
        ent["options"] = None
        ent["last_done"] = now
        changed += 1
    return changed


def get_status(sid: str, host: str = LOCAL_HOST) -> str:
    """세션의 현재 상태 문자열. 모르는 세션은 idle."""
    sweep()
    ent = _state.get(_key(sid, host))
    return ent.get("status", IDLE) if ent else IDLE


def status_for_cwd(cwd: Optional[str], host: str = LOCAL_HOST) -> str:
    """cwd로 상태를 찾는다 — 큐 5번 관문(A4)과 UI가 쓴다.

    같은 cwd가 여럿이면 "가장 개입이 필요한" 상태를 돌려준다(2-6의 정렬
    우선순위와 같은 순서). 여기서 아무것도 안 돌려주면 큐가 waiting인 pane에
    투입해버릴 수 있어, 모호할 때는 **더 보수적인 쪽**을 고른다.
    """
    if not cwd:
        return IDLE
    sweep()
    # 두 맥의 홈 경로는 사용자 이름이 같으면 문자열까지 똑같다 — host를 같이
    # 보지 않으면 원격 세션의 상태가 로컬 조회에 섞인다.
    found = [e.get("status", IDLE) for e in _state.values()
             if e.get("cwd") == cwd and e.get("host", LOCAL_HOST) == host]
    if not found:
        return IDLE
    return min(found, key=lambda s: _URGENCY.get(s, 9))


def get_state(sid: Optional[str] = None, host: str = LOCAL_HOST) -> dict:
    """전체 또는 특정 세션 상태 반환."""
    sweep()
    if sid:
        return _state.get(_key(sid, host), {})
    return dict(_state)


def all_active() -> list[dict]:
    """현재 도구 실행 중(working이고 tool이 붙어 있는) 세션 목록.

    기존 소비자(grid.js 등)가 `tool` 필드를 보고 working을 파생시키던 계약을
    그대로 유지한다 — status를 함께 실어 보내되 필드는 하나도 빼지 않는다.
    """
    sweep()
    now = time.time()
    return [
        # session_id/host는 엔트리 안에 이미 있다 — 키를 파싱해 되살리지 않는다.
        {"session_id": data.get("session_id", key), "host": data.get("host", LOCAL_HOST),
         **data, "elapsed": now - data.get("since", now)}
        for key, data in _state.items()
        if data.get("tool")
    ]
