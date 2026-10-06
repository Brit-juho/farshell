"""P3-2(40-mcp.md §3, ADR-36) — MCP 헬스체크 단위 테스트.

§3.5-4가 이 항목의 선행 조건이다: 손자 프로세스까지 정리되는지를 이 파일이
단언하지 못하면 계획서는 "헬스체크를 구현하지 않는다"고 못박는다. 그래서
`test_grandchild_process_is_cleaned_up`을 가장 무겁게 다룬다.

실제 외부 MCP 서버에 붙는 테스트는 만들지 않는다 — 전부 합성 스텁.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

import mcp_verify

# 최소 MCP 서버 스텁 — initialize/tools/list만 답한다.
STUB_MINIMAL = """
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        resp = {"jsonrpc": "2.0", "id": msg["id"],
                "result": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "serverInfo": {"name": "stub", "version": "0"}}}
        sys.stdout.write(json.dumps(resp) + "\\n"); sys.stdout.flush()
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        resp = {"jsonrpc": "2.0", "id": msg["id"],
                "result": {"tools": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}}
        sys.stdout.write(json.dumps(resp) + "\\n"); sys.stdout.flush()
"""

# 응답을 아예 안 하는 스텁 — 타임아웃 경로 검증용.
STUB_HANGS = """
import sys
sys.stdin.read()
"""

# stderr에 시크릿을 흘리고 죽는 스텁 — 마스킹 검증용.
STUB_LEAKS_SECRET = """
import sys, os
sys.stderr.write("boom: token=" + os.environ.get("FSH_TEST_SECRET", "") + "\\n")
sys.stderr.flush()
sys.exit(1)
"""


def _stub_args(script: str) -> list[str]:
    return ["-c", script]


def _pgid_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def test_connected_reports_tool_count():
    result = asyncio.run(mcp_verify.verify_stdio(sys.executable, _stub_args(STUB_MINIMAL)))
    assert result["state"] == "connected"
    assert result["tool_count"] == 3
    assert result["error"] is None
    assert isinstance(result["checked_at"], float)


def test_missing_command_reports_error_with_name_only():
    result = asyncio.run(mcp_verify.verify_stdio("/some/very/secret/path/nonexistent-mcp-xyz"))
    assert result["state"] == "error"
    assert result["tool_count"] is None
    assert "nonexistent-mcp-xyz" in result["error"]
    assert "/some/very/secret/path" not in result["error"]


def test_unresponsive_stub_times_out_and_leaves_no_process(monkeypatch):
    monkeypatch.setattr(mcp_verify, "SIGTERM_GRACE_SEC", 0.3)
    result = asyncio.run(
        mcp_verify.verify_stdio(sys.executable, _stub_args(STUB_HANGS), timeout=0.3)
    )
    assert result["state"] == "error"
    assert "시간 초과" in result["error"]


def test_grandchild_process_is_cleaned_up(tmp_path, monkeypatch):
    """§3.5-4 — 이 항목의 선행 조건. 실패하면 P3-2를 구현하지 않는다."""
    pidfile = tmp_path / "grandchild.pid"
    grandchild_script = f"""
import os, time
with open({str(pidfile)!r}, "w") as f:
    f.write(str(os.getpid()))
time.sleep(30)
"""
    child_script = f"""
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", {grandchild_script!r}])
time.sleep(30)
"""
    top_script = f"""
