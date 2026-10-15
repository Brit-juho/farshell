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

## 세션 소유자 (2026-10-13, docs/plan-2.4/60)

tmux 세션 하나에는 **그 pane을 지금 가진 에이전트 세션 하나**(소유자)만 상태를 낸다.
그전에는 그 이름을 단 엔트리를 전부 모아 "가장 급한 상태"를 골랐는데, `/clear`·
`--resume`·재실행마다 새 session_id가 생기고 옛 엔트리가 30~60분 남았다. 순서가
`waiting < done < working`이라 **옛 `done`이 지금의 `working`을 이겼고**(실서버에서 한
세션에 엔트리 셋), 같은 pane의 `TMUX_PANE`을 물려받은 `claude -p` 같은 자식 프로세스의
훅도 같은 세션에 상태를 보탰다.

  - 소유자는 SessionStart(startup/resume/clear)가 정하거나, 소유자가 없을 때 첫 이벤트가
    차지한다. 바뀌면 옛 소유자는 **은퇴**한다(엔트리 삭제, 이후 이벤트 무시 — herdr의
    stale 목록). resume은 은퇴한 대화를 되살린다.
  - 소유자가 아닌 session_id의 이벤트는 상태에 반영하지 않는다.
  - SessionEnd는 완료가 아니라 **반납**이다 — `/clear` 한 번이 「완료」 30분 + 큐 투입이
    되던 경로를 끊는다.
  - 훅이 아닌 경로(`fsh pane report`, 화면 감지가 만든 `pane:<이름>`)는 소유자가 없는
    세션에서만 보인다(예전 집계 규칙 그대로).
