"""포트 서비스 식별 (P3-5, 60-observability.md §3).

포트 3000과 11434가 화면에서 구분되지 않는다 — 사용자는 명령줄을 읽고
추측해야 했다. crush `internal/discover/`의 `RegisterEnricher(name, impl)`
레지스트리를 가져온다 — 서비스 하나 추가가 매처 함수 하나다.

## 두 단계

1단계(기본, 공짜) — `portscan.py`가 이미 들고 있는 `cmd`/`command`로 라벨링한다.
네트워크를 안 탄다.

2단계(옵트인, 기본 OFF, `VT_PORT_PROBE=1`) — 알려진 포트에 HTTP 프로빙으로
확정한다. 사용자의 로컬 서비스로 우리가 요청을 보내는 것이므로 기본을 끄고,
`server/usage/ollama.py`와 같은 원칙(짧은 타임아웃, 실패는 에러가 아니라 조용히
빈 결과)을 따른다.

`confidence` 필드에 추측/확인을 싣는다 — `"cmd"`(프로세스 이름 추론) /
`"probe"`(HTTP로 확인). ADR-35(P2-2)와 같은 규율 — 화면이 둘을 다르게 그려야
하므로 섞지 않는다.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

Matcher = Callable[[dict], Optional[dict]]

PROBE_TIMEOUT_SEC = 0.3


def _cmd_text(row: dict) -> str:
    return f"{row.get('cmd', '')} {row.get('command', '')}".lower()


# (검사 함수, 서비스 이름) — 위에서부터 첫 매치를 쓴다. crush의 대상 목록
# (ollama · lmstudio · llamacpp · litellm) 중 로컬 개발에서 실제로 자주 보는
# 것들 + 우리 코드베이스에서 이미 다루는 도구들을 우선 등록했다.
_KNOWN_CMDS: list[tuple[Callable[[str], bool], str]] = [
    (lambda t: "ollama" in t, "Ollama"),
    (lambda t: "lm studio" in t or "lmstudio" in t, "LM Studio"),
    (lambda t: "llama.cpp" in t or "llama-server" in t, "llama.cpp"),
    (lambda t: "vite" in t, "Vite"),
    (lambda t: "webpack-dev-server" in t or "webpack serve" in t, "Webpack"),
    (lambda t: "next-server" in t or ("next" in t and "node" in t), "Next.js"),
    (lambda t: "nodemon" in t, "Nodemon"),
    (lambda t: "uvicorn" in t, "Uvicorn"),
    (lambda t: "gunicorn" in t, "Gunicorn"),
    (lambda t: "redis-server" in t, "Redis"),
    (lambda t: "postgres" in t, "PostgreSQL"),
    (lambda t: "mysqld" in t, "MySQL"),
    (lambda t: "mongod" in t, "MongoDB"),
]


def _match_known_cmds(row: dict) -> Optional[dict]:
    text = _cmd_text(row)
    for check, name in _KNOWN_CMDS:
        if check(text):
            return {"service": name, "confidence": "cmd"}
    return None


_matchers: list[Matcher] = [_match_known_cmds]


def register(matcher: Matcher) -> None:
    """1단계 매처를 등록한다. `(row) -> {"service", "confidence": "cmd"} | None`."""
    _matchers.append(matcher)


def _fetch_json(url: str) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_SEC) as r:
            data = json.load(r)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    return data if isinstance(data, dict) else None


def _probe_ollama(url: str = "http://localhost:11434/api/tags") -> Optional[str]:
    data = _fetch_json(url)
    return "Ollama" if data and "models" in data else None


# 포트 → 확인 함수. 등록된 포트에서만 프로빙한다 — 모든 리스닝 포트를
# 두드리면 사용자 서비스에 원치 않는 부작용을 줄 수 있다.
_PORT_PROBES: dict[int, Callable[[], Optional[str]]] = {
    11434: _probe_ollama,
}


def probe_enabled() -> bool:
    return os.environ.get("VT_PORT_PROBE", "").strip() in ("1", "true", "yes")


def identify(row: dict) -> Optional[dict]:
    """`row`(portscan 행)의 서비스 식별. 못 알아보면 None.

    2단계가 켜져 있고 이 포트에 등록된 프로버가 있으면 먼저 확인을
    시도한다(더 높은 신뢰도) — 실패하면 1단계 cmd 매칭으로 내려간다.
    """
    port = row.get("port")
    if probe_enabled() and isinstance(port, int):
        prober = _PORT_PROBES.get(port)
        if prober is not None:
            name = prober()
            if name:
                return {"service": name, "confidence": "probe"}
    for matcher in _matchers:
        result = matcher(row)
        if result:
            return result
    return None
