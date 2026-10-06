"""MCP 헬스체크 (P3-2, 40-mcp.md §3, ADR-36) — 사용자가 누르는 1회성 검증.

crush `init.go:1115-1120`이 서술한 사고를 피하는 게 이 모듈의 유일한 존재
이유다: stdio MCP 서버는 자기 자식을 또 스폰하는 경우가 흔한데(signal-mcp가
signal-cli를 띄우는 식), 기본 취소는 직접 자식만 죽이고 손자는 PPID 1로
고아가 된다 — *"production accumulated 15+ such zombies over two days."*

그래서 반드시:
  1. `start_new_session=True`로 스폰한다 — 이 프로세스가 새 세션/그룹의
     리더가 된다(우리 tmux/PTY 프로세스 그룹과도 격리된다).
  2. 검증이 끝나면(성공이든 실패든) `os.killpg`로 **그룹 전체**를 정리한다
     — 직접 자식이 아니라 그룹 전체가 핵심이다.
  3. SIGTERM으로 먼저 묻고, `SIGTERM_GRACE_SEC` 안에 안 죽으면 SIGKILL.

**타임아웃으로 강제 종료한 뒤에는 그 프로세스의 stdout/stderr를 더 읽지
않는다** — `docs/ref/agents.md`의 "Hook aggregation" 절(P3-6 조사)이 crush
`runner.go:172`에서 그대로 가져온 규율이다. 강제로 끝낸 파이프를 계속
읽으면 데이터 경쟁이다. 그래서 stderr는 프로세스가 **스스로** 끝났을
때만(EOF가 자연스럽게 왔고 `wait()`가 종료를 확인했을 때만) 건진다.

이 모듈은 결과를 저장하지 않는다(§3.4) — 호출부(라우트)가 메모리에서만
들고 있다가 응답으로 돌려준다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import signal
import time
from typing import Optional

DEFAULT_TIMEOUT_SEC = 5.0
SIGTERM_GRACE_SEC = 2.0
STDERR_DRAIN_SEC = 0.5
MAX_STDERR_CHARS = 2000

CONNECTED = "connected"
ERROR = "error"
# 값을 구하지 못해 띄우지 않았다(ADR-46). 실패가 아니다 — 에이전트 안에서는 동작할 수 있다.
UNVERIFIED = "unverified"

_PLAIN_ENV = frozenset({"HOME", "LOGNAME", "PATH", "SHELL", "USER", "LANG", "LC_ALL",
                        "TERM", "TMPDIR", "TZ", "__CF_USER_TEXT_ENCODING"})

# 97번 §2-3과 같은 원칙 — 우리가 env로 넘긴 자격증명 값은 정확히 알고 있으므로
# 문자열 치환으로 확실히 지운다. 그 외에 stderr에 우연히 찍히는(env로 넘기지
# 않은, 예를 들어 서버가 자기 설정 파일에서 직접 읽은) 값까지 잡기 위해
# 흔한 토큰 모양도 같이 마스킹한다 — 이건 안전망이지 1차 방어가 아니다.
_TOKEN_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\"?\s*[:=]\s*\"?[A-Za-z0-9_\-./+=]{8,}"),
]


def _redact(text: str, known_secrets: list[str]) -> str:
    if not text:
        return text
    for secret in known_secrets:
        if secret:
            text = text.replace(secret, "***")
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub("***", text)
    if len(text) > MAX_STDERR_CHARS:
        text = text[:MAX_STDERR_CHARS] + "…(truncated)"
    return text


async def _kill_group(proc: "asyncio.subprocess.Process") -> None:
    """그룹 전체를 정리한다. 손자까지 죽이는 게 핵심(§3.3, E15)."""
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=SIGTERM_GRACE_SEC)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await proc.wait()


def _rpc(id_: Optional[int], method: str, params: Optional[dict] = None) -> bytes:
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        msg["id"] = id_
    if params is not None:
        msg["params"] = params
    return (json.dumps(msg) + "\n").encode("utf-8")


async def _speak(proc: "asyncio.subprocess.Process") -> int:
    """initialize -> notifications/initialized -> tools/list. 도구 개수를 반환."""
    proc.stdin.write(_rpc(1, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "farshell-verify", "version": "1"},
    }))
    await proc.stdin.drain()
    line = await proc.stdout.readline()
    if not line:
        raise ConnectionError("initialize 응답 없음(EOF)")

    proc.stdin.write(_rpc(None, "notifications/initialized"))
    await proc.stdin.drain()

    proc.stdin.write(_rpc(2, "tools/list"))
    await proc.stdin.drain()
    line = await proc.stdout.readline()
    if not line:
        raise ConnectionError("tools/list 응답 없음(EOF)")
    data = json.loads(line)
    if "error" in data:
        raise ConnectionError(str(data["error"]))
    tools = (data.get("result") or {}).get("tools")
    if not isinstance(tools, list):
        raise ConnectionError("tools/list 응답 형식이 예상과 다름")
    return len(tools)


async def verify_stdio(
    command: str,
    args: Optional[list[str]] = None,
    env: Optional[dict[str, str]] = None,
    *,
    inherit: Optional[list[str]] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> dict:
    """stdio MCP 서버를 실제로 한 번 띄워 initialize+tools/list를 해본다.

    `inherit`: 우리 환경에서 물려줄 이름. None이면 전부 — 도구가 서버에 주는 환경을
    그대로 흉내 내기 위한 것이다(codex는 허용 목록만 준다, `mcp_adapters._CODEX_DEFAULT_ENV`).

    반환: `{"state", "tool_count", "error", "checked_at"}`.
    """
    checked_at = time.time()
    base = dict(os.environ) if inherit is None else {
        k: os.environ[k] for k in inherit if k in os.environ}
    known_secrets = [v for v in (env or {}).values() if v]
    if inherit is not None:
        # 허용 목록으로 물려준 값 중 경로·로캘이 아닌 것(codex `env_vars`로 넘긴 토큰)도
        # 가린다. 경로까지 가리면 "명령을 못 찾음" 같은 메시지가 읽을 수 없게 된다.
        known_secrets += [v for k, v in base.items() if v and k not in _PLAIN_ENV]
    full_env = {**base, **(env or {})}

    try:
        proc = await asyncio.create_subprocess_exec(
            command, *(args or []),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env,
            start_new_session=True,  # setsid — §3.3, 타협 불가
        )
    except FileNotFoundError:
        # E20 — 메시지에 명령 이름만. 경로가 넘어왔어도 basename만 보인다.
        return {"state": ERROR, "tool_count": None,
                "error": f"명령을 찾을 수 없음: {os.path.basename(command)}",
                "checked_at": checked_at}
    except OSError as e:
        return {"state": ERROR, "tool_count": None,
                "error": _redact(str(e), known_secrets), "checked_at": checked_at}

    try:
        tool_count = await asyncio.wait_for(_speak(proc), timeout=timeout)
        return {"state": CONNECTED, "tool_count": tool_count, "error": None,
                "checked_at": checked_at}
    except asyncio.TimeoutError:
        # E16/E17 — 그룹 kill은 finally가 한다. 여기서는 죽이는 중인
        # 프로세스의 출력을 더 안 읽는다(위 docstring의 규율).
        return {"state": ERROR, "tool_count": None,
                "error": "응답 시간 초과", "checked_at": checked_at}
    except (ConnectionError, json.JSONDecodeError) as e:
        # EOF/파싱 실패 — 프로세스가 스스로 끝났을 가능성이 높다. **그게
        # 확인될 때만**(wait()가 종료를 알려줄 때만) stderr를 건진다. 아직
        # 살아 있다면(예: stdout만 닫고 다른 일을 하는 중) 건드리지 않는다.
        stderr_text = ""
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=STDERR_DRAIN_SEC)
        if proc.returncode is not None:
            with contextlib.suppress(Exception):
                raw = await asyncio.wait_for(proc.stderr.read(), timeout=STDERR_DRAIN_SEC)
                stderr_text = raw.decode("utf-8", errors="replace").strip()
        msg = stderr_text or str(e)
        return {"state": ERROR, "tool_count": None,
                "error": _redact(msg, known_secrets), "checked_at": checked_at}
    finally:
        await _kill_group(proc)


def _net_error(e: BaseException) -> str:
    """사람이 읽는 말로. `str(URLError)`는 `<urlopen error [Errno 61] …>`라 화면에 못 낸다."""
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        return f"서버가 HTTP {e.code}로 응답했습니다"
    reason = getattr(e, "reason", e)
    if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
        return "응답 시간 초과"
    if isinstance(reason, ConnectionRefusedError):
        return "연결이 거부됐습니다 — 서버가 떠 있지 않거나 주소가 다릅니다"
    if isinstance(reason, OSError) and reason.strerror:
        return f"연결하지 못했습니다 — {reason.strerror}"
    return f"연결하지 못했습니다 — {reason}"


async def verify_http(url: str, *, confirmed: bool, timeout: float = DEFAULT_TIMEOUT_SEC) -> dict:
    """http/sse MCP 서버에 `initialize` 1회. 네트워크 행위라 명시 확인이 필수(E21)."""
    checked_at = time.time()
    if not confirmed:
        return {"state": ERROR, "tool_count": None,
                "error": "네트워크 확인이 필요합니다", "checked_at": checked_at}

    import urllib.error
    import urllib.request

    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "farshell-verify", "version": "1"},
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    )

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    def _do() -> dict:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(req, timeout=timeout) as r:
            payload = r.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                raise ValueError("응답 크기 제한")
            raw = payload.decode("utf-8", errors="replace")
        return json.loads(raw)

    try:
        data = await asyncio.to_thread(_do)
    except urllib.error.HTTPError as e:
        return {"state": "auth_required" if e.code in (401, 403) else ERROR, "tool_count": None,
                "error": f"HTTP {e.code}", "checked_at": checked_at, "evidence": "independent_probe", "runtime_state": "unknown"}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"state": ERROR, "tool_count": None, "error": _redact(_net_error(e), []),
                "checked_at": checked_at}
    except ValueError:
        return {"state": ERROR, "tool_count": None,
                "error": "initialize 응답을 JSON으로 읽을 수 없음", "checked_at": checked_at}

    if (not isinstance(data, dict) or "error" in data or data.get("id") != 1
            or not isinstance(data.get("result"), dict)
            or not data["result"].get("protocolVersion") or not isinstance(data["result"].get("serverInfo"), dict)):
        return {"state": ERROR, "tool_count": None,
                "error": "initialize 응답 형식이 올바르지 않습니다",
                "checked_at": checked_at}
    # http/sse는 initialize 성공만 확인한다 — tools/list까지 요구하면 이 검증이
    # 세션 상태(초기화 완료 통지)를 더 들고 있어야 해서 범위를 넘는다.
    return {"state": CONNECTED, "tool_count": None, "error": None, "checked_at": checked_at}
