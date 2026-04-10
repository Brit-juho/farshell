"""PTY Manager — tmux 대체 핵심 모듈.

Python pty 모듈로 세션을 생성하고, asyncio 호환 read loop로 I/O를 스트리밍한다.
macOS PTY fd는 순수 asyncio selector 등록이 불안정하므로 thread 기반 read를 사용한다.

[C1] 동일 세션 다중 WebSocket 접속을 위해 broadcast 패턴 사용.
     session당 1개의 read loop → 여러 subscriber에게 데이터 전달.
"""

import asyncio
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ALLOWED_SHELLS = {"/bin/bash", "/bin/zsh", "/bin/sh", "/bin/fish"}


@dataclass
class PTYSession:
    session_id: str
    pid: int  # child process pid
    fd: int  # master pty fd
    cols: int = 80
    rows: int = 24
    # [C1] broadcast: 여러 subscriber에 데이터 전달
    _subscribers: set = field(default_factory=set, repr=False)  # set[Callable[[bytes], None]]
    _read_task: Optional[asyncio.Task] = field(default=None, repr=False)


class PTYManager:
    def __init__(self):
        self._sessions: dict[str, PTYSession] = {}

    @property
    def sessions(self) -> dict[str, PTYSession]:
        return dict(self._sessions)

    # tmux 등 허용 명령
    ALLOWED_COMMANDS = {"/opt/homebrew/bin/tmux", "/usr/bin/tmux", "/usr/local/bin/tmux"}

    def create_session(
        self,
        session_id: str,
        cmd: str = "",
        cmd_args: Optional[list[str]] = None,
        cols: int = 80,
        rows: int = 24,
        env: Optional[dict[str, str]] = None,
    ) -> PTYSession:
        if session_id in self._sessions:
            raise ValueError(f"Session {session_id!r} already exists")

        if not cmd:
            cmd = os.environ.get("SHELL", "/bin/zsh")
        if cmd not in ALLOWED_SHELLS and cmd not in self.ALLOWED_COMMANDS:
            cmd = "/bin/zsh"

        if cmd_args is None:
            cmd_args = [cmd]

        child_env = os.environ.copy()
        child_env["TERM"] = "xterm-256color"
        child_env["COLORTERM"] = "truecolor"
        if env:
            child_env.update(env)

        master_fd, slave_fd = pty.openpty()

        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        child_pid = os.fork()
        if child_pid == 0:
            # --- child process ---
            os.close(master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.execvpe(cmd, cmd_args, child_env)
        else:
            # --- parent process ---
            os.close(slave_fd)
            fd = master_fd

            session = PTYSession(
                session_id=session_id,
                pid=child_pid,
                fd=fd,
                cols=cols,
                rows=rows,
            )
            self._sessions[session_id] = session

            # [C1] 세션 생성 시 바로 read loop 시작
            session._read_task = asyncio.create_task(
                self._read_loop(session_id)
            )
            return session

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self._get(session_id)
        session.cols = cols
        session.rows = rows
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(session.fd, termios.TIOCSWINSZ, winsize)
            os.killpg(os.getpgid(session.pid), signal.SIGWINCH)
        except (OSError, ProcessLookupError):
            pass

    def write(self, session_id: str, data: bytes) -> None:
        session = self._get(session_id)
        try:
            os.write(session.fd, data)
        except OSError as e:
            logger.warning(f"Write failed for session {session_id}: {e}")

    # [C1] subscriber 관리 — 여러 WS가 같은 세션 출력을 받을 수 있음
    def subscribe(self, session_id: str, callback: Callable[[bytes], None]) -> None:
        session = self._get(session_id)
        session._subscribers.add(callback)

    def unsubscribe(self, session_id: str, callback: Callable[[bytes], None]) -> None:
        session = self._sessions.get(session_id)
        if session:
            session._subscribers.discard(callback)

    async def _read_loop(self, session_id: str) -> None:
        """[C1] session당 1개의 read loop. 모든 subscriber에 broadcast."""
        session = self._get(session_id)
        loop = asyncio.get_running_loop()

        def _blocking_read() -> Optional[bytes]:
            try:
                return os.read(session.fd, 4096)
            except OSError:
                return None

        while session_id in self._sessions:
            data = await loop.run_in_executor(None, _blocking_read)
            if data is None or len(data) == 0:
                break
            # broadcast to all subscribers
            dead = set()
            for cb in list(session._subscribers):
                try:
                    cb(data)
                except Exception:
                    dead.add(cb)
            session._subscribers -= dead

    def destroy_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return

        # [C3] read_task cancel
        if session._read_task and not session._read_task.done():
            session._read_task.cancel()

        # close fd — read loop이 OSError로 종료됨
        try:
            os.close(session.fd)
        except OSError:
            pass

        # [C2] 좀비 프로세스 방지
        pid = session.pid
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

        # 백그라운드 스레드에서 blocking waitpid (이벤트 루프 블로킹 방지)
        import threading
        def _reap():
            for _ in range(10):
                try:
                    rpid, _ = os.waitpid(pid, os.WNOHANG)
                    if rpid != 0:
                        return
                except ChildProcessError:
                    return
                time.sleep(0.1)
            # 마지막 시도
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass

        threading.Thread(target=_reap, daemon=True).start()

        logger.info(f"Session {session_id} destroyed (pid={session.pid})")

    def destroy_all(self) -> None:
        for sid in list(self._sessions):
            self.destroy_session(sid)

    def _get(self, session_id: str) -> PTYSession:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise ValueError(f"Session {session_id!r} not found")
