"""P3-5(60-observability.md §3) — 포트 서비스 식별 단위 테스트.

합성 lsof/ps 행으로 매처를 검증한다. 2단계(프로빙)는 로컬 스텁 서버로.
실제 외부 서비스에 붙는 테스트는 만들지 않는다.
"""

import http.server
import threading

import portdetect


def _row(cmd="", command="", port=0):
    return {"cmd": cmd, "command": command, "port": port}


def test_unknown_command_returns_none():
    assert portdetect.identify(_row(cmd="Google Chrome", command="/Applications/...")) is None


def test_ollama_matched_by_cmd():
    result = portdetect.identify(_row(cmd="ollama", command="/usr/local/bin/ollama serve"))
    assert result == {"service": "Ollama", "confidence": "cmd"}


def test_vite_matched_by_full_command():
    result = portdetect.identify(_row(cmd="node", command="node node_modules/.bin/vite"))
    assert result == {"service": "Vite", "confidence": "cmd"}


def test_uvicorn_matched_by_full_command():
    result = portdetect.identify(_row(cmd="python3.13", command="python -m uvicorn app:app"))
    assert result == {"service": "Uvicorn", "confidence": "cmd"}


def test_first_registered_matcher_wins(monkeypatch):
    monkeypatch.setattr(portdetect, "_matchers", list(portdetect._matchers))
    portdetect.register(lambda row: {"service": "Custom", "confidence": "cmd"})
    # 내장 매처(ollama)가 먼저 등록돼 있으므로 그게 이긴다.
    result = portdetect.identify(_row(cmd="ollama"))
    assert result == {"service": "Ollama", "confidence": "cmd"}
    # 내장 매처가 못 알아보는 이름은 새로 등록한 매처로 넘어간다.
    result = portdetect.identify(_row(cmd="something-else"))
    assert result == {"service": "Custom", "confidence": "cmd"}


def test_probe_is_off_by_default(monkeypatch):
    monkeypatch.delenv("VT_PORT_PROBE", raising=False)
    assert portdetect.probe_enabled() is False


def test_probe_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("VT_PORT_PROBE", "1")
    assert portdetect.probe_enabled() is True


def test_probe_skipped_when_disabled_falls_back_to_cmd(monkeypatch):
    monkeypatch.delenv("VT_PORT_PROBE", raising=False)
    calls = []
    monkeypatch.setitem(portdetect._PORT_PROBES, 11434, lambda: (calls.append(1), "Ollama")[1])
    result = portdetect.identify(_row(cmd="ollama", port=11434))
    assert result == {"service": "Ollama", "confidence": "cmd"}
    assert calls == []  # 프로브 함수 자체가 안 불렸다 — 네트워크를 안 탔다


class _StubOllamaHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"models": [{"name": "llama3"}]}')

    def log_message(self, *a):  # 테스트 출력 조용히
        pass


def test_probe_confirms_ollama_via_local_stub_server(monkeypatch):
    server = http.server.HTTPServer(("127.0.0.1", 0), _StubOllamaHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("VT_PORT_PROBE", "1")
        monkeypatch.setitem(
            portdetect._PORT_PROBES,
            11434,
            lambda: portdetect._probe_ollama(f"http://127.0.0.1:{port}/api/tags"),
        )
        result = portdetect.identify(_row(cmd="python3", port=11434))
        assert result == {"service": "Ollama", "confidence": "probe"}
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_probe_failure_falls_back_to_cmd_matching(monkeypatch):
    monkeypatch.setenv("VT_PORT_PROBE", "1")
    monkeypatch.setitem(portdetect._PORT_PROBES, 11434, lambda: None)
    result = portdetect.identify(_row(cmd="ollama", port=11434))
    assert result == {"service": "Ollama", "confidence": "cmd"}
