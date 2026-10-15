"""authz 특성 테스트의 실행 틀 — 가짜 세계 + 호출자 행렬 + 사례 실행기.

이 파일은 **판정을 바꾸지 않는다.** 지금 서버가 내리는 판정을 그대로 재생해 기록할 뿐이다.
그래서 서버 코드는 하나도 건드리지 않고, 판정 **바깥**의 부수효과만 가짜로 바꾼다:

- tmux: `tmux_runner.run`(+ `tmux_target`의 직접 subprocess 호출)을 메모리 위의 가짜 tmux로.
  실제 tmux 소켓(사용자의 `-L fsh`도, 스위트 전용 소켓도)에 닿는 호출은 없다.
- 네트워크: `peer_client`·`host_relay`의 httpx·`websockets.connect`를 기록용 가짜로.
- 상태 파일: `VT_STATE_DIR`·`VT_WORKSPACE_PATH`·auth 경로를 테스트마다 tmp 로.
- 호출자 식별(`share_identity`)은 **실제 코드**를 돈다. 가짜 tmux가 pane 표를 주고,
  부모 체인 조회(`parent_of`)만 "모름"으로 고정한다 — 신고 pid 가 pane_pid 와 같을 때만 통과한다
  (TestClient 연결은 `testclient` 약한 경로라 신고 pid 를 쓴다, share_identity 머리말).

판정 결과는 `{"status", "error"}`(HTTP) · `{"close"}`(WS) · 함수 반환값(func)으로만 남긴다 —
시각·id 같은 흔들리는 값은 싣지 않아 matrix.json 이 실행마다 같다.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess as _real_subprocess
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlsplit

# --- 고정 값 -----------------------------------------------------------------------------
# 진짜 비밀이 아니라는 표시로 조각을 이어 만든다(test_agent_share.py 와 같은 관례).
PASSWORD = "pw-" + "for-authz-char"
MACHINE = "-".join(["machine", "tok", "authz", "char"])
SESSION_KEY = "authz-char-" + "signing-key"
SOCK = "fsh-authz-char"
TMUX_ENV = f"/private/tmp/tmux-501/{SOCK},1,0"
OTHER_TMUX_ENV = "/private/tmp/tmux-501/default,1,0"

_PW_HASH: Optional[str] = None   # scrypt 는 느리다 — 한 번만 만든다


def password_hash() -> str:
    global _PW_HASH
    if _PW_HASH is None:
        import auth
        _PW_HASH = auth.hash_password(PASSWORD)
    return _PW_HASH


MODES = ("password", "token", "none")
# 호출자 행렬 — 위임 자식(agent_child)·서명 위조(peer_forged).
CALLERS = (
    "session", "session_elev", "machine", "none",
    "peer_view", "peer_control", "peer_control_agent", "peer_forged",
    "agent_verified", "agent_child", "agent_foreign",
)
PEER_IDS = {"peer_view": "pv", "peer_control": "pc", "peer_control_agent": "pca", "peer_forged": "pc"}


# --- 가짜 tmux ---------------------------------------------------------------------------


@dataclass
class FakeSess:
    name: str
    sid: str
    created: int
    pane: str
    pane_pid: int
    command: str = "zsh"
    path: str = "/tmp"
    opts: dict = field(default_factory=dict)


class FakeTmux:
    """판정에 쓰이는 만큼만 흉내 낸다. 모르는 명령은 성공(rc 0, 빈 출력)으로 기록만 한다.
    `down=True`면 서버가 없는 tmux 처럼 전부 실패한다(`no server running`)."""

    def __init__(self):
        self.sessions: dict[str, FakeSess] = {}
        self.calls: list[list[str]] = []
        self.down = False
        self._n = 0

    def add(self, name: str, pane_pid: int, **opts) -> FakeSess:
        self._n += 1
        s = FakeSess(name=name, sid=f"${self._n}", created=1000 + self._n, pane=f"%{self._n}",
                     pane_pid=pane_pid, opts=dict(opts))
        self.sessions[name] = s
        return s

    def _find(self, target: str) -> Optional[FakeSess]:
        t = (target or "").strip()
        exact = t.startswith("=")
        t = t.lstrip("=").rstrip(":")
        if "." in t and not t.startswith("%"):
            t = t.split(":", 1)[0]
        for s in self.sessions.values():
            if t in (s.name, s.sid, s.pane):
                return s
        if not exact:
            for s in self.sessions.values():
                if s.name.startswith(t):
                    return s
        return None

    @staticmethod
    def _render(fmt: str, s: FakeSess) -> str:
        vals = {
            "pane_id": s.pane, "pane_pid": str(s.pane_pid), "session_id": s.sid,
            "session_created": str(s.created), "session_name": s.name, "pane_active": "1",
            "window_active": "1", "pane_current_command": s.command, "pane_current_path": s.path,
            "session_windows": "1", "session_attached": "0",
        }
        return re.sub(r"#\{([^}]+)\}", lambda m: vals.get(m.group(1), s.opts.get(m.group(1), "")), fmt)

    def _arg(self, args: list[str], flag: str) -> Optional[str]:
        return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None

    def run(self, args, timeout=2.0, input=None):  # noqa: A002 — tmux_runner.run 과 같은 서명
        args = [str(a) for a in args]
        self.calls.append(args)
        if self.down:
            return 1, b"", b"no server running on /tmp/tmux-501/" + SOCK.encode()
        cmd = args[0] if args else ""
        if cmd in ("list-panes", "list-sessions"):
            fmt = self._arg(args, "-F") or "#{session_name}"
            tgt = self._arg(args, "-t")
            rows = [self._find(tgt)] if tgt else list(self.sessions.values())
            return 0, "".join(self._render(fmt, s) + "\n" for s in rows if s).encode(), b""
        if cmd == "display-message":
            s = self._find(self._arg(args, "-t") or "")
            if s is None:
                return 1, b"", b"can't find session"
            return 0, (self._render(args[-1], s) + "\n").encode(), b""
        if cmd == "has-session":
            return (0 if self._find(self._arg(args, "-t") or "") else 1), b"", b""
        if cmd == "new-session":
            name = self._arg(args, "-s") or f"auto{self._n + 1}"
            if name in self.sessions:
                return 1, b"", b"duplicate session"
            self.add(name, 90000 + self._n)
            return 0, b"", b""
        if cmd == "kill-session":
            s = self._find(self._arg(args, "-t") or "")
            if s is None:
                return 1, b"", b"can't find session"
            self.sessions.pop(s.name, None)
            return 0, b"", b""
        if cmd == "rename-session":
            s = self._find(self._arg(args, "-t") or "")
            if s is None:
                return 1, b"", b"can't find session"
            self.sessions.pop(s.name)
            s.name = args[-1]
            self.sessions[s.name] = s
            return 0, b"", b""
        if cmd == "set-option":
            s = self._find(self._arg(args, "-t") or "")
            if s is None:
                return 1, b"", b"no such session"
            rest = [a for a in args[1:] if a not in ("-u", "-t", self._arg(args, "-t"))]
            if "-u" in args:
                s.opts.pop(rest[0], None)
            elif len(rest) >= 2:
                s.opts[rest[0]] = rest[1]
            return 0, b"", b""
        if cmd == "capture-pane":
            return 0, b"fake screen line\n", b""
        return 0, b"", b""


class SubprocessShim:
    """`tmux_target`이 tmux 를 `subprocess.run`으로 직접 부르는 곳을 가짜 tmux 로 돌린다.
    tmux 가 아닌 명령은 받지 않는다(이 모듈이 다른 걸 부르기 시작하면 바로 드러나게)."""

    CalledProcessError = _real_subprocess.CalledProcessError
    TimeoutExpired = _real_subprocess.TimeoutExpired
    PIPE = _real_subprocess.PIPE
    DEVNULL = _real_subprocess.DEVNULL

    def __init__(self, fake: FakeTmux):
        self.fake = fake

    def run(self, cmd, capture_output=False, text=False, timeout=None, check=False, **_kw):
        assert cmd and cmd[0] == "tmux", f"tmux 아닌 subprocess 호출: {cmd!r}"
        i = 1
        while i < len(cmd) and cmd[i] in ("-L", "-S", "-f", "-u"):
            i += 1 if cmd[i] == "-u" else 2
        rc, out, err = self.fake.run(list(cmd[i:]))
        if check and rc != 0:
            raise _real_subprocess.CalledProcessError(rc, cmd, out, err)
        if text:
            out, err = out.decode(), err.decode()
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


# --- 사례 ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """matrix.json 한 줄. `id`는 사람이 읽고 이후 단계가 다시 찾는 키라 바꾸지 않는다."""
    id: str
    group: str
    caller: str
    mode: str = "password"
    method: str = "GET"            # HTTP 메서드 | "WS" | "FUNC"
    route: str = ""                # 라우트 템플릿(커버리지 메타 테스트의 키) 또는 함수 이름
    path: str = ""                 # 실제 경로(+쿼리). `{rid}` 같은 자리는 seed 가 채운다
    body: Any = None               # dict → JSON, bytes → 그대로
    headers: tuple = ()            # 추가 헤더 (k, v) 쌍
    tmux: str = "up"
    hole: Optional[str] = None     # "H1" 등 — 알려진 구멍을 가리키는 짧은 라벨(설명은 사례 id·docstring 에 있다)
    rule: Optional[str] = None     # relay_policy 규칙 패턴(커버리지 키)
    real_dispatch: bool = False    # relay: 안쪽 라우트까지 실제로 돈다(기본은 판정만 보고 멈춘다)
    note: str = ""

    def meta(self) -> dict:
        m = {"group": self.group, "caller": self.caller, "mode": self.mode, "tmux": self.tmux,
             "method": self.method, "route": self.route, "path": self.path}
        if self.hole:
            m["hole"] = self.hole
        if self.rule:
            m["rule"] = self.rule
        if self.note:
            m["note"] = self.note
        return m


# --- 세계 -------------------------------------------------------------------------------


class World:
    """사례 하나가 도는 격리된 서버 상태. conftest 의 `world` 픽스처가 만든다."""

    def __init__(self, monkeypatch, tmp_path):
        self.mp = monkeypatch
        self.tmp = tmp_path
        self.tmux = FakeTmux()
        self.net: list[tuple] = []          # 가짜 네트워크 호출 기록
        self.dispatched: list[str] = []     # relay 안쪽까지 넘어간 대상
        self.pty_writes: list[tuple] = []
        self.secrets: dict[str, str] = {}
        self.subs: dict[str, str] = {}
        self._install()
        self._seed()

    # -- 설치 --
    def _install(self) -> None:
        mp, tmp = self.mp, self.tmp
        state = tmp / "vt"
        mp.setenv("VT_STATE_DIR", str(state))
        mp.setenv("VT_WORKSPACE_PATH", str(tmp / "workspace.json"))
        mp.setenv("VT_NETWORK_MODE", "all")
        mp.setenv("VT_TMUX_SOCKET", SOCK)
        mp.delenv("VT_AGENT_SPAWN", raising=False)

        import auth
        mp.setattr(auth, "STATE_DIR", state)
        mp.setattr(auth, "DEVICES_PATH", state / "devices.json")
        mp.setattr(auth, "TOTP_PATH", state / "totp.json")
        mp.setattr(auth, "TICKETS_PATH", state / "tickets.json")

        import tmux_runner
        import tmux_target
        mp.setattr(tmux_runner, "VT_TMUX_SOCKET", SOCK)
        mp.setattr(tmux_runner, "run", self.tmux.run)
        mp.setattr(tmux_target, "subprocess", SubprocessShim(self.tmux))
        mp.setattr(tmux_target, "TMUX_BASE", ["tmux", "-L", SOCK])

        import share_identity
        mp.setattr(share_identity, "parent_of", lambda pid: None)
        mp.setattr(share_identity, "socket_owner_pid", lambda *a, **k: None)

        # 모듈 전역 속도 제한·캐시 — 앞 사례의 호출이 다음 사례의 판정을 바꾸지 않게 비운다.
        import agent_msg
        import agent_share
        from routes import hosts as hosts_routes
        from ttl_cache import TTLCache
        mp.setattr(agent_share, "_rate", {})
        mp.setattr(agent_msg, "_rate", {})
        mp.setattr(hosts_routes, "_sessions_cache", TTLCache(ttl=30.0))

        import push
        mp.setattr(push, "available", lambda: False)
        mp.setattr(push, "send", lambda *a, **k: None)

        from routes import peer_protocol
        import host_store
        mp.setattr(peer_protocol, "_nonces", host_store.NonceCache())

        # 네트워크 — peer_client 의 바깥 호출을 전부 기록용 가짜로.
        import peer_client
        net = self.net

        def _call_sync(peer, method, path, body=None, timeout=None, query=None):
            net.append(("call_sync", peer["id"], method, path))
            return {"stub": "peer", "path": path, "results": [], "sessions": []}

        async def _call(peer, method, path, body=None):
            net.append(("call", peer["id"], method, path))
            return {"stub": "peer", "path": path}

        def _ping(peer):
            net.append(("ping", peer["id"]))
            return {"stub": "ping"}

        def _send_bytes(peer, path, data, headers, timeout=None):
            net.append(("send_bytes", peer["id"], path))
            return {"ok": True, "conv": "c-stub", "hop": 1, "status": "queued", "immediate": False}

        mp.setattr(peer_client, "_call_sync", _call_sync)
        mp.setattr(peer_client, "call", _call)
        mp.setattr(peer_client, "ping_sync", _ping)
        mp.setattr(peer_client, "send_bytes_sync", _send_bytes)

        import httpx
        from routes import host_relay

        def _upstream(request: httpx.Request) -> httpx.Response:
            net.append(("relay_http", request.method, request.url.path))
            return httpx.Response(200, json={"stub": "upstream"}, headers={"x-peer-relay": "1"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(_upstream))
        mp.setattr(host_relay, "_client", client)
        mp.setattr(host_relay, "_http", lambda: client)

        import websockets

        async def _ws_connect(*a, **k):
            net.append(("relay_ws",))
            raise OSError("authz-char: 네트워크 없음")

        mp.setattr(websockets, "connect", _ws_connect)

        # relay 안쪽 실행 — 기본은 판정까지만 보고 멈춘다(사례마다 real_dispatch 로 켠다).
        from fastapi.responses import JSONResponse
        from routes import peer_relay
        self._real_dispatch = peer_relay._dispatch
        self.real_dispatch_on = False

        async def _dispatch(request, target, query, peer_id, data):
            self.dispatched.append(target)
            if self.real_dispatch_on:
                return await self._real_dispatch(request, target, query, peer_id, data)
            return JSONResponse({"relayDispatched": True}, headers={peer_relay.RELAY_HEADER: "1"})

        mp.setattr(peer_relay, "_dispatch", _dispatch)

        # PTY 쓰기는 항상 기록만 한다. 가짜 PTY 자체는 `/api/sessions/pty-1/…` 사례에서만 심는다
        # (다른 라우트 — 스크롤백 검색 등 — 가 진짜 PTYSession 으로 알고 읽지 않게).
        from deps import pty_mgr
        import routes.pty as pty_routes
        self._pty_mgr = pty_mgr
        mp.setattr(pty_mgr, "write", lambda sid, data: self.pty_writes.append((sid, data)))
        mp.setattr(pty_routes, "on_terminal_input", lambda *a, **k: None)

    # -- 기본 상태 --
    def _seed(self) -> None:
        t = self.tmux
        self.agent_a = t.add("agent-a", 4242)                               # $1 %1 — 위임 부모·공유 요청자
        self.target = t.add("target", 4343, **{"@fsh_share_nick": "shell",   # $2 %2 — 읽기·쓰기 공유
                                               "@fsh_share_mode": "readwrite"})
        self.other = t.add("other", 4545)                                   # $3 %3 — 공유 안 함
        self.child = t.add("child-b", 4646)                                 # $4 %4 — 위임 자식

        import host_store
        for pid, level, agent in (("pv", host_store.LEVEL_VIEW, False),
                                  ("pc", host_store.LEVEL_CONTROL, False),
                                  ("pca", host_store.LEVEL_CONTROL, True)):
            _, secret = host_store.add_grant(pid, pid.upper())
            host_store.set_grant_level(pid, level)
            if agent:
                host_store.set_grant_agent(pid, True)
            self.secrets[pid] = secret
        host_store.add_peer("m5", "http://m5.invalid", "s" * 43, "M5")

        import vt_paths
        d = vt_paths.state_dir()
        d.mkdir(parents=True, exist_ok=True)
        item = {"id": "dseed00001", "state": "running", "agent": "claude", "created_at": time.time(),
                "task_preview": "seed", "cwd": "/tmp", "outbox": [],
                "parent": {"key": f"{self.agent_a.sid}:{self.agent_a.created}", "name": "agent-a", "pane": "%1"},
                "child": {"key": f"{self.child.sid}:{self.child.created}", "name": "child-b", "pane": "%4"}}
        (d / "delegations.json").write_text(json.dumps({"items": [item], "spawns": []}), encoding="utf-8")
        self.subs["did"] = "dseed00001"

    def caller_obj(self, s: FakeSess):
        import share_identity
        return share_identity.Caller(sid=s.sid, created=s.created, name=s.name, pane_id=s.pane)

    def _request_access(self, snap, tgt):
        """상태를 **미리 만들어 두는** 호출 — 승인 요청은 「그 요청이 이미 있던 상황」을 깔기 위한 것이라, 비밀번호 없는 서버에서
        새 승인 요청을 막는 `legacy` 문(`legacy_gate`)은 건너뛴다(시험 대상은 이 뒤의 요청이다)."""
        import agent_share
        from authz import legacy_gate
        with self.mp.context() as m:
            m.setattr(legacy_gate, "approval_unavailable", lambda: False)
            return agent_share.request_access(snap, self.caller_obj(self.agent_a), tgt, agent_share.KIND_READ)

    def seed(self, path: str, body) -> None:
        """경로·본문의 `{rid}`·`{gid}`·`{ticket}` 자리를 채울 상태를 그때 만든다."""
        blob = path + json.dumps(body, default=str) if not isinstance(body, bytes) else path
        import agent_share
        if "{rid}" in blob or "{gid}" in blob:
            snap = agent_share.snapshot()
            tgt = next(s for s in snap.values() if s.name == "target")
            req, _ = self._request_access(snap, tgt)
            self.subs["rid"] = req["id"]
            if "{gid}" in blob:
                self.subs["gid"] = agent_share.decide(req["id"], "allow", None, snap)["grant"]["id"]
        if "{ticket}" in blob:
            import host_store
            self.subs["ticket"] = host_store.issue_pair_ticket()

    def grant_read(self) -> None:
        """agent-a → target 읽기 권한(사람이 승인한 상태)."""
        import agent_share
        snap = agent_share.snapshot()
        tgt = next(s for s in snap.values() if s.name == "target")
        req, _ = self._request_access(snap, tgt)
        agent_share.decide(req["id"], "allow", None, snap)

    def fill(self, s: str) -> str:
        for k, v in self.subs.items():
            s = s.replace("{" + k + "}", v)
        return s

    # -- 서버 모드 --
    def set_mode(self, mode: str) -> None:
        """password: 비밀번호+기계 토큰+서명키(보통 설치) / token: 기계 토큰+서명키, 비밀번호 없음 /
        none: 아무것도 없음."""
        import auth
        pw, tok, key = {"password": (password_hash(), MACHINE, SESSION_KEY),
                        "token": ("", MACHINE, SESSION_KEY),
                        "none": ("", "", "")}[mode]
        self.mp.setattr(auth, "VT_AUTH_PASSWORD_HASH", pw)
        self.mp.setattr(auth, "VT_AUTH_TOKEN", tok)
        self.mp.setattr(auth, "VT_AUTH_SESSION_KEY", key)

    # -- 호출자 --
    def headers_for(self, caller: str, method: str, path_q: str, body: bytes) -> dict:
        import auth
        import host_store
        h: dict[str, str] = {}
        if caller in ("session", "session_elev"):
            elev = int(time.time()) + 900 if caller == "session_elev" else None
            cookie = auth.make_session("", elev_exp=elev)
            if cookie:   # 서명키가 없는 서버(none 모드)에선 세션을 만들 수 없다 — 쿠키 없음
                h["Cookie"] = f"vt_session={cookie}"
        elif caller == "machine":
            h["Authorization"] = f"Bearer {MACHINE}"
        elif caller in ("agent_verified", "agent_child", "agent_foreign"):
            h["Authorization"] = f"Bearer {MACHINE}"
            s = self.child if caller == "agent_child" else self.agent_a
            pid = 99999 if caller == "agent_foreign" else s.pane_pid   # foreign: 다른 프로세스가 pane 을 주장
            h.update({"X-Fsh-Pane": s.pane, "X-Fsh-Tmux": TMUX_ENV, "X-Fsh-Pid": str(pid)})
        elif caller in PEER_IDS:
            pid = PEER_IDS[caller]
            secret = "x" * 43 if caller == "peer_forged" else self.secrets[pid]
            ts = int(time.time())
            nonce = f"n-{time.time_ns()}"
            path_only = urlsplit(path_q).path
            # peer_client 와 같은 규칙: 본문 해시는 본문 서명 라우트·relay 에만, relay 는 쿼리까지 서명.
            relay = path_only.startswith("/api/peer/relay")
            body_signed = relay or path_only in ("/api/peer/agent/message", "/api/peer/file")
            digest = hashlib.sha256(body).hexdigest() if body_signed else ""
            signed_path = path_q if relay else path_only
            h.update({"X-Peer-Id": pid, "X-Peer-Ts": str(ts), "X-Peer-Nonce": nonce,
                      "X-Peer-Sig": host_store.sign_request(secret, method, signed_path, ts, nonce, digest)})
            if digest:
                h["X-Peer-Body"] = digest
        elif caller != "none":
            raise AssertionError(f"모르는 호출자: {caller}")
        return h

    # -- 실행 --
    def run(self, case: Case) -> dict:
        self.set_mode(case.mode)
        self.real_dispatch_on = case.real_dispatch
        if case.method == "FUNC":
            raise AssertionError("FUNC 사례는 각 테스트 파일이 직접 실행한다")
        self.seed(case.path, case.body)
        path = self.fill(case.path)
        if path.startswith("/api/sessions/pty-1/"):
            self.mp.setitem(self._pty_mgr._sessions, "pty-1", SimpleNamespace(session_id="pty-1"))
        if isinstance(case.body, bytes):
            data = case.body
        elif case.body is None:
            data = b""
        else:
            data = json.dumps(json.loads(self.fill(json.dumps(case.body)))).encode()
        if case.tmux == "down":
            self.tmux.down = True
        import main
        from starlette.testclient import TestClient
        client = TestClient(main.app, raise_server_exceptions=False)
        method = "GET" if case.method == "WS" else case.method
        headers = self.headers_for(case.caller, method, path, data)
        headers.update(dict(case.headers))
        if case.method == "WS":
            return {"close": _ws_close_code(client, path, headers)}
        if data and "content-type" not in {k.lower() for k in headers} and not isinstance(case.body, bytes):
            headers["Content-Type"] = "application/json"
        r = client.request(case.method, path, content=data or None, headers=headers)
        out = {"status": r.status_code, "error": _error_of(r)}
        if case.group in ("relay", "host_relay"):
            out["reached"] = bool(self.dispatched) if case.group == "relay" else \
                any(n[0] == "relay_http" for n in self.net)
        return out


def _error_of(r) -> Optional[str]:
    try:
        j = r.json()
    except ValueError:
        return None
    if isinstance(j, dict):
        e = j.get("error")
        if isinstance(e, str):
            return e
        d = j.get("detail")
        if isinstance(d, dict) and isinstance(d.get("error"), str):
            return d["error"]
    return None


def _ws_close_code(client, path: str, headers: dict) -> int:
    """WS 핸드셰이크의 결말 — 닫힘 코드. accept 전 거절은 starlette 가 예외로 준다."""
    from starlette.websockets import WebSocketDisconnect
    try:
        with client.websocket_connect(path, headers=headers) as ws:
            msg = ws.receive()
            if msg.get("type") == "websocket.close":
                return int(msg.get("code", 1000))
            return -1   # 열린 채로 데이터가 왔다 — 판정상 "통과"
    except WebSocketDisconnect as e:
        return int(e.code)