import subprocess, sys
subprocess.Popen([sys.executable, "-c", {child_script!r}])
sys.stdin.read()
"""
    monkeypatch.setattr(mcp_verify, "SIGTERM_GRACE_SEC", 0.5)
    result = asyncio.run(
        mcp_verify.verify_stdio(sys.executable, _stub_args(top_script), timeout=2.0)
    )
    assert result["state"] == "error"  # 응답 안 하니 타임아웃

    # 손자가 pid 파일을 쓸 시간을 조금 준다(스폰 체인 2단계라 즉시는 아니다).
    deadline = time.monotonic() + 3.0
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pidfile.exists(), "손자가 pid를 남기지 못했다 — 테스트 자체가 손자를 못 띄웠다"

    grandchild_pid = int(pidfile.read_text())
    # 핵심 단언 — 손자가 살아 있으면 안 된다.
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)


def test_secret_env_value_is_redacted_from_error():
    secret = "sk-testsecretvalue1234567890"
    result = asyncio.run(
        mcp_verify.verify_stdio(
            sys.executable, _stub_args(STUB_LEAKS_SECRET),
            env={"FSH_TEST_SECRET": secret},
        )
    )
    assert result["state"] == "error"
    assert secret not in result["error"]
    assert "***" in result["error"]


def test_http_verify_refuses_without_confirmation():
    result = asyncio.run(mcp_verify.verify_http("http://127.0.0.1:1/mcp", confirmed=False))
    assert result["state"] == "error"
    assert "확인" in result["error"]


def test_http_verify_reports_connection_failure_when_confirmed():
    # 아무도 안 듣는 포트 — 네트워크 실패 경로만 확인(실제 서비스에 안 붙는다).
    result = asyncio.run(
        mcp_verify.verify_http("http://127.0.0.1:1/mcp", confirmed=True, timeout=0.5)
    )
    assert result["state"] == "error"
    assert result["tool_count"] is None


# ADR-46 — codex는 허용 목록만 물려준다. 검증이 우리 환경을 통째로 주면 codex 안에서는
# 실패할 서버가 검증에서는 통과한다(거짓 통과).
STUB_NEEDS_LEAKED_VAR = STUB_MINIMAL.replace(
    "import sys, json", "import sys, json, os\nif not os.environ.get('VT_ONLY_IN_OUR_ENV'):\n    sys.exit(3)")


def test_inherit_allowlist_excludes_everything_else(monkeypatch):
    monkeypatch.setenv("VT_ONLY_IN_OUR_ENV", "1")
    full = asyncio.run(mcp_verify.verify_stdio(sys.executable, _stub_args(STUB_NEEDS_LEAKED_VAR)))
    assert full["state"] == "connected", "전제: 환경을 통째로 주면 통과한다"
    limited = asyncio.run(mcp_verify.verify_stdio(
        sys.executable, _stub_args(STUB_NEEDS_LEAKED_VAR), inherit=["PATH", "HOME"]))
    assert limited["state"] == "error"


def test_inherited_secret_is_masked(monkeypatch):
    monkeypatch.setenv("FSH_TEST_SECRET", "sk-inherited-secret-value")
    result = asyncio.run(mcp_verify.verify_stdio(
        sys.executable, _stub_args(STUB_LEAKS_SECRET), inherit=["PATH", "FSH_TEST_SECRET"]))
    assert result["state"] == "error"
    assert "sk-inherited-secret-value" not in json.dumps(result)


def test_http_connection_refused_is_a_readable_message():
    """`str(URLError)`(`<urlopen error [Errno 61] …>`)을 화면에 그대로 내지 않는다."""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    r = asyncio.run(mcp_verify.verify_http(f"http://127.0.0.1:{port}/mcp", confirmed=True, timeout=3))
    assert r["state"] == "error"
    assert "urlopen" not in r["error"] and "거부" in r["error"]


def test_http_probe_distinguishes_auth_and_validates_initialize():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    class Handler(BaseHTTPRequestHandler):
        code = 401
        payload = {}
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(self.code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(self.payload).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}/mcp'
        assert asyncio.run(mcp_verify.verify_http(url, confirmed=True))['state'] == 'auth_required'
        Handler.code = 200
        assert asyncio.run(mcp_verify.verify_http(url, confirmed=True))['state'] == 'error'
        Handler.payload = {'jsonrpc': '2.0', 'id': 1, 'result': {'protocolVersion': '2024-11-05', 'serverInfo': {'name': 'test', 'version': '1'}}}
        result = asyncio.run(mcp_verify.verify_http(url, confirmed=True))
        assert result['state'] == 'connected', result
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