"""

import logging
import os
import re
import time
from typing import Optional
from i18n import tr

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

# (host, tmux 세션 이름) → 소유자 엔트리의 _key. 모듈 주석 「세션 소유자」.
_owners: dict[tuple[str, str], str] = {}
# 은퇴한 _key → 은퇴 시각. 늦게 도착한 옛 세션의 훅을 버리는 데만 쓴다.
_retired: dict[str, float] = {}
RETIRED_TTL = 3600.0

# 상태가 훅 요청 밖에서 바뀔 때(화면 감지·중단·입력 해제·TTL·은퇴) 알릴 곳.
# routes/agents.py가 구독해 바로 push한다 — 예전엔 15초 하트비트까지 화면이 몰랐다.
_listeners: list = []


def subscribe(fn) -> None:
    """fn(entry, reason)을 등록한다. entry는 공개 형태, 반납이면 `released: True`."""
    if fn not in _listeners:
        _listeners.append(fn)


def unsubscribe(fn) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def _emit(ent: dict, reason: str) -> None:
    pub = public_entry(ent)
    for fn in list(_listeners):
        try:
            fn(pub, reason)
        except Exception as e:  # noqa: BLE001 — 알림 실패가 상태 전이를 막으면 안 된다
            logger.warning(f"agent_status 알림 실패: {e}")


def _released(ent: dict) -> dict:
    """반납·은퇴된 엔트리를 화면에 알릴 때의 모양. 화면은 이걸 받으면 엔트리를 지운다."""
    return {"session_id": ent.get("session_id"), "host": ent.get("host", LOCAL_HOST),
            "tmux_session": ent.get("tmux_session"), "agent": ent.get("agent"),
            "status": IDLE, "released": True}


def _drop_owner_refs(key: str) -> None:
    for ok, ov in list(_owners.items()):
        if ov == key:
            del _owners[ok]


def _retire(key: str, now: Optional[float] = None) -> Optional[dict]:
    """엔트리를 지우고 이후 이벤트를 무시하게 한다. 지운 엔트리를 반환한다."""
    now = time.time() if now is None else now
    ent = _state.pop(key, None)
    _retired[key] = now
    _drop_owner_refs(key)
    if ent is not None:
        _emit(_released(ent), "release")
    return ent


def _prune_retired(now: float) -> None:
    for k, at in list(_retired.items()):
        if now - at > RETIRED_TTL:
            del _retired[k]


def _owner_entry(name: Optional[str], host: str = LOCAL_HOST) -> Optional[dict]:
    """그 tmux 세션의 소유자 엔트리. 엔트리가 사라졌거나 다른 세션으로 옮겨 갔으면 None."""
    if not name:
        return None
    k = _owners.get((host, name))
    if k is None:
        return None
    ent = _state.get(k)
    if ent is None or ent.get("tmux_session") != name:
        _owners.pop((host, name), None)
        return None
    return ent


def _claim(name: str, key: str, host: str) -> None:
    """key를 그 세션의 소유자로 정한다. 화면 감지·자기보고 자리표시(`pane:<이름>`)는
    훅 소유자가 생기면 의미가 없다 — 남겨 두면 소유자가 반납한 뒤 옛 값이 되살아난다."""
    _owners[(host, name)] = key
    placeholder = _key(f"pane:{name}", host)
    if placeholder != key and placeholder in _state:
        ent = _state.pop(placeholder)
        _emit(_released(ent), "release")


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
    _prune_retired(now)
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
            _emit(ent, "ttl")
        elif status == WAITING and age > ttls[WAITING]:
            _set_status(ent, WORKING, now)
            ent["question"] = None
            ent["options"] = None
            changed += 1
            _emit(ent, "ttl")
        elif status == DONE and age > ttls[DONE]:
            _set_status(ent, IDLE, now)
            changed += 1
            _emit(ent, "ttl")
        elif status == IDLE and age > ttls[DONE]:
            del _state[sid]
            _drop_owner_refs(sid)
            changed += 1
            _emit(_released(ent), "ttl")
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
# Stop 훅 직후 그려지는 계획 확인 화면(Codex Plan 모드)을 done 엔트리가 받는 시간(초).
PLAN_AFTER_DONE_SEC = 10.0

# 훅 tool_name -> kind. 목록에 없는 도구는 approval(PermissionRequest인데 도구를 모르면
# 승인 요청이다).
_TOOL_KINDS = {"AskUserQuestion": KIND_CHOICE, "ExitPlanMode": KIND_PLAN}
# Codex는 질문을 PermissionRequest가 아니라 PreToolUse로 알린다.
_CODEX_CHOICE_TOOLS = ("request_user_input",)

def _kind_question(kind: str) -> str:
    return {
        KIND_CHOICE: tr("선택이 필요합니다"),
        KIND_PLAN: tr("계획 확인이 필요합니다"),
        KIND_QUESTION: tr("입력이 필요합니다"),
    }[kind]


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
        ent.pop("_wait_tuid", None)
        ent.pop("_wait_agent", None)
        ent.pop("_title_seen", None)
    if status != DONE:
        ent.pop("_reopen", None)
    if status != WAITING:
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
    # 화면이 먼저 선택지를 뽑아 둔 waiting이면 승인 훅이 그걸 지우지 않는다 — Claude Code는
    # 대화상자를 그린 뒤에 PermissionRequest 훅을 부르는 일이 있어(2.1.289 실측) 순서가 화면
    # -> 훅이면 모바일 인라인 승인 버튼이 사라지고 「터미널로」로 폴백했다. 승인 훅 자체는
    # 선택지를 주지 않으므로 화면 값이 유일한 출처다.
    screen_options = (ent.get("options") if ent.get("status") == WAITING
                      and ent.get("_kind_src") == "screen" else None)
    ent["kind"] = kind
    ent["_kind_src"] = "hook"
    if kind == KIND_APPROVAL:
        ent["question"] = tr("{tool} 실행 승인이 필요합니다", tool=tool) if tool else tr("실행 승인이 필요합니다")
        ent["options"] = screen_options
    else:
        ent["question"] = _tool_question(tool_input) or _kind_question(kind)
        ent["options"] = _tool_options(tool_input) if kind == KIND_CHOICE else None


def _ends_with_question(text) -> bool:
    return isinstance(text, str) and text.rstrip().endswith(("?", "？"))


# Claude가 사람의 답을 기다리는 도구. PreToolUse에서 바로 waiting으로 올린다(Orca와 같은
# 판정, Codex `request_user_input` 분기와 같은 모양). bypass 모드에서도 PreToolUse →
# PermissionRequest 순으로 온다(2026-10-13 실측, 2.1.296) — pre에서 먼저 올려 두면
# PermissionRequest가 늦거나 유실돼도 질문이 보이고, 답한 뒤 같은 tool_use_id의 post가 푼다.
_CLAUDE_WAIT_TOOLS = {"AskUserQuestion": KIND_CHOICE, "ExitPlanMode": KIND_PLAN}

_HOOK_EVENTS = ("prompt", "user_prompt_submit", "permission", "permission_request",
                "notification", "pre", "post", "session_start", "stop", "session_end")


def _gate(event: str, payload: dict, key: str, session: Optional[str], host: str,
          verified: Optional[bool] = None) -> bool:
    """이 훅 이벤트를 상태에 반영해도 되는가(모듈 주석 「세션 소유자」).

    False면 호출부는 아무것도 바꾸지 않는다. 판정 자체가 소유자를 정하거나 옛 소유자를
    은퇴시키는 부수효과를 가진다 — 검사와 기록을 한 곳에 둬야 둘이 어긋나지 않는다.

    `verified`는 hook_identity의 프로세스 대조 결과다. True(그 pane의 포그라운드 대화형
    에이전트)면 소유자를 바로 넘겨받는다 — 비정상 종료로 반납이 없었던 옛 소유자가 바빠
    보여도 막지 않는다. False(같은 pane을 물려받은 자식)면 소유자가 있는 세션을 절대 넘겨받지
    못한다. None(판정 불가·옛 훅)이면 아래의 상태 기반 규칙을 쓴다.
    """
    now = time.time()
    _prune_retired(now)
    ent = _state.get(key)
    name = session or (ent or {}).get("tmux_session")
    source = payload.get("source")
    starting = event == "session_start" and source != "compact"
    if key in _retired and not (starting and source == "resume"):
        return False   # 은퇴한 세션의 늦은 훅. resume만 그 대화를 되살린다.
    if not name:
        # 어느 tmux 세션인지 모른다 — 차지할 자리가 없으니 예전처럼 엔트리만 갱신한다.
        return True
    owner = _owner_entry(name, host)
    owner_key = _owners.get((host, name)) if owner is not None else None
    if starting:
        if owner is not None and owner_key != key:
            if verified is False:
                logger.info(f"agent {key}: {name}의 자식 프로세스가 연 세션 — 무시")
                return False
            if verified is None and source == "startup" and owner.get("status") in (WORKING, WAITING):
                # 바쁜 소유자가 있는 pane에서 새 세션이 열렸다 — 소유자가 띄운 `claude -p`
                # 같은 자식일 가능성이 크다(같은 TMUX_PANE을 물려받는다). 대화형 세션의
                # 교체는 clear/resume으로 오거나, 종료(SessionEnd 반납) 뒤의 startup이다.
                logger.info(f"agent {key}: 바쁜 소유자가 있는 {name}에서 온 startup — 무시")
                return False
            _retire(owner_key, now)
        _retired.pop(key, None)
        _claim(name, key, host)
        return True
    if owner is None:
        if event not in ("session_end", "notification"):
            _claim(name, key, host)
        return True
    if owner_key != key:
        if verified is True and event != "session_end":
            # 그 pane의 포그라운드 에이전트가 다른 session_id로 말한다 — 소유자가 바뀌었는데
            # SessionStart를 못 받은 경우(훅 미등록·유실)다. 옛 소유자를 은퇴시킨다.
            _retire(owner_key, now)
            _retired.pop(key, None)
            _claim(name, key, host)
            return True
        logger.debug(f"agent {key}: {name}의 소유자가 아니다 — {event} 무시")
        return False
    return True


def _resolves_wait(ent: dict, sub: Optional[str], tuid: Optional[str]) -> bool:
    """이 pre/post가 지금의 waiting을 끝내는가.

    대기를 만든 쪽(메인/서브에이전트)과 같은 쪽이고, 도구 id를 양쪽 다 알면 같은 도구여야
    한다. 예전엔 아무 pre/post나 대기를 풀어서, 질문을 띄운 채 백그라운드 서브에이전트나
    병렬 도구 하나가 끝나면 질문 상태가 사라졌다. id를 모르면(옛 CLI·화면 감지 대기)
    예전 규칙 그대로 메인 에이전트의 이벤트가 푼다.
    """
    if (sub or None) != ent.get("_wait_agent"):
        return False
    want = ent.get("_wait_tuid")
    return not want or not tuid or want == tuid


def _clear_prompt(ent: dict) -> None:
    ent["question"] = None
    ent["options"] = None


def on_event(event: str, payload: dict, session: Optional[str] = None,
             host: str = LOCAL_HOST, agent: Optional[str] = None,
             verified: Optional[bool] = None, ts: Optional[int] = None) -> Optional[dict]:
    """훅 이벤트 처리.

    Args:
        event: "pre" | "post" | "stop" | "permission" | "prompt" | "session_start" |
               "session_end" | "notification" (그 외는 무시)
        payload: Claude Code / Codex hook stdin JSON
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
        verified: hook_identity의 판정(True=ok, False=child, None=모름). `_gate` 참고.
        ts: 훅이 이벤트를 받은 시각(ns). 같은 엔트리에 이보다 늦게 받은 이벤트가 이미
            반영됐으면 이 이벤트를 버린다 — 2초 타임아웃으로 늦게 도착한 pre가 stop 뒤에
            working을 되살리지 않게(herdr·crush의 seq 최고수위).

    Returns:
        해당 세션의 갱신된 상태(엔트리 전체). 반납(session_end)이면 `released: True`인
        idle 모양. 소유자가 아니거나 은퇴한 세션의 이벤트, 알 수 없는 세션의 알 수 없는
        이벤트면 None — 호출부는 None이면 큐·위임·음성을 건드리지 않는다.
    """
    sweep()
    sid = _sid(payload)
    key = _key(sid, host)
    if event not in _HOOK_EVENTS:
        return _state.get(key)   # 알 수 없는 이벤트 — 상태를 건드리지 않는다.
    prev = _state.get(key)
    if (isinstance(ts, int) and prev is not None and isinstance(prev.get("_ts"), int)
            and ts < prev["_ts"]):
        logger.debug(f"agent {key}: 늦게 도착한 {event} — 버림")
        return None
    if not _gate(event, payload, key, session, host, verified):
        return None
    # Claude Code 훅 JSON은 이벤트 종류와 무관하게 항상 cwd를 담고 있다.
    cwd = payload.get("cwd")
    # 서브에이전트가 낸 이벤트면 agent_id가 붙는다(Claude Code·Codex). 도구 호출 id는
    # PermissionRequest에는 없어서 직전 pre의 값을 이어받는다.
    sub = payload.get("agent_id") or None
    tuid = payload.get("tool_use_id") or None

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

    if event == "session_end":
        # 반납 — 이 세션은 끝났다. 완료(done)로 두면 `/clear`마다 「완료」가 30분 남고
        # 큐가 흘러들었다. 엔트리를 지우고 늦게 오는 훅을 무시한다(resume이 되살린다).
        ent = _state.get(key)
        if ent is None:
            return None
        _retire(key)
        return _released(ent)

    if event == "session_start" and payload.get("source") == "compact":
        # 압축(compact) 뒤에도 이 훅이 오는데 작업 도중일 수 있어 무시한다
        # (claude_hooks.MATCHERS와 같은 규칙 — matcher 없이 등록된 옛 설정 대비).
        return _state.get(key)

    ent = _entry(sid, cwd, host)
    _apply_agent(ent, agent)
    if session:
        ent["tmux_session"] = session
    if isinstance(ts, int):
        ent["_ts"] = ts

    if event in ("prompt", "user_prompt_submit"):
        ent["tool"] = None
        ent["since"] = time.time()
        _set_status(ent, WORKING)
        _clear_prompt(ent)
        ent.pop("badge", None)
        return ent

    if event in ("permission", "permission_request"):
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
        ent["_wait_tuid"] = tuid or (ent.get("_pre_tuids") or {}).get(sub or "")
        ent["_wait_agent"] = sub
        _set_status(ent, WAITING)
        return ent

    if event == "pre":
        tool = payload.get("tool_name") or payload.get("tool", "?")
        ent.setdefault("_pre_tuids", {})[sub or ""] = tuid
        ent["count"] = ent.get("count", 0) + 1
        resolved = False
        if ent.get("status") == WAITING:
            if not _resolves_wait(ent, sub, tuid):
                return ent   # 다른 도구·다른 에이전트 — 사람이 아직 답하지 않았다.
            resolved = True
        is_codex = (agent or ent.get("agent")) == "codex"
        if is_codex and tool in _CODEX_CHOICE_TOOLS:
            # Codex Plan 모드의 질문 — PermissionRequest 없이 PreToolUse로만 온다.
            wait_kind = KIND_CHOICE
        elif not is_codex and tool in _CLAUDE_WAIT_TOOLS:
            wait_kind = _CLAUDE_WAIT_TOOLS[tool]
        else:
            wait_kind = None
        if wait_kind:
            ent["tool"] = tool
            ent["since"] = time.time()
            ent["input"] = payload.get("tool_input", {})
            ent.pop("badge", None)
            _set_hook_prompt(ent, wait_kind, tool, ent["input"])
            ent["_wait_tuid"] = tuid
            ent["_wait_agent"] = sub
            _set_status(ent, WAITING)
            return ent
        if sub:
            # 서브에이전트의 도구는 메인 상태를 바꾸지 않는다 — 백그라운드 서브에이전트는
            # 메인의 Stop 뒤에도 돌아서, 여기서 working으로 올리면 끝난 세션이 되살아났다.
            # 서브에이전트가 만든 대기의 그 도구가 이어지는 경우만 대기를 푼다.
            if resolved:
                _set_status(ent, WORKING)
                _clear_prompt(ent)
            return ent
        ent["tool"] = tool
        ent["since"] = time.time()
        ent["input"] = payload.get("tool_input", {})
        ent.pop("badge", None)
        # waiting/done에서 pre가 오면 그것도 working으로 — 새 도구가 시작됐다는
        # 뜻이므로 이전 상태(승인 대기/완료 표시)는 더 이상 유효하지 않다.
        _set_status(ent, WORKING)
        # N38: 이전 승인 질문(있었다면)도 함께 폐기 — 새 도구가 시작됐으니
        # 그 질문은 더 이상 답할 대상이 아니다.
        _clear_prompt(ent)
        return ent

    if event == "post":
        # 상태는 그대로 둔다(파일 상단 규칙). 도구가 끝났을 뿐 다음 도구가
        # 곧바로 이어질 수 있어서다. 대기는 그 대기를 만든 도구의 post만 푼다.
        if ent.get("status") == WAITING:
            if not _resolves_wait(ent, sub, tuid):
                return ent
            _set_status(ent, WORKING)
            _clear_prompt(ent)
        if sub:
            return ent
        ent["last_tool"] = ent.get("tool")
        ent["tool"] = None
        ent["last_done"] = time.time()
        return ent

    if event == "session_start":
        # 에이전트가 막 열렸다 — 아직 아무 일도 안 했으니 idle.
        ent["tool"] = None
        _set_status(ent, IDLE)
        _clear_prompt(ent)
        ent.pop("badge", None)
        return ent

    # stop
    ent["tool"] = None
    _set_status(ent, DONE)
    _clear_prompt(ent)
    # 마지막 응답이 `?`로 끝나면 보조 표시만 붙인다. 대기로 올리지 않는다 —
    # 수사적 질문·인용 때문에 오탐 위험이 커서 큐/푸시를 막을 근거가 못 된다.
    if _ends_with_question(payload.get("last_assistant_message")):
        ent["badge"] = KIND_QUESTION
    else:
        ent.pop("badge", None)
    return ent


def on_waiting(
    sid: str,
    waiting: bool,
    cwd: Optional[str] = None,
    question: Optional[str] = None,
    options: Optional[list] = None,
    host: str = LOCAL_HOST,
    kind: Optional[str] = None,
    session: Optional[str] = None,
    source: str = "screen",
) -> Optional[dict]:
    """A3(승인 프롬프트 감지)이 부르는 진입점.

    `session`은 엔트리를 새로 만들 때 붙일 tmux 세션 이름이다(화면 감지 자리표시
    `pane:<이름>`). 알림보다 먼저 붙어야 화면이 그 세션에 매칭한다.

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
    before = _view_sig(ent)
    if ent is None:
        if not waiting:
            return None
        ent = _entry(sid, cwd, host)
        if session:
            ent["tmux_session"] = session
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
        # 거절/ESC로 막 끝난 엔트리(`_reopen`)는 done이어도 다음 화면 프롬프트를 받는다 —
        # 훅이 없는 CLI(agy 등)는 새 턴 시작을 알릴 pre 훅이 없어 done에서 영영 못 빠져나온다.
        # 옛 프롬프트가 윈도우에 남아 되살리는 일은 감지기가 윈도우를 비워 막는다.
        # Codex Plan 모드는 계획을 다 쓴 턴의 Stop 훅이 먼저 오고 `Implement this plan?` 화면이 그
        # 직후(2026-10-06 0.160.0 실측: 38ms 뒤)에 그려진다 — done만 받으면 계획 확인이
        # waiting으로 영영 올라오지 못한다. 방금(PLAN_AFTER_DONE_SEC 안에) 끝난 done에 한해
        # plan kind만 받는다. 다른 kind는 옛 규칙(뒤늦은 감지가 완료를 되돌리면 안 됨) 그대로다.
        fresh_plan = (kind == KIND_PLAN and ent["status"] == DONE
                      and time.time() - ent.get("updated_at", 0) <= PLAN_AFTER_DONE_SEC)
        if (ent["status"] in (WORKING, WAITING) or fresh_plan
                or (ent["status"] == DONE and ent.get("_reopen"))):
            hook_kind = ent["status"] == WAITING and ent.get("_kind_src") == "hook"
            # 훅의 구조화된 질문과 선택지는 화면의 안내 문구/이전 프롬프트보다 정확하다.
            # 단, 승인·계획 훅(ExitPlanMode)은 선택지를 주지 않으므로 화면에서 추출한 버튼을 보강한다.
            if not hook_kind:
                ent["question"] = question
                ent["options"] = options
                # 화면 패턴 kind, 없으면 폴백 `question`. 훅 kind가 있으면 그것이 이긴다.
                ent["kind"] = kind if kind in KINDS else KIND_QUESTION
                ent["_kind_src"] = source
            elif ent.get("kind") in (KIND_APPROVAL, KIND_PLAN) and options is not None:
                ent["options"] = options
            _set_status(ent, WAITING)
    elif ent["status"] == WAITING:
        _set_status(ent, WORKING)
        ent["question"] = None
        ent["options"] = None
    if _view_sig(ent) != before:
        # 화면 감지는 같은 프롬프트를 다시 그릴 때마다 여기로 온다 — 보이는 값이 바뀔 때만 알린다.
        _emit(ent, "screen")
    return ent


