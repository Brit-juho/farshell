"""에이전트 세션 공유 — 「이 호출은 정말 그 tmux 칸의 프로세스에서 왔는가」.

권한(grant)은 호출 에이전트의 tmux 세션에 묶인다. 그러니 호출자가 어느 세션인지를 **속일 수
없어야** 한다 — 속일 수 있으면 에이전트 B 가 "나는 A 칸이다"라고 말하고 A 가 받은 권한을 쓴다.
기계 토큰은 모든 에이전트가 같은 값을 갖고 있어서 이 구분을 못 한다. 그래서 MCP 가 보내는
세 값(`TMUX_PANE`, `TMUX`, 자기 `pid`)을 서버가 전부 대조한다(하나라도 안 맞으면 접수하지 않는다):

  1. `TMUX`(소켓 경로)가 우리 소켓이다 — 사용자의 개인 tmux 에서 온 `%12` 는 우리 `%12` 와 무관하다
     (`pane_resolve.is_our_socket`, A2 와 같은 이유).
  2. `TMUX_PANE` 이 우리 tmux 의 실제 pane 이다 → 그 pane 의 `pane_pid`(칸의 첫 프로세스)와
     소속 세션(`#{session_id}`+`#{session_created}`)을 **tmux 에게 직접** 얻는다.
  3. 호출 프로세스의 부모 체인(최대 `MAX_DEPTH` 단계)이 그 `pane_pid` 에 닿는다 — 즉 그 칸에서
     실제로 돌고 있는 프로세스다. 다른 칸의 프로세스가 `TMUX_PANE=%3` 을 위조해도 체인이 다르다.

**pid 는 자기 신고다.** 같은 맥의 직접 연결(루프백 + 프록시 헤더 없음)에서는 서버가 TCP 소켓의
소유 프로세스를 `lsof` 로 직접 찾아 신고 값 대신 쓴다(`socket_owner_pid`) — 신고를 속여도 소용없다.
이 조회가 불가능한 경로(터널·프록시를 거침, `lsof` 없음)는 신고된 pid 로 내려가고, 그때는 같은
맥의 다른 프로세스가 "pid 위조 + 같은 칸의 pane id"를 알아내야 하는 한계가 남는다(docs/ref/terminal.md
「Agent-shared sessions」 알려진 한계).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

import pane_resolve
import tmux_runner

MAX_DEPTH = 64
_PANE_RE = re.compile(r"%\d{1,9}")
_PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "cf-connecting-ip",
                  "x-forwarded-host", "true-client-ip")
_LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


class IdentityError(Exception):
    """호출자 식별 실패 — 403 `identity_unverified`."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Caller:
    sid: str          # tmux `#{session_id}` ("$3")
    created: int      # `#{session_created}`
    name: str         # 표시용(바뀔 수 있어 식별에는 쓰지 않는다)
    pane_id: str
    weak: bool = False  # pid 를 소켓 소유자로 확인하지 못하고 자기 신고를 썼다

    @property
    def key(self) -> str:
        return f"{self.sid}:{self.created}"


@dataclass(frozen=True)
class PaneRow:
    pane_id: str
    pane_pid: int
    sid: str
    created: int
    name: str
    active: bool          # 창 안에서도, 세션 안에서도 활성인 pane
    command: str
    nick: str
    mode: str


def list_pane_rows() -> Optional[list[PaneRow]]:
    """우리 tmux 의 모든 pane 을 **한 번의 호출**로. tmux 서버가 없으면 [], 호출이 실패(타임아웃 등)하면
    None — 호출부가 "세션이 없다"와 "모르겠다"를 구분해야 권한 정리를 함부로 안 한다."""
    fmt = ("#{pane_id}\t#{pane_pid}\t#{session_id}\t#{session_created}\t#{session_name}\t"
           "#{pane_active}#{window_active}\t#{pane_current_command}\t#{@fsh_share_nick}\t#{@fsh_share_mode}")
    rc, out, err = tmux_runner.run(["list-panes", "-a", "-F", fmt], timeout=3.0)
    if rc != 0:
        if rc == 1 and (b"no server" in err or b"no sessions" in err or b"error connecting" in err):
            return []
        return None
    rows: list[PaneRow] = []
    for line in out.decode("utf-8", errors="replace").split("\n"):
        parts = line.split("\t")
        if len(parts) < 7 or not parts[1].isdigit():
            continue
        try:
            created = int(parts[3])
        except ValueError:
            continue
        rows.append(PaneRow(
            pane_id=parts[0], pane_pid=int(parts[1]), sid=parts[2], created=created, name=parts[4],
            active=parts[5] == "11", command=parts[6],
            nick=parts[7] if len(parts) > 7 else "", mode=parts[8] if len(parts) > 8 else ""))
    return rows


# --- 프로세스 부모 체인 -----------------------------------------------------------------


