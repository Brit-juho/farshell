"""97번 계획서 1단계 2/n — MCP 서버를 스코프 하나에서 켜고 끈다(쓰기).

읽기(mcp_adapters)와 달리 여기서는 **남의 설정 파일을 고친다.** `~/.claude.json`은
Claude Code 설정 전체가 든 파일이고, 실수하면 사용자의 작업 환경이 통째로
날아간다 — 레퍼런스 agent-deck이 실제로 그 사고를 냈다(#1956: 파싱 실패를 빈
맵으로 대체해 저장 → 설정 전체 증발). 그래서 이 모듈의 대부분은 기능이 아니라
방어다.

## 쓰기 절차 (97번 §3-1)

    재읽기 → 외부 변경 충돌 검사 → 최소 수정 → 검증 → 원자적 교체 → 재읽기

**협조하지 않는 외부 작성자와 경쟁한다는 사실은 없앨 수 없다.** `~/.claude.json`엔
Claude Code 자신도 쓰고, flock은 협조적 락이라 상대가 지키지 않으면 아무것도
막아주지 못한다. 그래서 "막았다"가 아니라 **"어긋나면 알아챈다"**로 설계한다 —
교체 직전에 mtime·크기를 다시 확인하고, 교체 뒤 다시 읽어 의도한 상태인지
확인한다.

## 결과는 세 가지다 (97번 §3-3)

`ok` / `failed` / **`unknown`**. 썼는데 확인에 실패한 경우를 성공이라 말하면
안 되고, 실패라 말해도 안 된다(이미 바뀌었을 수 있다). 재시도는 목표 상태를
지정하는 형태(`enabled=False`)라 **멱등**하므로 unknown이면 그냥 다시 부르면 된다.

## 경로는 클라이언트에서 받지 않는다

로컬 스코프 대상은 `worktree_id`로만 지정한다. 실제 경로는 `worktree`가 이미
`fsguard.resolve_under_roots`를 통과시킨 것만 돌려주므로, 임의 경로를 받아
쓰는 길 자체를 만들지 않는다.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import mcp_adapters
import mcp_scan
import codex_cli

TOOLS = ("claude", "codex", "agy", "opencode")


class WriteRefused(Exception):
    """쓰기를 시작하지 않았다 — 파일은 그대로다."""


class WriteUnverified(Exception):
    """썼지만 확인하지 못했다 — 바뀌었을 수도, 아닐 수도 있다."""


# ------------------------------------------------------------------ 잠금

def _lock_dir() -> Path:
    d = Path(os.environ.get("VT_STATE_DIR") or (Path.home() / ".vt")) / "locks"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


@contextmanager
def _locked(target: Path):
    """대상 파일 하나에 대한 잠금. 잠금 파일은 사용자의 설정 디렉토리를
    더럽히지 않도록 `~/.vt/locks/` 아래에 둔다."""
    key = hashlib.sha1(str(target).encode()).hexdigest()[:16]
    fd = os.open(str(_lock_dir() / f"{key}.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# -------------------------------------------------------- 원자적 교체·검증

def _stamp(path: Path) -> Optional[tuple]:
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except FileNotFoundError:
        return None


def _atomic_write(path: Path, text: str) -> None:
    """같은 디렉토리에 임시 파일로 쓰고 rename. 기존 파일이 있으면 권한을
    그대로 물려받는다(0644인 `.mcp.json`을 0600으로 바꿔버리면 그것도 남의
    설정을 건드리는 것이다)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        mode = 0o600
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".vt-mcp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _guard_minimal_change(before: dict, after: dict, touched: str,
                          *, allow_new_key: bool = False) -> None:
    """최상위 키가 사라지거나, 손대지 않기로 한 키의 값이 바뀌면 거부한다.

    "키는 있는데 값이 통째로 빈 경우"까지 잡으려면 키 집합 비교만으로는
    부족하다 — 손대지 않은 키는 **값까지 그대로**인지 본다.

    `allow_new_key`는 가져오기(deploy) 전용이다. 토글은 이미 있는 항목을
    뒤집는 일이라 최상위 키가 늘 이유가 없지만, 정의를 처음 심을 때는
    `mcpServers`가 아예 없을 수 있다. **그때도 늘어나는 키는 손대기로 한
    그 하나뿐**이어야 한다 — 그래서 기본값은 끈 채로 두고 여기서만 연다.
    """
    lost = sorted(set(before) - set(after))
    if lost:
        raise WriteRefused(f"최상위 키가 사라진다: {lost}")
    added = sorted(set(after) - set(before))
    if added and not (allow_new_key and added == [touched]):
        raise WriteRefused(f"최상위 키가 늘어난다: {added}")
    for key in before:
        if key == touched:
            continue
        if before[key] != after[key]:
            raise WriteRefused(f"손대지 않아야 할 키가 바뀐다: {key}")


