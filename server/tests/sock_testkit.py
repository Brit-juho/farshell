"""unix socket 시험 도구(plan-2.6 2단계) — 실제 AF_UNIX 서버를 프로세스 안에 띄운다.

- `short_dir()`: AF_UNIX 경로 한도(macOS 104바이트) 때문에 `/tmp/fsXXXXXXXX` 같은 짧은 임시 디렉터리.
- `LiveSock`: 별도 스레드의 event loop 에서 `authz.sock.SockService` 를 돌린다(실제 uvicorn).
- `UnixRecorder` / `TcpRecorder`: 요청을 기록하고 정해 둔 응답을 주는 stdlib HTTP 서버(클라이언트 시험용).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def short_dir() -> str:
    return tempfile.mkdtemp(prefix="fs", dir="/tmp")


class LiveSock:
    def __init__(self, app, resolver=None):
        from authz import sock
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.svc = sock.SockService(app, resolver=resolver)

    def start(self) -> dict:
        return asyncio.run_coroutine_threadsafe(self.svc.start(), self.loop).result(30)

    def stop(self) -> None:
        if getattr(self, "_stopped", False):
            return
        self._stopped = True
        try:
            asyncio.run_coroutine_threadsafe(self.svc.stop(), self.loop).result(15)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401
        pass

    def _serve(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        rec = {"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
               "body": body}
        self.server.records.append(rec)
        status, payload = self.server.respond(rec)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _serve


class _Unix(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        r, _ = super().get_request()
        return r, ("unix", 0)


class _Tcp(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


class UnixRecorder:
    def __init__(self, path: str, respond=None, mode: int = 0o600):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        os.chmod(os.path.dirname(path), 0o700)
        self.path = path
        old = os.umask(0o177)
        try:
            self.srv = _Unix(path, _Handler)
        finally:
            os.umask(old)
        os.chmod(path, mode)
        self.srv.records = []
        self.srv.respond = respond or (lambda rec: (200, {"ok": True, "via": "socket"}))
        self.thread = threading.Thread(target=lambda: self.srv.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()

    @property
    def records(self):
        return self.srv.records

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class TcpRecorder:
    def __init__(self, respond=None):
        self.srv = _Tcp(("127.0.0.1", 0), _Handler)
        self.srv.records = []
        self.srv.respond = respond or (lambda rec: (200, {"ok": True, "via": "tcp"}))
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=lambda: self.srv.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()

    @property
    def records(self):
        return self.srv.records

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def rmtree(p: str) -> None:
    shutil.rmtree(p, ignore_errors=True)