# Codex TUI는 사람의 답이 필요한 화면(승인·질문·MCP 요청·사용자 확인)마다 터미널 제목 앞에
# 이 문구를 붙인다(`codex-rs/tui/src/chatwidget/status_surfaces.rs`: `[ ! ] Action Required`,
# 깜빡임의 다른 쪽은 `[ . ] Action Required`). 데몬에서 돈 훅이 세션을 못 찾아도 pane 제목은
# 그 pane의 것이다.
CODEX_ACTION_TITLE = "Action Required"


_SPINNER = re.compile("[\u2800-\u28ff\u25d0-\u25d3]")   # 작업 중 제목 앞의 스피너(herdr와 같은 판정)


def apply_titles(titles: dict[str, str], host: str = LOCAL_HOST) -> int:
    """{tmux 세션 이름: pane 제목}으로 Codex 소유자의 대기를 보강한다. 바뀐 엔트리 수.

    - 작업 중인데 제목에 「Action Required」 → 대기로 올린다(훅이 데몬에서 돌아 못 온 경우).
    - 대기인데 제목에서 그 문구가 사라졌다 → 이번 대기 동안 제목에서 그 문구를 **본 적이
      있을 때만** 내린다. 제목 설정을 끈 Codex에서 훅이 만든 대기를 곧바로 지우지 않게.
      스피너가 있으면 작업이 이어지는 것이고(working), 없으면 거절·ESC로 턴이 끝난 것이다
      (done — Codex는 이때 훅을 내지 않는다, session-view-measurements.md 1장).
    """
    changed = 0
    for name, title in titles.items():
        ent = _owner_entry(name, host)
        if ent is None or ent.get("agent") != "codex":
            continue
        title = title or ""
        need = CODEX_ACTION_TITLE in title
        st = ent.get("status")
        if need:
            if st == WORKING:
                on_waiting(ent["session_id"], True, host=host, source="title")
                changed += 1
            if ent.get("status") == WAITING:
                ent["_title_seen"] = True
        elif st == WAITING and (ent.get("_kind_src") == "title" or ent.get("_title_seen")):
            if _SPINNER.search(title):
                on_waiting(ent["session_id"], False, host=host)
            else:
                now = time.time()
                _set_status(ent, DONE, now)
                ent["_reopen"] = True
                ent["tool"] = None
                _clear_prompt(ent)
                ent["last_done"] = now
                _emit(ent, "title")
            changed += 1
    return changed


