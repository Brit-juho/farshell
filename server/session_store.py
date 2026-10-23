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
    catalog_session_id: str | None = None
    runtime_id: str | None = None
    catalog_host: str | None = None
    # attach 요청의 vt_session 쿠키로 알아낸 등록 기기 별명 — 「연결된 화면」이
    # 이 세션의 tty를 tmux client_name(대개 tty 경로 그대로)이 아니라 사람이
    # 알아볼 라벨로 보여주는 데 쓴다. 기기 등록이 없으면(비보호 서버 등) None.
    device_label: str | None = None
    # 한 브라우저 탭의 attach 식별자. None은 이전 버전/비 tmux 세션.
    screen_id: str | None = None


class SessionStore:
    """세션 메타 저장소. 한 tmux 세션에 여러 웹 화면이 붙을 수 있다."""

    def __init__(self):
        self._sessions: dict[str, SessionInfo] = {}
        self._by_tmux_name: dict[str, set[str]] = {}

    def add(self, session_id: str, name: str = "", cmd: str = "/bin/bash", device_label: str | None = None,
            screen_id: str | None = None) -> SessionInfo:
        info = SessionInfo(session_id=session_id, name=name or session_id, cmd=cmd,
                           device_label=device_label, screen_id=screen_id)
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
        if info.tmux_name:
            self._unindex(info.tmux_name, session_id)
        info.tmux_name = tmux_name
        if tmux_name:
            self._by_tmux_name.setdefault(tmux_name, set()).add(session_id)

    def _unindex(self, tmux_name: str, session_id: str) -> None:
        ids = self._by_tmux_name.get(tmux_name)
        if ids:
            ids.discard(session_id)
            if not ids:
                self._by_tmux_name.pop(tmux_name, None)

    def remove(self, session_id: str) -> None:
        info = self._sessions.pop(session_id, None)
        if info and info.tmux_name:
            self._unindex(info.tmux_name, session_id)

    def find_by_tmux_name(self, tmux_name: str) -> SessionInfo | None:
        """O(1) 역인덱스 조회."""
        return next(iter(self.find_all_by_tmux_name(tmux_name)), None)

    def find_all_by_tmux_name(self, tmux_name: str) -> list[SessionInfo]:
        return sorted((self._sessions[sid] for sid in self._by_tmux_name.get(tmux_name, ())
                       if sid in self._sessions), key=lambda info: (info.created_at, info.session_id))

    def find_by_tmux_and_screen(self, tmux_name: str, screen_id: str) -> SessionInfo | None:
        return next((info for info in self.find_all_by_tmux_name(tmux_name)
                     if info.screen_id == screen_id), None)

    def list_all(self) -> list[SessionInfo]:
        return list(self._sessions.values())
