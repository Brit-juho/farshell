"""97번 계획서 1단계 1/n — 도구별 MCP 설정 **읽기** 어댑터.

각 CLI(claude/codex/agy)가 MCP 서버를 어디에 어떤 모양으로 적어두는지는
전부 공식 문서에 나온 안정 포맷이다(97번 §0-1). 이 모듈은 그 파일들을
**읽기만** 하고 하나의 정규화된 스키마로 바꾼다. 쓰기는 다음 단계.

## 값은 절대 내보내지 않는다 (97번 §2-3)

`~/.claude.json` 하나를 읽으면 거기 적힌 **다른 모든 서버의 env·헤더 값**까지
같이 올라온다. 이 UI는 터널 너머 공개 인터넷에 노출되므로, 정규화 결과에는
**키 이름과 "값이 있다/참조다"만** 담고 값 자체는 어떤 경로로도 담지 않는다.
96번(Codex 사용량)에서 credits/balance를 막은 것과 같은 규율이고, 테스트로
고정한다.

## 참조 문법이 도구마다 다르다 (97번 §0-3)

- claude        : `${VAR}` · `${VAR:-기본값}` · `$VAR`
- codex         : **확장 없음.** `env_vars = ["VAR"]`로 이름만 적는다.
                  그래서 codex의 env에 `${VAR}`를 적으면 확장되지 않고
                  **리터럴 7글자가 그대로 서버에 전달된다** — 조용히 깨지는
                  종류라 진단으로 잡아 알려준다.
- agy           : **확장 없음**(1.2.7 격리 HOME 실측, 2026-09-23 — `${VAR}`·`$VAR`
                  둘 다 글자 그대로 전달). 대신 agy 자신의 환경을 통째로 물려준다.
                  이름만 적는 칸도 없어서 **참조를 쓸 방법이 없다**(ADR-46).
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from urllib.parse import urlparse
from pathlib import Path
from typing import Optional

import codex_cli

# `${VAR}` / `${VAR:-기본}` / `$VAR` — claude가 확장하는 형태
_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}$|^\$([A-Za-z_][A-Za-z0-9_]*)$")

# opencode 고유 문법 — `{env:VAR}`. **`${VAR}`는 opencode에서 동작하지 않는다**
# (소스 확인, §0-3). 그래서 같은 정규식으로 처리하면 opencode의 정상적인 참조가
# "리터럴 값"으로 분류돼 화면이 "키가 파일에 박혀 있다"고 거짓 경고를 한다.
_OPENCODE_REF_RE = re.compile(r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")


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


def _read_toml_text(text: str) -> tuple[Optional[dict], Optional[str]]:
    try:
        return tomllib.loads(text), None
    except tomllib.TOMLDecodeError:
        return None, "TOML 파싱 실패"


def _read_toml(path: Path) -> tuple[Optional[dict], Optional[str]]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as e:
        return None, f"읽기 실패: {e.__class__.__name__}"
    try:
        return _read_toml_text(raw.decode("utf-8"))
    except UnicodeDecodeError:
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


def _classify_ref(value, *, style: str = "dollar") -> tuple[Optional[str], bool]:
    """(참조하는 변수 이름, 리터럴 값인가).

    값 자체는 절대 돌려주지 않는다. 참조면 변수 **이름**만 돌려주는데,
    변수 이름은 시크릿이 아니라 "무엇이 필요한지"를 알려주는 정보다.

    `style`은 그 도구의 문법이다 — `dollar`(claude·codex·agy) / `brace_env`
    (opencode). 문법을 하나로 뭉치면 opencode의 정상 참조가 리터럴로 분류돼
    화면이 거짓 경고를 한다.
    """
    if not isinstance(value, str) or value == "":
        return None, False
    v = value.strip()
    if style == "brace_env":
        m = _OPENCODE_REF_RE.match(v)
        return (m.group(1), False) if m else (None, True)
    m = _REF_RE.match(v)
    if m:
        return m.group(1) or m.group(2), False
    return None, True


def _describe_fields(mapping, *, expands: Optional[bool],
                     style: str = "dollar") -> list[dict]:
    """env/headers를 [{key, ref, literal}] 로. **값은 담지 않는다.**

    `expands`는 그 도구가 `${VAR}`를 실제 값으로 바꿔주는지다.
    - True(claude)  : 참조로 인정
    - False(codex·agy) : 확장이 없으므로 리터럴로 분류하고 호출부가 경고를 붙인다
    - None          : **확인하지 못한 도구.** 참조로 보되 그 사실을 표시한다 —
                      둘 중 어느 쪽으로 단정해도 사용자에게 거짓말이 된다.
    """
    out = []
    for key, value in sorted(_as_mapping(mapping).items()):
        ref, literal = _classify_ref(value, style=style)
        if expands is False and ref is not None:
            # 확장 안 되는 도구인데 참조 모양 — 리터럴로 전달된다.
            out.append({"key": key, "ref": None, "literal": True, "dead_ref": ref})
        elif expands is None and ref is not None:
            out.append({"key": key, "ref": ref, "literal": False, "ref_unverified": True})
        else:
            out.append({"key": key, "ref": ref, "literal": literal})
    return out


def _fingerprint(defn: dict) -> str:
    """mcp_catalog.fingerprint와 **같은 계산**이어야 한다 — 저장할 때와 맞출
    때 재료가 다르면 지문이 영원히 안 맞아 키가 절대 주입되지 않는다.
    계산을 여기 두는 이유는 어댑터가 카탈로그를 import하지 않게 하기 위함이고,
    두 벌이 어긋나지 않도록 테스트가 둘을 나란히 비교한다."""
    import hashlib
    parts = [
        str(defn.get("command") or ""),
        " ".join(str(x) for x in (defn.get("args") or [])),
        str(defn.get("url") or ""),
    ]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


# P2-2(ADR-35) — MCP 스펙에서 sse는 http과 별개 전송이다(둘 다 url을 갖지만
# 프로토콜이 다르다). `type`/`transport`를 안 보고 url 유무만으로 추론하면
# `type: "sse"` 서버가 화면에 "http"로 뜬다.
def _transport(defn: dict) -> str:
    # 1) 선언이 있으면 선언이 이긴다 — 모순(예: type:"http" + command)이어도
    #    화면이 사실을 그대로 보여준다. 인식 못 하는 값(예: "websocket")도
    #    원문 그대로 싣는다 — "unknown"으로 뭉개면 정보가 있는데 없다고
    #    말하는 셈이다. `type`/`transport` 외 도구별 동의어는 아직 없다 —
    #    확인 전엔 추측으로 키를 늘리지 않는다(docs/plan-2.3/40-mcp.md §1).
    declared = str(defn.get("type") or defn.get("transport") or "").strip().lower()
    if declared:
        return declared
    # 2) 선언이 없을 때만 추론(현행 유지)
    if defn.get("url") or defn.get("httpUrl") or defn.get("serverUrl"):
        return "http"
    if defn.get("command"):
        return "stdio"
    return "unknown"


def _level(tool: str, scope: str, shared: bool) -> str:
    """user(나, 모든 프로젝트) / local(나, 이 프로젝트) / project(저장소에 커밋)."""
    if scope == "global":
        return "user"
    # 로컬 스코프 중 커밋되지 않는 곳은 Claude의 `~/.claude.json` projects 블록뿐이다.
    return "local" if tool == "claude" and not shared else "project"


def _entry(
    *,
    name: str,
    tool: str,
    scope: str,
    path: Path,
    enabled: bool,
    defn: dict,
    expands: Optional[bool],
    shared: bool = False,
    worktree_id: Optional[str] = None,
    notes: Optional[list[str]] = None,
    extra: Optional[dict] = None,
) -> dict:
    style = "brace_env" if tool == "opencode" else "dollar"
    env = _describe_fields(defn.get("env"), expands=expands, style=style)
    headers = _describe_fields(defn.get("headers"), expands=expands, style=style)
    notes = list(notes or [])

    for field in (*env, *headers):
        if field.get("dead_ref"):
            notes.append(
                f"`{field['key']}`가 `${{{field['dead_ref']}}}` 모양이지만 "
                f"{tool}는 확장하지 않는다 — 문자열 그대로 전달된다"
            )
        elif field.get("ref_unverified"):
            notes.append(
                f"`{field['key']}`가 환경변수 참조 모양인데 {tool}가 이를 확장하는지는 "
                f"확인되지 않았다 — 동작을 직접 확인할 것"
            )

    # 공유 파일(.mcp.json)에 리터럴 값이 있으면 git에 그대로 올라간다.
    if shared and any(f["literal"] for f in (*env, *headers)):
        example = "{env:VAR}" if tool == "opencode" else "${VAR}"
        notes.append(f"이 파일은 저장소에 커밋된다 — 값을 직접 적지 말고 `{example}` 참조를 쓸 것")

    # §2-5 — OAuth 토큰은 **복제 대상에서 제외**한다. 만료·갱신·audience
    # 제약이 있어 API 키처럼 다른 스코프로 복사할 수 없고, 복사해봐야 받는
    # 쪽에서 안 먹거나 조용히 만료된다. 그래서 "있다"는 사실만 표시하고
    # 가져오기 대상에서 뺀다(값은 물론 절대 안 내보낸다).
    has_oauth = bool(defn.get("oauth")) or "oauth" in _as_mapping(defn.get("auth"))
    if has_oauth:
        notes.append("OAuth를 쓰는 서버입니다 — 토큰은 다른 스코프로 복제할 수 없어 "
                     "가져오기 대상에서 제외됩니다. 대상 도구에서 직접 인증하세요")

    out = {
        "name": name,
        "tool": tool,
        "oauth": has_oauth,
        "scope": scope,
        # 화면 표시·거르기 전용. `scope`는 쓰기 경로의 기준이라 그대로 두고,
        # "누구에게 적용되는가"를 따로 싣는다 — Claude의 local(나만, 이
        # 프로젝트)과 project(`.mcp.json`, 팀 공유)가 `scope`로는 둘 다 local이다.
        "level": _level(tool, scope, shared),
        "source": str(path),
        "shared": shared,
        "worktree_id": worktree_id,
        "enabled": bool(enabled),
        "transport": _transport(defn),
        # 97번 3단계 §2-5 — 자격증명을 **이름이 아니라 검증된 대상**에 묶기
        # 위한 지문. 같은 이름으로 다른 명령/URL이 걸린 서버에 키가 자동으로
        # 흘러가는 것을 막는 유일한 근거다. 값이 아니라 command/args/url만
        # 재료로 쓰므로 이 필드가 응답에 실려도 비밀이 새지 않는다.
        "fingerprint": _fingerprint(defn),
        "env": env,
        "headers": headers,
        "notes": notes,
    }
    # ADR-48 — 검증 화면이 "이 주소로 요청을 보냅니다"를 물을 때 쓴다. **호스트 이름만**
    # 내보낸다 — URL 전체는 쿼리에 토큰이 붙는 경우가 있어 안 내보낸다.
    raw_url = defn.get("url") or defn.get("httpUrl") or defn.get("serverUrl")
    if isinstance(raw_url, str) and raw_url:
        try:
            out["url_host"] = urlparse(raw_url).hostname or None
        except ValueError:
            out["url_host"] = None
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------- claude

def claude_paths() -> dict:
    return {"global": _home() / ".claude.json"}


def claude_settings_dir() -> Path:
    """`~/.claude` — settings.json·plugins/가 있는 곳. `CLAUDE_CONFIG_DIR`을 따른다.
    테스트 격리(VT_MCP_HOME)가 켜져 있으면 그쪽이 이긴다."""
    if os.environ.get("VT_MCP_HOME"):
        return _home() / ".claude"
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else _home() / ".claude"


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

    # config.toml에는 인증 결과가 없다. Codex 자신이 내놓는 상태만 이름으로
    # 합친다. 조회 실패는 MCP 정의 자체를 숨기지 않고 진단으로만 남긴다.
    if servers:
        auth_by_name, auth_err = codex_cli.mcp_auth_statuses()
        if auth_err:
            errors.append({"source": "codex mcp list --json", "reason": auth_err})
        for server in servers:
            status = auth_by_name.get(server["name"])
            if status:
                server["auth_status"] = status
            if status == "not_logged_in":
                server["notes"].append(
                    f"인증이 필요합니다 — `codex mcp login {server['name']}`")

    return {"servers": servers, "errors": errors}


# ------------------------------------------------------------------- agy

# agy = Antigravity CLI. 설정 폴더 이름은 agy가 정한 것이라(`~/.gemini`) 그대로
# 따른다. 구글의 이전 CLI와는 **다른 제품**이고 구조도 다르다 — 2026-09-16 이 맥의 agy 1.1.27을 격리된 HOME에서
# 직접 돌려 확인한 것만 여기 적는다(사용자 실제 설정은 건드리지 않았다):
#
#   - 설정은 `~/.gemini/config/mcp_config.json` 하나. `settings.json`이 아니다.
#   - **스코프가 전역 하나뿐이다.** add/enable/disable 어디에도 스코프 플래그가
#     없고, 프로젝트 디렉토리에 설정을 두고 `agy mcp list`를 돌려도 무시된다.
#   - on/off는 정의 안의 `"disabled": true`. codex의 `enabled`와 **방향이 반대**다.
#   - `agy mcp disable` → `disabled: true` 기록, `agy mcp enable` → **키를 제거**.
#     즉 키가 없으면 켜진 것이다.
#
# 2026-09-23, agy 1.2.7에서 같은 방식(격리 HOME + env를 파일에 적는 스텁)으로 추가 확인:
#   - `agy mcp list`는 서버를 띄우지 않는다. `agy -p`는 인증 전에 서버를 띄운다.
#   - env의 `${VAR}`·`$VAR`는 **확장되지 않고** 글자 그대로 전달된다.
#   - agy 자신의 프로세스 환경은 서버에 **통째로** 물려준다.

def agy_paths() -> dict:
    return {"global": _home() / ".gemini" / "config" / "mcp_config.json"}


def scan_agy(worktree_path: Optional[str] = None, worktree_id: Optional[str] = None) -> dict:
    """agy는 전역 설정 하나만 본다 — `worktree_*`는 받기만 하고 쓰지 않는다
    (다른 어댑터와 호출 규약을 맞추기 위한 것)."""
    path = agy_paths()["global"]
    data, err = _read_json(path)
    if err:
        return {"servers": [], "errors": [{"source": str(path), "reason": err}]}
    if data is None:
        return {"servers": [], "errors": []}

    servers = []
    for name, defn in sorted(_as_mapping(data.get("mcpServers")).items()):
        if not isinstance(defn, dict):
            continue
        servers.append(_entry(
            name=name, tool="agy", scope="global", path=path,
            # 키가 없으면 켜진 것 — `agy mcp enable`이 키를 지우기 때문이다.
            enabled=defn.get("disabled", False) is not True,
            # 1.2.7 실측: 확장하지 않는다(모듈 상단 §0-3). 참조 모양은 dead_ref로 경고된다.
            defn=defn, expands=False, worktree_id=None,
        ))
    return {"servers": servers, "errors": []}


ADAPTERS = {"claude": scan_claude, "codex": scan_codex, "agy": scan_agy}


# ------------------------------------------------------------ 검증(P3-2) 전용
#
# 위 scan_*()는 **값을 절대 안 담는다**(97번 §2-3) — 화면에 나가기 때문이다.
# 검증은 정반대로 실제 명령을 스폰해야 하므로 값이 필요하다. 그래서 별도
# 경로를 둔다: `find_raw_defn`이 원본 정의(dict, 값 그대로)를 찾고,
# `resolve_launch`가 스폰 인자로 조립한다. **이 두 함수의 반환값은 절대
# API 응답에 그대로 실으면 안 된다** — `mcp_verify`에 넘기는 용도로만 쓴다.


def find_raw_defn(
    tool: str, scope: str, name: str, *,
    worktree_path: Optional[str] = None, worktree_id: Optional[str] = None,
) -> tuple[Optional[dict], Optional[Path]]:
    """이름으로 원본 정의 하나를 찾는다. scan_*()와 같은 파일을 같은 순서로 본다."""
    if tool == "claude":
        path = claude_paths()["global"]
        data, err = _read_json(path)
        if err or data is None:
            return None, path
        if scope == "global":
            defn = _as_mapping(data.get("mcpServers")).get(name)
            return (defn if isinstance(defn, dict) else None), path
        project = _claude_project_entry(data, worktree_path)
        defn = _as_mapping(project.get("mcpServers")).get(name)
        if isinstance(defn, dict):
            return defn, path
        if worktree_path:
            mcp_json = Path(worktree_path) / ".mcp.json"
            pdata, perr = _read_json(mcp_json)
            if not perr and pdata:
                defn = _as_mapping(pdata.get("mcpServers")).get(name)
                if isinstance(defn, dict):
                    return defn, mcp_json
        return None, path

    if tool == "codex":
        path = codex_paths()["global"]
        if scope == "local" and worktree_path:
            local = Path(worktree_path) / ".codex" / "config.toml"
            ldata, lerr = _read_toml(local)
            if not lerr and ldata:
                defn = _as_mapping(ldata.get("mcp_servers")).get(name)
                if isinstance(defn, dict):
                    return defn, local
            return None, local
        data, err = _read_toml(path)
        if not err and data:
            defn = _as_mapping(data.get("mcp_servers")).get(name)
            if isinstance(defn, dict):
                return defn, path
        return None, path

    if tool == "agy":
        path = agy_paths()["global"]
        data, err = _read_json(path)
        if err or data is None:
            return None, path
        defn = _as_mapping(data.get("mcpServers")).get(name)
        return (defn if isinstance(defn, dict) else None), path

    if tool == "opencode":
        path = (Path(worktree_path) / "opencode.json") if scope == "local" and worktree_path else opencode_paths()["global"]
        data, err = _read_json(path)
        if err or not data:
            return None, path
        defn = _as_mapping(data.get("mcp")).get(name)
        return (defn if isinstance(defn, dict) else None), path

    return None, None


# codex-rs `rmcp-client/src/utils.rs` (0.155.1) — codex는 stdio 서버에 자기 환경을
# **통째로 주지 않는다.** 이 목록 + CA 인증서 변수 + `env_vars`에 적힌 이름만 넘기고,
# 그 위에 `env`를 얹는다(같은 키면 `env`가 이긴다). 검증도 같은 모양으로 띄운다 —
# 우리 환경을 통째로 주면 codex 안에서는 실패할 서버가 검증에서는 통과한다.
_CODEX_DEFAULT_ENV = (
    "HOME", "LOGNAME", "PATH", "SHELL", "USER", "__CF_USER_TEXT_ENCODING",
    "LANG", "LC_ALL", "TERM", "TMPDIR", "TZ",
)
_CODEX_CA_ENV = (
    "CODEX_CA_CERTIFICATE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "GIT_SSL_CAINFO", "CARGO_HTTP_CAINFO", "PIP_CERT",
    "BUNDLE_SSL_CA_CERT", "npm_config_cafile", "NPM_CONFIG_CAFILE",
)
_REF_DEFAULT_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}$")


def resolve_launch(
    tool: str, scope: str, name: str, *,
    worktree_path: Optional[str] = None, worktree_id: Optional[str] = None,
) -> Optional[dict]:
    """검증(`mcp_verify`)에 넘길 스폰 정보. **값이 실려 있다 — 응답으로 내보내지 않는다.**

    stdio: `{"transport": "stdio", "command", "args", "env", "inherit", "missing"}`.
    - `inherit`: 우리 환경에서 물려줄 이름 목록. None이면 전부(claude·agy·opencode).
    - `missing`: 값을 구하지 못한 변수 **이름**. 비어 있지 않으면 검증하지 않는다(ADR-46).
    http/sse: `{"transport": "http"|"sse", "url"}`.
    못 찾거나 command/url이 없으면 None.
    """
    defn, _path = find_raw_defn(tool, scope, name, worktree_path=worktree_path, worktree_id=worktree_id)
    if defn is None:
        return None

    transport = _transport(defn)
    if transport in ("http", "sse"):
        url = defn.get("url") or defn.get("httpUrl") or defn.get("serverUrl")
        if not isinstance(url, str) or not url:
            return None
        return {"transport": transport, "url": url}

    command = defn.get("command")
    if not isinstance(command, str) or not command:
        return None
    args = [a for a in (defn.get("args") or []) if isinstance(a, str)]

    resolved_env: dict[str, str] = {}
    missing: list[str] = []
    inherit: Optional[list[str]] = None
    literal_env = {k: v for k, v in _as_mapping(defn.get("env")).items() if isinstance(v, str)}

    if tool == "codex":
        inherit = list(_CODEX_DEFAULT_ENV + _CODEX_CA_ENV)
        for key in _as_name_list(defn.get("env_vars")):
            # codex는 자기를 띄운 셸에서 이 이름을 찾는다. 우리 서버 환경에 없으면
            # "실패"가 아니라 "확인 불가"다 — pane 안에서는 있을 수 있다.
            if os.environ.get(key):
                inherit.append(key)
            else:
                missing.append(key)
        # codex는 env를 확장하지 않는다 — 글자 그대로가 codex의 실제 동작이다.
        resolved_env.update(literal_env)
    elif tool == "agy":
        # agy도 확장하지 않고(1.2.7 실측) 자기 환경을 통째로 물려준다 — 그대로 흉내 낸다.
        resolved_env.update(literal_env)
    else:
        style = "brace_env" if tool == "opencode" else "dollar"
        creds = None
        for key, value in literal_env.items():
            ref, literal = _classify_ref(value, style=style)
            if literal or ref is None:
                resolved_env[key] = value
                continue
            # 참조 — 우리 자격증명 저장소, 그다음 우리 환경. 값을 지어내지 않는다.
            if creds is None:
                import mcp_catalog
                creds = mcp_catalog.env_for([{"name": name, "fingerprint": _fingerprint(defn)}])
            val = creds.get(ref) or creds.get(key) or os.environ.get(ref)
            if val:
                resolved_env[key] = val
                continue
            m = _REF_DEFAULT_RE.match(value.strip()) if style == "dollar" else None
            if m:
                resolved_env[key] = m.group(1)   # `${VAR:-기본값}` — 도구도 이렇게 푼다
            else:
                missing.append(ref)

    return {"transport": "stdio", "command": command, "args": args, "env": resolved_env,
            "inherit": inherit, "missing": sorted(set(missing))}


# ------------------------------------------------------------ 참조 쓰기 (3단계)
#
# 읽기(_classify_ref)의 짝. **도구마다 문법이 다르고, 통일하면 조용히 깨진다**
# (§0-3): Codex에 `${VAR}`를 쓰면 확장 없이 리터럴 문자열 그대로 전달된다
# (codex-rs/config/src/mcp_types.rs · rmcp-client/src/utils.rs 확인 — 확장 로직
# 부재). 그래서 "하나의 문법"이라는 편한 길을 택하지 않는다.


def ref_syntax(tool: str) -> str:
    """그 도구가 설정 파일 **값 자리**에서 쓰는 참조 문법의 종류.

    - `dollar_brace` — `${VAR}`를 값 자리에 그대로 쓴다(claude)
    - `brace_env`    — `{env:VAR}` 고유 문법(opencode). **`${VAR}`는 안 먹는다**
    - `name_only`    — 값 자리에 참조를 못 쓴다. **이름만 따로 적는다**(codex)
    - `none`         — 참조를 쓸 방법이 아예 없다(agy, 1.2.7 실측). 자격증명이 있는
                       정의는 이 도구로 옮길 수 없다(`mcp_write.deploy`가 거절)
    """
    if tool == "agy":
        return "none"
    if tool == "codex":
        return "name_only"
    if tool == "opencode":
        return "brace_env"
    return "dollar_brace"


def render_ref(tool: str, env_name: str) -> Optional[str]:
    """값 자리에 넣을 문자열. `name_only` 도구는 None — 부르는 쪽이
    `env_vars` 같은 별도 칸에 이름을 넣어야 한다."""
    style = ref_syntax(tool)
    if style in ("name_only", "none"):
        return None
    if style == "brace_env":
        return "{env:%s}" % env_name
    return "${%s}" % env_name


def apply_refs(tool: str, defn: dict, mapping: dict) -> dict:
    """정의 하나에서 **값을 참조로 바꾼다**. 원본은 안 건드리고 새 dict를 준다.

    `mapping`은 `{"env": {"TOKEN": "FSH_MCP_X_TOKEN"}, "headers": {…}}` 꼴로,
    "이 칸의 값을 이 환경변수 이름으로 대체하라"는 뜻이다.

    **값을 지우는 것이 이 함수의 전부다.** 여기서 하나라도 빠뜨리면 그 값이
    그대로 CLI 설정 파일에 남고, `.mcp.json`이라면 git에 커밋된다(§2-2 —
    레퍼런스 agent-deck이 실제로 밟고 있는 경로).
    """
    if ref_syntax(tool) == "none" and any(mapping.get(s) for s in ("env", "headers")):
        # 참조를 쓸 수 없는 도구에 참조를 적으면 값 대신 `${…}` 글자가 전달된다.
        raise ValueError(f"{tool}는 환경변수 참조를 지원하지 않는다")
    out = json.loads(json.dumps(defn))   # 깊은 복사
    name_only = ref_syntax(tool) == "name_only"
    names: list[str] = []

    for section in ("env", "headers"):
        want = mapping.get(section) or {}
        if not want:
            continue
        block = out.get(section)
        if not isinstance(block, dict):
            block = {}
        for key, env_name in want.items():
            names.append(env_name)
            if name_only:
                # 값 자리에 참조를 못 쓰는 도구 — 그 칸을 **지운다.** 값을
                # 남겨두면 그게 곧 평문 유출이다.
                block.pop(key, None)
            else:
                block[key] = render_ref(tool, env_name)
        if block:
            out[section] = block
        else:
            out.pop(section, None)

    if name_only and names:
        # Codex 공식 방식 — 이름만 적고 Codex가 자기 프로세스 환경에서 읽는다.
        existing = _as_name_list(out.get("env_vars"))
        out["env_vars"] = sorted(set(existing) | set(names))
    return out


def has_literal_secret(defn: dict, *, tool: str = "claude") -> list[str]:
    """아직 값이 남아 있는 칸의 이름. **쓰기 직전의 마지막 관문이다** —
    비어 있지 않으면 그 정의를 파일에 쓰면 안 된다.

    `tool`이 필요한 이유: opencode의 정상 참조 `{env:VAR}`를 `dollar` 문법으로
    검사하면 리터럴로 잡혀 정상적인 배포가 영원히 막힌다."""
    style = "brace_env" if tool == "opencode" else "dollar"
    bad: list[str] = []
    for section in ("env", "headers"):
        block = defn.get(section)
        if not isinstance(block, dict):
            continue
        for key, value in block.items():
            ref, literal = _classify_ref(value, style=style)
            if literal:
                bad.append(f"{section}.{key}")
    return bad


# ------------------------------------------------------------- opencode (4단계)

def opencode_paths() -> dict:
    """전역 설정. `XDG_CONFIG_HOME`을 존중한다 — opencode가 그러기 때문이다."""
    xdg = os.environ.get("VT_OPENCODE_HOME") or os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else _home() / ".config"
    return {"global": base / "opencode" / "opencode.json"}


def scan_opencode(worktree_path: Optional[str] = None,
                  worktree_id: Optional[str] = None) -> dict:
    """opencode — 전역 + 프로젝트 `opencode.json`(둘은 **병합**된다).

    on/off는 `enabled: true/false`. codex의 `enabled`와 같은 방향이고
    claude의 `disabled*` 목록과는 반대다.

    ⚠ **upstream 버그가 있다**(§6 4단계): 꺼둔 서버가 조용히 다시 켜지는
    경우가 보고돼 있다. 그래서 여기서 "껐다"를 확정으로 말하지 않고 사실을
    `TOOL_FACTS`로 함께 내려보낸다 — 화면이 정직하게 말하는 쪽이 이 기능의
    값어치다(§4).
    """
    servers: list[dict] = []
    errors: list[dict] = []

    targets = [(opencode_paths()["global"], "global", False)]
    if worktree_path:
        targets.append((Path(worktree_path) / "opencode.json", "local", True))

    for path, scope, shared in targets:
        data, err = _read_json(path)
        if err:
            errors.append({"source": str(path), "reason": err})
            continue
        if data is None:
            continue
        for name, defn in sorted(_as_mapping(data.get("mcp")).items()):
            if not isinstance(defn, dict):
                continue
            servers.append(_entry(
                name=name, tool="opencode", scope=scope, path=path,
                enabled=defn.get("enabled", True) is not False,
                defn=defn,
                # `{env:VAR}`를 설정 텍스트 전체에 치환한다 — 확장한다(§0-3).
                expands=True,
                shared=shared,
                worktree_id=worktree_id if scope == "local" else None,
            ))
    return {"servers": servers, "errors": errors}


ADAPTERS["opencode"] = scan_opencode