def _view_sig(ent: Optional[dict]) -> tuple:
    """화면에 보이는 값만 모은 지문. 같으면 다시 알릴 필요가 없다."""
    if ent is None:
        return ()
    return (ent.get("status"), ent.get("prompt_id"), ent.get("kind"), ent.get("question"),
            tuple(o.get("key", "") for o in (ent.get("options") or [])), ent.get("badge"),
            ent.get("tmux_session"))


def ack(sid: str, host: str = LOCAL_HOST) -> Optional[dict]:
    """사용자가 완료를 확인했다(탭/카드 클릭) → done을 idle로 내린다.

    done이 아닌 상태에는 아무 것도 하지 않는다 — 작업 중인 세션을 클릭했다고
    working이 지워지면 안 된다.
    """
    sweep()
    ent = _state.get(_key(sid, host))
    if ent and ent.get("status") == DONE:
        _set_status(ent, IDLE)
        _emit(ent, "ack")
    return ent


def ack_session(tmux_name: str, host: str = LOCAL_HOST) -> int:
    """tmux 세션 이름으로 그 세션의 완료(done)를 확인 처리한다 → 바뀐 엔트리 수.

    화면은 세션 이름만 안다(훅의 session_id는 모른다). 소유자가 있으면 소유자 하나만,
    없으면 그 이름의 엔트리들(`_entries_for`와 같은 규칙)을 본다. done이 아닌 상태는
    건드리지 않는다 — 작업 중인 세션을 클릭했다고 working이 지워지면 안 된다. 반복 호출해도
    두 번째부터는 0을 돌려줄 뿐이다.
    """
    sweep()
    n = 0
    for ent in _entries_for(tmux_name, host):
        if ent.get("status") == DONE:
            _set_status(ent, IDLE)
            _emit(ent, "ack")
            n += 1
    return n


