"""PTY Manager — tmux 대체 핵심 모듈.

Python pty 모듈로 세션을 생성하고, asyncio 호환 read loop로 I/O를 스트리밍한다.
읽기는 이벤트 루프의 add_reader(kqueue/macOS, epoll/Linux)로 등록한 콜백
(_on_readable) 기반이다 — 세션마다 스레드를 쓰지 않으므로 ThreadPoolExecutor
기본 상한(코어 수+4)에 걸려 세션이 많을 때(이 머신 기준 약 14개↑) read loop이
굶는 문제가 없다(S2). 실측(2026-09): 동시 PTY 25개 + 버스트 쓰기 5회씩 →
전량 정상 수신. add_reader 자체는 이 macOS/Python 조합에서 불안정하지 않았고,
유일한 실제 위험은 fd를 close()하기 전에 remove_reader()를 안 부르는 것 —
fd 번호가 재사용되면 이전 등록이 새 fd에 걸려 콜백이 영영 안 불렸다(실측
재현). 그래서 close 경로는 전부 _unregister_reader()를 close보다 먼저
호출하도록 통일했다(_on_readable의 EOF 분기, destroy_session).

[C1] 동일 세션 다중 WebSocket 접속을 위해 broadcast 패턴 사용.
     session당 콜백 하나 → 여러 subscriber에게 데이터 전달.
"""

import asyncio
import fcntl
import logging
import os
import pty
import re
import shutil
import signal
import struct
import termios
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ALLOWED_SHELLS = {"/bin/bash", "/bin/zsh", "/bin/sh", "/bin/fish", "/usr/bin/bash"}

# 클라이언트 터미널(xterm.js)이 PTY query에 자동 응답한 시퀀스를 영구 차단한다.
# 이 패턴들은 사용자가 vim 등에서 직접 입력할 일이 거의 없고, PTY stdin으로
# 흘러가면 zsh가 명령으로 인식해 `command not found: 2c0` 같은 노이즈를 낸다.
TERMINAL_AUTO_REPLY_RE = re.compile(
    rb"\x1b\[[\?>]?[\d;]*[cR]"
    rb"|\x1b\]\d{1,2};[^\x07\x1b]*(?:\x07|\x1b\\)"
)

# Phase 9 #6: PTY 출력의 query를 server가 직접 응답하고, 클라이언트로는 query를 안 보낸다.
# 이렇게 하면 ws 재연결 등 어떤 시점에도 client→server 자동응답 트래픽이 발생하지 않는다.
# 부팅 후 PTY_QUERY_INTERCEPT_SEC 동안만 활성 — vim/htop 등 TUI가 자체 query를 쓸 수 있어
# 영구 가로채기는 위험.
PTY_QUERY_INTERCEPT_SEC = 1.5
PTY_OUT_QUERY_REPLIES = (
    (b"\x1b[c",        b"\x1b[?6c"),                           # DA1
    (b"\x1b[0c",       b"\x1b[?6c"),
    (b"\x1b[>c",       b"\x1b[>0;1;0c"),                       # DA2
    (b"\x1b[>0c",      b"\x1b[>0;1;0c"),
    (b"\x1b]10;?\x07", b"\x1b]10;rgb:cdcd/d6d6/f4f4\x07"),     # OSC10 fg
    (b"\x1b]11;?\x07", b"\x1b]11;rgb:1e1e/1e1e/2e2e\x07"),     # OSC11 bg
)