# ------------------------------------------------------------ JSON 공통

def _load_json_for_write(path: Path) -> dict:
    data, err = mcp_adapters._read_json(path)
    if err:
        # 못 읽은 파일을 빈 설정으로 간주하고 쓰면 그 순간 전부 날아간다.
        raise WriteRefused(f"{path.name}: {err} — 쓰기를 중단했다")
    return {} if data is None else data


def _commit_json(path: Path, before: dict, after: dict, touched: str, verify,
                 *, allow_new_key: bool = False) -> dict:
    _guard_minimal_change(before, after, touched, allow_new_key=allow_new_key)
    stamp = _stamp(path)
    text = json.dumps(after, indent=2, ensure_ascii=False) + "\n"

    if _stamp(path) != stamp:
        raise WriteRefused("쓰는 사이 외부에서 파일이 바뀌었다 — 다시 시도할 것")
    _atomic_write(path, text)

    # 재읽기 — 우리가 의도한 상태가 실제로 파일에 있는지.
    reread, err = mcp_adapters._read_json(path)
    if err or reread is None:
        raise WriteUnverified(f"쓴 뒤 다시 읽지 못했다: {err or '파일 없음'}")
    if not verify(reread):
        raise WriteUnverified("쓴 내용이 다시 읽은 파일과 다르다")
    return {"status": "ok", "changed": True}


def _set_membership(container: dict, key: str, name: str, present: bool) -> None:
    """이름 목록에 넣거나 뺀다. 목표 상태를 지정하므로 멱등이다."""
    current = [x for x in mcp_adapters._as_name_list(container.get(key))]
    if present and name not in current:
        current.append(name)
    elif not present and name in current:
        current = [x for x in current if x != name]
    if current or key in container:
        container[key] = current


# ----------------------------------------------------------------- claude

def _claude_set(name: str, enabled: bool, *, worktree_path: Optional[str], shared: bool) -> dict:
    """Claude의 on/off는 **프로젝트별로** 기록된다(97번 §0-1) — 전역 스코프로
    정의한 서버라도 끄고 켠 상태는 프로젝트마다 따로다. 그래서 워크트리를
    모르면 토글 자체가 불가능하다."""
    if not worktree_path:
        raise WriteRefused("claude는 켜짐/꺼짐을 프로젝트별로 기록한다 — 워크트리를 지정해야 한다")

    path = mcp_adapters.claude_paths()["global"]
    with _locked(path):
        before = _load_json_for_write(path)
        after = json.loads(json.dumps(before))  # 깊은 복사

        projects = after.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise WriteRefused("projects가 객체가 아니다 — 쓰기를 중단했다")
        entry = projects.setdefault(worktree_path, {})
        if not isinstance(entry, dict):
            raise WriteRefused("projects 항목이 객체가 아니다 — 쓰기를 중단했다")

        if shared:
            # `.mcp.json`이 선언한 서버는 프로젝트별 **승인** 목록으로 다룬다.
            _set_membership(entry, "enabledMcpjsonServers", name, enabled)
            _set_membership(entry, "disabledMcpjsonServers", name, not enabled)
        else:
            _set_membership(entry, "disabledMcpServers", name, not enabled)

        if before == after:
            return {"status": "ok", "changed": False}

        def verify(d):
            e = (d.get("projects") or {}).get(worktree_path) or {}
            if shared:
                return (name in mcp_adapters._as_name_list(e.get("enabledMcpjsonServers"))) == enabled
            return (name in mcp_adapters._as_name_list(e.get("disabledMcpServers"))) != enabled

        return _commit_json(path, before, after, "projects", verify)


# -------------------------------------------------------------------- agy

