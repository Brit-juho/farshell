"""unix socket 경로 — 같은 event loop 의 두 번째 uvicorn 서버.

`<state>/run/fsh.sock`(디렉터리 0700, 소켓 0600)에 붙은 프로세스의 **커널이 알려 주는** uid/pid 로 호출자를
정한다. 토큰도, 헤더의 자기 주장도 신원이 아니다. TCP 서버와 앱·미들웨어를 공유하지만 이 경로로 들어온
요청은 아래 `Gate` 를 먼저 지난다.

## 흐름 (요청 하나)
  PeerH11.connection_made   커널에서 peer uid/pid 를 읽어 연결에 묶는다. uid 가 서버 uid 가 아니면 즉시 닫는다.
  Gate                      HTTP 만(WS 거부) → 자가 시험 통과 전엔 시험 경로 외 전부 503 → 모호성(400)
                            → 경로 허용표(403) → 주체 해석 → `scope["fsh_sock"]=True`, `state["fsh_principal"]`
  main.TokenAuthMiddleware  `scope["fsh_sock"]` 면 토큰 검사를 건너뛴다(그 키는 이 게이트만 세울 수 있다 —
                            uvicorn 이 만드는 scope 에 클라이언트가 키를 넣을 방법이 없다).

## 주체 해석 (principals.from_socket)
peer pid 의 부모 체인이 **우리 tmux** 의 어느 pane 의 `pane_pid` 에 닿으면 agent(`sid:created`), 훅이면 P5.
아니면 선언된 `X-Fsh-Svc` 이름이면 svc, 아니면 cli. pane 안의 프로세스가 svc 를 자처해도 agent 로 남는다.
peer 가 이미 죽었거나(pid 재사용 위험) tmux 를 읽지 못하면 **분류하지 않고 거부**한다(fail closed).

## 이 단계의 인가
기존 라우트는 소켓 요청을 "기계 토큰과 동등"으로 받는다 — 단 `ROUTES` 허용표에 있는 (메서드, 경로)만. 나머지는
403 `socket_route_not_allowed`. 사람 전용 라우트(`require_human_session`)는 토큰이 그렇듯 소켓으로도 못 연다.
허용표는 저장소 안 클라이언트가 실제로 부르는 것만이고, 새 클라이언트를 만들면 여기에 한 줄 더한다.

## 플랫폼
macOS: `getsockopt(SOL_LOCAL=0, LOCAL_PEERCRED=1, 76)` → xucred(uid), `LOCAL_PEERPID=2` → int. python `socket`
에 상수가 없다. Linux: `SO_PEERCRED` → struct ucred(pid, uid, gid). pid 는 **connect() 를 부른 프로세스**다
(fork 한 자식이 fd 를 상속해도 바뀌지 않는다). 시작할 때 자기 자신에게 붙어 `peer pid == getpid()` 를 확인하고,
틀리면 소켓을 열어 두지 않는다(fail closed) — uvicorn 업그레이드로 신원이 조용히 약해지는 것을 막는 장치다.

## 건너뛰는 경우 (경고만 남기고 서버는 계속)
`VT_AUTHZ_SOCKET=0`, WSL 의 `/mnt/` 아래(권한이 안 맞는다), 경로가 AF_UNIX 한도(100바이트)를 넘음,
이미 살아 있는 다른 리스너가 있음(그 소켓을 지우지 않는다), 소켓이 아닌 파일이 그 자리에 있음.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import re
import socket
import stat
import struct
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import fsh_client
import vt_paths

from . import principals as P

logger = logging.getLogger(__name__)

SOL_LOCAL = 0
LOCAL_PEERCRED = 1
LOCAL_PEERPID = 2
_XUCRED_LEN = 76          # u_int version, uid_t uid, short ngroups(+pad), gid_t groups[16]
MAX_PATH_BYTES = 100      # macOS sun_path 104, Linux 108 — 여유를 둔다
SELFTEST_PATH = "/api/authz/sock/selftest"
WHOAMI_PATH = "/api/authz/whoami"

# --- 허용표 ----------------------------------------------------------------------------------
# (메서드, 경로 정규식 fullmatch, 부르는 곳). 저장소 안 클라이언트가 소켓으로 부르는 것만이다.
ROUTES: tuple[tuple[str, str, str], ...] = (
    ("POST", r"/api/agent/event", "agent_hook.sh (svc:hook / 에이전트 pane 안이면 agent via=hook)"),
    ("POST", r"/api/agent/report", "fsh pane report"),
    ("POST", r"/api/clipboard/push", "clipboard_daemon (svc:clipboard)"),
    ("POST", r"/voice/(cancel|input|target|bind|stt/preload)", "voice daemon · voice_bind (svc:voice)"),
    ("GET", r"/api/capabilities", "fsh manage (svc:tui)"),
    ("GET", r"/api/tmux/sessions", "fsh manage (svc:tui)"),
    ("DELETE", r"/api/tmux/kill/[^/]+", "fsh manage (svc:tui)"),
    ("PATCH", r"/api/sessions/[^/]+", "fsh manage (svc:tui)"),
    ("POST", r"/api/notify/client-event", "tmux_client_notify.sh (svc:tmux-notify)"),
    ("POST", r"/api/agent/message", "mcp_farshell send_message"),
    ("GET", r"/api/agent/(messages|directory|shared)", "mcp_farshell"),
    ("POST", r"/api/agent/shared/(read|send)", "mcp_farshell"),
    ("GET", r"/api/delegations(/role)?", "mcp_farshell"),
    ("POST", r"/api/delegations(/report|/[^/]+/(send|close))?", "mcp_farshell"),
    ("POST", r"/api/delegations/[^/]+/close-by-cli", "fsh delegate close (cli / pane agent)"),
    ("POST", r"/api/queue/run", "mcp_farshell"),
    ("POST", r"/api/ports/_proxy/sync", "bin/fsh (tunnel expose/pin/mode)"),
    ("POST", r"/api/host-grants/[^/]+/change", "fsh host allow-control|deny-control|allow-agent|deny-agent (cli / pane agent)"),
)
_ROUTES_RE = tuple((m, re.compile(p), who) for m, p, who in ROUTES)


def route_allowed(method: str, path: str) -> bool:
    m = method.upper()
    return any(m == rm and rx.fullmatch(path) for rm, rx, _ in _ROUTES_RE)


# --- peer 자격 ---------------------------------------------------------------------------------


class PeerCredError(Exception):
    pass


def peer_creds(sock) -> tuple[int, int]:
    """연결된 AF_UNIX 소켓의 상대 (uid, pid). 읽지 못하면 PeerCredError — 호출부는 거부한다."""
    try:
        if sys.platform == "darwin":
            raw = sock.getsockopt(SOL_LOCAL, LOCAL_PEERCRED, _XUCRED_LEN)
            version, uid = struct.unpack_from("=II", raw, 0)
            if version != 0:
                raise PeerCredError(f"unexpected xucred version {version}")
            pid = struct.unpack("=i", sock.getsockopt(SOL_LOCAL, LOCAL_PEERPID, 4))[0]
        elif sys.platform.startswith("linux"):
            raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            pid, uid, _gid = struct.unpack("3i", raw)
        else:
            raise PeerCredError(f"unsupported platform {sys.platform}")
    except (OSError, struct.error) as e:
        raise PeerCredError(str(e)) from e
    if pid <= 0:
        raise PeerCredError("peer pid unavailable")
    return uid, pid


# --- uvicorn 연결 프로토콜 ---------------------------------------------------------------------


def _make_protocol():
    from uvicorn.protocols.http.h11_impl import H11Protocol

    class PeerH11(H11Protocol):
        """연결이 생기자마자 커널에서 peer 자격을 읽어 그 연결의 모든 요청 scope 에 `fsh_peer` 로 싣는다."""

        def connection_made(self, transport):  # type: ignore[override]
            super().connection_made(transport)
            peer: dict = {"uid": None, "pid": None}
            try:
                sock = transport.get_extra_info("socket")
                uid, pid = peer_creds(sock)
                peer = {"uid": uid, "pid": pid}
            except PeerCredError as e:
                peer["error"] = str(e)
            if peer["uid"] is not None and peer["uid"] != os.geteuid():
                # 소켓이 0600 이라 root 말고는 여기까지 올 수 없다. 본문을 파싱하지 않고 닫는다.
                logger.warning("[authz-sock] 다른 uid(%s)의 연결을 닫았습니다", peer["uid"])
                transport.close()
                return
            inner = self.app

            async def with_peer(scope, receive, send, _inner=inner, _peer=peer):
                scope["fsh_peer"] = dict(_peer)
                await _inner(scope, receive, send)

            self.app = with_peer

    return PeerH11


# --- 게이트 -------------------------------------------------------------------------------------


class Reject(Exception):
    def __init__(self, status: int, error: str, reason: str = ""):
        super().__init__(error)
        self.status, self.error, self.reason = status, error, reason


async def _respond(send, status: int, body: dict) -> None:
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]})
    await send({"type": "http.response.body", "body": raw})


def _headers(scope) -> dict:
    out: dict = {}
    for k, v in scope.get("headers", []):
        out[k.decode("latin-1").lower()] = v.decode("latin-1")
    return out


def ambiguous_credential(scope, headers: dict) -> Optional[str]:
    """소켓 요청은 자격을 싣지 않는다. 하나라도 있으면 주체가 둘이라는 뜻이다(어느 쪽도 고르지 않는다)."""
    if headers.get("authorization"):
        return "authorization"
    if "vt_session" in headers.get("cookie", ""):
        return "cookie"
    if any(k.startswith("x-peer-") for k in headers):
        return "peer_hmac"
    q = scope.get("query_string", b"").decode("latin-1")
    if re.search(r"(^|&)token=", q):
        return "query_token"
    return None


class Resolver:
    """peer → 주체. pane 목록(tmux 호출 1회)과 프로세스 표(ps 1회)를 짧게 캐시한다."""

    TTL = 2.0

    def __init__(self, rows_fn=None, ps_fn=None):
        self._rows_fn = rows_fn
        self._ps_fn = ps_fn
        self._cache: dict = {}

    def _rows(self):
        if self._rows_fn:
            return self._rows_fn()
        import share_identity
        return share_identity.list_pane_rows()

    def _parent_fn(self):
        import hook_identity
        import share_identity
        table = (self._ps_fn or hook_identity._ps_table)()
        if table is None:
            return share_identity.parent_of
        return lambda pid: (table[pid][0] if pid in table else None)

    def resolve_sync(self, peer: dict, svc_claim: str) -> P.Principal:
        import share_identity
        uid, pid = peer.get("uid"), peer.get("pid")
        if not isinstance(uid, int) or not isinstance(pid, int) or pid <= 1:
            raise Reject(403, "peer_unidentified", "peer 자격을 읽지 못했습니다")
        key = (pid, svc_claim)
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit and now - hit[0] < self.TTL:
            return hit[1]
        if not _alive(pid):
            raise Reject(403, "peer_gone", "요청한 프로세스가 이미 없습니다")
        rows = self._rows()
        if rows is None:
            raise Reject(503, "identity_unavailable", "tmux 상태를 읽지 못했습니다")
        parent = self._parent_fn()
        if parent(pid) is None:       # 표에 없다 = 방금 죽었거나 읽지 못했다 — 체인을 만들 수 없으니 분류하지 않는다
            raise Reject(403, "peer_gone", "프로세스 부모 체인을 읽지 못했습니다")
        chain = share_identity.ancestors(pid, parent=parent)
        cs = set(chain)
        row = next((r for r in rows if r.pane_pid in cs), None)
        principal = P.from_socket({"uid": uid, "pid": pid}, row=row, svc_claim=svc_claim)
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[key] = (now, principal)
        return principal


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def principal_view(p: P.Principal) -> dict:
    a = p.attrs
    out = {"kind": p.kind, "id": p.id, "uid": a.get("uid"), "pid": a.get("pid"),
           "via": "hook" if a.get("via") == "hook" else "socket"}
    for k in ("name", "svc", "pane", "key"):
        if a.get(k):
            out[k] = a[k]
    return out


class Gate:
    """소켓 서버가 앱 앞에 두는 ASGI 래퍼. 모듈 머리말의 「흐름」."""

    def __init__(self, app, resolver: Optional[Resolver] = None):
        self.app = app
        self.resolver = resolver or Resolver()
        self.armed = False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        try:
            await self._handle(scope, receive, send)
        except Reject as r:
            await _respond(send, r.status, {"error": r.error, "reason": r.reason})

    async def _handle(self, scope, receive, send):
        peer = scope.get("fsh_peer")
        if not isinstance(peer, dict) or peer.get("uid") is None:
            raise Reject(403, "peer_unidentified", "peer 자격을 읽지 못했습니다")
        if peer["uid"] != os.geteuid():
            raise Reject(403, "peer_uid_mismatch", "다른 사용자의 연결입니다")
        method, path = scope.get("method", "GET").upper(), scope.get("path", "")
        if path == SELFTEST_PATH and not self.armed and method == "GET":
            await _respond(send, 200, {"uid": peer["uid"], "pid": peer["pid"]})
            return
        if not self.armed:
            raise Reject(503, "socket_not_armed", "소켓 자가 시험이 끝나지 않았습니다")
        headers = _headers(scope)
        amb = ambiguous_credential(scope, headers)
        if amb:
            raise Reject(400, "ambiguous_principal", f"소켓 요청에 {amb} 자격이 함께 실렸습니다")
        if path == WHOAMI_PATH and method == "GET":
            p = await asyncio.to_thread(self.resolver.resolve_sync, peer, headers.get("x-fsh-svc", "").strip().lower())
            await _respond(send, 200, {"principal": principal_view(p)})
            return
        if not route_allowed(method, path):
            raise Reject(403, "socket_route_not_allowed", f"{method} {path} 는 소켓으로 열려 있지 않습니다")
        p = await asyncio.to_thread(self.resolver.resolve_sync, peer, headers.get("x-fsh-svc", "").strip().lower())
        state = dict(scope.get("state") or {})
        state["fsh_principal"] = p
        state["auth_kind"] = "socket"
        scope["state"] = state
        scope["fsh_sock"] = True
        await self.app(scope, receive, send)


# --- 서비스(시작·시험·정리) ---------------------------------------------------------------------


def enabled() -> bool:
    return fsh_client.enabled()


def server_port() -> str:
    try:
        import vt_env
        return str(vt_env.getenv("VT_PORT", os.environ.get("PORT") or fsh_client.DEFAULT_PORT)).strip()
    except Exception:  # noqa: BLE001
        return os.environ.get("VT_PORT") or fsh_client.DEFAULT_PORT


def socket_path() -> Path:
    over = (os.environ.get("VT_AUTHZ_SOCKET_PATH") or "").strip()
    if over:
        return Path(os.path.expanduser(over))
    p = server_port()
    return vt_paths.state_dir() / "run" / ("fsh.sock" if p == fsh_client.DEFAULT_PORT else f"fsh-{p}.sock")


def status_path(sock: Optional[Path] = None) -> Path:
    sp = sock or socket_path()
    return sp.parent / (sp.name + ".status")        # 소켓 이름마다 따로 — 같은 상태 폴더의 서버 둘이 서로 덮지 않게


def _write_status(sock: Path, state: str, reason: str = "", **extra) -> dict:
    rec = {"state": state, "reason": reason, "path": str(sock), "pid": os.getpid(), "ts": int(time.time()), **extra}
    try:
        p = status_path(sock)
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, p)
    except OSError:
        pass
    return rec


def _live_listener(path: Path) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(1.0)
        s.connect(str(path))
        return True
    except (ConnectionRefusedError, FileNotFoundError):
        return False
    except OSError as e:
        return e.errno not in (errno.ECONNREFUSED, errno.ENOENT)   # 모르겠으면 살아 있다고 본다(지우지 않는다)
    finally:
        s.close()


def prepare_socket(path: Path) -> socket.socket:
    """디렉터리 0700 → stale 정리 → bind → chmod 0600 → listen. 실패는 SkipSocket(사유)."""
    d = path.parent
    try:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = os.stat(d)
        if st.st_uid != os.geteuid():
            raise SkipSocket(f"디렉터리 {d} 가 내 것이 아닙니다")
        if st.st_mode & 0o077:
            os.chmod(d, 0o700)
    except OSError as e:
        raise SkipSocket(f"디렉터리 {d} 를 준비하지 못했습니다: {e}") from e
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        st = None
    if st is not None:
        if not stat.S_ISSOCK(st.st_mode):
            raise SkipSocket(f"{path} 에 소켓이 아닌 파일이 있습니다 — 지우지 않았습니다")
        if _live_listener(path):
            raise SkipSocket(f"{path} 에 이미 살아 있는 리스너가 있습니다(다른 서버?) — 지우지 않았습니다")
        os.unlink(path)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        old = os.umask(0o177)
        try:
            s.bind(str(path))
        finally:
            os.umask(old)
        os.chmod(path, 0o600)            # listen 전에 — uvicorn 의 uds 경로는 listen 뒤에 chmod 한다
        s.listen(128)
        s.setblocking(False)
    except OSError as e:
        s.close()
        raise SkipSocket(f"소켓을 열지 못했습니다: {e}") from e
    return s


class SkipSocket(Exception):
    pass


class SockService:
    def __init__(self, app, *, resolver: Optional[Resolver] = None):
        self.gate = Gate(app, resolver)
        self.path: Optional[Path] = None
        self.server = None
        self.task: Optional[asyncio.Task] = None
        self._sock: Optional[socket.socket] = None
        self._ino: Optional[int] = None
        self.status: dict = {"state": "disabled"}

    async def start(self) -> dict:
        path = socket_path()
        self.path = path
        if not enabled():
            self.status = {"state": "disabled", "reason": "VT_AUTHZ_SOCKET=0", "path": str(path)}
            return self.status
        try:
            if sys.platform == "win32":
                raise SkipSocket("Windows 네이티브는 지원하지 않습니다(WSL 안에서 실행하세요)")
            if _on_wsl_drvfs(path):
                raise SkipSocket(f"{path} 가 /mnt/ 아래(WSL 의 Windows 파일시스템)입니다 — AF_UNIX 권한이 맞지 않아 건너뜁니다")
            if len(os.fsencode(str(path))) > MAX_PATH_BYTES:
                raise SkipSocket(f"소켓 경로가 AF_UNIX 한도({MAX_PATH_BYTES}바이트)를 넘습니다 — VT_AUTHZ_SOCKET_PATH 로 짧게 지정")
            self._sock = prepare_socket(path)
        except SkipSocket as e:
            logger.warning("[authz-sock] 소켓을 열지 않습니다: %s", e)
            self.status = _write_status(path, "skipped", str(e))
            return self.status
        self._ino = os.stat(path).st_ino
        try:
            import uvicorn
            cfg = uvicorn.Config(self.gate, http=_make_protocol(), lifespan="off", interface="asgi3",
                                 log_level="warning", access_log=False, proxy_headers=False,
                                 server_header=False, timeout_keep_alive=5)
            self.server = uvicorn.Server(cfg)
            # uvicorn.Server.serve 는 SIGINT/SIGTERM 핸들러를 가로채 메인 서버의 종료를 막는다 — 끈다.
            self.server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]
            self.task = asyncio.create_task(self.server.serve(sockets=[self._sock]))
            for _ in range(250):
                if self.server.started or self.task.done():
                    break
                await asyncio.sleep(0.02)
            if not self.server.started:
                raise RuntimeError("소켓 서버가 시작되지 않았습니다")
            ok, why = await self.selftest()
            if not ok:
                raise RuntimeError(why)
        except Exception as e:  # noqa: BLE001
            logger.error("[authz-sock] 자가 시험 실패 — 소켓을 서비스하지 않습니다(fail closed): %s", e)
            await self._teardown()
            self.status = _write_status(path, "failed", f"{type(e).__name__}: {e}", selftest=False)
            return self.status
        self.gate.armed = True
        self.status = _write_status(path, "listening", "", selftest=True)
        logger.info("[authz-sock] listening %s (uid %d, selftest ok)", path, os.geteuid())
        return self.status

    async def selftest(self) -> tuple[bool, str]:
        """자기 자신에게 붙어 서버가 읽은 peer pid/uid 가 내 것인지 본다."""
        try:
            r, w = await asyncio.wait_for(asyncio.open_unix_connection(str(self.path)), 3.0)
            w.write(f"GET {SELFTEST_PATH} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
            await w.drain()
            raw = await asyncio.wait_for(r.read(), 3.0)
            w.close()
            head, _, body = raw.partition(b"\r\n\r\n")
            if b" 200 " not in head.split(b"\r\n", 1)[0]:
                return False, f"자가 시험 응답 이상: {head[:60]!r}"
            got = json.loads(body.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            return False, f"자가 시험 연결 실패: {type(e).__name__}: {e}"
        if got.get("pid") != os.getpid() or got.get("uid") != os.geteuid():
            return False, f"peer 자격 불일치: 서버가 본 pid={got.get('pid')} uid={got.get('uid')}, 실제 pid={os.getpid()} uid={os.geteuid()}"
        return True, ""

    async def _teardown(self) -> None:
        self.gate.armed = False
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, 5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                self.task.cancel()
                with contextlib.suppress(BaseException):
                    await self.task
        self.task = None
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None
        if self.path is not None and self._ino is not None:
            try:
                if os.lstat(self.path).st_ino == self._ino:     # 내가 만든 것만 지운다
                    os.unlink(self.path)
            except OSError:
                pass
        self._ino = None

    async def stop(self) -> None:
        was = self.status.get("state")
        await self._teardown()
        if was == "listening" and self.path is not None:
            self.status = _write_status(self.path, "stopped", "")


def _on_wsl_drvfs(path: Path) -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        wsl = "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        wsl = False
    return wsl and str(path.resolve() if path.exists() else path).startswith("/mnt/")


# --- 상태 보고(fsh doctor / status) --------------------------------------------------------------


def status_report(port=None) -> dict:
    """서버 밖에서 부르는 진단 — 상태 파일 + 파일 권한 + 실제 연결 시험 + 최근 24h legacy 토큰 사용 수."""
    from . import legacy_gate, legacy_use
    path = Path(fsh_client.socket_path(port))
    out: dict = {"path": str(path), "enabled": enabled(), "state": "absent", "reason": "", "selftest": None,
                 "perms": None, "dir_perms": None, "reachable": False, "legacy_token_24h": legacy_use.count_24h(),
                 "legacy_enforced": legacy_gate.active(), "legacy_denied_24h": legacy_use.denied_24h(),
                 "approval_unavailable": legacy_gate.approval_unavailable()}
    try:
        rec = json.loads(status_path(path).read_text())
        out.update(state=rec.get("state", "absent"), reason=rec.get("reason", ""), selftest=rec.get("selftest"),
                   server_pid=rec.get("pid"))
        spid = rec.get("pid")
        if rec.get("state") == "listening" and isinstance(spid, int) and not _alive(spid):
            out.update(state="stale", reason="상태 파일의 서버 프로세스가 없습니다")
    except (OSError, ValueError):
        pass
    try:
        st = os.lstat(path)
        out["perms"] = oct(stat.S_IMODE(st.st_mode))
        out["is_socket"] = stat.S_ISSOCK(st.st_mode)
        out["dir_perms"] = oct(stat.S_IMODE(os.stat(path.parent).st_mode))
    except OSError:
        pass
    if out["perms"]:
        try:
            status, body = fsh_client.socket_request("GET", WHOAMI_PATH, sock_path=str(path), timeout=2.0)
            out["reachable"] = status == 200
            if status == 200:
                out["whoami"] = json.loads(body.decode("utf-8")).get("principal")
        except (OSError, ValueError):
            pass
    return out