def _entries_for(tmux_name: str, host: str = LOCAL_HOST) -> list[dict]:
    """tmux 세션 이름으로 엔트리를 찾는다.

    `deps.py`의 `_on_waiting_change`가 targets를 계산하던 것과 **같은 규칙**
    (tmux_session == tmux_name 이고 host == 주어진 host)이다. 두 곳이 각자
    조건을 들고 있으면 하나만 고쳐질 위험이 있어 여기 하나로 모았다 —
    `deps.py`도 이 헬퍼를 쓴다. host 기본값이 LOCAL_HOST인 이유: 두 맥의
    기본 tmux 세션 이름(`dev`)이 흔히 같아서, host를 안 거르면 원격의 같은
    이름 세션이 로컬 조회에 걸려든다.

    소유자가 있으면 **소유자 하나만** 돌려준다(모듈 주석 「세션 소유자」). 화면 감지·중단
    문구·승인 토큰·상태 조회가 전부 여기를 거치므로, 옛 세션이나 자식 프로세스의 엔트리가
    그 세션의 상태에 섞이지 않는다. 소유자가 없을 때(자기보고·화면 감지만 있는 CLI)는
    예전처럼 그 이름의 엔트리 전부다.
    """
    if not tmux_name:
        return []
    owner = _owner_entry(tmux_name, host)
    if owner is not None:
        return [owner]
    return [e for e in _state.values()
            if e.get("tmux_session") == tmux_name and e.get("host", LOCAL_HOST) == host]