def _agy_set(name: str, enabled: bool) -> dict:
    """agy는 정의 안의 `disabled` 필드를 쓴다. `agy mcp enable`이 키를
    **지우는** 동작이라 우리도 그대로 맞춘다(2026-09-16 실측)."""
    path = mcp_adapters.agy_paths()["global"]
    with _locked(path):
        before = _load_json_for_write(path)
        after = json.loads(json.dumps(before))

        servers = after.get("mcpServers")
        if not isinstance(servers, dict) or name not in servers:
            raise WriteRefused(f"agy에 `{name}` 서버 정의가 없다")
        defn = servers[name]
        if not isinstance(defn, dict):
            raise WriteRefused(f"`{name}` 정의가 객체가 아니다 — 쓰기를 중단했다")

        if enabled:
            defn.pop("disabled", None)
        else:
            defn["disabled"] = True

        if before == after:
            return {"status": "ok", "changed": False}

        def verify(d):
            got = (d.get("mcpServers") or {}).get(name) or {}
            return (got.get("disabled", False) is not True) == enabled

        return _commit_json(path, before, after, "mcpServers", verify)


# ------------------------------------------------------------------ codex

_TOML_TABLE_RE = re.compile(r"^\s*\[")


def _codex_header_re(name: str, table: str = "mcp_servers") -> re.Pattern:
    """`[table.name]` 과 `[table."name"]` 둘 다 받는다."""
    esc = re.escape(name)
    prefix = re.escape(table)
    return re.compile(rf'^\s*\[{prefix}\.(?:{esc}|"{esc}")\]\s*$')


def toml_set_enabled(text: str, name: str, enabled: bool,
                     *, table: str = "mcp_servers") -> tuple[str, bool]:
    """TOML **텍스트 수술** — 구조체로 파싱해 재직렬화하지 않는다.

    파이썬 `tomllib`은 읽기 전용이라 되돌려 쓰려면 직접 직렬화해야 하는데,
    그러면 사용자가 손으로 적은 주석·정렬·따옴표 스타일이 전부 날아간다.
    남의 설정 파일에 그런 짓을 하면 안 되므로 `enabled` 한 줄만 건드린다.

    반환 (새 텍스트, 서버를 찾았는가).
    """
    lines = text.splitlines(keepends=True)
    header = _codex_header_re(name, table)
    start = None
    for i, line in enumerate(lines):
        if header.match(line):
            start = i
            break
    if start is None:
        return text, False

    # 이 서버의 테이블은 다음 `[`가 나오기 전까지다. 하위 테이블
    # (`[mcp_servers.name.env]`)도 새 `[`이므로 자연히 경계가 된다.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _TOML_TABLE_RE.match(lines[i]):
            end = i
            break

    value = "true" if enabled else "false"
    for i in range(start + 1, end):
        if re.match(r"^\s*enabled\s*=", lines[i]):
            lines[i] = re.sub(r"=\s*\S+", f"= {value}", lines[i], count=1)
            return "".join(lines), True

    # 없으면 헤더 바로 다음 줄에 넣는다(하위 테이블 안으로 들어가지 않게).
    lines.insert(start + 1, f"enabled = {value}\n")
    return "".join(lines), True


