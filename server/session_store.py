"""Session Store — 세션 메타데이터 관리.

PTYManager가 프로세스/fd를 관리하고, 이 모듈은 세션의 이름, 생성 시각 등
메타데이터를 관리한다. 서버 재시작 시 PTY 프로세스는 소멸되므로 메모리 저장.
"""

import secrets
import time
from dataclasses import dataclass, field


def new_session_id() -> str:
    """추측 불가능한 세션 ID 생성.

    secrets.token_urlsafe(12) → 16자 URL-safe 문자열 (~96비트 엔트로피).
    기존 uuid4()[:8] (~32비트)보다 훨씬 안전. URL hash/WebSocket path에 그대로 사용.
    """
    return secrets.token_urlsafe(12)


@dataclass
class SessionInfo:
    session_id: str
    name: str = ""
    created_at: float = field(default_factory=time.time)
    cmd: str = "/bin/bash"
    tmux_name: str | None = None


class SessionStore:
    """세션 메타 저장소. tmux_name 역인덱스로 O(1) 조회 (Phase 8 G6)."""

    def __init__(self):
        self._sessions: dict[str, SessionInfo] = {}
        # 역인덱스: tmux_name → session_id (find_by_tmux_name O(N) → O(1))
        self._by_tmux_name: dict[str, str] = {}

    def add(self, session_id: str, name: str = "", cmd: str = "/bin/bash") -> SessionInfo:
        info = SessionInfo(session_id=session_id, name=name or session_id, cmd=cmd)
        self._sessions[session_id] = info
        return info

    def get(self, session_id: str) -> SessionInfo | None:
        return self._sessions.get(session_id)

    def update_tmux_name(self, session_id: str, tmux_name: str | None) -> None:
        """tmux_name 갱신 + 역인덱스 동기화."""
        info = self._sessions.get(session_id)
        if info is None:
            return
        # 옛 인덱스 제거
        if info.tmux_name and self._by_tmux_name.get(info.tmux_name) == session_id:
            self._by_tmux_name.pop(info.tmux_name, None)
        info.tmux_name = tmux_name
        if tmux_name:
            self._by_tmux_name[tmux_name] = session_id

    def remove(self, session_id: str) -> None:
        info = self._sessions.pop(session_id, None)
        if info and info.tmux_name and self._by_tmux_name.get(info.tmux_name) == session_id:
            self._by_tmux_name.pop(info.tmux_name, None)

    def find_by_tmux_name(self, tmux_name: str) -> SessionInfo | None:
        """O(1) 역인덱스 조회."""
        sid = self._by_tmux_name.get(tmux_name)
        if sid is None:
            return None
        return self._sessions.get(sid)

    def list_all(self) -> list[SessionInfo]:
        return list(self._sessions.values())