_last_uid_of: dict[str, dict[str, str]] = {}


def reconcile_sessions(live: list[tuple[str, str]], host: str = LOCAL_HOST) -> int:
    """tmux가 알려준 지금의 세션 목록(`(#{session_id}, 이름)`)으로 엔트리를 맞춘다.

    엔트리는 세션을 **이름**으로 들고 있어서, 이름을 바꾸면 배지가 다음 훅까지 사라지고
    닫힌 세션의 이름을 새 세션이 다시 쓰면 옛 상태(최대 60분)가 붙었다. 처음 본 엔트리에
    tmux의 불변 id(`$3`)를 새겨 두고, 다음 대조에서 그 id의 이름이 바뀌었으면 따라가고
    id가 사라졌으면(세션 종료) 은퇴시킨다. id를 아직 못 새긴 엔트리는 이름이 목록에 없으면
    은퇴한다. 바뀐 엔트리 수를 반환한다. tmux 훅(kick) 때 deps.on_tmux_change가 부른다.
    """
    name_of = {uid: name for uid, name in live if uid}
    uid_of = {name: uid for uid, name in live if uid}
    # 직전 대조의 이름 → id. 지난 대조 뒤에 생긴 엔트리는 아직 id가 없는데, 그사이 이름이
    # 바뀌면 지금 목록에는 옛 이름이 없다 — 직전 목록에서 그 이름의 id를 찾는다.
    before = _last_uid_of.get(host, {})
    _last_uid_of[host] = uid_of
    now = time.time()
    changed = 0
    for key, ent in list(_state.items()):
        if ent.get("host", LOCAL_HOST) != host:
            continue
        name = ent.get("tmux_session")
        if not name:
            continue
        uid = ent.get("_tmux_uid") or uid_of.get(name) or before.get(name)
        if uid is None:
            _retire(key, now)
            changed += 1
            continue
        ent["_tmux_uid"] = uid
        if uid not in name_of:
            _retire(key, now)
            changed += 1
            continue
        new = name_of[uid]
        if new != name:
            ent["tmux_session"] = new
            if _owners.get((host, name)) == key:
                del _owners[(host, name)]
                _owners[(host, new)] = key
            # 화면은 엔트리를 host+session_id로 들고 있어서, 새 이름을 단 같은 엔트리가 옛 것을 대신한다.
            _emit(ent, "rename")
            changed += 1
    return changed