def _codex_set(name: str, enabled: bool, *, scope: str, worktree_path: Optional[str]) -> dict:
    if scope == "local":
        if not worktree_path:
            raise WriteRefused("로컬 스코프는 워크트리를 지정해야 한다")
        path = Path(worktree_path) / ".codex" / "config.toml"
    else:
        path = mcp_adapters.codex_paths()["global"]

    with _locked(path):
        raw, err = mcp_adapters._read_toml(path)
        if err:
            raise WriteRefused(f"{path.name}: {err} — 쓰기를 중단했다")
        if raw is None:
            raise WriteRefused(f"{path} 가 없다")
        if (raw.get("mcp_servers") or {}).get(name) is None:
            raise WriteRefused(f"codex({scope})에 `{name}` 서버 정의가 없다")

        text = path.read_text(encoding="utf-8")
        new_text, found = toml_set_enabled(text, name, enabled)
        if not found:
            # 파싱으로는 보이는데 텍스트에서 못 찾았다 —
            # 형태를 모르는 파일이므로 추측해서 고치지 않는다.
            raise WriteRefused(f"`{name}` 테이블을 텍스트에서 찾지 못했다 — 쓰기를 중단했다")
        if new_text == text:
            return {"status": "ok", "changed": False}

        # 수술 결과가 여전히 올바른 TOML이고, `enabled` 말고는 안 바뀌었는지.
        parsed, perr = mcp_adapters._read_toml_text(new_text)
        if perr:
            raise WriteRefused(f"수정 결과가 올바른 TOML이 아니다: {perr}")
        expected = json.loads(json.dumps(raw, default=str))
        expected.setdefault("mcp_servers", {}).setdefault(name, {})["enabled"] = enabled
        if json.loads(json.dumps(parsed, default=str)) != expected:
            raise WriteRefused("수정이 `enabled` 말고 다른 곳까지 바꾼다 — 쓰기를 중단했다")

        stamp = _stamp(path)
        if _stamp(path) != stamp:
            raise WriteRefused("쓰는 사이 외부에서 파일이 바뀌었다 — 다시 시도할 것")
        _atomic_write(path, new_text)

        reread, rerr = mcp_adapters._read_toml(path)
        if rerr or reread is None:
            raise WriteUnverified(f"쓴 뒤 다시 읽지 못했다: {rerr or '파일 없음'}")
        got = (reread.get("mcp_servers") or {}).get(name) or {}
        if (got.get("enabled", True) is not False) != enabled:
            raise WriteUnverified("쓴 내용이 다시 읽은 파일과 다르다")
        return {"status": "ok", "changed": True}


# ------------------------------------------------------------------- 공개

def set_enabled(
    tool: str,
    name: str,
    enabled: bool,
    *,
    scope: str = "global",
    worktree_id: Optional[str] = None,
    shared: bool = False,
) -> dict:
    """서버 하나를 스코프 하나에서 켜거나 끈다.

    **목표 상태를 지정한다**(토글이 아니다) — 부분 실패 뒤 그대로 다시 불러도
    안전하다. 결과는 `{"status": "ok"|"failed"|"unknown", ...}`.
    """
    if tool not in TOOLS:
        return {"status": "failed", "reason": f"모르는 도구: {tool}", "changed": False}

    wt = mcp_scan.find_worktree(worktree_id)
    if worktree_id and wt is None:
        return {"status": "failed", "reason": "워크트리를 찾지 못했다", "changed": False}
    wt_path = wt.get("path") if wt else None

    try:
        if tool == "claude":
            out = _claude_set(name, enabled, worktree_path=wt_path, shared=shared)
        elif tool == "opencode":
            out = _opencode_set(name, enabled, scope=scope, worktree_path=wt_path)
        elif tool == "agy":
            if scope == "local":
                raise WriteRefused("agy는 전역 설정 하나만 쓴다 — 로컬 스코프가 없다")
            out = _agy_set(name, enabled)
        else:
            out = _codex_set(name, enabled, scope=scope, worktree_path=wt_path)
    except WriteRefused as e:
        return {"status": "failed", "reason": str(e), "changed": False}
    except WriteUnverified as e:
        # 이미 바뀌었을 수 있다 — 성공이라고도, 실패라고도 말하지 않는다.
        return {"status": "unknown", "reason": str(e), "changed": None}
    except OSError as e:
        return {"status": "failed", "reason": f"파일 오류: {e.__class__.__name__}", "changed": False}

    out.setdefault("reason", None)
    return out


# ------------------------------------------------------------------ 그룹

