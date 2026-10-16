"""서버 둘 시험 도구 — 실제 FarShell 서버 프로세스를 둘 띄워 서로 호출하게 한다(프로토콜 v2 시험용).

`test_peer_relay.py` 는 한 앱이 자기 자신을 peer 로 등록해 네트워크 없이 끝까지 돈다. v2 는 **두 호스트의 저장소·tmux·프로세스가 다르다**는 사실이
요점이라(B 의 승인함, B 의 공유 세션, A 의 스트림) 호스트마다 별도 프로세스 + 별도 `VT_STATE_DIR` + 별도 tmux 소켓으로 띄운다. 사용자의 실제 `~/.vt`,
7777 포트, 실제 tmux(`-L fsh`)에는 닿지 않는다(HOME·상태 폴더·tmux 소켓·포트 전부 호스트 전용).

시험 프로세스 안에서 «그 호스트의 사람/CLI 가 하는 일»(승인, `fsh host pair` 등)은 `Host.ctx()` 로 환경을 그 호스트의 것으로 잠깐 바꾼 채 라이브러리를
직접 부른다 — 서버 라우트가 부르는 함수와 같은 함수다.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def have_tmux() -> bool:
    return shutil.which("tmux") is not None


class Host:
    """서버 프로세스 하나. `Host("a", base, enforce="peer,deleg")`."""

    def __init__(self, base: Path, name: str, enforce: str = "peer,deleg,pair"):
        self.name = name
        self.root = Path(base) / name
        self.home = self.root / "home"
        self.state = self.root / "state"
        for d in (self.root, self.home, self.state):
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)
        self.sock = f"fsh-v2-{name}-{os.getpid()}"
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.enforce = enforce
        self.proc: subprocess.Popen | None = None
        self.log = self.root / "server.log"

    # --- 환경 --------------------------------------------------------------------------------------------------

    def env_vars(self) -> dict:
        return {
            "HOME": str(self.home), "VT_STATE_DIR": str(self.state), "VT_TMUX_SOCKET": self.sock,
            "VT_AUTHZ_ENFORCE": self.enforce, "VT_AUTHZ_SOCKET": "0", "VT_WORKSPACE_PATH": str(self.root / "workspace.json"),
            "VT_NETWORK_MODE": "all", "VT_LANG": "ko", "VT_DELEGATE_SWEEP_SEC": "5",
            "VT_CONFIG": os.environ.get("VT_CONFIG", ""), "VT_PORT": str(self.port),
        }

    def _server_env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith("VT_")}
        env.update(self.env_vars())
        env["CLAUDE_CONFIG_DIR"] = str(self.home / ".claude")
        return env

    @contextlib.contextmanager
    def ctx(self, enforce: str | None = None):
        """이 호스트의 사람/CLI 로 일하는 동안 시험 프로세스의 환경을 이 호스트의 것으로 바꾼다."""
        keys = ("HOME", "VT_STATE_DIR", "VT_AUTHZ_ENFORCE", "VT_TMUX_SOCKET")
        old = {k: os.environ.get(k) for k in keys}
        vars_ = self.env_vars()
        for k in keys:
            os.environ[k] = vars_[k]
        if enforce is not None:
            os.environ["VT_AUTHZ_ENFORCE"] = enforce
        # 시험 프로세스의 tmux 모듈은 import 때 소켓 이름을 굳혀 둔다 — 이 호스트의 tmux 를 보게 잠깐 바꾼다.
        import tmux_runner
        import tmux_target
        saved = (tmux_runner.VT_TMUX_SOCKET, tmux_target.TMUX_SOCKET)
        tmux_runner.VT_TMUX_SOCKET = tmux_target.TMUX_SOCKET = self.sock
        try:
            yield self
        finally:
            tmux_runner.VT_TMUX_SOCKET, tmux_target.TMUX_SOCKET = saved
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # --- 프로세스 ------------------------------------------------------------------------------------------------

    def start(self, timeout: float = 40.0) -> "Host":
        logf = open(self.log, "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=str(SERVER_DIR), env=self._server_env(), stdout=logf, stderr=logf)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"서버 {self.name} 가 바로 끝났다:\n{self.log.read_text(errors='replace')[-2000:]}")
            try:
                urllib.request.urlopen(self.url + "/api/peer/v2/rotate", timeout=1)
            except urllib.error.HTTPError:
                return self                      # 라우트가 답한다(401/404 — 어느 쪽이든 살아 있다)
            except OSError:
                time.sleep(0.3)
        raise RuntimeError(f"서버 {self.name} 가 {timeout}초 안에 뜨지 않았다:\n{self.log.read_text(errors='replace')[-2000:]}")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        subprocess.run(["tmux", "-L", self.sock, "kill-server"], capture_output=True)

    # --- tmux (이 호스트 전용 소켓) -----------------------------------------------------------------------------------

    def tmux(self, *args: str) -> str:
        r = subprocess.run(["tmux", "-L", self.sock, *args], capture_output=True, text=True, timeout=10)
        return r.stdout

    def new_session(self, name: str, *, nick: str = "", mode: str = "readwrite") -> str:
        """세션을 만들고 key(`#{session_id}:#{session_created}`)를 돌려준다. `nick` 이 있으면 에이전트에게 공유한다."""
        self.tmux("new-session", "-d", "-s", name, "-x", "120", "-y", "30")
        if nick:
            self.tmux("set-option", "-t", f"={name}:", "@fsh_share_nick", nick)
            self.tmux("set-option", "-t", f"={name}:", "@fsh_share_mode", mode)
        return self.session_key(name)

    def session_key(self, name: str) -> str:
        out = self.tmux("display-message", "-p", "-t", f"={name}:", "#{session_id}\t#{session_created}").strip()
        sid, created = out.split("\t")
        return f"{sid}:{created}"

    def capture(self, name: str) -> str:
        return self.tmux("capture-pane", "-p", "-J", "-t", f"={name}:")

    def wait_for(self, name: str, text: str, timeout: float = 8.0) -> str:
        end = time.time() + timeout
        cur = ""
        while time.time() < end:
            cur = self.capture(name)
            if text in cur:
                return cur
            time.sleep(0.2)
        return cur


def pair(client: Host, server: Host) -> dict:
    """`client`(A)가 `server`(B)를 부를 수 있게 한다 — B 에서 티켓을 받아 A 에서 등록(`fsh host pair` + `fsh host add`).
    A.peers[B] 에 secret 이 생기고 B.grants[A] 에 같은 secret 이 생긴다. `{client_id, server_id}`."""
    import host_store
    import peer_client
    with server.ctx():
        ticket = host_store.issue_pair_ticket()
        sid = host_store.get_self()["id"]
    with client.ctx():
        peer_client.pair_sync(server.url, ticket)
        cid = host_store.get_self()["id"]
    return {"client_id": cid, "server_id": sid}


def wait_until(fn, timeout: float = 10.0, step: float = 0.2):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()