def visible_state() -> dict:
    """클라이언트에 내려줄 엔트리 전체(`get_state()`와 같은 키). 소유자가 있는 세션의
    소유자 아닌 엔트리는 뺀다 — 화면이 세션별로 "가장 급한 엔트리"를 고를 때 옛 엔트리가
    끼어들지 않게. 서버 안의 판정은 `_entries_for`가 같은 규칙을 쓴다."""
    sweep()
    out = {}
    for k, e in _state.items():
        name = e.get("tmux_session")
        if name:
            owner_key = _owners.get((e.get("host", LOCAL_HOST), name))
            if owner_key is not None and owner_key != k and owner_key in _state:
                continue
        out[k] = e
    return out


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
        raise ValueError(tr("알 수 없는 상태: {status} (가능: {allowed})", status=status, allowed=", ".join(STATUSES)))
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
        before = ent.get("badge")
        if badge:
            ent["badge"] = badge
        else:
            ent.pop("badge", None)
        if ent.get("badge") != before:
            _emit(ent, "screen")
        n += 1
    return n


def known_status_for_session(name: Optional[str], host: str = LOCAL_HOST) -> Optional[str]:
    """`status_for_session`과 같지만 엔트리가 없으면 IDLE이 아니라 None —
    "쉬고 있다"와 "모른다"를 가려야 하는 호출부용(에이전트 메시지의 즉시 전달)."""
    if not name:
        return None
    sweep()
    found = [e.get("status", IDLE) for e in _entries_for(name, host)]
    if not found:
        return None
    return min(found, key=lambda s: _URGENCY.get(s, 9))