def apply_group(
    tag: str,
    enabled: bool,
    *,
    worktree_id: Optional[str] = None,
) -> dict:
    """태그가 붙은 서버 전부를 **목표 상태로 맞춘다**(97번 §1-3, 2단계).

    "뒤집기"가 아니라 "전부 on으로 맞추기"인 것이 핵심이다. 그래서 일부가
    실패한 뒤 사용자가 같은 버튼을 다시 눌러도 안전하고(이미 맞은 것은 아무
    일도 안 한다), 섞인 상태에서 눌러도 결과가 결정적이다. 멱등성이 별도
    장치 없이 정의에서 따라 나온다.

    **이미 목표 상태인 항목은 파일을 아예 열지 않는다.** 남의 설정 파일을
    건드리는 횟수가 그만큼 줄고(§3의 모든 위험이 거기서 나온다), 실패할 수
    있는 지점도 줄어든다.

    결과의 status는 항목별 결과를 합친 것이다:
      ok      — 전부 ok(또는 이미 맞아서 건드릴 필요 없었다)
      partial — 일부만 성공. **어느 것이 실패했는지 `results`에 그대로 담는다**
      unknown — 실패는 없는데 확인 못 한 게 있다
      failed  — 하나도 못 바꿨다
    """
    import mcp_catalog

    tags_map = mcp_catalog.get_tags()
    names = set(mcp_catalog.members(tag, tags_map))
    if not names:
        return {"status": "failed", "reason": "그 태그가 붙은 서버가 없다",
                "tag": tag, "results": [], "changed": 0}

    scan = mcp_scan.scan(worktree_id)
    targets = [s for s in scan["servers"] if s["name"] in names]
    if not targets:
        # 태그는 있는데 지금 이 워크트리 기준으로는 아무 항목도 안 보인다.
        # 실패가 아니라 "여기엔 없다"이므로 그대로 말한다.
        return {"status": "ok", "reason": "이 워크트리에서 보이는 항목이 없다",
                "tag": tag, "results": [], "changed": 0}

    results: list[dict] = []
    for s in targets:
        entry = {
            "name": s["name"], "tool": s["tool"], "scope": s["scope"],
            "shared": bool(s.get("shared")),
        }
        if bool(s.get("enabled")) == enabled:
            results.append({**entry, "status": "ok", "changed": False, "reason": None,
                            "skipped": True})
            continue
        out = set_enabled(
            s["tool"], s["name"], enabled,
            scope=s["scope"],
            worktree_id=s.get("worktree_id") or worktree_id,
            shared=bool(s.get("shared")),
        )
        results.append({**entry, **out, "skipped": False})

    statuses = [r["status"] for r in results if not r.get("skipped")]
    changed = sum(1 for r in results if r.get("changed"))
    if not statuses:
        status = "ok"                       # 전부 이미 맞아 있었다
    elif all(x == "ok" for x in statuses):
        status = "ok"
    elif all(x == "failed" for x in statuses):
        status = "failed"
    elif any(x == "failed" for x in statuses):
        status = "partial"
    else:
        status = "unknown"

    return {"status": status, "tag": tag, "enabled": enabled,
            "results": results, "changed": changed}


# ------------------------------------------------------- 가져오기 (3단계, §2)
#
# "FarShell이 들고 있는 MCP 설정을 원하는 도구·스코프로 쉽게 가져온다" — 이게
# 3단계의 목적이다. 값은 절대 따라가지 않는다: 자격증명은 FarShell이 보관하고
# 설정 파일에는 **참조만** 심는다(§2-2). 도구별 문법 차이는 어댑터가 흡수한다.

# 심을 수 있는 자리. **codex는 없다** — config.toml에 새 테이블을 텍스트 수술로
# 만들어 넣는 건 기존 `enabled` 한 글자를 뒤집는 것과 위험이 다르다(TOML을
# 생성해야 하고, 실패하면 사용자의 codex 설정 전체가 깨진다). §3의 "추측해서
# 고치지 않는다"를 그대로 적용해 **거절하고 공식 명령을 안내한다.**
DEPLOY_TARGETS = {
    ("claude", "global"): "~/.claude.json 의 mcpServers",
    ("claude", "local"): "워크트리의 .mcp.json(공유) 또는 ~/.claude.json projects(비공유)",
    ("agy", "global"): "~/.gemini/config/mcp_config.json 의 mcpServers",
    ("opencode", "global"): "~/.config/opencode/opencode.json 의 mcp",
    ("opencode", "local"): "워크트리의 opencode.json 의 mcp",
}


def _deploy_target_path(tool: str, scope: str, *, worktree_path, shared: bool):
    if tool == "opencode":
        if scope == "local":
            if not worktree_path:
                raise WriteRefused("로컬 스코프는 워크트리를 지정해야 한다")
            return Path(worktree_path) / "opencode.json", "mcp", None
        return mcp_adapters.opencode_paths()["global"], "mcp", None
    if tool == "agy":
        return mcp_adapters.agy_paths()["global"], "mcpServers", None
    if tool == "claude":
        if scope == "global":
            return mcp_adapters.claude_paths()["global"], "mcpServers", None
        if not worktree_path:
            raise WriteRefused("로컬 스코프는 워크트리를 지정해야 한다")
        if shared:
            return Path(worktree_path) / ".mcp.json", "mcpServers", None
        return mcp_adapters.claude_paths()["global"], "projects", worktree_path
    raise WriteRefused(
        f"{tool}에는 정의를 심지 않는다 — config.toml에 새 테이블을 만드는 것은 "
        f"위험이 다르다. 공식 명령을 쓸 것: `{tool} mcp add`"
    )


