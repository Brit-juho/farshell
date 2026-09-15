"""97번 계획서 1단계 1/n — 도구별 MCP 설정 **읽기** 어댑터.

각 CLI(claude/codex/gemini)가 MCP 서버를 어디에 어떤 모양으로 적어두는지는
전부 공식 문서에 나온 안정 포맷이다(97번 §0-1). 이 모듈은 그 파일들을
**읽기만** 하고 하나의 정규화된 스키마로 바꾼다. 쓰기는 다음 단계.

## 값은 절대 내보내지 않는다 (97번 §2-3)

`~/.claude.json` 하나를 읽으면 거기 적힌 **다른 모든 서버의 env·헤더 값**까지
같이 올라온다. 이 UI는 터널 너머 공개 인터넷에 노출되므로, 정규화 결과에는
**키 이름과 "값이 있다/참조다"만** 담고 값 자체는 어떤 경로로도 담지 않는다.
96번(Codex 사용량)에서 credits/balance를 막은 것과 같은 규율이고, 테스트로
고정한다.

## 참조 문법이 도구마다 다르다 (97번 §0-3)

- claude/gemini : `${VAR}` · `${VAR:-기본값}` · `$VAR`
- codex         : **확장 없음.** `env_vars = ["VAR"]`로 이름만 적는다.
                  그래서 codex의 env에 `${VAR}`를 적으면 확장되지 않고
                  **리터럴 7글자가 그대로 서버에 전달된다** — 조용히 깨지는
                  종류라 진단으로 잡아 알려준다.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Optional

# `${VAR}` / `${VAR:-기본}` / `$VAR` — claude·gemini가 확장하는 형태
_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}$|^\$([A-Za-z_][A-Za-z0-9_]*)$")


def _home() -> Path:
    """홈 디렉토리. 테스트가 통째로 격리할 수 있게 VT_MCP_HOME을 먼저 본다
    (96번의 VT_CODEX_HOME과 같은 이유 — 개발자 기계의 실제 설정에 테스트
    결과가 좌우되면 안 된다)."""
    override = os.environ.get("VT_MCP_HOME")
    if override:
        return Path(override)
    return Path.home()


def _read_json(path: Path) -> tuple[Optional[dict], Optional[str]]:
    """(데이터, 오류사유). 파일이 없으면 (None, None) — 정상이다.

    파싱 실패는 (None, 사유)로 구분해 돌려준다. 이걸 뭉뚱그려 빈 dict로
    바꾸면 다음 단계(쓰기)가 "설정이 비어 있다"고 오해해 통째로 날릴 수
    있다 — agent-deck이 실제로 겪은 사고(#1956)의 시작점이 그것이었다.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"읽기 실패: {e.__class__.__name__}"
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None, "JSON 파싱 실패"
    # json.loads("null")은 조용히 None을 돌려준다 — dict가 아니면 거부한다.
    if not isinstance(data, dict):
        return None, "최상위가 객체가 아님"
    return data, None


def _read_toml(path: Path) -> tuple[Optional[dict], Optional[str]]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"읽기 실패: {e.__class__.__name__}"
    try:
        return tomllib.loads(raw.decode("utf-8")), None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None, "TOML 파싱 실패"


def _as_mapping(value) -> dict:
    """`mcpServers`가 비어 있을 때 실제 파일에는 `{}`가 아니라 `[]`로
    들어 있는 경우가 있다(이 기계의 ~/.claude.json 실측). dict가 아니면
    빈 매핑으로 본다 — 추측해서 채우지 않는다."""
    return value if isinstance(value, dict) else {}


def _as_name_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [x for x in value if isinstance(x, str)]


def _classify_ref(value) -> tuple[Optional[str], bool]:
    """(참조하는 변수 이름, 리터럴 값인가).

    값 자체는 절대 돌려주지 않는다. 참조면 변수 **이름**만 돌려주는데,
    변수 이름은 시크릿이 아니라 "무엇이 필요한지"를 알려주는 정보다.
    """
    if not isinstance(value, str) or value == "":
        return None, False
    m = _REF_RE.match(value.strip())
    if m:
        return m.group(1) or m.group(2), False
    return None, True


def _describe_fields(mapping, *, expands: bool) -> list[dict]:
    """env/headers를 [{key, ref, literal}] 로. **값은 담지 않는다.**

    expands=False(codex)면 `${VAR}` 모양이어도 확장되지 않으므로 리터럴로
    분류하고 호출부가 경고를 붙인다.
    """
    out = []
    for key, value in sorted(_as_mapping(mapping).items()):
        ref, literal = _classify_ref(value)
        if not expands and ref is not None:
            # 확장 안 되는 도구인데 참조 모양 — 리터럴로 전달된다.
            out.append({"key": key, "ref": None, "literal": True, "dead_ref": ref})
        else:
            out.append({"key": key, "ref": ref, "literal": literal})
    return out


def _transport(defn: dict) -> str:
    if defn.get("url") or defn.get("httpUrl") or defn.get("serverUrl"):
        return "http"
    if defn.get("command"):
        return "stdio"
    return "unknown"


def _entry(
    *,
    name: str,
    tool: str,
    scope: str,
    path: Path,
    enabled: bool,
    defn: dict,
    expands: bool,
    shared: bool = False,
    worktree_id: Optional[str] = None,
    notes: Optional[list[str]] = None,
    extra: Optional[dict] = None,
) -> dict:
    env = _describe_fields(defn.get("env"), expands=expands)
    headers = _describe_fields(defn.get("headers"), expands=expands)
    notes = list(notes or [])

    for field in (*env, *headers):
        if field.get("dead_ref"):
            notes.append(
                f"`{field['key']}`가 `${{{field['dead_ref']}}}` 모양이지만 "
                f"{tool}는 확장하지 않는다 — 문자열 그대로 전달된다"
            )

    # 공유 파일(.mcp.json)에 리터럴 값이 있으면 git에 그대로 올라간다.
    if shared and any(f["literal"] for f in (*env, *headers)):
        notes.append("이 파일은 저장소에 커밋된다 — 값을 직접 적지 말고 `${VAR}` 참조를 쓸 것")

    out = {
        "name": name,
        "tool": tool,
        "scope": scope,
        "source": str(path),
        "shared": shared,
        "worktree_id": worktree_id,
        "enabled": bool(enabled),
        "transport": _transport(defn),
        "env": env,
        "headers": headers,
        "notes": notes,
    }
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------- claude

def claude_paths() -> dict:
    return {"global": _home() / ".claude.json"}


def _claude_project_entry(data: dict, worktree_path: Optional[str]) -> dict:
    """~/.claude.json의 projects["<경로>"] 블록. 없으면 빈 dict."""
    if not worktree_path:
        return {}
    projects = _as_mapping(data.get("projects"))
    return _as_mapping(projects.get(worktree_path))


def scan_claude(worktree_path: Optional[str] = None, worktree_id: Optional[str] = None) -> dict:
    """Claude Code. 전역(user) + 로컬(project 공유 `.mcp.json` / 비공유
    `~/.claude.json`의 projects 블록).

    **on/off는 프로젝트별로 기록된다** — user 스코프로 정의한 서버라도
    켜고 끈 상태 자체는 프로젝트마다 따로다(97번 §0-1). 그래서 전역 서버의
    enabled도 "지금 보고 있는 워크트리 기준"으로만 말할 수 있고, 그 사실을
    `enabled_basis`로 함께 돌려준다.
    """
    path = claude_paths()["global"]
    data, err = _read_json(path)
    if err:
        return {"servers": [], "errors": [{"source": str(path), "reason": err}]}
    if data is None:
        return {"servers": [], "errors": []}

    project = _claude_project_entry(data, worktree_path)
    disabled = set(_as_name_list(project.get("disabledMcpServers")))
    json_enabled = set(_as_name_list(project.get("enabledMcpjsonServers")))
    json_disabled = set(_as_name_list(project.get("disabledMcpjsonServers")))

    basis = "per_project" if worktree_path else "unknown"
    servers = []

    # 1) 전역(user 스코프)
    for name, defn in sorted(_as_mapping(data.get("mcpServers")).items()):
        if not isinstance(defn, dict):
            continue
        notes = []
        if not worktree_path:
            notes.append("켜짐/꺼짐은 프로젝트마다 따로 기록된다 — 워크트리를 골라야 알 수 있다")
        servers.append(_entry(
            name=name, tool="claude", scope="global", path=path,
            enabled=(name not in disabled) if worktree_path else True,
            defn=defn, expands=True, worktree_id=None, notes=notes,
            extra={"enabled_basis": basis},
        ))

    # 2) 로컬 비공유 — ~/.claude.json의 projects["<경로>"].mcpServers
    for name, defn in sorted(_as_mapping(project.get("mcpServers")).items()):
        if not isinstance(defn, dict):
            continue
        servers.append(_entry(
            name=name, tool="claude", scope="local", path=path,
            enabled=name not in disabled, defn=defn, expands=True,
            shared=False, worktree_id=worktree_id,
            extra={"enabled_basis": basis},
        ))

    # 3) 로컬 공유 — <워크트리>/.mcp.json (git에 커밋되는 파일)
    if worktree_path:
        mcp_json = Path(worktree_path) / ".mcp.json"
        pdata, perr = _read_json(mcp_json)
        if perr:
            return {"servers": servers, "errors": [{"source": str(mcp_json), "reason": perr}]}
        for name, defn in sorted(_as_mapping((pdata or {}).get("mcpServers")).items()):
            if not isinstance(defn, dict):
                continue
            # `.mcp.json`은 정의만으로 켜지지 않는다 — 프로젝트별 승인이 따로 있다.
            pending = name not in json_enabled and name not in json_disabled
            enabled = (name in json_enabled) and (name not in disabled)
            notes = ["승인 대기 — 파일에 있지만 이 프로젝트에서 아직 승인되지 않았다"] if pending else []
            servers.append(_entry(
                name=name, tool="claude", scope="local", path=mcp_json,
                enabled=enabled, defn=defn, expands=True, shared=True,
                worktree_id=worktree_id, notes=notes,
                extra={"enabled_basis": basis, "pending_approval": pending},
            ))

    return {"servers": servers, "errors": []}


# ----------------------------------------------------------------- codex

def codex_paths() -> dict:
    home = os.environ.get("VT_CODEX_HOME") or os.environ.get("CODEX_HOME")
    base = Path(home) if home else _home() / ".codex"
    return {"global": base / "config.toml"}


def _codex_servers(data: dict, *, path: Path, scope: str, worktree_id, notes_common) -> list[dict]:
    out = []
    for name, defn in sorted(_as_mapping(data.get("mcp_servers")).items()):
        if not isinstance(defn, dict):
            continue
        # `env_vars`는 **이름만** 적는 공식 방식 — 시크릿이 파일에 안 남는다.
        env_vars = _as_name_list(defn.get("env_vars"))
        extra = {"env_vars": env_vars}
        bearer = defn.get("bearer_token_env_var")
        if isinstance(bearer, str) and bearer:
            extra["bearer_token_env_var"] = bearer
        out.append(_entry(
            name=name, tool="codex", scope=scope, path=path,
            enabled=defn.get("enabled", True) is not False,
            defn=defn, expands=False, worktree_id=worktree_id,
            notes=list(notes_common), extra=extra,
        ))
    return out


def codex_trusted(data: dict, worktree_path: str) -> bool:
    """`~/.codex/config.toml`의 projects."<경로>".trust_level == "trusted".

    프로젝트 스코프 설정은 신뢰된 프로젝트에서만 읽힌다(공식 문서) — 신뢰
    등록이 안 됐으면 파일에 잘 써넣어도 **적용되지 않는다.** 이걸 모르면
    "썼는데 왜 안 되지"가 된다.
    """
    projects = _as_mapping(data.get("projects"))
    entry = _as_mapping(projects.get(worktree_path))
    return entry.get("trust_level") == "trusted"


def scan_codex(worktree_path: Optional[str] = None, worktree_id: Optional[str] = None) -> dict:
    path = codex_paths()["global"]
    data, err = _read_json(path) if path.suffix == ".json" else _read_toml(path)
    errors = []
    if err:
        return {"servers": [], "errors": [{"source": str(path), "reason": err}]}
    data = data or {}

    servers = _codex_servers(data, path=path, scope="global", worktree_id=None, notes_common=[])

    if worktree_path:
        local = Path(worktree_path) / ".codex" / "config.toml"
        ldata, lerr = _read_toml(local)
        if lerr:
            errors.append({"source": str(local), "reason": lerr})
        elif ldata is not None:
            notes = []
            if not codex_trusted(data, worktree_path):
                notes.append("이 프로젝트는 codex에 신뢰 등록되지 않았다 — 프로젝트 설정이 무시된다")
            servers += _codex_servers(
                ldata, path=local, scope="local", worktree_id=worktree_id, notes_common=notes)

    return {"servers": servers, "errors": errors}


# ---------------------------------------------------------------- gemini

def gemini_paths() -> dict:
    base = _home() / ".gemini"
    return {"global": base / "settings.json", "enablement": base / "mcp-server-enablement.json"}


def _gemini_enablement() -> dict:
    """`~/.gemini/mcp-server-enablement.json`.

    이 파일의 정확한 스키마는 공식 문서에서 확인하지 못했다(이 기계엔 파일이
    아직 없다). 그래서 **모르면 꺼졌다고 말하지 않는다** — 알아볼 수 있는
    모양(이름→bool, 이름→{enabled:bool})만 해석하고 나머지는 무시한다.
    없는 상태를 "꺼짐"으로 단정하는 쪽이 훨씬 해롭다.
    """
    data, _err = _read_json(gemini_paths()["enablement"])
    out = {}
    for name, value in _as_mapping(data or {}).items():
        if isinstance(value, bool):
            out[name] = value
        elif isinstance(value, dict) and isinstance(value.get("enabled"), bool):
            out[name] = value["enabled"]
    return out


def _gemini_servers(data: dict, *, path: Path, scope: str, worktree_id, enablement) -> list[dict]:
    out = []
    for name, defn in sorted(_as_mapping(data.get("mcpServers")).items()):
        if not isinstance(defn, dict):
            continue
        out.append(_entry(
            name=name, tool="gemini", scope=scope, path=path,
            enabled=enablement.get(name, True),
            defn=defn, expands=True, worktree_id=worktree_id,
        ))
    return out


def scan_gemini(worktree_path: Optional[str] = None, worktree_id: Optional[str] = None) -> dict:
    path = gemini_paths()["global"]
    data, err = _read_json(path)
    errors = []
    if err:
        return {"servers": [], "errors": [{"source": str(path), "reason": err}]}
    enablement = _gemini_enablement()
    servers = _gemini_servers(
        data or {}, path=path, scope="global", worktree_id=None, enablement=enablement)

    if worktree_path:
        local = Path(worktree_path) / ".gemini" / "settings.json"
        ldata, lerr = _read_json(local)
        if lerr:
            errors.append({"source": str(local), "reason": lerr})
        elif ldata is not None:
            servers += _gemini_servers(
                ldata, path=local, scope="local", worktree_id=worktree_id, enablement=enablement)

    return {"servers": servers, "errors": errors}


ADAPTERS = {"claude": scan_claude, "codex": scan_codex, "gemini": scan_gemini}