def status_for_session(name: Optional[str], host: str = LOCAL_HOST) -> str:
    """tmux 세션 이름으로 상태를 찾는다 — A2 이후의 **정확한** 경로.

    `status_for_cwd`와 같은 우선순위 규칙(가장 개입이 필요한 상태)을 쓰되,
    키가 cwd 문자열이 아니라 3단 해석이 특정한 세션 이름이라 같은 디렉토리에
    세션이 둘이어도 서로 안 섞인다. 소유자가 있으면 그 하나의 상태다(`_entries_for`).
    """
    if not name:
        return IDLE
    sweep()
    found = [e.get("status", IDLE) for e in _entries_for(name, host)]
    if not found:
        return IDLE
    return min(found, key=lambda s: _URGENCY.get(s, 9))


def _finish_session(name: Optional[str], host: str, statuses: tuple) -> int:
    """그 tmux 세션의 엔트리(소유자가 있으면 소유자) 중 `statuses`인 것만 `done`으로 마친다."""
    if not name:
        return 0
    sweep()
    now = time.time()
    changed = 0
    for ent in _entries_for(name, host):
        if ent.get("status") not in statuses:
            continue
        _set_status(ent, DONE, now)
        ent["_reopen"] = True
        ent["tool"] = None
        ent["question"] = None
        ent["options"] = None
        ent["last_done"] = now
        changed += 1
        _emit(ent, "interrupt")
    return changed


def finish_interrupted_for_session(name: Optional[str], host: str = LOCAL_HOST) -> int:
    """승인 거절·ESC 중단으로 끝난 세션의 waiting/working 엔트리를 `done`으로 마친다.

    Claude Code는 사용자가 권한 대화상자를 거절하거나(`4. No`) 작업을 ESC로 끊어도 Stop
    훅을 내지 않는다(2.1.289 실측: 거절 뒤 화면은 `Interrupted · What should Claude do
    instead?`와 빈 입력줄인데 엔트리는 `working`으로 15분 TTL까지 남았다). 화면의 중단
    문구(`detect/*.toml`의 `interrupt`)가 그 순간을 알리는 유일한 신호다.
    """
    return _finish_session(name, host, (WAITING, WORKING))


def finish_waiting_for_session(name: Optional[str], host: str = LOCAL_HOST) -> int:
    """승인 화면에서 취소(Escape)한 세션을 `done`으로 마친다.

    Codex는 PermissionRequest 훅은 보내지만 승인 화면을 Escape로 취소할 때
    Stop/SessionEnd 훅을 보내지 않는다(0.155.0 실측). 입력 감지만으로 waiting을
    풀면 working으로 돌아가 15분간 남는다. Escape는 실행 승인이 아니라 현재
    요청 중단이므로, 그 순간 waiting이던 **해당 tmux 세션의 로컬 엔트리만**
    완료 처리한다. 다른 상태·호스트는 건드리지 않는다.
    """
    return _finish_session(name, host, (WAITING,))


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
