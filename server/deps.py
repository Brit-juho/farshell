"""공유 인스턴스 (Dependency Injection 대체).

모든 라우터가 여기서 import하여 같은 인스턴스를 사용.
circular import 없이 전역 상태를 한 곳에서 관리.
"""

from __future__ import annotations

from fastapi import WebSocket
from pty_manager import PTYManager
from session_store import SessionStore
from output_watcher import OutputWatcher
import agent_prompt_detect
import agent_status
import auto_responder

pty_mgr = PTYManager()
session_store = SessionStore()
output_watcher = OutputWatcher()

# Phase 8 G5: trust prompt 자동 응답 (옵트인 — VT_AUTO_TRUST=1)
_auto_responder = auto_responder.get_global_responder(
    write_fn=lambda sid, data: pty_mgr.write(sid, data)
)

# 알림용 WebSocket 클라이언트 집합
notify_clients: set[WebSocket] = set()

# A3: 승인 대기(waiting) 감지 — auto_responder 옆에서 같은 출력 스트림을 먹되
# 아무것도 쓰지 않고 상태만 알린다. PTY 세션 id(웹 키스페이스)를 tmux 세션
# 이름(상태 키스페이스)으로 옮기는 다리가 아래 콜백이다 — 그 매핑을 아는 곳은
# session_store 하나뿐이라 여기서 묶는다.
def _on_waiting_change(pty_session_id: str, waiting: bool) -> None:
    info = session_store.get(pty_session_id)
    tmux_name = info.tmux_name if info else None
    if not tmux_name:
        # tmux가 아닌 순수 PTY 세션 — 상태를 붙일 대상(카드/탭)이 없다.
        return
    # 훅이 만든 엔트리가 있으면 그쪽을, 없으면 pane 자기보고와 같은 키를 쓴다.
    targets = [sid for sid, e in agent_status.get_state().items()
               if e.get("tmux_session") == tmux_name]
    for sid in targets or [f"pane:{tmux_name}"]:
        agent_status.on_waiting(sid, waiting)
        if not targets:
            agent_status.get_state(sid)["tmux_session"] = tmux_name

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
        loop.create_task(asyncio.to_thread(push.send, "승인 대기", "세션에서 확인이 필요합니다", "/"))
    except RuntimeError:
        pass  # 이벤트 루프 밖(테스트 등) — 푸시는 부가 기능이라 조용히 건너뛴다
    except Exception:
        pass


_prompt_detector = agent_prompt_detect.get_global_detector(_on_waiting_change)

# Phase 8 G2: WS 연결 한도 카운터 (single-worker 전용 — TODOS.md D1)
ws_count_per_session: dict[str, int] = {}
ws_total_count = 0
