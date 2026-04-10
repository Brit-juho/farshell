"""Session Store — 세션 메타데이터 관리.

PTYManager가 프로세스/fd를 관리하고, 이 모듈은 세션의 이름, 생성 시각 등
메타데이터를 관리한다. 서버 재시작 시 PTY 프로세스는 소멸되므로 메모리 저장.
"""

import time
from dataclasses import dataclass, field


@dataclass
class SessionInfo:
    session_id: str
    name: str = ""
    created_at: float = field(default_factory=time.time)
    cmd: str = "/bin/bash"


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

    def list_all(self) -> list[SessionInfo]:
        return list(self._sessions.values())