def deploy(
    name: str,
    defn: dict,
    *,
    tool: str,
    scope: str = "global",
    worktree_path: Optional[str] = None,
    shared: bool = False,
    env_map: Optional[dict] = None,
) -> dict:
    """서버 정의 하나를 그 도구·스코프에 심는다. **값은 참조로 바꿔서.**

    `env_map`은 `{"env": {"TOKEN": "FSH_MCP_X_TOKEN"}, …}` — 어느 칸을 어느
    환경변수 이름으로 대체할지. 대체 후에도 값이 남아 있으면 **쓰지 않는다**
    (`has_literal_secret`가 마지막 관문이다). 그래야 `.mcp.json`이 git에
    커밋되며 키가 저장소에 박히는 사고가 구조적으로 불가능해진다(§2-2).
    """
    import mcp_catalog

    if tool not in TOOLS:
        return {"status": "failed", "reason": f"모르는 도구: {tool}", "changed": False}
    if not isinstance(defn, dict) or not defn:
        return {"status": "failed", "reason": "정의가 비었다", "changed": False}

    # §2-5 — OAuth는 복제 대상이 아니다. 만료·갱신·audience 제약이 있어
    # 옮겨봐야 받는 쪽에서 안 먹거나 조용히 만료된다. "옮겼는데 안 된다"보다
    # "못 옮긴다"가 정직하다.
    if defn.get("oauth") or "oauth" in (defn.get("auth") or {}):
        return {"status": "failed", "changed": False,
                "reason": "OAuth를 쓰는 서버는 다른 스코프로 복제할 수 없다 — "
                          "대상 도구에서 직접 인증할 것"}

    # 1) 값 → 참조.
    ready = mcp_adapters.apply_refs(tool, defn, env_map or {})

    # 2) **마지막 관문.** 하나라도 값이 남아 있으면 파일을 열지 않는다.
    leaked = mcp_adapters.has_literal_secret(ready, tool=tool)
    if leaked:
        return {"status": "failed", "changed": False,
                "reason": f"값이 그대로 남은 칸이 있다: {', '.join(leaked)} — "
                          f"자격증명을 먼저 등록하고 그 칸을 참조로 지정할 것"}

    try:
        path, top_key, project_path = _deploy_target_path(
            tool, scope, worktree_path=worktree_path, shared=shared)
    except WriteRefused as e:
        return {"status": "failed", "reason": str(e), "changed": False}

    try:
        with _locked(path):
            before = _load_json_for_write(path)
            after = json.loads(json.dumps(before))

            if project_path:
                projects = after.setdefault("projects", {})
                if not isinstance(projects, dict):
                    raise WriteRefused("projects가 객체가 아니다 — 쓰기를 중단했다")
                entry = projects.setdefault(project_path, {})
                if not isinstance(entry, dict):
                    raise WriteRefused("projects 항목이 객체가 아니다 — 쓰기를 중단했다")
                block = entry.setdefault("mcpServers", {})
            else:
                block = after.setdefault(top_key, {})
            if not isinstance(block, dict):
                raise WriteRefused(f"{top_key}가 객체가 아니다 — 쓰기를 중단했다")

            block[name] = ready
            if before == after:
                return {"status": "ok", "changed": False, "target": str(path)}

            def verify(d):
                if project_path:
                    e = (d.get("projects") or {}).get(project_path) or {}
                    got = (e.get("mcpServers") or {}).get(name)
                else:
                    got = (d.get(top_key) or {}).get(name)
                return got == ready

            out = _commit_json(path, before, after,
                               "projects" if project_path else top_key, verify,
                               allow_new_key=True)
    except WriteRefused as e:
        return {"status": "failed", "reason": str(e), "changed": False}
    except WriteUnverified as e:
        return {"status": "unknown", "reason": str(e), "changed": None}
    except OSError as e:
        return {"status": "failed", "reason": f"파일 오류: {e.__class__.__name__}",
                "changed": False}

    # 3) 회수용 기록 — 우리가 심은 참조가 어디 있는지. 이름 규칙이 아니라
    #    쓴 사실 자체를 남긴다(§2-5, 사용자가 이름을 덮어써도 추적이 안 끊긴다).
    for section in ("env", "headers"):
        for env_name in (env_map or {}).get(section, {}).values():
            mcp_catalog.record_ref(tool=tool, scope=scope, source=str(path),
                                   server=name, env=env_name)

    out["target"] = str(path)
    return out


