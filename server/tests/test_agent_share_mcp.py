"""에이전트 세션 공유 — MCP 도구 면(server/mcp_farshell.py). 서버 쪽 규칙은 test_agent_share.py."""

from __future__ import annotations

import os

# 같은 픽스처(격리 tmux·로그인 세션·기계 토큰 클라이언트)를 쓴다.
from test_agent_share import app_auth, approve, env, setup_pair, web  # noqa: F401


# ── 10. MCP 도구 ──────────────────────────────────────────────────────────────────────


def test_mcp_share_tools_listed_with_the_required_description():
    import mcp_farshell as m

    tools = {t["name"]: t for t in m.TOOLS}
    for name in ("list_shared_sessions", "read_session", "send_to_session"):
        d = tools[name]["description"]
        assert "공유된 세션만 쓸 수 있고" in d and "사용자 승인이 필요" in d and "같은 호출을 다시" in d
        assert "세션을 만들거나 닫을 수 없다" in d
    assert tools["read_session"]["inputSchema"]["required"] == ["nickname"]
    assert set(tools["send_to_session"]["inputSchema"]["properties"]) == {"nickname", "text", "enter"}
    assert "승인" not in "".join(k for k in m.HANDLERS)            # 승인 도구는 없다


def test_mcp_sends_caller_identity_and_formats_pending(monkeypatch):
    import mcp_farshell as m

    seen = {}

    def fake_api(method, path, body=None, headers=None):
        seen.update(method=method, path=path, body=body, headers=headers)
        return 202, {"state": "pending", "requestId": "abc123", "reason": "사용자 승인 대기 중(읽기) — 승인되면 같은 호출을 다시 하세요"}

    monkeypatch.setattr(m, "_api", fake_api)
    monkeypatch.setenv("TMUX_PANE", "%7")
    monkeypatch.setenv("TMUX", "/tmp/tmux-501/fsh,12,0")
    r = m.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "read_session", "arguments": {"nickname": "dev", "lines": 50}}})["result"]
    assert r["isError"] is False and "승인되면 같은 호출을 다시 하세요" in r["content"][0]["text"]
    assert seen["path"] == "/api/agent/shared/read" and seen["body"] == {"nickname": "dev", "lines": 50}
    assert seen["headers"]["X-Fsh-Pane"] == "%257" and seen["headers"]["X-Fsh-Pid"] == str(os.getpid())
    assert seen["headers"]["X-Fsh-Tmux"] == "/tmp/tmux-501/fsh,12,0"
    monkeypatch.setattr(m, "_api", lambda *a, **k: (403, {"error": "read_only_share", "reason": "이 공유는 읽기 전용입니다"}))
    r = m.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "send_to_session", "arguments": {"nickname": "dev", "text": "ls"}}})["result"]
    assert r["isError"] is True and "읽기 전용" in r["content"][0]["text"]


def test_mcp_end_to_end_through_the_real_http_app(web, monkeypatch):
    """MCP 의 도구 함수를 ASGI 앱에 직접 연결해(소켓 없이) 처음 호출 → 대기 → 승인 → 성공."""
    import mcp_farshell as m

    human, agent, env = setup_pair(web)
    r = env.row("claude-a")
    monkeypatch.setenv("TMUX_PANE", r.pane_id)
    monkeypatch.setenv("TMUX", f"/private/tmp/tmux-501/{env.sock},1,0")
    monkeypatch.setattr(os, "getpid", lambda: r.pane_pid)               # 이 테스트 프로세스가 그 칸의 프로세스인 척

    def via_app(method, path, body=None, headers=None):
        resp = agent.request(method, path, json=body, headers=headers or {})
        return resp.status_code, resp.json()

    monkeypatch.setattr(m, "_api", via_app)
    text, err = m.tool_read_session({"nickname": "shell"})
    assert not err and "승인되면 같은 호출을 다시" in text
    approve(human, human.get("/api/agent/share/requests").json()["requests"][0]["id"])
    text, err = m.tool_read_session({"nickname": "shell", "lines": 5})
    assert not err and "ready-marker" in text
    text, err = m.tool_list_shared_sessions({})
    assert "shell" in text and "읽기 허용됨" in text