def parent_of(pid: int) -> Optional[int]:
    """pid 의 부모. 모르면 None. psutil 이 있으면 쓰고 없으면 `ps`."""
    try:
        import psutil  # type: ignore

        try:
            return psutil.Process(pid).ppid()
        except psutil.Error:
            return None
    except ImportError:
        pass
    try:
        r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=2.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    txt = r.stdout.strip()
    return int(txt) if r.returncode == 0 and txt.isdigit() else None


def chain_reaches(pid: int, target_pid: int, *, parent=parent_of, max_depth: int = MAX_DEPTH) -> bool:
    """pid 에서 부모를 따라 올라가 `target_pid` 에 닿는가(pid 자신 포함). 깊이 제한 + 순환 방지."""
    seen: set[int] = set()
    cur: Optional[int] = pid
    for _ in range(max_depth):
        if cur is None or cur <= 1 or cur in seen:
            return False
        if cur == target_pid:
            return True
        seen.add(cur)
        cur = parent(cur)
    return False


def _lsof_path() -> Optional[str]:
    """launchd/systemd 로 뜬 서버의 PATH 에는 /usr/sbin 이 없는 일이 흔하다(macOS 의 lsof 는 거기 있다)."""
    for cand in ("/usr/sbin/lsof", "/usr/bin/lsof"):
        if os.access(cand, os.X_OK):
            return cand
    return shutil.which("lsof")


def socket_owner_pid(client: Optional[tuple], headers: dict, server_port: Optional[int]) -> Optional[int]:
    """같은 맥에서 루프백으로 직접 들어온 연결이면 그 TCP 연결의 **클라이언트 쪽 프로세스 pid**.
    프록시 헤더가 있거나(터널) 루프백이 아니거나 조회가 안 되면 None — 호출부가 신고 값을 쓴다."""
    lsof = _lsof_path()
    if not client or len(client) < 2 or not lsof:
        return None
    host, port = client[0], client[1]
    if host not in _LOOPBACK or not isinstance(port, int):
        return None
    if any(h in headers for h in _PROXY_HEADERS):
        return None
    try:
        r = subprocess.run([lsof, "-nP", f"-iTCP:{port}", "-sTCP:ESTABLISHED", "-Fpn"],
                           capture_output=True, text=True, timeout=3.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_lsof_owner(r.stdout, port, server_port)


def parse_lsof_owner(text: str, client_port: int, server_port: Optional[int]) -> Optional[int]:
    """`lsof -Fpn` 출력에서 **로컬 끝이 client_port 인** 연결을 가진 프로세스(= 클라이언트). 같은 연결의
    서버 쪽 끝은 로컬 포트가 서버 포트라 걸러진다."""
    pid: Optional[int] = None
    found: Optional[int] = None
    for line in text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            local, _, remote = line[1:].partition("->")
            if not remote:
                continue
            lport = local.rsplit(":", 1)[-1]
            rport = remote.rsplit(":", 1)[-1]
            if lport == str(client_port) and (server_port is None or rport == str(server_port)):
                if found is not None and found != pid:
                    return None  # 모호하면 쓰지 않는다
                found = pid
    return found


# --- 검증 -------------------------------------------------------------------------------


def verify_caller(pane: Optional[str], tmux_env: Optional[str], claimed_pid, *,
                  client: Optional[tuple] = None, headers: Optional[dict] = None,
                  server_port: Optional[int] = None, rows: Optional[list[PaneRow]] = None) -> Caller:
    """호출자 세션을 확정하거나 IdentityError. 모듈 머리말의 1~3."""
    if not pane:
        raise IdentityError("TMUX_PANE 이 없습니다 — tmux 밖에서는 호출한 칸을 식별할 수 없습니다")
    if not _PANE_RE.fullmatch(pane):
        raise IdentityError("TMUX_PANE 형식이 올바르지 않습니다")
    if not pane_resolve.is_our_socket(tmux_env):
        raise IdentityError("TMUX 가 FarShell 의 tmux 소켓이 아닙니다 — FarShell 에서 연 세션 안에서 실행하세요")
    if rows is None:
        rows = list_pane_rows()
    if rows is None:
        raise IdentityError("tmux 상태를 읽지 못했습니다 — 잠시 뒤 다시 시도하세요")
    row = next((r for r in rows if r.pane_id == pane), None)
    if row is None:
        raise IdentityError("FarShell 의 tmux 에 그런 pane 이 없습니다")

    weak = False
    owner = socket_owner_pid(client, headers or {}, server_port)
    if owner is not None:
        pid = owner
    else:
        weak = True
        try:
            pid = int(claimed_pid)
        except (TypeError, ValueError):
            raise IdentityError("호출 프로세스 pid 가 없습니다") from None
        if pid <= 1:
            raise IdentityError("호출 프로세스 pid 가 올바르지 않습니다")
    if not chain_reaches(pid, row.pane_pid):
        raise IdentityError("호출 프로세스가 그 tmux pane 안에서 실행된 것이 아닙니다(부모 체인이 pane 에 닿지 않음)")
    return Caller(sid=row.sid, created=row.created, name=row.name, pane_id=row.pane_id, weak=weak)
