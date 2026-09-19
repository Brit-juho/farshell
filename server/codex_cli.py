"""Codex CLI의 JSON 조회를 격리하는 작은 어댑터.

MCP 인증 상태와 플러그인 설치 목록은 ``config.toml``만 읽어서는 알 수 없다.
Codex 자신이 제공하는 JSON 출력을 단일 경로로 읽되, 셸을 거치지 않고 짧은
타임아웃과 출력 크기 상한을 둔다. stderr/stdout 원문은 설정값이나 경로를
포함할 수 있으므로 오류 응답에 싣지 않는다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

MAX_JSON_BYTES = 5 * 1024 * 1024


def codex_home() -> Path:
    explicit = os.environ.get("VT_CODEX_HOME") or os.environ.get("CODEX_HOME")
    if explicit:
        return Path(explicit)
    mcp_home = os.environ.get("VT_MCP_HOME")
    if mcp_home:
        return Path(mcp_home) / ".codex"
    return Path.home() / ".codex"


def executable() -> Optional[str]:
    explicit = os.environ.get("VT_CODEX_BIN") or os.environ.get("CODEX_CLI_PATH")
    if explicit:
        return explicit
    return shutil.which("codex")


def run_json(args: list[str], *, timeout: float = 10.0) -> tuple[object | None, str | None]:
    binary = executable()
    if not binary:
        return None, "Codex CLI를 찾지 못했습니다"

    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home())
    try:
        proc = subprocess.run(
            [binary, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return None, "Codex CLI 응답 시간이 초과됐습니다"
    except OSError as exc:
        return None, f"Codex CLI 실행 실패: {exc.__class__.__name__}"

    if proc.returncode != 0:
        return None, f"Codex CLI가 종료 코드 {proc.returncode}을 반환했습니다"
    if len(proc.stdout) > MAX_JSON_BYTES:
        return None, "Codex CLI JSON 응답이 너무 큽니다"
    try:
        return json.loads(proc.stdout.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, "Codex CLI JSON을 해석하지 못했습니다"


def installed_plugin_ids() -> tuple[set[str], Optional[str]]:
    data, err = run_json(["plugin", "list", "--json"])
    if err:
        return set(), err
    if not isinstance(data, dict) or not isinstance(data.get("installed"), list):
        return set(), "Codex 플러그인 응답 형식이 예상과 다릅니다"
    return {
        str(row.get("pluginId"))
        for row in data["installed"]
        if isinstance(row, dict) and row.get("pluginId")
    }, None


def mcp_auth_statuses() -> tuple[dict[str, str], Optional[str]]:
    data, err = run_json(["mcp", "list", "--json"])
    if err:
        return {}, err
    if not isinstance(data, list):
        return {}, "Codex MCP 응답 형식이 예상과 다릅니다"
    out: dict[str, str] = {}
    for row in data:
        if not isinstance(row, dict) or not row.get("name"):
            continue
        status = row.get("auth_status")
        if isinstance(status, str) and status:
            out[str(row["name"])] = status
    return out, None
