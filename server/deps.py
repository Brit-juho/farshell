"""공유 인스턴스 (Dependency Injection 대체).

모든 라우터가 여기서 import하여 같은 인스턴스를 사용.
circular import 없이 전역 상태를 한 곳에서 관리.
"""

from __future__ import annotations

from fastapi import WebSocket
from pty_manager import PTYManager
from session_store import SessionStore
from output_watcher import OutputWatcher
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

# Phase 8 G2: WS 연결 한도 카운터 (single-worker 전용 — TODOS.md D1)
ws_count_per_session: dict[str, int] = {}
ws_total_count = 0