# --------------------------------------------------------------- opencode

def _opencode_set(name: str, enabled: bool, *, scope: str,
                  worktree_path: Optional[str]) -> dict:
    """`enabled: true/false` — codex와 같은 방향, claude의 `disabled*`와 반대.

    JSON이라 codex와 달리 텍스트 수술이 필요 없다(codex는 TOML이라 파서를
    거치면 주석·서식이 날아가서 원문을 직접 고쳤다).
    """
    if scope == "local":
        if not worktree_path:
            raise WriteRefused("로컬 스코프는 워크트리를 지정해야 한다")
        path = Path(worktree_path) / "opencode.json"
    else:
        path = mcp_adapters.opencode_paths()["global"]

    with _locked(path):
        before = _load_json_for_write(path)
        after = json.loads(json.dumps(before))

        block = after.get("mcp")
        if not isinstance(block, dict) or name not in block:
            raise WriteRefused(f"opencode({scope})에 `{name}` 서버 정의가 없다")
        if not isinstance(block[name], dict):
            raise WriteRefused("서버 정의가 객체가 아니다 — 쓰기를 중단했다")

        block[name]["enabled"] = enabled
        if before == after:
            return {"status": "ok", "changed": False}

        def verify(d):
            got = (d.get("mcp") or {}).get(name) or {}
            return (got.get("enabled", True) is not False) == enabled

        return _commit_json(path, before, after, "mcp", verify)


# ------------------------------------------------------------ 플러그인 (4단계)

