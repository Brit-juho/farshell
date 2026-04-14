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
    def __init__(self):
        self._sessions: dict[str, SessionInfo] = {}

    def add(self, session_id: str, name: str = "", cmd: str = "/bin/bash") -> SessionInfo:
        info = SessionInfo(session_id=session_id, name=name or session_id, cmd=cmd)
        self._sessions[session_id] = info
        return info

    def get(self, session_id: str) -> SessionInfo | None:
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def find_by_tmux_name(self, tmux_name: str) -> SessionInfo | None:
        for info in self._sessions.values():
            if info.tmux_name == tmux_name:
                return info
        return None

    def list_all(self) -> list[SessionInfo]:
        return list(self._sessions.values())
