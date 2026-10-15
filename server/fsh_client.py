"""FarShell 서버 호출 — unix socket 우선, 기계 토큰 fallback (plan-2.6 2단계, 의존성 없음).

`~/.vt/run/fsh.sock`(0600)에 붙으면 서버가 연결의 peer uid/pid 로 호출자를 안다(authz/sock.py).
토큰을 환경·파일에서 읽어 실어 보낼 필요가 없다. 소켓이 없거나(옛 서버·소켓 비활성·stale) 서버가
소켓 쪽에서 거부하면(`socket_route_not_allowed` 등) 예전 방식 그대로 TCP + `Authorization: Bearer`.

이 모듈은 stdlib 만 쓴다 — MCP·훅·daemon·CLI 가 공용으로 부르고, 서버 패키지(fastapi 등)가 없는
환경에서도 import 되어야 한다.

소켓을 쓰지 않는 경우(= 곧바로 TCP):
  - `VT_AUTHZ_SOCKET=0|off|false|no`
  - `server`(= VT_SERVER)가 이 맥의 루프백이 아니다 — 원격 서버에 로컬 소켓으로 말하면 엉뚱한 서버다
  - 소켓 파일이 없거나, 소켓이 아니거나, 내 것이 아니거나, 그룹/기타가 접근할 수 있다
소켓 파일 이름은 포트를 따라간다: 기본 포트(7777)는 `fsh.sock`, 그 외 `fsh-<port>.sock`.
같은 상태 디렉터리를 쓰는 서버가 둘일 때 클라이언트가 엉뚱한 서버에 붙지 않게 하려는 것이다.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import socket
import stat
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

DEFAULT_PORT = "7777"
_OFF = ("0", "off", "false", "no")
_LOOPBACK = ("localhost", "127.0.0.1", "::1", "[::1]")
# 서버의 소켓 계층이 내는 거부 — 토큰 경로로 한 번 더 시도해도 되는 것들(소켓 쪽 정책 때문이지 요청 때문이 아니다).
SOCK_REJECTS = ("socket_route_not_allowed", "socket_not_armed", "peer_unidentified", "peer_gone",
                "identity_unavailable")


class SocketUnavailable(OSError):
    """소켓에 붙지 못했다 — 호출부는 TCP/토큰으로 내려간다."""


def enabled() -> bool:
    return os.environ.get("VT_AUTHZ_SOCKET", "1").strip().lower() not in _OFF


def state_dir() -> str:
    d = (os.environ.get("VT_STATE_DIR") or "").strip()
    if d:
        return os.path.expanduser(d)
    return os.path.join(os.environ.get("HOME") or os.path.expanduser("~"), ".vt")


def socket_path(port=None) -> str:
    """소켓 경로. `VT_AUTHZ_SOCKET_PATH` 가 있으면 그것(길이 한도가 빡빡한 환경·시험용)."""
    over = (os.environ.get("VT_AUTHZ_SOCKET_PATH") or "").strip()
    if over:
        return os.path.expanduser(over)
    p = str(port or os.environ.get("VT_PORT") or os.environ.get("PORT") or DEFAULT_PORT).strip()
    name = "fsh.sock" if p == DEFAULT_PORT else f"fsh-{p}.sock"
    return os.path.join(state_dir(), "run", name)


def _is_local_server(server: Optional[str]) -> bool:
    if not server:
        return True
    try:
        host = urllib.parse.urlsplit(server if "://" in server else "http://" + server).hostname or ""
    except ValueError:
        return False
    return host.lower() in _LOOPBACK or host in ("::1",)


def usable_path(port=None, server: Optional[str] = None) -> Optional[str]:
    """붙어볼 만한 소켓 경로, 아니면 None. 연결 가능 여부는 연결해 봐야 안다."""
    if not enabled() or not _is_local_server(server):
        return None
    path = socket_path(port)
    try:
        st = os.lstat(path)
        parent = os.lstat(os.path.dirname(path) or ".")
    except OSError:
        return None
    me = os.geteuid()
    if not stat.S_ISSOCK(st.st_mode) or st.st_uid != me or (st.st_mode & 0o077):
        return None
    if parent.st_uid != me or (parent.st_mode & 0o022):
        return None
    return path


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._sock_path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(self.timeout)
            s.connect(self._sock_path)
        except OSError as e:
            s.close()
            raise SocketUnavailable(str(e)) from e
        self.sock = s


def socket_request(method: str, path: str, *, body: Optional[bytes] = None, headers: Optional[dict] = None,
                   timeout: float = 3.0, sock_path: Optional[str] = None, svc: str = "",
                   port=None) -> tuple[int, bytes]:
    """소켓으로 한 번 호출 → (status, body). 붙지 못하면 SocketUnavailable. 붙은 뒤의 시간 초과·끊김은
    OSError 그대로(요청이 이미 처리됐을 수 있어 다른 경로로 재시도하지 않는다)."""
    sp = sock_path or usable_path(port)
    if not sp:
        raise SocketUnavailable("socket not available")
    h = {"Accept": "application/json"}
    if svc:
        h["X-Fsh-Svc"] = svc
    if body is not None:
        h.setdefault("Content-Type", "application/json")
    h.update(headers or {})
    conn = _UnixConnection(sp, timeout)
    try:
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def _sock_rejected(status: int, data: bytes) -> bool:
    if status not in (403, 503):
        return False
    try:
        return json.loads(data.decode("utf-8") or "{}").get("error") in SOCK_REJECTS
    except (ValueError, AttributeError):
        return False


def call(method: str, path: str, *, body=None, headers: Optional[dict] = None, timeout: float = 3.0,
         svc: str = "", server: Optional[str] = None, token: str = "", port=None,
         raise_for_status: bool = False) -> tuple[int, bytes]:
    """소켓 우선 → TCP+토큰 fallback. `body`는 bytes | dict | list | None(dict/list 는 JSON).

    반환 (status, bytes). TCP 에 닿지 못하면 `urllib.error.URLError`(OSError)가 그대로 난다.
    `raise_for_status=True` 면 4xx/5xx 에 `urllib.error.HTTPError`(urlopen 과 같은 모양)."""
    if isinstance(body, (dict, list)):
        data: Optional[bytes] = json.dumps(body).encode("utf-8")
    else:
        data = body
    status = 0
    resp = b""
    via = "tcp"
    sp = usable_path(port, server)
    if sp:
        try:
            status, resp = socket_request(method, path, body=data, headers=headers, timeout=timeout,
                                          sock_path=sp, svc=svc)
            via = "socket"
            if _sock_rejected(status, resp):
                via = "tcp"
        except SocketUnavailable:
            via = "tcp"
    if via == "tcp":
        base = (server or f"http://127.0.0.1:{port or os.environ.get('VT_PORT') or DEFAULT_PORT}").rstrip("/")
        req = urllib.request.Request(base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if svc:
            req.add_header("X-Fsh-Svc", svc)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                status, resp = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, resp = e.code, e.read()
    if raise_for_status and status >= 400:
        raise urllib.error.HTTPError(path, status, "HTTP %d" % status, {}, io.BytesIO(resp))  # type: ignore[arg-type]
    return status, resp


def call_json(method: str, path: str, **kw) -> tuple[int, dict]:
    """`call` + JSON 해석. 본문이 비었거나 JSON 이 아니면 {}."""
    status, data = call(method, path, **kw)
    try:
        v = json.loads(data.decode("utf-8") or "{}")
    except ValueError:
        v = {}
    return status, v if isinstance(v, dict) else {"value": v}
