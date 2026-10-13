"""공유 인스턴스 (Dependency Injection 대체).

모든 라우터가 여기서 import하여 같은 인스턴스를 사용.
circular import 없이 전역 상태를 한 곳에서 관리.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket
from i18n import tr
from pty_manager import PTYManager
from session_store import SessionStore
from output_watcher import OutputWatcher
import agent_prompt_detect
import agent_status
import auto_responder
import pane_input
import terminal_reports

logger = logging.getLogger(__name__)

pty_mgr = PTYManager()
session_store = SessionStore()
output_watcher = OutputWatcher()

# Phase 8 G5: trust prompt 자동 응답 (옵트인 — VT_AUTO_TRUST=1)
_auto_responder = auto_responder.get_global_responder(
    write_fn=lambda sid, data: pty_mgr.write(sid, data)
)

# 알림용 WebSocket 클라이언트 집합
notify_clients: set[WebSocket] = set()

# 웹 PTY(웹 세션 id) → 그 tmux 클라이언트가 **지금** 붙어 있는 세션 이름. session_store의
# tmux_name은 attach 때 한 번 적은 값이라, 클라이언트가 다른 세션으로 옮겨 가면(세션 종료 뒤
# `detach-on-destroy off`의 자동 전환, 터미널 안의 `fsh` switch-client, `prefix s`) 그 화면의
# 질문·중단 문구가 원래 세션에 적용됐다. 화면 감지 경로는 이 값을 먼저 본다. 화면(프런트)이
# 보는 session_store 값은 바꾸지 않는다 — 탭과 PTY의 짝은 프런트의 계약이다.
_pty_tmux_now: dict[str, str] = {}


def tmux_for_pty(pty_session_id: str) -> str | None:
    """화면 감지·승인 토큰이 쓸 tmux 세션 이름. 클라이언트가 옮겨 갔으면 옮겨 간 곳."""
    now = _pty_tmux_now.get(pty_session_id)
    if now:
        return now
    info = session_store.get(pty_session_id)
    return info.tmux_name if info else None


def _read_tmux_state() -> tuple[list[tuple[str, str]], dict[str, str]] | None:
    """(세션 목록 [(#{session_id}, 이름)], {클라이언트 tty: 세션 이름}). tmux가 없으면 None."""
    import tmux_runner
    sess = tmux_runner.run_text(["list-sessions", "-F", "#{session_id}\t#{session_name}"], timeout=2.0)
    if sess is None:
        return None
    live = [tuple(line.split("\t", 1)) for line in sess.splitlines() if "\t" in line]
    clients = tmux_runner.run_text(["list-clients", "-F", "#{client_tty}\t#{session_name}"], timeout=2.0) or ""
    by_tty = dict(tuple(line.split("\t", 1)) for line in clients.splitlines() if "\t" in line)
    return live, by_tty


def apply_tmux_state(live: list[tuple[str, str]], by_tty: dict[str, str]) -> None:
    """tmux에서 읽은 값을 반영한다(이벤트 루프에서 — agent_status는 루프 안에서만 바꾼다)."""
    agent_status.reconcile_sessions(live)
    for sid in list(_pty_tmux_now):
        if sid not in pty_mgr.sessions:
            del _pty_tmux_now[sid]
    for sid, s in list(pty_mgr.sessions.items()):
        info = session_store.get(sid)
        tty = getattr(s, "tty", "")
        if not info or not info.tmux_name or not tty or tty not in by_tty:
            continue
        cur = by_tty[tty]
        if cur == tmux_for_pty(sid):
            continue
        if cur == info.tmux_name:
            _pty_tmux_now.pop(sid, None)
        else:
            _pty_tmux_now[sid] = cur
        # 감지 윈도우에는 이전 세션의 글자가 남아 있다 — 그걸로 다음 세션을 판정하지 않는다.
        _prompt_detector.remove(sid)


async def on_tmux_change() -> None:
    """tmux 훅(`/api/tmux/kick`)마다 — 세션 이름 변경·종료와 클라이언트 이동을 반영한다."""
    import asyncio
    try:
        got = await asyncio.to_thread(_read_tmux_state)
    except Exception as e:  # noqa: BLE001 — 다음 훅에 다시 본다
        logger.debug(f"tmux 상태 읽기 실패: {e}")
        return
    if got is not None:
        apply_tmux_state(*got)

# A3: 승인 대기(waiting) 감지 — auto_responder 옆에서 같은 출력 스트림을 먹되
# 아무것도 쓰지 않고 상태만 알린다. PTY 세션 id(웹 키스페이스)를 tmux 세션
# 이름(상태 키스페이스)으로 옮기는 다리가 아래 콜백이다 — 그 매핑을 아는 곳은
# session_store 하나뿐이라 여기서 묶는다.
def _on_waiting_change(pty_session_id: str, waiting: bool) -> None:
    tmux_name = tmux_for_pty(pty_session_id)
    if not tmux_name:
        # tmux가 아닌 순수 PTY 세션 — 상태를 붙일 대상(카드/탭)이 없다.
        return
    # N38 — waiting=True일 때만 질문/선택지를 함께 싣는다(agent_prompt_detect가
    # 같은 pty_session_id로 이미 뽑아 둔 값). False일 땐 그대로 None, None —
    # agent_status.on_waiting이 그 경우 엔트리 쪽 값도 지운다.
    question, options = (_prompt_detector.get_prompt(pty_session_id) if waiting else (None, None))

    # 훅이 만든 엔트리가 있으면 그쪽을, 없으면 pane 자기보고와 같은 키를 쓴다.
    # PTY는 이 서버가 직접 돌리는 것이므로 항상 로컬 엔트리만 본다 — host를 안
    # 거르면 원격 호스트의 같은 이름 세션(`dev`는 기본 이름이라 흔하다)이 여기
    # 걸려들어 남의 pane 상태를 로컬 것으로 바꿔버린다. P1-1(ADR-33) —
    # `agent_status._entries_for`와 같은 규칙을 쓴다(consume_prompt도 이걸
    # 쓴다) — 두 곳이 각자 조건을 들면 하나만 고쳐질 위험이 있다.
    kind = _prompt_detector.get_kind(pty_session_id) if waiting else None
    # 소유자가 있으면 소유자 하나만 받는다(agent_status 「세션 소유자」) — 옛 세션·자식
    # 프로세스의 엔트리에 화면 판정이 번지지 않는다.
    targets = [e.get("session_id") for e in agent_status._entries_for(tmux_name)]
    for sid in targets or [f"pane:{tmux_name}"]:
        agent_status.on_waiting(sid, waiting, question=question, options=options, kind=kind,
                                session=tmux_name)

    # A5: 승인 대기는 **사용자가 답해야만 풀리는** 상태라, 화면을 안 보고 있으면
    # 세션이 그대로 멈춰 있는다. 기존 "작업 완료" 푸시와 같은 규칙을 그대로 따른다:
    #   - WS 클라이언트가 하나라도 붙어 있으면 보내지 않는다(같은 알림 두 번 방지)
    #   - 본문에 명령·경로·코드를 넣지 않는다(잠금화면에 그대로 뜬다).
    #     세션 이름조차 넣지 않는다 — 사용자가 프로젝트 이름으로 세션을 짓는 일이
    #     흔해서, 그 자체가 경로만큼 알려주는 정보가 된다.
    if waiting:
        _push_waiting_notice()


def _push_waiting_notice() -> None:
    if notify_clients:
        return
    try:
        import asyncio

        import push

        if not push.available():
            return
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(push.send, tr("승인 대기"), tr("세션에서 확인이 필요합니다"), "/"))
    except RuntimeError:
        pass  # 이벤트 루프 밖(테스트 등) — 푸시는 부가 기능이라 조용히 건너뛴다
    except Exception:
        pass


def _agent_for_pty(pty_session_id: str):
    """이 PTY 세션의 tmux pane에서 도는 CLI 이름 — kind 패턴을 그 CLI의 spec으로만
    매칭하기 위한 판별. 1차 신호(엔트리의 `agent`: 훅/자기보고) 다음 프로세스 스캔."""
    tmux_name = tmux_for_pty(pty_session_id)
    if not tmux_name:
        return None
    for ent in agent_status._entries_for(tmux_name):
        if ent.get("agent"):
            return ent["agent"]
    import agent_detector

    found = agent_detector.detect(tmux_name)
    name = (found or {}).get("agent")
    return name if name in agent_detector.KNOWN_AGENTS else None


def _on_badge_change(pty_session_id: str, badge) -> None:
    tmux_name = tmux_for_pty(pty_session_id)
    if tmux_name:
        agent_status.set_badge_for_session(tmux_name, badge)


def _on_interrupt(pty_session_id: str) -> None:
    """화면에 거절/중단 문구가 그려졌다 — CLI는 이때 Stop 훅을 내지 않는다."""
    tmux_name = tmux_for_pty(pty_session_id)
    if tmux_name:
        agent_status.finish_interrupted_for_session(tmux_name)


_prompt_detector = agent_prompt_detect.get_global_detector(
    _on_waiting_change, agent_for=_agent_for_pty, on_badge=_on_badge_change,
    on_interrupt=_on_interrupt)


def on_terminal_input(pty_session_id: str, data: bytes | str | None = None) -> None:
    """PTY 입력과 훅 기반 waiting 상태를 한 경로에서 해제한다.

    훅이 PTY 감지보다 먼저 waiting을 만들거나 PTY 패턴이 렌더 잡음 때문에
    누락돼도, 실제 사용자 입력은 대기를 끝내는 확실한 신호다. Escape는 Codex의
    승인 취소이므로 `done`, 그 밖의 답은 도구 실행이 이어질 수 있어 `working`.
    """
    # tmux가 attach마다 보내는 질의(DA/XTVERSION/색/포커스)에 xterm.js가 답하는 바이트는 사람이
    # 친 키가 아니다 — 새로고침·두 번째 화면 attach가 waiting을 풀던 근본 원인(terminal_reports).
    if terminal_reports.is_terminal_report(data):
        return
    tmux_name = tmux_for_pty(pty_session_id)
    # 자동 투입(큐·에이전트 메시지)이 사람이 쓰는 중인 입력에 끼어들지 않게(pane_input).
    pane_input.note_human_input(tmux_name)
    was_waiting = agent_status.status_for_session(tmux_name) == agent_status.WAITING
    is_cancel = data == b"\x1b" or data == "\x1b"

    # 먼저 done으로 바꾸면 detector의 waiting=False 콜백은 done을 다시 working으로
    # 내리지 않는다(agent_status.on_waiting의 기존 계약).
    if was_waiting and is_cancel:
        agent_status.finish_waiting_for_session(tmux_name)

    _prompt_detector.on_user_input(pty_session_id)

    # 훅만 waiting을 만들었고 detector 내부는 그 상태를 모를 수 있다. 이 경우
    # on_user_input이 콜백을 내지 않으므로 명시적으로 한 번 동기화한다.
    if was_waiting and not is_cancel and agent_status.status_for_session(tmux_name) == agent_status.WAITING:
        _on_waiting_change(pty_session_id, False)

# Phase 8 G2: WS 연결 한도 카운터 (single-worker 전용 — TODOS.md D1)
ws_count_per_session: dict[str, int] = {}
ws_total_count = 0