@dataclass
class PTYSession:
    session_id: str
    pid: int  # child process pid
    fd: int  # master pty fd
    cols: int = 80
    rows: int = 24
    # [C1] broadcast: 여러 subscriber에 데이터 전달
    _subscribers: set = field(default_factory=set, repr=False)  # set[Callable[[bytes], None]]
    # S2: loop.add_reader로 등록된 상태인지 — pause/resume/destroy가 중복
    # add_reader/remove_reader를 호출하면 asyncio가 예외를 던지므로 가드용.
    _reader_registered: bool = field(default=False, repr=False)
    # add_reader/remove_reader를 건 이벤트 루프. get_running_loop()을 매번 쓰면
    # (테스트처럼) 루프가 안 도는 시점에 pause/resume/destroy를 호출할 때 깨진다 —
    # 생성 시점 루프를 붙잡아두고 그걸로만 등록/해제한다.
    _loop: Optional[asyncio.AbstractEventLoop] = field(default=None, repr=False)
    # scrollback 버퍼 — 재접속 시 이전 출력 복원.
    # maxlen(청크 수) 대신 바이트 예산으로 제한한다(_append_scrollback). 예전엔
    # maxlen=5000 × 4KB = 세션당 최대 19.5MB를 저장했지만 재접속 시 실제 전송은
    # 256KB뿐이라 78배 과다 저장 → 세션 몇 개만 열려도 수백 MB로 폭증했다.
    _scrollback: deque = field(default_factory=deque, repr=False)
    _scrollback_bytes: int = field(default=0, repr=False)  # 현재 저장 바이트 합
    # D3: 백프레셔(backpressure) 플래그 — True이면 add_reader가 등록 해제된 상태
    _paused: bool = field(default=False, repr=False)
    # P0 fix: PTY 시작 시각 — grace period 동안 클라이언트 자동응답(ESC 시퀀스) 차단용
    _start_monotonic: float = field(default=0.0, repr=False)
    # R3: 출력 배치 버퍼 — read마다 즉시 broadcast하지 않고 짧은 창(BATCH_WINDOW_SEC)
    # 동안 모았다가 한 번에 내보낸다. wetty의 tinybuffer(2ms, 512KB) 패턴.
    _out_buf: bytearray = field(default_factory=bytearray, repr=False)
    _flush_handle: Optional[asyncio.TimerHandle] = field(default=None, repr=False)
    # 입력(stdin) 쓰기 백프레셔 — fd가 non-blocking이라 큰 붙여넣기 등으로 커널
    # tty 입력 큐가 꽉 차면 os.write()가 짧게 쓰거나 EAGAIN을 낸다. 못 쓴 나머지를
    # 여기 쌓아두고 add_writer로 fd가 다시 쓰기 가능해질 때 이어서 흘려보낸다
    # (읽기 쪽 _out_buf/add_reader와 대칭되는 패턴).
    _in_buf: bytearray = field(default_factory=bytearray, repr=False)
    _writer_registered: bool = field(default=False, repr=False)


