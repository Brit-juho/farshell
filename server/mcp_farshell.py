"""`fsh mcp` — 에이전트가 FarShell 에이전트 메시지를 쓰는 MCP 서버(stdio).

Claude Code·Codex·agy처럼 MCP를 지원하는 에이전트에 등록하면 도구 3개가 생긴다:

    list_agents     이 맥과 페어링된 맥의 tmux 세션(무엇이 돌고 있나·상태)
    send_message    다른 세션의 에이전트에게 메시지(받는 쪽이 작업을 마친 뒤 전달)
    read_messages   주고받은 메시지 기록(대화 id로 거를 수 있다)

## 왜 SDK 없이 직접 쓰나
MCP stdio는 줄 단위 JSON-RPC 2.0이고 여기서 쓰는 메서드는 initialize·tools/list·
tools/call 셋뿐이다. 이것 때문에 새 의존성을 들이지 않는다(requirements-core는
터미널만 쓰는 사람도 설치한다).

## 이 프로세스가 하는 일은 로컬 FarShell 서버 호출뿐이다
권한 판단·전달 시점·기록은 전부 서버(agent_msg.py)가 한다. 여기서는 `$TMUX_PANE`을
실어 보내 서버가 "누가 보냈나"를 정하게 하고(주장하지 않는다), 결과를 에이전트가
읽을 글로 바꾼다. 인증은 기계 토큰(Bearer) — 환경변수가 없으면 `~/.vt.env`에서 읽는다.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 20.0

TOOLS = [
    {
        "name": "list_agents",
        "description": (
            "FarShell로 연결된 맥들(이 맥 + 페어링된 맥)의 tmux 세션 목록. 각 세션에서 무엇이 "
            "돌고 있는지(command)와 에이전트 상태(idle/working/waiting/done)를 보여준다. "
            "send_message의 to에 쓸 '호스트/세션' 이름을 여기서 고른다."),
        "inputSchema": {"type": "object", "properties": {
            "fresh": {"type": "boolean", "description": "원격 목록 캐시(30초)를 무시하고 새로 묻기"}},
            "additionalProperties": False},
    },
    {
        "name": "send_message",
        "description": (
            "다른 세션의 에이전트에게 메시지를 보낸다. 받는 에이전트가 지금 작업 중이면 끝난 뒤, "
            "쉬고 있으면 곧바로 그 세션에 입력된다(FarShell 프롬프트 큐). 다른 맥으로 보내려면 "
            "그 맥에서 이 맥에 control 등급과 에이전트 메시지 수신(fsh host allow-agent)을 켜 "
            "둬야 한다. 받은 메시지에 답할 때는 reply_to에 그 메시지 id를 넣는다. 한 대화는 "
            "10번까지만 오갈 수 있다."),
        "inputSchema": {"type": "object", "properties": {
            "to": {"type": "string", "description": "'세션' 또는 '호스트/세션' (예: 'dev', 'M5/api')"},
            "text": {"type": "string", "description": "보낼 내용(최대 4000자, 한 문단으로 전달된다)"},
            "reply_to": {"type": "string", "description": "답장이면 받은 메시지의 id(12자리)"}},
            "required": ["to", "text"], "additionalProperties": False},
    },
    {
        "name": "read_messages",
        "description": "주고받은 에이전트 메시지 기록(최근 순). 보낸 메시지가 전달됐는지(status)도 여기서 본다.",
        "inputSchema": {"type": "object", "properties": {
            "conv": {"type": "string", "description": "이 대화 id만"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            "additionalProperties": False},
    },
]


def _config() -> tuple[str, str]:
    env = dict(os.environ)
    try:
        import vt_env

        file_env = vt_env.load()
    except Exception:  # noqa: BLE001 — 설정 파일이 없어도 환경변수로 돈다
        file_env = {}
    port = env.get("VT_PORT") or file_env.get("VT_PORT") or "7777"
    server = env.get("VT_SERVER") or f"http://localhost:{port}"
    token = (env.get("VT_AUTH_TOKEN") or env.get("VT_TOKEN")
             or file_env.get("VT_AUTH_TOKEN") or file_env.get("VT_TOKEN") or "")
    return server.rstrip("/"), token


def _api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    server, token = _config()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(server + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"reason": f"FarShell 서버에 연결할 수 없습니다({server}) — 'fsh status'로 확인: {e}"}


def _fail(status: int, data: dict) -> str:
    return data.get("reason") or data.get("error") or f"HTTP {status}"


def tool_list_agents(args: dict) -> tuple[str, bool]:
    q = "?fresh=1" if args.get("fresh") else ""
    status, data = _api("GET", f"/api/agent/directory{q}")
    if status != 200:
        return _fail(status, data), True
    lines = []
    for h in data.get("hosts", []):
        head = f"{h['label']} (to에 쓸 이름: {'local' if h['host'] == 'local' else h['host']})"
        if not h.get("online"):
            lines.append(f"{head} — 연결 안 됨 {h.get('reason') or ''}".rstrip())
            continue
        lines.append(head)
        for s in h.get("sessions") or []:
            cmd = f" · {s['command']}" if s.get("command") else ""
            lines.append(f"  - {s['name']}{cmd} · {s.get('status', 'idle')}")
        if not h.get("sessions"):
            lines.append("  (세션 없음)")
    return "\n".join(lines) or "연결된 호스트가 없습니다", False


def tool_send_message(args: dict) -> tuple[str, bool]:
    body = {
        "to": args.get("to", ""), "text": args.get("text", ""),
        "replyTo": args.get("reply_to") or None,
        "pane": os.environ.get("TMUX_PANE"), "tmux": os.environ.get("TMUX"),
        "cwd": os.getcwd(),
    }
    status, data = _api("POST", "/api/agent/message", body)
    if status != 200 or not data.get("ok"):
        return f"보내지 못했습니다: {_fail(status, data)}", True
    when = ("받는 에이전트가 쉬고 있어 곧 입력됩니다" if data.get("immediate")
            else "받는 에이전트가 작업을 마치면 입력됩니다(상태를 모르면 사람이 수동으로 전달)")
    return (f"보냄 · id {data['id']} · 대화 {data['conv']} · {data['to']} · {when}. "
            "답장은 이 세션에 새 입력으로 도착합니다."), False


def tool_read_messages(args: dict) -> tuple[str, bool]:
    params = {}
    if args.get("conv"):
        params["conv"] = args["conv"]
    params["limit"] = int(args.get("limit") or 20)
    status, data = _api("GET", "/api/agent/messages?" + urllib.parse.urlencode(params))
    if status != 200:
        return _fail(status, data), True
    rows = []
    for m in data.get("messages", []):
        frm, to = m.get("from") or {}, m.get("to") or {}
        arrow = (f"{frm.get('hostLabel') or frm.get('host')}/{frm.get('session') or '?'} → 이 맥/{to.get('session')}"
                 if m.get("dir") == "in" else
                 f"이 맥/{frm.get('session') or '?'} → {to.get('hostLabel') or to.get('host')}/{to.get('session')}")
        reason = f" ({m['reason']})" if m.get("reason") else ""
        rows.append(f"#{m['id']} [{m.get('status')}{reason}] 대화 {m.get('conv')} · {arrow}: {m.get('text', '')}")
    return "\n".join(rows) or "기록이 없습니다", False


HANDLERS = {
    "list_agents": tool_list_agents,
    "send_message": tool_send_message,
    "read_messages": tool_read_messages,
}


def handle(req: dict) -> dict | None:
    """요청 하나 → 응답(알림이면 None)."""
    method = req.get("method")
    rid = req.get("id")
    if rid is None:
        return None  # notifications/initialized 등 — 답하지 않는다
    if method == "initialize":
        client_ver = (req.get("params") or {}).get("protocolVersion") or PROTOCOL_VERSION
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": client_ver,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "farshell", "version": "1"},
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") or {}
        fn = HANDLERS.get(params.get("name"))
        if fn is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"unknown tool: {params.get('name')}"}}
        try:
            text, is_error = fn(params.get("arguments") or {})
        except Exception as e:  # noqa: BLE001 — 도구 실패는 에이전트가 읽을 글로
            text, is_error = f"도구 실행 중 오류: {e}", True
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": text}], "isError": is_error}}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(req) if isinstance(req, dict) else None
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
