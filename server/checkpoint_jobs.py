"""에이전트 훅 이벤트 → 턴 체크포인트 예약. **자동으로 git을 실행하는 유일한 곳**이다.

`routes/agents.py agent_event`가 훅 이벤트마다 `on_agent_event`를 **기다리지 않고** 부른다. 이 모듈은
- 훅 응답의 내용·시간을 바꾸지 않는다(동기 부분은 메모리 장부만 만지고, git은 전부 `ckpt` 풀의 스레드에서 돈다),
- 새 훅을 등록하지 않는다(이미 오는 `session_start`·`prompt`·`pre`·`stop`만 쓴다 — 에이전트 설정 파일에 서버가 쓰지 않는다는 계약 16),
- 저장소별로 **켠 곳에서만** 돈다(`checkpoint_store`, 기본 꺼짐)고, 서버 전체 스위치 `VT_CHECKPOINTS=0`이면 아무것도 안 한다.

## 언제 찍는가

| 이벤트 | 종류 | 동작 |
|---|---|---|
| `session_start` | `baseline` | 이 세션의 체크포인트가 아직 없으면 1개 |
| `prompt`/`user_prompt_submit` | `pre-turn` | 새 턴의 시작 상태(비동기 — 에이전트의 첫 수정과 경쟁할 수 있다) |
| `pre`(도구 실행 전) | — | 찍지 않는다. 턴의 **첫** `pre`의 시각(`ts`)만 장부에 적는다 |
| `stop` | `turn-end` | 턴의 끝 상태 + 그 턴의 `pre-turn`을 믿을지(`PreTrusted`) 확정 |

**`pre-turn`을 믿는 기준**: `pre-turn` 캡처의 `write-tree`가 끝난 시각 < 같은 턴 첫 `pre`의 훅 시각이면 "턴 시작 전 상태"로 본다
(도구가 안 불린 턴은 믿는다). `pre` 훅이 늦게 도착할 수 있어 **턴이 끝난 뒤** `turn-end` 커밋에 적는다. 서버가 턴 도중 재시작돼
장부를 잃으면 믿지 않는다(보수적).

## 안전

- 소유자 규칙을 그대로 따른다: `on_event`가 None을 돌려준 이벤트(자식·은퇴 세션)와 서브에이전트(`agent_id`)·반납은 예약하지 않는다.
- 세션 하나당 순서대로(같은 종류가 연달아 오면 최신 하나만), 전역 동시 실행은 `ckpt` 풀(2)이 묶는다. 풀이 꽉 차면 그 캡처를 **건너뛴다**
  (큐에 쌓지 않는다). 같은 작업 폴더가 연속 3번 실패하면 5분 쉰다(`failure_backoff`).
- 세션의 저장소는 첫 캡처 때 훅의 `cwd`로 정해 고정한다(에이전트가 다른 저장소로 `cd`해도 처음 저장소만 찍는다 — 알려진 한계).
- 원격·보호 마운트 위의 `cwd`는 건드리지 않는다. 라벨(tmux 이름·마지막 지시)은 이 메모리에만 있고 저장소 객체에는 안 들어간다.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Optional

import checkpoint_store
import checkpoints
from failure_backoff import FailureBackoff
from resilience import mounts as res_mounts, pools as res_pools

logger = logging.getLogger(__name__)

EVENTS = frozenset({"session_start", "prompt", "user_prompt_submit", "pre", "stop"})
_KIND = {"session_start": "baseline", "prompt": "pre-turn", "user_prompt_submit": "pre-turn", "stop": "turn-end"}
FAILS_BEFORE_BACKOFF = 3
BACKOFF_SECONDS = 300.0
MAX_SESSIONS = 256
NEGATIVE_TTL = 30.0            # 켜지 않은(또는 저장소가 아닌) cwd를 다시 조사하기까지
BUSY_RETRY_DELAY = 1.0         # 같은 작업 폴더의 다른 캡처가 잠금을 쥐었을 때 한 번만 다시 시도


def globally_enabled() -> bool:
    """서버 전체 스위치. 기본 켜짐(저장소별로 켠 곳에서만 동작한다)."""
    return os.environ.get("VT_CHECKPOINTS", "1").strip().lower() not in ("0", "false", "no", "off")


class _Sess:
    __slots__ = ("key", "agent", "cwd", "top", "repo_key", "base_turn", "turns_seen", "turn_open", "first_pre_ns",
                 "pre_tree_ns", "tmux", "seen", "pending", "running", "wrote")

    def __init__(self, key: str, agent: str, cwd: str):
        self.key = key
        self.agent = agent
        self.cwd = cwd
        self.top: Optional[str] = None          # 첫 캡처 때 정한 작업 트리 루트(고정)
        self.repo_key: Optional[str] = None
        self.base_turn: Optional[int] = None    # 서버 재시작 전 ref에서 복원한 마지막 턴 번호
        self.turns_seen = 0                     # 이 서버가 본 prompt 수
        self.turn_open = False
        self.first_pre_ns: Optional[int] = None
        self.pre_tree_ns: Optional[int] = None
        self.tmux: Optional[str] = None
        self.seen = time.monotonic()
        self.pending: dict[str, dict] = {}      # 종류 → 작업(같은 종류는 최신 하나만, 넣은 순서대로 처리)
        self.running = False
        self.wrote = False


_sessions: dict[str, _Sess] = {}
_negative: dict[str, float] = {}
_backoff = FailureBackoff(BACKOFF_SECONDS)
_fails: dict[str, int] = {}
_last: dict[str, dict] = {}                      # 작업 트리 루트 → 마지막 결과(메모리, 재시작하면 사라진다)


def session_key(agent: str, session_id: str) -> str:
    return hashlib.sha256(f"{agent}:local:{session_id}".encode()).hexdigest()[:16]


def on_agent_event(event: str, state: Optional[dict], payload: dict, ts: Optional[int], *, agent: Optional[str] = None) -> None:
    """훅 이벤트 하나를 장부에 반영하고 필요하면 캡처를 예약한다. 기다리지 않고, 어떤 경우에도 예외를 던지지 않는다."""
    try:
        _on_agent_event(event, state, payload, ts, agent)
    except Exception as e:  # noqa: BLE001 — 체크포인트 문제가 훅 응답을 깨면 안 된다
        logger.debug("[ckpt] 이벤트 처리 실패: %s", type(e).__name__)


def _on_agent_event(event, state, payload, ts, agent) -> None:
    if event not in EVENTS or not globally_enabled():
        return
    if state is None or state.get("released") or not isinstance(payload, dict) or payload.get("agent_id"):
        return
    sid, cwd = payload.get("session_id"), payload.get("cwd")
    if not isinstance(sid, str) or not sid or not isinstance(cwd, str) or not cwd.startswith("/") or "\0" in cwd:
        return
    if not checkpoint_store.any_enabled():          # 켠 저장소가 하나도 없으면 git은 아예 안 부른다
        return
    ag = agent if agent in ("claude", "codex") else "claude"
    key = session_key(ag, sid)
    s = _sessions.get(key)
    if event == "pre":
        if s is not None and s.turn_open and s.first_pre_ns is None and isinstance(ts, int):
            s.first_pre_ns = ts
        return
    if s is None:
        if time.monotonic() < _negative.get(cwd, 0.0):
            return
        s = _new_session(key, ag, cwd)
    s.seen = time.monotonic()
    tmux = state.get("tmux_session")
    if isinstance(tmux, str):
        s.tmux = tmux
    kind = _KIND[event]
    if kind == "pre-turn":
        s.turns_seen += 1
        s.turn_open = True
        s.first_pre_ns = None
        s.pre_tree_ns = None
    elif kind == "turn-end":
        s.turn_open = False
    job = {"kind": kind}
    s.pending.pop(kind, None)                       # 같은 종류는 최신 하나만(뒤로 보내 순서를 지킨다)
    s.pending[kind] = job
    if not s.running:
        s.running = True
        asyncio.get_running_loop().create_task(_drain(s))


def _new_session(key: str, agent: str, cwd: str) -> _Sess:
    if len(_sessions) >= MAX_SESSIONS:
        idle = sorted((x for x in _sessions.values() if not x.running and not x.pending), key=lambda x: x.seen)
        for x in idle[: max(1, MAX_SESSIONS // 8)]:
            _sessions.pop(x.key, None)
    s = _sessions[key] = _Sess(key, agent, cwd)
    return s


async def _drain(s: _Sess) -> None:
    try:
        while s.pending:
            kind = next(iter(s.pending))
            job = s.pending.pop(kind)
            await _run_job(s, job)
    except Exception as e:  # noqa: BLE001
        logger.debug("[ckpt] 작업 실패: %s", type(e).__name__)
    finally:
        s.running = False
        if s.pending and not s.running:             # 끝나는 사이 들어온 작업
            s.running = True
            asyncio.get_running_loop().create_task(_drain(s))


def _resolve(s: _Sess) -> str:
    """세션의 저장소를 처음 한 번 정한다(스레드에서). 결과: `ok` | `skip`(저장소 아님·마운트·꺼짐)."""
    skip = res_mounts.skip_for_scan(s.cwd)
    if skip:
        return "skip"
    info = checkpoints.repo_key(s.cwd)
    if info is None or not checkpoint_store.is_enabled(info["key"]):
        return "skip"
    s.top, s.repo_key = info["top"], info["key"]
    return "ok"


def _capture_job(s: _Sess, kind: str, pre_trusted: Optional[bool]) -> dict:
    """`ckpt` 풀의 스레드에서 도는 캡처 한 건."""
    top = s.top
    if s.base_turn is None:
        mine = [e["turn"] for e in checkpoints.list_refs(top) if e["sess"] == s.key and e["turn"] is not None]
        s.base_turn = max(mine) if mine else 0
    if kind == "baseline" and any(e["sess"] == s.key for e in checkpoints.list_refs(top)):
        return {"status": "exists"}
    turn = s.base_turn + s.turns_seen
    res = checkpoints.capture(top, kind=kind, sess_key=s.key, agent=s.agent, turn=turn, trusted=True, pre_trusted=pre_trusted)
    if res["status"] == "ok":
        checkpoints.gc(top)
    return res


async def _run_job(s: _Sess, job: dict) -> None:
    kind = job["kind"]
    if s.top is None:
        try:
            outcome = await res_pools.run("ckpt", _resolve, s, op="ckpt.resolve", key=s.key, timeout=15)
        except Exception as e:  # noqa: BLE001 — 풀 포화·기한
            logger.debug("[ckpt] 저장소 확인 건너뜀: %s", type(e).__name__)
            return
        if outcome != "ok":
            _negative[s.cwd] = time.monotonic() + NEGATIVE_TTL
            if len(_negative) > 512:
                _negative.clear()
            s.pending.clear()
            _sessions.pop(s.key, None)
            return
    if not checkpoint_store.is_enabled(s.repo_key or ""):      # 그 사이 껐다
        s.pending.clear()
        _sessions.pop(s.key, None)
        return
    top = s.top
    if _backoff.should_skip(top):
        _note(top, kind, "backoff")
        return
    pre_trusted: Optional[bool] = None
    if kind == "turn-end" and s.pre_tree_ns is not None:
        pre_trusted = s.first_pre_ns is None or s.pre_tree_ns < s.first_pre_ns
    res = None
    for attempt in range(2):
        try:
            res = await res_pools.run("ckpt", _capture_job, s, kind, pre_trusted, op="ckpt.capture", key=s.key,
                                      timeout=checkpoints.timeout_seconds() + 10)
        except res_pools.Busy:
            _note(top, kind, "pool_busy")                    # 큐에 쌓지 않고 건너뛴다
            return
        except Exception as e:  # noqa: BLE001 — OpTimeout 등
            res = {"status": "timeout" if isinstance(e, TimeoutError) else "error"}
        if res["status"] == "busy" and attempt == 0:         # 같은 작업 폴더의 다른 세션이 캡처 중
            await asyncio.sleep(BUSY_RETRY_DELAY)
            continue
        break
    status = res["status"]
    if kind == "pre-turn" and res.get("tree_at_ns"):
        s.pre_tree_ns = res["tree_at_ns"]
    _note(top, kind, status)
    if status in ("ok", "unchanged", "exists", "unsupported", "skipped_busy", "too_large", "norepo"):
        _fails.pop(top, None)
        _backoff.clear(top)
    else:                                                    # timeout · error · busy(끝까지)
        n = _fails.get(top, 0) + 1
        _fails[top] = n
        if n >= FAILS_BEFORE_BACKOFF:
            _backoff.mark_failed(top)
            _fails.pop(top, None)
    if status == "ok":
        s.wrote = True
        await _notify()


async def _notify() -> None:
    try:
        from routes import system
        await system.broadcast_workspace_event({"type": "checkpoints_changed"})
    except Exception as e:  # noqa: BLE001
        logger.debug("[ckpt] 알림 실패: %s", type(e).__name__)


def _note(top: str, kind: str, status: str) -> None:
    if len(_last) > 256:
        _last.clear()
    _last[top] = {"kind": kind, "status": status, "at": time.time()}


# --- 읽기(화면용) -------------------------------------------------------------


def last_result(top: str) -> Optional[dict]:
    return _last.get(top)


def labels(sess_key: str) -> dict:
    """이 서버가 아는 세션 라벨(재시작하면 사라진다). tmux 이름은 이 맥 화면에만 나간다."""
    s = _sessions.get(sess_key)
    return {"tmux": s.tmux} if s is not None and s.tmux else {}


def _reset_for_tests() -> None:
    _sessions.clear()
    _negative.clear()
    _fails.clear()
    _last.clear()
    _backoff.__init__(BACKOFF_SECONDS)