class PTYManager:
    def __init__(self):
        self._sessions: dict[str, PTYSession] = {}
        # SIGCHLD 핸들러는 의도적으로 등록하지 않는다 — `subprocess.run` 같은 stdlib이
        # 자체 wait를 수행하므로 process-wide reaper와 충돌해 ECHILD/returncode 깨짐을
        # 유발할 수 있다. 대신 `_on_readable`의 EOF 분기와 `destroy_session`에서 PTY 자식만
        # 명시적으로 회수한다 (TEST_REPORT.md Bug #3).

    @property
    def sessions(self) -> dict[str, PTYSession]:
        return dict(self._sessions)

    # tmux 등 허용 명령
    ALLOWED_COMMANDS = {p for p in [
        shutil.which("tmux"),
        "/opt/homebrew/bin/tmux", "/usr/bin/tmux", "/usr/local/bin/tmux",
    ] if p}

    def create_session(
        self,
        session_id: str,
        cmd: str = "",
        cmd_args: Optional[list[str]] = None,
        cols: int = 80,
        rows: int = 24,
        env: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> PTYSession:
        if session_id in self._sessions:
            raise ValueError(f"Session {session_id!r} already exists")

        if not cmd:
            from platform_utils import get_default_shell
            cmd = get_default_shell()

        # 새 세션 시작 디렉토리 — 지정 없으면 홈(기본). 서버 cwd(프로젝트 폴더)를
        # 물려받아 항상 프로젝트에서 열리던 문제를 막는다.
        from platform_utils import default_start_dir
        start_dir = cwd or default_start_dir()
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
            try:
                os.chdir(start_dir)
            except OSError:
                pass  # 시작 디렉토리 접근 불가 시 상속된 cwd 그대로
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
                _start_monotonic=time.monotonic(),
                _loop=asyncio.get_running_loop(),
            )
            self._sessions[session_id] = session

            # S2: ThreadPoolExecutor 기반 스레드 read 대신 이벤트 루프의
            # add_reader(kqueue/epoll)로 등록 — 세션마다 스레드 하나씩 쓰지 않으므로
            # 기본 실행자 상한(ThreadPoolExecutor max_workers)에 걸려 세션 14개
            # 이상에서 read loop이 굶는 문제가 없다. (실측: 동시 PTY 25개 + 버스트
            # 쓰기 5회씩 → 전량 정상 수신, 스레드 기반이 없어 상한 자체가 사라짐.)
            os.set_blocking(fd, False)
            self._register_reader(session)
            return session

    def _register_reader(self, session: "PTYSession") -> None:
        """이 세션의 fd를 이벤트 루프에 등록 — 이미 등록돼 있으면 아무것도 안 한다
        (pause/resume/destroy가 중복 호출해도 안전)."""
        if session._reader_registered or session._loop is None:
            return
        session._loop.add_reader(session.fd, self._on_readable, session.session_id)
        session._reader_registered = True

    def _unregister_reader(self, session: "PTYSession") -> None:
        """close() 전에 반드시 먼저 호출해야 한다 — fd 번호가 재사용될 때 이전
        add_reader 등록이 새 fd에 잘못 걸려 콜백이 영영 안 불리는 문제를 막는다
        (실측 확인: remove_reader 없이 close만 하면 재사용된 fd에서 데이터가 와도
        콜백이 발화하지 않았다)."""
        if not session._reader_registered or session._loop is None:
            return
        try:
            session._loop.remove_reader(session.fd)
        except (RuntimeError, ValueError):
            pass
        session._reader_registered = False

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

    # 안전 모드 — 라인 버퍼 (Enter 입력 시 검사)
    _line_buffer: dict[str, bytes] = {}

    # PTY 시작 직후 grace period — 이 동안 ESC로 시작하는 클라이언트 입력은 무시.
    # xterm.js가 PTY의 DA1/DA2/OSC10/11 query에 자동 응답한 시퀀스가 zsh stdin에
    # 들어가 명령으로 오인되는 누수를 차단한다 (TEST_REPORT.md Bug #1).
    PTY_BOOT_GRACE_SEC = 0.5

    def write(self, session_id: str, data: bytes) -> None:
        session = self._get(session_id)

        # P0 fix #1 — 영구 차단: 클라이언트가 보낸 DA/CPR/OSC 자동응답을 stdin에서 제거.
        # 이게 1순위 방어선이다. WS 재연결/탭 전환 등 grace 윈도우 밖에서도 보호된다.
        if data:
            filtered = TERMINAL_AUTO_REPLY_RE.sub(b"", data)
            if not filtered:
                return
            data = filtered

        # P0 fix #2 — 부팅 직후 추가 안전망: 다른 종류의 ESC 시퀀스도 0.5s 동안 무시.
        # 사용자 키 입력은 ESC로 시작하지 않는 일반 ASCII이므로 영향 없다.
        if (
            data.startswith(b"\x1b")
            and time.monotonic() - session._start_monotonic < self.PTY_BOOT_GRACE_SEC
        ):
            return

        # 안전 모드 검사 — Enter 입력 시 누적 라인 검사
        try:
            import safe_mode
            if safe_mode.is_enabled():
                buf = self._line_buffer.get(session_id, b"") + data
                if b"\r" in buf or b"\n" in buf:
                    # 첫 개행 이전까지가 검사 대상 라인
                    line_bytes = buf.split(b"\r", 1)[0].split(b"\n", 1)[0]
                    cmd = line_bytes.decode("utf-8", errors="ignore")
                    ok, reason = safe_mode.check(cmd)
                    if not ok:
                        # 차단 메시지를 PTY 출력으로 broadcast
                        block_msg = (
                            f"\r\n\x1b[31m[안전모드 차단] {reason}: {cmd}\x1b[0m\r\n"
                        ).encode()
                        for cb in list(session._subscribers):
                            try:
                                cb(block_msg)
                            except Exception:
                                pass
                        # 입력 무효화 — Ctrl+C로 라인 비움
                        try:
                            os.write(session.fd, b"\x03")
                        except OSError:
                            pass
                        self._line_buffer[session_id] = b""
                        return
                    self._line_buffer[session_id] = b""
                else:
                    self._line_buffer[session_id] = buf
        except ImportError:
            pass

        self._write_raw(session, data)

    def _write_raw(self, session: "PTYSession", data: bytes) -> None:
        """os.write()는 non-blocking fd에서 요청한 바이트를 전부 쓴다고 보장하지
        않는다 — 짧게 쓰거나(반환값 < len(data)) 큐가 꽉 차면 EAGAIN을 낸다.
        예전엔 반환값/예외를 확인하지 않고 한 번만 호출해서, 큰 붙여넣기의 뒷부분이
        조용히 유실됐다(잘려서 들어간 것처럼 보임). 순서를 지키기 위해 이미 밀린
        버퍼가 있으면 새 데이터도 무조건 뒤에 이어붙이고, 비어있을 때만 즉시
        os.write를 시도한다."""
        if not data:
            return
        if session._in_buf:
            session._in_buf += data
            self._register_writer(session)
            return
        try:
            n = os.write(session.fd, data)
        except BlockingIOError:
            n = 0
        except OSError as e:
            logger.warning(f"Write failed for session {session.session_id}: {e}")
            return
        if n < len(data):
            session._in_buf += data[n:]
            self._register_writer(session)

    def _register_writer(self, session: "PTYSession") -> None:
        if session._writer_registered or session._loop is None:
            return
        session._loop.add_writer(session.fd, self._on_writable, session.session_id)
        session._writer_registered = True

    def _unregister_writer(self, session: "PTYSession") -> None:
        if not session._writer_registered or session._loop is None:
            return
        try:
            session._loop.remove_writer(session.fd)
        except (RuntimeError, ValueError):
            pass
        session._writer_registered = False

    def _on_writable(self, session_id: str) -> None:
        """add_writer가 fd를 다시 쓸 수 있게 됐을 때 호출 — 밀린 _in_buf를 마저
        흘려보낸다."""
        session = self._sessions.get(session_id)
        if session is None:
            return  # destroy_session과의 경쟁 — 이미 정리됨(remove_writer도 함께 정리됨)
        buf = session._in_buf
        if not buf:
            self._unregister_writer(session)
            return
        try:
            n = os.write(session.fd, bytes(buf))
        except BlockingIOError:
            return  # 아직 안 됨 — 다음 writable 이벤트를 기다린다
        except OSError as e:
            logger.warning(f"Write failed for session {session_id}: {e}")
            buf.clear()
            self._unregister_writer(session)
            return
        del buf[:n]
        if not buf:
            self._unregister_writer(session)

    def pause_read(self, session_id: str, requester_id: str) -> None:
        """D3+Codex: 백프레셔 — 구독자별 pause 카운트.

        다중 WS가 같은 세션을 구독할 때, 한 WS가 느려도 다른 WS는
        계속 받을 수 있어야 한다. pause 요청자를 set으로 추적하여
        모든 요청자가 resume해야만 read loop을 재개한다.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        if not hasattr(session, "_pause_requesters"):
            session._pause_requesters = set()
        session._pause_requesters.add(requester_id)
        session._paused = True
        # S2: add_reader 등록을 통째로 해제 — 콜백이 아예 안 불리므로 os.read를
        # 안 해서 커널 PTY 버퍼가 차고, 그러면 쓰는 쪽(셸/tmux)이 write()에서
        # 자연히 block된다. sleep 폴링보다 진짜 OS 레벨 백프레셔에 가깝다.
        self._unregister_reader(session)

    def resume_read(self, session_id: str, requester_id: str) -> None:
        """D3+Codex: 백프레셔 — 해당 requester의 pause 해제.

        모든 구독자가 resume해야만 실제로 read loop 재개.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        requesters = getattr(session, "_pause_requesters", set())
        requesters.discard(requester_id)
        if not requesters:
            session._paused = False
            self._register_reader(session)

    # [C1] subscriber 관리 — 여러 WS가 같은 세션 출력을 받을 수 있음
    def subscribe(self, session_id: str, callback: Callable[[bytes], None]) -> None:
        session = self._get(session_id)
        session._subscribers.add(callback)

    def unsubscribe(self, session_id: str, callback: Callable[[bytes], None]) -> None:
        session = self._sessions.get(session_id)
        if session:
            session._subscribers.discard(callback)

    def _on_readable(self, session_id: str) -> None:
        """S2: add_reader가 fd에 데이터가 준비될 때마다 호출하는 콜백.
        이전엔 세션마다 스레드 하나(run_in_executor)로 select+os.read를 반복해
        기본 ThreadPoolExecutor 상한(코어 수+4, 이 머신 기준 약 14)을 넘는 세션이
        생기면 나머지 세션의 read loop이 스레드를 못 배정받아 굶었다. add_reader는
        이벤트 루프(kqueue/epoll) 하나가 모든 fd를 감시하므로 세션 수에 상관없이
        전부 처리된다 — 스레드를 전혀 쓰지 않기 때문에 애초에 그 상한이 없다."""
        session = self._sessions.get(session_id)
        if session is None:
            return  # destroy_session과의 경쟁 — 이미 정리됨

        try:
            data = os.read(session.fd, 4096)
        except BlockingIOError:
            return  # 가짜 readable 알림(드묾) — 다음 콜백을 기다린다
        except OSError:
            data = b""  # EIO 등 — 자식 종료로 인한 EOF와 동일하게 취급

        # Phase 9 #6: PTY 출력의 query를 가로채 server가 직접 응답.
        # client로는 query를 안 보내므로 client→server 자동응답 트래픽 0.
        if data and time.monotonic() - session._start_monotonic < PTY_QUERY_INTERCEPT_SEC:
            for q, resp in PTY_OUT_QUERY_REPLIES:
                if q in data:
                    try:
                        os.write(session.fd, resp)
                    except OSError:
                        pass
                    data = data.replace(q, b"")
            if not data:
                return

        if not data:
            # EOF — tmux detach 등으로 프로세스 종료. 순서를 지킨다: 남은 배치 버퍼를
            # 먼저 내보내야 [process exited] 메시지가 그 앞의 출력보다 먼저 도착하는
            # 일이 없다.
            self._flush_session(session_id)
            # 좀비 방지를 위해 명시적 회수.
            try:
                os.waitpid(session.pid, os.WNOHANG)
            except ChildProcessError:
                pass
            eof_msg = b"\r\n[process exited]\r\n"
            for cb in list(session._subscribers):
                try:
                    cb(eof_msg)
                except Exception:
                    pass
            # 세션 목록에서 정리 — 안 지우면 죽은 세션이 self._sessions에 남아
            # /api/sessions가 죽은 세션까지 반환하고(=클라이언트가 죽은 터미널을
            # 생성) scrollback 버퍼가 메모리를 붙잡는다. destroy_session()으로
            # 먼저 정리된 경우엔 이미 pop돼 있어 아래 조건이 False → 무해하게 통과.
            if self._sessions.get(session_id) is session:
                self._unregister_reader(session)
                self._sessions.pop(session_id, None)
                self._line_buffer.pop(session_id, None)
                try:
                    os.close(session.fd)
                except OSError:
                    pass
            return

        # R3: 매 read마다 즉시 scrollback+broadcast 하지 않고, 짧은 창(BATCH_WINDOW_SEC)
        # 동안 모아서 한 번에 내보낸다 — 빠른 출력(build 로그 등)에서 WS 프레임 수·
        # xterm.js write() 호출 수를 줄인다(wetty의 tinybuffer 패턴). BATCH_MAX_BYTES를
        # 넘으면 창을 기다리지 않고 즉시 flush — 지연시간에 상한을 둔다.
        session._out_buf.extend(data)
        if len(session._out_buf) >= self.BATCH_MAX_BYTES:
            self._flush_session(session_id)
            return
        if session._flush_handle is None and session._loop is not None:
            session._flush_handle = session._loop.call_later(
                self.BATCH_WINDOW_SEC, self._flush_session, session_id
            )

    # wetty tinybuffer 참고 — 2ms/512KB. 사람이 타이핑한 에코는 2ms 정도로는 체감
    # 지연이 없고(사람 반응속도 훨씬 느림), 빌드 로그 같은 폭주 출력에서만 실제로
    # 여러 read를 하나의 WS 프레임으로 묶는 효과가 난다.
    BATCH_WINDOW_SEC = 0.002
    BATCH_MAX_BYTES = 512 * 1024

    def _flush_session(self, session_id: str) -> None:
        """배치 버퍼를 scrollback에 반영하고 구독자에게 한 번에 broadcast."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session._flush_handle is not None:
            session._flush_handle.cancel()
            session._flush_handle = None
        if not session._out_buf:
            return
        data = bytes(session._out_buf)
        session._out_buf.clear()

        # scrollback에 저장 (바이트 예산으로 트리밍)
        self._append_scrollback(session, data)
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

        # R3: 예약된 배치 flush 타이머 취소 — 안 지우면 이미 pop된 session_id로
        # _flush_session이 뒤늦게 불려 no-op하긴 하지만, 굳이 늦게 도는 타이머를
        # 남겨둘 이유가 없다.
        if session._flush_handle is not None:
            session._flush_handle.cancel()
            session._flush_handle = None

        # 세션별 보조 상태 정리 — 안 지우면 세션이 생성/삭제될 때마다 dict가 무한히 커진다.
        self._line_buffer.pop(session_id, None)

        # [C3] S2: remove_reader를 close보다 반드시 먼저 호출한다 — 순서가
        # 바뀌면 곧바로 생성될 다음 세션이 이 fd 번호를 재사용했을 때 이전
        # add_reader 등록이 새 fd에 걸려 콜백이 영영 발화하지 않는다(실측 확인).
        self._unregister_reader(session)
        self._unregister_writer(session)
        try:
            os.close(session.fd)
        except OSError:
            pass

        # [C2] 좀비 프로세스 방지
        pid = session.pid
        try:
            pgid = os.getpgid(pid)
        except (OSError, ProcessLookupError):
            pgid = None

        # 치명적 안전장치: create_session의 자식이 os.setsid()로 자기 프로세스
        # 그룹을 분리하기 전에 destroy가 호출되면(빠른 생성→종료 레이스), 자식은
        # 아직 부모(서버) 프로세스 그룹에 속해 있다. 이때 killpg를 하면 서버 자신의
        # 프로세스 그룹 전체에 SIGKILL을 보내 서버가 죽거나 hang한다.
        # → 자식이 자기 그룹의 리더(pgid == pid)임이 확인될 때만 그룹 kill을 하고,
        #   그 외에는 자식 프로세스만 개별 kill한다 (부모 그룹은 절대 건드리지 않음).
        own_pgid = os.getpgrp()
        group_kill_safe = pgid is not None and pgid == pid and pgid != own_pgid
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                if group_kill_safe:
                    os.killpg(pgid, sig)
                else:
                    os.kill(pid, sig)
            except (OSError, ProcessLookupError):
                pass

        # 백그라운드 스레드에서 blocking waitpid (이벤트 루프 블로킹 방지).
        # SIGCHLD 핸들러가 먼저 회수할 수도 있으므로 ChildProcessError는 정상 종료로 본다.
        import threading
        def _reap():
            try:
                os.waitpid(pid, 0)  # blocking — 자식이 죽을 때까지 대기
            except ChildProcessError:
                pass  # SIGCHLD 핸들러가 이미 회수함

        threading.Thread(target=_reap, daemon=True).start()

        logger.info(f"Session {session_id} destroyed (pid={session.pid})")

    def destroy_all(self) -> None:
        for sid in list(self._sessions):
            self.destroy_session(sid)

    # Phase 9 #7: scrollback을 마지막 N 바이트로 제한 → 모바일 재접속 트래픽 ↓.
    SCROLLBACK_MAX_BYTES = 256 * 1024
    # 실제로 저장하는 상한. 재접속 시 SCROLLBACK_MAX_BYTES만 보내므로 그 이상 저장은
    # 낭비다. 여유로 2배(512KB)까지만 보관 → 세션당 메모리 19.5MB → 최대 512KB.
    SCROLLBACK_STORE_BYTES = 512 * 1024
    # 작은 청크가 폭주해도 deque 항목 수(파이썬 객체 오버헤드)를 제한하는 안전 상한.
    SCROLLBACK_MAX_CHUNKS = 5000

    def _append_scrollback(self, session: PTYSession, data: bytes) -> None:
        """scrollback에 청크 추가 후 바이트/청크 예산 초과분을 앞(오래된)부터 버린다."""
        sb = session._scrollback
        sb.append(data)
        session._scrollback_bytes += len(data)
        while session._scrollback_bytes > self.SCROLLBACK_STORE_BYTES and len(sb) > 1:
            session._scrollback_bytes -= len(sb.popleft())
        while len(sb) > self.SCROLLBACK_MAX_CHUNKS:
            session._scrollback_bytes -= len(sb.popleft())

    def get_scrollback(self, session_id: str) -> list[bytes]:
        """재접속 시 이전 출력을 전송하기 위한 scrollback 데이터 반환.

        용량 제한: 합쳐서 SCROLLBACK_MAX_BYTES (256KB)를 넘으면 뒤(최근)부터 잘라
        반환한다. 단일 `bytes`가 아닌 list[bytes] 시그니처는 호환을 위해 유지.
        """
        session = self._get(session_id)
        chunks = list(session._scrollback)
        total = sum(len(c) for c in chunks)
        if total <= self.SCROLLBACK_MAX_BYTES:
            return chunks
        # 뒤에서부터 누적해 SCROLLBACK_MAX_BYTES 안에 들어오는 만큼만
        out: list[bytes] = []
        size = 0
        for c in reversed(chunks):
            if size + len(c) > self.SCROLLBACK_MAX_BYTES:
                # 마지막 chunk를 잘라 정확히 맞춘다
                remain = self.SCROLLBACK_MAX_BYTES - size
                if remain > 0:
                    out.append(c[-remain:])
                break
            out.append(c)
            size += len(c)
        return list(reversed(out))

    def _get(self, session_id: str) -> PTYSession:
        try:
            return self._sessions[session_id]
        except KeyError:
            raise ValueError(f"Session {session_id!r} not found")
