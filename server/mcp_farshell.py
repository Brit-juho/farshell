"""`fsh mcp` — 에이전트가 FarShell 에이전트 메시지를 쓰는 MCP 서버(stdio).

Claude Code·Codex·agy처럼 MCP를 지원하는 에이전트에 등록하면 도구가 생긴다(아래는 처음 6개):

    list_agents     이 맥과 페어링된 맥의 tmux 세션(무엇이 돌고 있나·상태)
    send_message    다른 세션의 에이전트에게 메시지(받는 쪽이 작업을 마친 뒤 전달)
    read_messages   주고받은 메시지 기록(대화 id로 거를 수 있다)

    list_shared_sessions  사용자가 에이전트에게 공유한 tmux 세션(닉네임·모드·내 권한)
    read_session          공유 세션의 화면을 읽는다(처음엔 사용자 승인이 필요)
    send_to_session       공유 세션에 한 줄 입력한다(읽기·쓰기 공유 + 별도 승인)

뒤의 셋은 「에이전트 세션 공유」다(server/agent_share.py). 에이전트는 세션을 만들지도 닫지도 못하고,
사용자가 공유한 세션만 쓸 수 있다. 승인은 사용자가 브라우저에서 한다 — 이 도구들에는 승인 수단이 없다.
호출자가 **어느 tmux 칸인지**는 `TMUX_PANE`·`TMUX`·자기 pid 를 헤더로 실어 서버가 검증한다(주장이 아니다).

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
import time
import urllib.error
import urllib.parse
import urllib.request

import fsh_client

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
            "둬야 한다. 받은 '요청'에 결과를 알릴 때는 reply_to에 그 메시지 id를 넣는다. "
            "받은 것이 '답장'이면 다시 답장하지 않는다 — 이어서 물어볼 게 있을 때만 보낸다. "
            "한 대화는 10번까지만 오갈 수 있다."),
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
    {
        "name": "list_shared_sessions",
        "description": (
            "사용자가 에이전트에게 공유한 tmux 세션 목록(닉네임·모드·상태·내 권한). "
            "공유된 세션만 쓸 수 있고, 처음 쓰면 사용자 승인이 필요하며(승인되면 같은 호출을 다시 하세요), 세션을 만들거나 닫을 수 없다."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "read_session",
        "description": (
            "공유된 세션의 화면(마지막 lines 줄, 기본 200·최대 2000, 출력은 64KB 까지)을 읽는다 — 읽기 전용. "
            "공유된 세션만 쓸 수 있고, 처음 쓰면 사용자 승인이 필요하며(승인되면 같은 호출을 다시 하세요), 세션을 만들거나 닫을 수 없다. 사용자가 거부하면 5분 동안 같은 요청을 다시 못 한다."),
        "inputSchema": {"type": "object", "properties": {
            "nickname": {"type": "string", "description": "list_shared_sessions 에서 본 닉네임"},
            "lines": {"type": "integer", "minimum": 1, "maximum": 2000}},
            "required": ["nickname"], "additionalProperties": False},
    },
    {
        "name": "send_to_session",
        "description": (
            "공유된 세션에 한 줄을 입력한다(읽기·쓰기로 공유된 세션만 — 읽기 전용 공유는 즉시 거부된다). "
            "공유된 세션만 쓸 수 있고, 처음 쓰면 사용자 승인이 필요하며(승인되면 같은 호출을 다시 하세요), 세션을 만들거나 닫을 수 없다. 읽기 승인과 쓰기 승인은 따로다. text 는 4000자 이하 한 줄이고 제어 문자·"
            "줄바꿈은 거부된다 — 실행하려면 enter 를 true 로 한다(여러 명령은 여러 번 호출). 상대 에이전트가 "
            "승인 대기·작업 중이면 거부된다."),
        "inputSchema": {"type": "object", "properties": {
            "nickname": {"type": "string"},
            "text": {"type": "string", "description": "입력할 한 줄(최대 4000자)"},
            "enter": {"type": "boolean", "description": "true 면 입력 뒤 Enter 를 눌러 실행한다(기본 false)"}},
            "required": ["nickname", "text"], "additionalProperties": False},
    },
]


DELEGATION_TOOLS = [
    {
        "name": "spawn_session",
        "description": (
            "하위 세션을 하나 만들어 작업을 맡긴다. 새 tmux 세션에 에이전트(claude 또는 codex)를 기본 권한으로 "
            "띄우고 task를 첫 지시로 넣는다. 사용자는 폰·브라우저에서 그 세션을 보고 끼어들 수 있다. "
            "결과는 나중에 이 세션의 입력으로 '[위임 결과 …]' 형태로 도착한다 — 폴링하지 말고 다른 일을 계속하라. "
            "하위 세션은 다시 하위 세션을 만들 수 없고, 열린 하위 세션은 3개까지다. 거부되면 다시 요청하지 말고 직접 처리하라."),
        "inputSchema": {"type": "object", "properties": {
            "task": {"type": "string", "description": "맡길 일. 여러 줄 가능. 혼자서 끝낼 수 있게 구체적으로"},
            "title": {"type": "string", "description": "세션 이름(영숫자·-·_ 40자 이하). 비우면 자동"},
            "agent": {"type": "string", "enum": ["claude", "codex"], "description": "비우면 이 세션과 같은 것"},
            "cwd": {"type": "string", "description": "작업 폴더 절대 경로. 비우면 이 세션의 현재 폴더"},
            "worktree": {"type": "object", "properties": {"branch": {"type": "string"}}, "required": ["branch"],
                         "description": "주면 이 세션의 HEAD에서 새 git worktree를 만들어 거기서 일한다"}},
            "required": ["task"], "additionalProperties": False},
    },
    {
        "name": "send_to_child",
        "description": "내가 만든 하위 세션에 추가 지시를 보낸다. 하위 에이전트가 작업 중이면 끝난 뒤에 들어간다.",
        "inputSchema": {"type": "object", "properties": {
            "delegation": {"type": "string"}, "text": {"type": "string"}},
            "required": ["delegation", "text"], "additionalProperties": False},
    },
    {
        "name": "close_delegation",
        "description": "내가 맡긴 위임을 닫는다. 세션은 지우지 않고 남겨 둔다(사용자가 결과를 확인한 뒤 닫는다). 열린 하위 세션 수에서 빠진다.",
        "inputSchema": {"type": "object", "properties": {"delegation": {"type": "string"}},
                        "required": ["delegation"], "additionalProperties": False},
    },
    {
        "name": "report_back",
        "description": ("이 세션이 다른 세션에게서 위임받은 작업의 결과를 보고한다. 위임 하나에 한 번만 된다. "
                        "실패했어도 outcome=failed로 보고하라."),
        "inputSchema": {"type": "object", "properties": {
            "outcome": {"type": "string", "enum": ["succeeded", "failed"]},
            "summary": {"type": "string", "description": "결과 요약(무엇을 했고, 어디에 있고, 남은 일). 4KB 넘으면 잘려서 전달된다"}},
            "required": ["outcome", "summary"], "additionalProperties": False},
    },
    {
        "name": "list_delegations",
        "description": "내가 맡긴 위임과 나에게 맡겨진 위임의 상태. 보고 전문(summary)도 여기서 읽는다.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
TOOLS = TOOLS + DELEGATION_TOOLS
AUTHZ_TOOLS = [
    {
        "name": "whoami",
        "description": (
            "FarShell 이 이 세션을 누구로 보는지와 지금 가진 권한(scope·조건)을 보여준다. 권한이 모자라 막혔을 때 먼저 확인하고, "
            "필요한 것만 request_access 로 요청하라."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "request_access",
        "description": (
            "리소스 하나에 대한 권한을 사람에게 요청한다(예: resource='session:local/<세션 key>', scopes=['session.read']). "
            "항상 '승인 대기'로 돌아오고, 사용자가 FarShell 화면에서 한 번만·이 세션 동안·거부 중에서 고른다. 승인되면 막혔던 호출을 다시 하세요. "
            "거부되면 5분 동안 같은 요청을 다시 못 한다. authz.admin·pair.manage 같은 관리 권한은 요청할 수 없다."),
        "inputSchema": {"type": "object", "properties": {
            "resource": {"type": "string", "description": "예: session:local/$2:1700000000, fs:local/<경로>/*, msg:local/<세션 key>"},
            "scopes": {"type": "array", "items": {"type": "string"}, "description": "예: ['session.read']"},
            "reason": {"type": "string", "description": "왜 필요한지 한두 문장(승인 화면에 그대로 보인다)"},
            "ttl": {"type": "integer", "minimum": 60, "description": "필요한 기간(초). 비우면 이 세션이 끝날 때까지"}},
            "required": ["resource", "scopes"], "additionalProperties": False},
    },
    {
        "name": "list_my_grants",
        "description": "내 세션에 사람이 승인해 준 권한 목록(리소스·scope·남은 횟수·만료)과 내가 낸 요청의 상태. 다른 세션의 것은 보이지 않는다.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
TOOLS = TOOLS + AUTHZ_TOOLS
LEASE_TOOLS = [
    {
        "name": "lease_acquire",
        "description": (
            "폴더(또는 glob)에 lease 를 잡아 「이 경로는 내가 쓰는 중」이라고 표시한다. 다른 세션이 겹치는 경로에 write lease 를 잡으려 하면 "
            "충돌(보유자 닉네임과 만료 시각)로 거절된다. 기본 30분, 최대 8시간이고 lease_renew 로 늘린다. 그 경로에 fs.write(읽기 lease 는 fs.read) "
            "권한이 있어야 한다 — 없으면 request_access 로 요청하라. 서버가 쓰기를 막는지(강제)는 서버 설정에 달려 있고, 막지 않는 동안은 권고다. "
            "같은 계정의 다른 프로세스는 lease 를 무시할 수 있다."),
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "description": "절대 경로 또는 glob. 예: /path/to/proj/src/*  (glob 문자가 없으면 그 폴더 전체)"},
            "mode": {"type": "string", "enum": ["write", "read"], "description": "기본 write"},
            "exclusive": {"type": "boolean", "description": "true 면 다른 세션의 겹치는 읽기 lease 도 막는다"},
            "ttl": {"type": "integer", "minimum": 1, "maximum": 28800, "description": "초. 기본 1800, 최대 28800"},
            "reason": {"type": "string", "description": "왜 잡는지 한 줄(사람 화면에만 보인다)"}},
            "required": ["path"], "additionalProperties": False},
    },
    {
        "name": "lease_renew",
        "description": "내 lease 의 남은 시간을 늘린다(만든 때부터 최대 8시간까지).",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string"}, "ttl": {"type": "integer", "minimum": 1, "maximum": 28800}},
            "required": ["id"], "additionalProperties": False},
    },
    {
        "name": "lease_release",
        "description": "내 lease 를 놓는다. 일이 끝나면 바로 놓아 다른 세션이 쓸 수 있게 하라.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}},
                        "required": ["id"], "additionalProperties": False},
    },
    {
        "name": "lease_list",
        "description": "내가 잡은 lease(상속받은 것 포함)와 남은 시간.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
TOOLS = TOOLS + LEASE_TOOLS
PAIR_TOOLS = [
    {
        "name": "pair_create",
        "description": (
            "다른 에이전트와 pair 를 만든다 — 같은 작업 묶음(세션·폴더)을 한쪽은 driver(입력·쓰기), 한쪽은 navigator(읽기·메시지)로 함께 한다. "
            "pair.manage 권한이 있어야 한다(없으면 request_access 로 사람에게 요청). 만들면 코드(8자)와 확인 문구 두 단어가 나온다 — 상대에게 전하고, "
            "상대가 pair_join 하면 사람이 한 화면에서 묶음 전체를 보고 승인한다. raw 입력 같은 위험 항목은 elevated 로 따로 요청하며 사람이 명시로 체크해야만 주어진다. "
            "driver 는 정확히 한 명이고 pair 는 2시간 놀면 끝난다."),
        "inputSchema": {"type": "object", "properties": {
            "sessions": {"type": "array", "items": {"type": "string"}, "description": "묶음에 넣을 세션 이름 또는 key"},
            "dirs": {"type": "array", "items": {"type": "string"}, "description": "묶음에 넣을 폴더(절대 경로)"},
            "role": {"type": "string", "enum": ["driver", "navigator"], "description": "내 역할(기본 driver). 상대는 반대 역할"},
            "leases": {"type": "boolean", "description": "true 면 driver 가 폴더마다 write lease 를 가진다"},
            "elevated": {"type": "array", "items": {"type": "string", "enum": ["raw_input", "session_manage", "relay", "browser_js", "browser_sensitive"]},
                         "description": "사람에게 명시 승인을 요청할 위험 항목"},
            "with": {"type": "string", "description": "합류할 세션을 이 하나로 제한(이름 또는 key)"}},
            "additionalProperties": False},
    },
    {
        "name": "pair_join",
        "description": "상대가 준 pair 코드(예: ABCD-EFGH)로 합류한다. 사람의 승인을 기다리는 상태가 되고, 결과는 pair_status 로 확인한다. 코드를 5번 틀리면 그 코드는 폐기된다.",
        "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"], "additionalProperties": False},
    },
    {
        "name": "pair_status",
        "description": "내 pair 의 상태·내 역할·세대·승인 여부. id 를 빼면 내가 속한 pair 목록. 합류한 뒤 승인 결과를 여기서 읽는다(응답을 놓쳐도 코드를 다시 받을 필요 없다).",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "additionalProperties": False},
    },
    {
        "name": "pair_handoff",
        "description": "driver 가 운전대를 넘기겠다고 제안한다. navigator 가 pair_accept 해야 바뀌며 2분 안에 수락하지 않으면 그대로다.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False},
    },
    {
        "name": "pair_accept",
        "description": "상대 driver 의 handoff 제안을 수락한다 — 내가 driver 가 되고 상대는 navigator 가 된다(세대 번호가 하나 오른다).",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False},
    },
]
TOOLS = TOOLS + PAIR_TOOLS
# 도구 → 이 중 하나라도 가진 호출자에게만 보인다(whoami 의 유효 scope 기준). 목록에 없는 도구는 늘 보인다 —
# 막히면 서버가 이유를 돌려주고, 그 도구들은 권한이 없을 때 승인 요청(202)으로 이어지기 때문이다.
TOOL_SCOPES = {
    "spawn_session": ("deleg.spawn",),
    "send_message": ("msg.send",),
    "request_access": ("authz.request",),
    "lease_acquire": ("fs.write", "fs.read"),
    "lease_renew": ("fs.write", "fs.read"),
    "lease_release": ("fs.write", "fs.read"),
    "lease_list": ("fs.write", "fs.read"),
}
PAIR_TOOL_NAMES = {t["name"] for t in PAIR_TOOLS}
_PARENT_ONLY = {"spawn_session", "send_to_child", "close_delegation"}
_CHILD_ONLY = {"report_back"}


def _config() -> tuple[str, str]:
    env = dict(os.environ)
    try:
        import vt_env

        file_env = vt_env.load()
    except Exception:  # noqa: BLE001 — 설정 파일이 없어도 환경변수로 돈다
        file_env = {}
    port = env.get("VT_PORT") or file_env.get("VT_PORT") or "7777"
    server = env.get("VT_SERVER") or f"http://localhost:{port}"
    # 보안 경계 값은 설정 파일이 환경변수보다 먼저다(AGENTS.md 계약 6). 에이전트 프로세스는 tmux 전역
    # 환경에서 낡은 토큰을 물려받는 일이 흔하다 — 토큰을 재발급해도 이미 떠 있던 세션의 환경변수가 이겨
    # 서버가 401 을 내던 것이 이유다. 설정 파일이 없는 환경(원격·CI)만 환경변수로 내려간다.
    token = (file_env.get("VT_AUTH_TOKEN") or file_env.get("VT_TOKEN")
             or env.get("VT_AUTH_TOKEN") or env.get("VT_TOKEN") or "")
    return server.rstrip("/"), token


def _identity_headers() -> dict:
    """호출자 식별 값 — 서버가 pane·소켓·부모 프로세스 체인으로 검증한다(share_identity.py).
    값이 비ASCII 일 수 있어(TMPDIR 경로) 퍼센트 인코딩한다."""
    out = {"X-Fsh-Pid": str(os.getpid())}
    for header, var in (("X-Fsh-Pane", "TMUX_PANE"), ("X-Fsh-Tmux", "TMUX")):
        val = os.environ.get(var)
        if val:
            out[header] = urllib.parse.quote(val, safe="/,:._-")
    return out


_IDENTITY_PATHS = frozenset({"/api/agent/message"})


def _api(method: str, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    """소켓 우선(fsh_client — peer pid 가 곧 신원), 소켓이 없으면 예전처럼 TCP + 토큰.
    `X-Fsh-*` 헤더는 소켓에서는 힌트일 뿐이고 TCP 에서는 share_identity 가 검증한다 — 둘 다 그대로 싣는다."""
    server, token = _config()
    if path in _IDENTITY_PATHS:        # 보낸 세션은 본문 주장이 아니라 서버가 이 값과 연결로 확인한다(VT_AUTHZ_ENFORCE=msg)
        headers = {**_identity_headers(), **(headers or {})}
    try:
        port = urllib.parse.urlsplit(server).port
    except ValueError:
        port = None
    try:
        status, raw = fsh_client.call(method, path, body=body, headers=headers, timeout=TIMEOUT, server=server,
                                      token=token, port=port or _config_port())
    except (urllib.error.URLError, OSError) as e:
        return 0, {"reason": f"FarShell 서버에 연결할 수 없습니다({server}) — 'fsh status'로 확인: {e}"}
    try:
        return status, json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return status, {}


def _config_port() -> str:
    try:
        import vt_env

        file_env = vt_env.load()
    except Exception:  # noqa: BLE001
        file_env = {}
    return os.environ.get("VT_PORT") or file_env.get("VT_PORT") or "7777"


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
    if status == 202:     # VT_AUTHZ_ENFORCE=msg — 관계 없는 세션 앞으로는 사람이 승인해야 한다(오류가 아니다)
        return (f"{data.get('reason') or '사용자 승인 대기 중 — 승인되면 같은 호출을 다시 하세요'} "
                f"(요청 {data.get('requestId')}) 사용자가 FarShell 화면에서 승인합니다."), False
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


def _share_result(status: int, data: dict, ok_fmt) -> tuple[str, bool]:
    """공유 세션 도구 공통 — 200 은 ok_fmt, 202 는 승인 대기(오류가 아니다), 나머지는 서버가 준 이유."""
    if status == 200 and data.get("state") == "ok":
        return ok_fmt(data), False
    if status == 202:
        return (f"{data.get('reason') or '사용자 승인 대기 중 — 승인되면 같은 호출을 다시 하세요'} "
                f"(요청 {data.get('requestId')}) 사용자가 FarShell 화면에서 승인합니다."), False
    return f"실패: {_fail(status, data)}", True


def tool_list_shared_sessions(args: dict) -> tuple[str, bool]:
    status, data = _api("GET", "/api/agent/shared", headers=_identity_headers())
    if status != 200:
        return f"실패: {_fail(status, data)}", True
    rows = []
    names = {"granted": "허용됨", "pending": "승인 대기", "denied": "거부됨", "none": "아직 없음",
             "unavailable": "불가(읽기 전용 공유)"}
    for s in data.get("sessions", []):
        p = s.get("permission") or {}
        cmd = f" · {s['command']}" if s.get("command") else ""
        st = f" · {s['status']}" if s.get("status") else ""
        rows.append(f"- {s['nick']} ({'읽기·쓰기' if s['mode'] == 'readwrite' else '읽기 전용'}){cmd}{st} — "
                    f"내 권한: 읽기 {names.get(p.get('read'), p.get('read'))}, 쓰기 {names.get(p.get('write'), p.get('write'))}")
    return "\n".join(rows) or "사용자가 공유한 세션이 없습니다(사용자가 FarShell 에서 세션을 공유해야 합니다)", False


def tool_read_session(args: dict) -> tuple[str, bool]:
    body = {"nickname": args.get("nickname", "")}
    if args.get("lines") is not None:
        body["lines"] = args["lines"]
    status, data = _api("POST", "/api/agent/shared/read", body, headers=_identity_headers())

    def fmt(d: dict) -> str:
        note = " (출력이 커서 앞부분이 잘렸습니다)" if d.get("truncated") else ""
        return f"[{d['nick']} · 마지막 {d['lines']}줄{note}]\n{d['text']}"

    return _share_result(status, data, fmt)


def tool_send_to_session(args: dict) -> tuple[str, bool]:
    body = {"nickname": args.get("nickname", ""), "text": args.get("text", ""), "enter": bool(args.get("enter", False))}
    status, data = _api("POST", "/api/agent/shared/send", body, headers=_identity_headers())

    def fmt(d: dict) -> str:
        return (f"{d['nick']} 에 {d['chars']}자를 입력했습니다" + (" 하고 Enter 를 눌렀습니다." if d.get("enter") else
                " (Enter 는 누르지 않았습니다). 결과는 read_session 으로 확인하세요."))

    return _share_result(status, data, fmt)


def _deleg_result(status: int, data: dict, ok_text) -> tuple[str, bool]:
    if 200 <= status < 300:
        return ok_text(data), False
    return f"실패: {_fail(status, data)}", True


def tool_spawn_session(args: dict) -> tuple[str, bool]:
    body = {k: args[k] for k in ("task", "title", "agent", "cwd", "worktree") if args.get(k)}
    status, data = _api("POST", "/api/delegations", body, headers=_identity_headers())
    return _deleg_result(status, data, lambda d: (
        f"하위 세션 '{d['child']}'을 만들었습니다(위임 {d['delegation']}, {d['agent']}, {d['cwd']}). "
        f"결과는 이 세션의 입력으로 도착합니다. 기다리며 폴링하지 말고 다른 일을 계속하세요."))


def tool_send_to_child(args: dict) -> tuple[str, bool]:
    did = str(args.get("delegation") or "")
    status, data = _api("POST", f"/api/delegations/{urllib.parse.quote(did, safe='')}/send",
                        {"text": args.get("text", "")}, headers=_identity_headers())
    return _deleg_result(status, data, lambda d: "보냈습니다. 하위 에이전트가 작업 중이면 끝난 뒤에 들어갑니다.")


def tool_close_delegation(args: dict) -> tuple[str, bool]:
    did = str(args.get("delegation") or "")
    status, data = _api("POST", f"/api/delegations/{urllib.parse.quote(did, safe='')}/close", {},
                        headers=_identity_headers())
    return _deleg_result(status, data, lambda d: "닫았습니다. 세션은 남아 있습니다.")


def tool_report_back(args: dict) -> tuple[str, bool]:
    body = {"outcome": args.get("outcome", ""), "summary": args.get("summary", "")}
    status, data = _api("POST", "/api/delegations/report", body, headers=_identity_headers())
    return _deleg_result(status, data, lambda d: "보고했습니다. 부모 세션에 전달됩니다.")


def tool_list_delegations(args: dict) -> tuple[str, bool]:
    status, data = _api("GET", "/api/delegations", headers=_identity_headers())
    if status != 200:
        return f"실패: {_fail(status, data)}", True
    rows = []
    for x in data.get("items", []):
        who = f"→ {x['child']}" if x.get("role") == "parent" else f"← {x['parent']}(나에게 맡김)"
        oc = f"/{x['outcome']}" if x.get("outcome") else ""
        rows.append(f"- {x['id']} {who} [{x['state']}{oc}] {x.get('task_preview', '')}")
        if x.get("summary"):
            rows.append(f"  보고: {x['summary']}")
    return "\n".join(rows) or "위임이 없습니다", False


def tool_whoami(args: dict) -> tuple[str, bool]:
    status, data = _api("GET", "/api/authz/whoami")
    if status != 200:
        return f"실패: {_fail(status, data)}", True
    p = data.get("principal") or {}
    lines = [f"나: {p.get('kind')} {p.get('id')} (경로 {p.get('via')})"]
    if data.get("scopes") is None:
        lines.append("유효 권한을 계산하지 못했습니다(권한 저장소 문제) — 서버 관리자에게 알리세요")
        return "\n".join(lines), False
    lines.append("유효 scope: " + (", ".join(data["scopes"]) or "없음"))
    for sc, conds in (data.get("caps") or {}).items():
        if conds:
            lines.append(f"  {sc}: " + "; ".join(f"{k}={'|'.join(v)}" for k, v in conds.items()))
    lines.append("맞는 프리셋: " + (", ".join(x["label"] for x in data.get("presets", [])) or "없음"))
    return "\n".join(lines), False


def tool_request_access(args: dict) -> tuple[str, bool]:
    body = {"resource": args.get("resource", ""), "scopes": args.get("scopes") or []}
    for k in ("reason", "ttl"):
        if args.get(k) not in (None, ""):
            body[k] = args[k]
    status, data = _api("POST", "/api/authz/requests", body, headers=_identity_headers())
    if status == 202:     # 오류가 아니다 — 사람이 승인한다 (다른 도구의 202 와 같은 문구)
        return (f"{data.get('reason') or '사용자 승인 대기 중 — 승인되면 같은 호출을 다시 하세요'} "
                f"(요청 {data.get('requestId')}) 사용자가 FarShell 화면에서 승인합니다."), False
    return f"요청하지 못했습니다: {_fail(status, data)}", True


def tool_list_my_grants(args: dict) -> tuple[str, bool]:
    status, data = _api("GET", "/api/authz/my-grants", headers=_identity_headers())
    if status != 200:
        return f"실패: {_fail(status, data)}", True
    rows = []
    for g in data.get("grants", []):
        left = f" · {g['maxUses'] - (g.get('uses') or 0)}회 남음" if g.get("maxUses") is not None else ""
        rows.append(f"- {g['id']} {g.get('resource')} : {', '.join(g.get('scopes', []))}{left}")
    s2, d2 = _api("GET", "/api/authz/requests", headers=_identity_headers())
    reqs = [f"- {r['requestId']} [{r['state']}] {r['resource']} : {', '.join(r['scopes'])}" for r in d2.get("requests", [])] if s2 == 200 else []
    out = ["승인받은 권한:"] + (rows or ["  (없음)"])
    if reqs:
        out += ["내 요청:"] + reqs
    return "\n".join(out), False


def _lease_line(l: dict) -> str:
    left = int(l.get("remaining") or 0)
    ex = ", exclusive" if l.get("exclusive") else ""
    src = " [상속]" if l.get("derivedFrom") else ""
    return f"- {l['id']} {l['glob']} ({l['mode']}{ex}) 남은 {left // 60}분{src}"


def tool_lease_acquire(args: dict) -> tuple[str, bool]:
    body = {"glob": args.get("path", ""), "mode": args.get("mode") or "write", "exclusive": bool(args.get("exclusive"))}
    for k in ("ttl", "reason"):
        if args.get(k) not in (None, ""):
            body[k] = args[k]
    status, data = _api("POST", "/api/leases", body, headers=_identity_headers())
    if status == 201:
        return "lease 를 잡았습니다:\n" + _lease_line(data["lease"]), False
    if status == 409:
        return (f"충돌: {data.get('holder')} 세션이 이 경로를 쓰는 중입니다(만료 "
                f"{time.strftime('%H:%M:%S', time.localtime(float(data['expires']))) if data.get('expires') else '?'}). 다른 경로를 쓰거나 끝나길 기다리세요."), True
    if status == 403 and data.get("need"):
        n = data["need"]
        return (f"권한이 없습니다 — {', '.join(n.get('scopes') or [])} on {n.get('resource')} 가 필요합니다. "
                f"request_access 로 사람에게 요청하세요."), True
    return f"실패: {_fail(status, data)}", True


def tool_lease_renew(args: dict) -> tuple[str, bool]:
    body = {"ttl": args["ttl"]} if args.get("ttl") else {}
    status, data = _api("POST", f"/api/leases/{urllib.parse.quote(str(args.get('id', '')), safe='')}/renew", body, headers=_identity_headers())
    if status != 200:
        return f"실패: {_fail(status, data)}", True
    return "갱신했습니다:\n" + _lease_line(data["lease"]) + (" (최대 시간에 닿았습니다)" if data["lease"].get("capped") else ""), False


def tool_lease_release(args: dict) -> tuple[str, bool]:
    status, data = _api("DELETE", f"/api/leases/{urllib.parse.quote(str(args.get('id', '')), safe='')}", headers=_identity_headers())
    if status != 200:
        return f"실패: {_fail(status, data)}", True
    return "lease 를 놓았습니다", False


def tool_lease_list(args: dict) -> tuple[str, bool]:
    status, data = _api("GET", "/api/leases", headers=_identity_headers())
    if status != 200:
        return f"실패: {_fail(status, data)}", True
    rows = [_lease_line(l) for l in data.get("leases", [])]
    note = "" if data.get("enforced") else "\n(권고 모드 — 서버가 쓰기를 막지 않습니다)"
    return ("\n".join(rows) or "lease 가 없습니다") + note, False


def _pair_line(v: dict) -> str:
    mem = ", ".join(f"{m['name'] or m['kind']}={m['role']}{'(나)' if m.get('you') else ''}" for m in v.get("members", []))
    b = v.get("bundle") or {}
    ho = v.get("handoff")
    extra = f" · handoff 제안 중(남은 {ho['expiresIn']}초)" if ho else ""
    return (f"- {v['id']} [{v['state']}] 세대 {v.get('generation', 0)} · {mem} · 세션 {len(b.get('sessions', []))}개 · 폴더 {len(b.get('dirs', []))}개"
            f" · 승인 {((v.get('approval') or {}).get('state') or '-')}{extra}")


def _pair_fail(status: int, data: dict) -> tuple[str, bool]:
    if data.get("error") == "pair_disabled":
        return "이 서버는 pair 기능이 꺼져 있습니다(VT_AUTHZ_ENFORCE 에 pair 가 필요).", True
    if status == 403 and data.get("need"):
        n = data["need"]
        return (f"권한이 없습니다 — {', '.join(n.get('scopes') or [])} on {n.get('resource')} 가 필요합니다. request_access 로 사람에게 요청하세요."), True
    return f"실패: {_fail(status, data)}", True


def tool_pair_create(args: dict) -> tuple[str, bool]:
    body = {k: args[k] for k in ("sessions", "dirs", "role", "leases", "elevated", "with") if args.get(k) not in (None, "", [])}
    status, data = _api("POST", "/api/pairs", body, headers=_identity_headers())
    if status not in (200, 201):
        return _pair_fail(status, data)
    pid = data["pair"]["id"]
    s2, c = _api("POST", f"/api/pairs/{urllib.parse.quote(pid, safe='')}/code", headers=_identity_headers())
    if s2 != 200:
        return _pair_fail(s2, c)
    return (f"pair 를 만들었습니다: {pid}\n코드: {c['code']}  확인 문구: {c['phrase']}  (5분 안에, 한 번만)\n"
            f"상대에게 코드를 전하세요. 상대가 pair_join 하면 사람이 승인함에서 확인 문구를 보고 승인합니다. 결과는 pair_status 로 확인하세요."), False


def tool_pair_join(args: dict) -> tuple[str, bool]:
    status, data = _api("POST", "/api/pairs/join", {"code": args.get("code", "")}, headers=_identity_headers())
    if status != 200:
        return _pair_fail(status, data)
    v = data["pair"]
    return (f"합류했습니다: {v['id']} — 내 역할 {v.get('role')}. 확인 문구: {v.get('phrase')} (사람이 승인할 때 이 문구가 보입니다). "
            f"승인을 기다리는 중입니다 — pair_status 로 확인하세요."), False


def tool_pair_status(args: dict) -> tuple[str, bool]:
    if args.get("id"):
        status, data = _api("GET", f"/api/pairs/{urllib.parse.quote(str(args['id']), safe='')}/status", headers=_identity_headers())
        if status != 200:
            return _pair_fail(status, data)
        v = data["pair"]
        extra = ""
        if v.get("role"):
            extra = f"\n내 역할: {v['role']}{' (운전 중)' if v.get('isDriver') else ''}"
        if v.get("endReason"):
            extra += f"\n끝난 이유: {v['endReason']}"
        return _pair_line(v) + extra, False
    status, data = _api("GET", "/api/pairs", headers=_identity_headers())
    if status != 200:
        return _pair_fail(status, data)
    return "\n".join(_pair_line(v) for v in data.get("pairs", [])) or "pair 가 없습니다", False


def _pair_post(path: str, args: dict, done: str) -> tuple[str, bool]:
    status, data = _api("POST", f"/api/pairs/{urllib.parse.quote(str(args.get('id', '')), safe='')}/{path}", headers=_identity_headers())
    if status != 200:
        return _pair_fail(status, data)
    return done + "\n" + _pair_line(data["pair"]), False


def tool_pair_handoff(args: dict) -> tuple[str, bool]:
    return _pair_post("handoff", args, "handoff 를 제안했습니다 — 상대가 pair_accept 하면 바뀝니다(2분 안에).")


def tool_pair_accept(args: dict) -> tuple[str, bool]:
    return _pair_post("handoff/accept", args, "수락했습니다 — 이제 내가 driver 입니다.")


def _caller_whoami() -> dict | None:
    status, data = _api("GET", "/api/authz/whoami", headers=_identity_headers())
    return data if status == 200 and isinstance(data, dict) else None


def _caller_scopes() -> set | None:
    """`whoami` 의 유효 scope. 소켓이 아니거나 서버가 계산하지 못했으면 None(= 거르지 않는다)."""
    data = _caller_whoami()
    if data is None or not isinstance(data.get("scopes"), list):
        return None
    return set(data["scopes"])


def tools_for_caller() -> list[dict]:
    """깊이 한도에 닿은 하위 세션에는 만들기 도구를 아예 보여주지 않는다(서버도 막는다). 그리고 `TOOL_SCOPES` 에 적힌 도구는
    그 scope 를 하나도 못 가진 호출자에게 숨긴다(whoami 의 유효 scope 기준 — 보여 줘도 서버가 거부할 도구를 에이전트에게 권하지 않는다)."""
    who = _caller_whoami()
    scopes = set(who["scopes"]) if who is not None and isinstance(who.get("scopes"), list) else None
    keep = TOOLS if scopes is None else [t for t in TOOLS if t["name"] not in TOOL_SCOPES or scopes & set(TOOL_SCOPES[t["name"]])]
    # pair 도구는 서버가 pair 기능을 켰다고 whoami 가 알려 줄 때만(unix socket) 보인다. 만들기는 pair.manage 도 있어야 한다.
    pair_on = bool(((who or {}).get("features") or {}).get("pair"))
    keep = [t for t in keep if t["name"] not in PAIR_TOOL_NAMES or (pair_on and (t["name"] != "pair_create" or "pair.manage" in (scopes or ())))]
    status, data = _api("GET", "/api/delegations/role", headers=_identity_headers())
    if status != 200:
        return keep
    hide = set()
    if data.get("is_child"):
        hide |= _PARENT_ONLY
    else:
        hide |= _CHILD_ONLY
        if not data.get("can_spawn"):
            hide.add("spawn_session")
    return [t for t in keep if t["name"] not in hide]


HANDLERS = {
    "spawn_session": tool_spawn_session,
    "send_to_child": tool_send_to_child,
    "close_delegation": tool_close_delegation,
    "report_back": tool_report_back,
    "list_delegations": tool_list_delegations,
    "list_shared_sessions": tool_list_shared_sessions,
    "read_session": tool_read_session,
    "send_to_session": tool_send_to_session,
    "list_agents": tool_list_agents,
    "send_message": tool_send_message,
    "read_messages": tool_read_messages,
    "whoami": tool_whoami,
    "request_access": tool_request_access,
    "list_my_grants": tool_list_my_grants,
    "lease_acquire": tool_lease_acquire,
    "lease_renew": tool_lease_renew,
    "lease_release": tool_lease_release,
    "lease_list": tool_lease_list,
    "pair_create": tool_pair_create,
    "pair_join": tool_pair_join,
    "pair_status": tool_pair_status,
    "pair_handoff": tool_pair_handoff,
    "pair_accept": tool_pair_accept,
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
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools_for_caller()}}
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