def set_plugin_enabled(name: str, enabled: bool, *, tool: str = "claude",
                       scope: str = "global",
                       worktree_id: Optional[str] = None) -> dict:
    """설치된 플러그인 하나를 켜거나 끈다. **설치는 하지 않는다**(§0-2).

    미설치 플러그인은 `enabled` 값만으론 켜지지 않으므로, **이미 목록에 있는
    것만** 다룬다. 없는 이름을 받으면 켠 것처럼 보이게 만들지 않고 거절한다 —
    "켰다고 표시했는데 아무 일도 안 일어난다"가 이 화면이 가장 피해야 할
    상태다.
    """
    if tool == "codex":
        if scope != "global":
            return {"status": "failed", "reason": "Codex 플러그인은 전역 스코프만 지원한다",
                    "changed": False}
        path = mcp_adapters.codex_paths()["global"]
        try:
            with _locked(path):
                before, err = mcp_adapters._read_toml(path)
                if err:
                    raise WriteRefused(f"{path.name}: {err} — 쓰기를 중단했다")
                if before is None:
                    raise WriteRefused(f"{path} 가 없다")

                installed, install_err = codex_cli.installed_plugin_ids()
                if install_err:
                    raise WriteRefused(f"설치 여부를 확인하지 못했다: {install_err}")
                if name not in installed:
                    raise WriteRefused(
                        f"`{name}` 플러그인이 설치 목록에 없다 — 먼저 `codex plugin add`로 설치할 것")

                plugins = before.get("plugins")
                if plugins is not None and not isinstance(plugins, dict):
                    raise WriteRefused("plugins가 테이블이 아니다 — 쓰기를 중단했다")
                current = mcp_adapters._as_mapping(plugins).get(name)
                if current is None and enabled:
                    # 설정이 없으면 Codex 기본값이 켜짐이다. 불필요한 키를 만들지 않는다.
                    return {"status": "ok", "changed": False}
                if current is not None and not isinstance(current, dict):
                    raise WriteRefused("플러그인 설정이 테이블이 아니다 — 쓰기를 중단했다")

                text = path.read_text(encoding="utf-8")
                if current is None:
                    suffix = "" if text.endswith("\n") or not text else "\n"
                    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
                    new_text = f'{text}{suffix}\n[plugins."{escaped}"]\nenabled = false\n'
                else:
                    new_text, found = toml_set_enabled(text, name, enabled, table="plugins")
                    if not found:
                        raise WriteRefused(
                            f"`{name}` 테이블을 텍스트에서 찾지 못했다 — 쓰기를 중단했다")
                if new_text == text:
                    return {"status": "ok", "changed": False}

                parsed, perr = mcp_adapters._read_toml_text(new_text)
                if perr or parsed is None:
                    raise WriteRefused(f"수정 결과가 올바른 TOML이 아니다: {perr}")
                expected = json.loads(json.dumps(before, default=str))
                expected.setdefault("plugins", {}).setdefault(name, {})["enabled"] = enabled
                if json.loads(json.dumps(parsed, default=str)) != expected:
                    raise WriteRefused("수정이 `enabled` 말고 다른 곳까지 바꾼다 — 쓰기를 중단했다")

                stamp = _stamp(path)
                if _stamp(path) != stamp:
                    raise WriteRefused("쓰는 사이 외부에서 파일이 바뀌었다 — 다시 시도할 것")
                _atomic_write(path, new_text)
                reread, rerr = mcp_adapters._read_toml(path)
                if rerr or reread is None:
                    raise WriteUnverified(f"쓴 뒤 다시 읽지 못했다: {rerr or '파일 없음'}")
                got = mcp_adapters._as_mapping(reread.get("plugins")).get(name) or {}
                if bool(got.get("enabled", True)) != enabled:
                    raise WriteUnverified("쓴 내용이 다시 읽은 파일과 다르다")
                return {"status": "ok", "changed": True}
        except WriteRefused as e:
            return {"status": "failed", "reason": str(e), "changed": False}
        except WriteUnverified as e:
            return {"status": "unknown", "reason": str(e), "changed": None}
        except OSError as e:
            return {"status": "failed", "reason": f"파일 오류: {e.__class__.__name__}",
                    "changed": False}

    if tool != "claude":
        return {"status": "failed", "changed": False,
                "reason": f"{tool} 플러그인 토글은 아직 지원하지 않는다 — "
                          f"공식 명령을 쓸 것"}
    if scope not in ("global", "local"):
        return {"status": "failed", "reason": "scope는 global/local", "changed": False}

    wt = mcp_scan.find_worktree(worktree_id)
    if scope == "local" and wt is None:
        return {"status": "failed", "reason": "로컬 스코프는 워크트리를 지정해야 한다",
                "changed": False}
    wt_path = wt.get("path") if wt else None
    path = mcp_adapters.claude_paths()["global"]

    try:
        with _locked(path):
            before = _load_json_for_write(path)
            after = json.loads(json.dumps(before))

            if scope == "local":
                projects = after.setdefault("projects", {})
                if not isinstance(projects, dict):
                    raise WriteRefused("projects가 객체가 아니다 — 쓰기를 중단했다")
                container = projects.setdefault(wt_path, {})
                touched = "projects"
            else:
                container = after
                touched = "enabledPlugins"
            if not isinstance(container, dict):
                raise WriteRefused("설정 블록이 객체가 아니다 — 쓰기를 중단했다")

            block = container.get("enabledPlugins")
            if not isinstance(block, dict) or name not in block:
                raise WriteRefused(
                    f"`{name}` 플러그인이 목록에 없다 — 설치되지 않은 플러그인은 "
                    f"값만 바꿔도 켜지지 않는다. 먼저 `claude plugin install`로 설치할 것")
            block[name] = enabled

            if before == after:
                return {"status": "ok", "changed": False}

            def verify(d):
                if scope == "local":
                    c = (d.get("projects") or {}).get(wt_path) or {}
                else:
                    c = d
                return bool((c.get("enabledPlugins") or {}).get(name)) == enabled

            return _commit_json(path, before, after, touched, verify,
                                allow_new_key=(scope == "global"))
    except WriteRefused as e:
        return {"status": "failed", "reason": str(e), "changed": False}
    except WriteUnverified as e:
        return {"status": "unknown", "reason": str(e), "changed": None}
    except OSError as e:
        return {"status": "failed", "reason": f"파일 오류: {e.__class__.__name__}",
                "changed": False}
