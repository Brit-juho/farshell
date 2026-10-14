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

import hashlib
import json
import os
import re
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import codex_cli
import mcp_adapters
import mcp_scan
import store_lock
import vt_paths
from i18n import tr

TOOLS = ("claude", "codex", "agy", "opencode")


class WriteRefused(Exception):
    """쓰기를 시작하지 않았다 — 파일은 그대로다."""


class WriteUnverified(Exception):
    """썼지만 확인하지 못했다 — 바뀌었을 수도, 아닐 수도 있다."""


# --------------------------------------------------------- P3-1(ADR-37) 배치
#
# `set_enabled`을 토글 하나마다 부르면 `~/.claude.json`에 그만큼(재읽기→충돌
# 검사→쓰기→재읽기) 사이클이 돈다. 웹 UI에서 서버 5개를 켜면 같은 파일에
# 5번 — 그 파일은 Claude Code 자신도 쓰므로, 사이클마다 "외부 변경 충돌"
# 창이 한 번씩 새로 열린다. 위험 노출이 변경 건수에 비례한다.
#
# 판단(뭘 바꿀지)과 I/O(어떻게 바꿀지)를 분리한다 — `plan_changes`는 파일도
# 전역도 안 만지는 순수 함수라 dict만으로 테스트된다.

def plan_changes(current: dict[str, bool], wanted: list[dict]) -> list[dict]:
    """파일에서 읽은 현재 상태 + 원하는 변경들 → 실제로 적용할 변경 목록.

    순수 함수. `current`는 `{name: enabled}` — 파일 형식은 도구마다 다르므로
    파싱은 호출자 몫이고, 여기는 "무엇을 적용해야 하는가"만 판단한다.

    - 이미 원하는 상태인 항목은 뺀다 — 파일을 아예 안 열 수도 있게 한다.
    - 같은 이름에 대한 중복 변경은 **마지막 것만** 남긴다. 순서를 지켜서
      "나중에 오는 것이 이긴다"가 그대로 `wanted`의 뒤쪽이 이긴다는 뜻이 되게 한다.
    """
    latest: dict[str, dict] = {}
    order: list[str] = []
    for w in wanted:
        name = w["name"]
        if name not in latest:
            order.append(name)
        latest[name] = w
    return [latest[name] for name in order if current.get(name) != latest[name]["enabled"]]


# ------------------------------------------------------------------ 잠금

def _lock_dir() -> Path:
    # P2-4(ADR-41) — 예전 관용구(`os.environ.get("VT_STATE_DIR") or (Path.home()
    # / ".vt")`)는 `VT_STATE_DIR=""`에서 빈 문자열이 falsy라 우연히 폴백이
    # 걸렸지만, `"~/vt-test"`에서는 `~`를 안 펴서 다른 16곳과 갈라졌다.
    d = vt_paths.state_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


@contextmanager
def _locked(target: Path):
    """대상 파일 하나에 대한 잠금. 잠금 파일은 사용자의 설정 디렉토리를
    더럽히지 않도록 `~/.vt/locks/` 아래에 둔다.

    기한이 다른 저장소보다 길다(10초) — 상대(Claude Code 자신)가 협조하지
    않을 수 있는 파일이라 정직한 경쟁에 더 여유를 준다
    (`docs/plan-2.3/20-store-durability.md` §3.3).
    """
    key = hashlib.sha1(str(target).encode()).hexdigest()[:16]
    lock_path = _lock_dir() / f"{key}.lock"
    with store_lock.exclusive(lock_path, deadline=10.0):
        yield


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
        raise WriteRefused(tr("최상위 키가 사라진다: {keys}", keys=lost))
    added = sorted(set(after) - set(before))
    if added and not (allow_new_key and added == [touched]):
        raise WriteRefused(tr("최상위 키가 늘어난다: {keys}", keys=added))
    for key in before:
        if key == touched:
            continue
        if before[key] != after[key]:
            raise WriteRefused(tr("손대지 않아야 할 키가 바뀐다: {key}", key=key))


# ------------------------------------------------------------ JSON 공통

def _load_json_for_write(path: Path) -> dict:
    data, err = mcp_adapters._read_json(path)
    if err:
        # 못 읽은 파일을 빈 설정으로 간주하고 쓰면 그 순간 전부 날아간다.
        raise WriteRefused(tr("{file}: {err} — 쓰기를 중단했다", file=path.name, err=err))
    return {} if data is None else data


def _commit_json(path: Path, before: dict, after: dict, touched: str, verify,
                 *, allow_new_key: bool = False, expected_revision: Optional[str] = None) -> dict:
    _guard_minimal_change(before, after, touched, allow_new_key=allow_new_key)
    stamp = _stamp(path)
    text = json.dumps(after, indent=2, ensure_ascii=False) + "\n"

    if _stamp(path) != stamp:
        raise WriteRefused(tr("쓰는 사이 외부에서 파일이 바뀌었다 — 다시 시도할 것"))
    if expected_revision is not None and mcp_adapters.file_revision(path) != expected_revision:
        raise WriteRefused(tr("미리보기 뒤 대상 파일이 바뀌었습니다"))
    _atomic_write(path, text)

    # 재읽기 — 우리가 의도한 상태가 실제로 파일에 있는지.
    reread, err = mcp_adapters._read_json(path)
    if err or reread is None:
        raise WriteUnverified(tr("쓴 뒤 다시 읽지 못했다: {err}", err=err or tr(tr("파일 없음"))))
    if not verify(reread):
        raise WriteUnverified(tr("쓴 내용이 다시 읽은 파일과 다르다"))
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
        raise WriteRefused(tr("claude는 켜짐/꺼짐을 프로젝트별로 기록한다 — 워크트리를 지정해야 한다"))

    path = mcp_adapters.claude_paths()["global"]
    with _locked(path):
        before = _load_json_for_write(path)
        after = json.loads(json.dumps(before))  # 깊은 복사

        projects = after.setdefault("projects", {})
        if not isinstance(projects, dict):
            raise WriteRefused(tr("projects가 객체가 아니다 — 쓰기를 중단했다"))
        entry = projects.setdefault(worktree_path, {})
        if not isinstance(entry, dict):
            raise WriteRefused(tr("projects 항목이 객체가 아니다 — 쓰기를 중단했다"))

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


def _claude_current(entry: dict, name: str, shared: bool) -> bool:
    """이 프로젝트 항목에서 `name`이 지금 켜져 있는가 — `mcp_adapters.scan_claude`의
    같은 판정을 여기서도 쓴다(공유는 승인 목록 멤버십, 비공유는 차단 목록 부재)."""
    if shared:
        return name in mcp_adapters._as_name_list(entry.get("enabledMcpjsonServers"))
    return name not in mcp_adapters._as_name_list(entry.get("disabledMcpServers"))


def _claude_set_batch(changes: list[dict]) -> list[dict]:
    """여러 서버를 **한 번의 락+검증+쓰기**로 `~/.claude.json`에 반영한다.

    `changes`: `[{"name", "enabled", "worktree_path", "shared"}, ...]`.
    이미 원하는 상태인 항목은 `plan_changes`가 걸러낸다 — 프로젝트별로
    "현재 상태"가 다르므로 (worktree_path, shared) 조합마다 따로 판단한다.
    검증 실패는 **파일 전체를 롤백**한다(원자 교체 전이라 공짜) — 반쯤
    적용된 파일을 만들지 않는다.
    """
    results: list[dict] = []
    applicable = []
    for c in changes:
        if not c.get("worktree_path"):
            results.append({"name": c["name"], "status": "failed", "changed": False,
                            "reason": tr("claude는 켜짐/꺼짐을 프로젝트별로 기록한다 — 워크트리를 지정해야 한다")})
        else:
            applicable.append(c)
    if not applicable:
        return results

    path = mcp_adapters.claude_paths()["global"]
    with _locked(path):
        before = _load_json_for_write(path)
        after = json.loads(json.dumps(before))
        projects = after.setdefault("projects", {})
        if not isinstance(projects, dict):
            for c in applicable:
                results.append({"name": c["name"], "status": "failed", "changed": False,
                                "reason": tr("projects가 객체가 아니다 — 쓰기를 중단했다")})
            return results

        # (worktree_path, shared)별로 묶어 plan_changes를 돌린다 — 같은 조합
        # 안에서만 "이미 원하는 상태"·"중복 이름" 판단이 성립한다.
        buckets: dict[tuple, list[dict]] = {}
        for c in applicable:
            buckets.setdefault((c["worktree_path"], bool(c.get("shared"))), []).append(c)

        applied: list[dict] = []
        for (wt_path, shared), bucket in buckets.items():
            entry = projects.get(wt_path)
            if entry is not None and not isinstance(entry, dict):
                for c in bucket:
                    results.append({"name": c["name"], "status": "failed", "changed": False,
                                    "reason": tr("projects 항목이 객체가 아니다 — 쓰기를 중단했다")})
                continue
            current = {c["name"]: _claude_current(entry or {}, c["name"], shared) for c in bucket}
            todo = plan_changes(current, bucket)
            skipped = {c["name"] for c in bucket} - {c["name"] for c in todo}
            for name in skipped:
                results.append({"name": name, "status": "ok", "changed": False})
            if not todo:
                continue
            entry = projects.setdefault(wt_path, {})
            for c in todo:
                if shared:
                    _set_membership(entry, "enabledMcpjsonServers", c["name"], c["enabled"])
                    _set_membership(entry, "disabledMcpjsonServers", c["name"], not c["enabled"])
                else:
                    _set_membership(entry, "disabledMcpServers", c["name"], not c["enabled"])
                applied.append(c)

        if not applied:
            return results

        def verify(d):
            for c in applied:
                e = (d.get("projects") or {}).get(c["worktree_path"]) or {}
                if _claude_current(e, c["name"], bool(c.get("shared"))) != c["enabled"]:
                    return False
            return True

        try:
            commit = _commit_json(path, before, after, "projects", verify)
        except WriteRefused as e:
            for c in applied:
                results.append({"name": c["name"], "status": "failed", "changed": False, "reason": str(e)})
            return results
        except WriteUnverified as e:
            for c in applied:
                results.append({"name": c["name"], "status": "unknown", "changed": None, "reason": str(e)})
            return results
        for c in applied:
            results.append({"name": c["name"], **commit})
        return results


def set_enabled_batch(
    tool: str,
    changes: list[dict],
    *,
    scope: str = "global",
    worktree_id: Optional[str] = None,
) -> list[dict]:
    """여러 서버를 한 번에 목표 상태로 맞춘다. `changes`: `[{"name","enabled","shared"?}, ...]`.

    **지금은 `claude`만 파일당 한 사이클로 배치한다** — `~/.claude.json`이
    이 항목의 동기가 된 파일이고(Claude Code 자신도 쓴다), 나머지 세 도구는
    토글이 훨씬 드물게 몰린다. codex/agy/opencode는 항목마다
    `set_enabled`을 그대로 부른다 — 배치 API 계약(항목별 결과)은 지키지만
    파일 사이클 절감은 claude만 받는다. 여기가 다음에 늘릴 자리다.
    """
    if tool not in TOOLS:
        return [{"name": c.get("name"), "status": "failed", "changed": False,
                 "reason": tr("모르는 도구: {tool}", tool=tool)} for c in changes]

    if tool == "claude":
        wt = mcp_scan.find_worktree(worktree_id)
        wt_path = wt.get("path") if wt else None
        batch = [{"name": c["name"], "enabled": c["enabled"],
                  "worktree_path": wt_path, "shared": bool(c.get("shared", False))}
                 for c in changes]
        try:
            return _claude_set_batch(batch)
        except OSError as e:
            return [{"name": c["name"], "status": "failed", "changed": False,
                     "reason": tr("파일 오류: {name}", name=e.__class__.__name__)} for c in changes]

    results = []
    for c in changes:
        out = set_enabled(
            tool, c["name"], c["enabled"],
            scope=scope, worktree_id=worktree_id, shared=bool(c.get("shared", False)),
        )
        results.append({"name": c["name"], **out})
    return results


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
            raise WriteRefused(tr("agy에 `{name}` 서버 정의가 없다", name=name))
        defn = servers[name]
        if not isinstance(defn, dict):
            raise WriteRefused(tr("`{name}` 정의가 객체가 아니다 — 쓰기를 중단했다", name=name))

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
            raise WriteRefused(tr("로컬 스코프는 워크트리를 지정해야 한다"))
        path = Path(worktree_path) / ".codex" / "config.toml"
    else:
        path = mcp_adapters.codex_paths()["global"]

    with _locked(path):
        raw, err = mcp_adapters._read_toml(path)
        if err:
            raise WriteRefused(tr("{file}: {err} — 쓰기를 중단했다", file=path.name, err=err))
        if raw is None:
            raise WriteRefused(tr("{path} 가 없다", path=path))
        if (raw.get("mcp_servers") or {}).get(name) is None:
            raise WriteRefused(tr("codex({scope})에 `{name}` 서버 정의가 없다", scope=scope, name=name))

        text = path.read_text(encoding="utf-8")
        new_text, found = toml_set_enabled(text, name, enabled)
        if not found:
            # 파싱으로는 보이는데 텍스트에서 못 찾았다 —
            # 형태를 모르는 파일이므로 추측해서 고치지 않는다.
            raise WriteRefused(tr("`{name}` 테이블을 텍스트에서 찾지 못했다 — 쓰기를 중단했다", name=name))
        if new_text == text:
            return {"status": "ok", "changed": False}

        # 수술 결과가 여전히 올바른 TOML이고, `enabled` 말고는 안 바뀌었는지.
        parsed, perr = mcp_adapters._read_toml_text(new_text)
        if perr:
            raise WriteRefused(tr("수정 결과가 올바른 TOML이 아니다: {err}", err=perr))
        expected = json.loads(json.dumps(raw, default=str))
        expected.setdefault("mcp_servers", {}).setdefault(name, {})["enabled"] = enabled
        if json.loads(json.dumps(parsed, default=str)) != expected:
            raise WriteRefused(tr("수정이 `enabled` 말고 다른 곳까지 바꾼다 — 쓰기를 중단했다"))

        stamp = _stamp(path)
        if _stamp(path) != stamp:
            raise WriteRefused(tr("쓰는 사이 외부에서 파일이 바뀌었다 — 다시 시도할 것"))
        _atomic_write(path, new_text)

        reread, rerr = mcp_adapters._read_toml(path)
        if rerr or reread is None:
            raise WriteUnverified(tr("쓴 뒤 다시 읽지 못했다: {err}", err=rerr or tr(tr("파일 없음"))))
        got = (reread.get("mcp_servers") or {}).get(name) or {}
        if (got.get("enabled", True) is not False) != enabled:
            raise WriteUnverified(tr("쓴 내용이 다시 읽은 파일과 다르다"))
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
        return {"status": "failed", "reason": tr("모르는 도구: {tool}", tool=tool), "changed": False}

    wt = mcp_scan.find_worktree(worktree_id)
    if worktree_id and wt is None:
        return {"status": "failed", "reason": tr("워크트리를 찾지 못했다"), "changed": False}
    wt_path = wt.get("path") if wt else None

    import mcp_library
    import mcp_sync
    before_activation, _ = mcp_library.target_raw(tool, scope, name, worktree_id)
    try:
        if tool == "claude":
            out = _claude_set(name, enabled, worktree_path=wt_path, shared=shared)
        elif tool == "opencode":
            out = _opencode_set(name, enabled, scope=scope, worktree_path=wt_path)
        elif tool == "agy":
            if scope == "local":
                raise WriteRefused(tr("agy는 전역 설정 하나만 쓴다 — 로컬 스코프가 없다"))
            out = _agy_set(name, enabled)
        else:
            out = _codex_set(name, enabled, scope=scope, worktree_path=wt_path)
    except WriteRefused as e:
        return {"status": "failed", "reason": str(e), "changed": False}
    except WriteUnverified as e:
        # 이미 바뀌었을 수 있다 — 성공이라고도, 실패라고도 말하지 않는다.
        return {"status": "unknown", "reason": str(e), "changed": None}
    except OSError as e:
        return {"status": "failed", "reason": tr("파일 오류: {name}", name=e.__class__.__name__), "changed": False}

    if out.get("status") == "ok":
        mcp_sync.record_activation(tool, scope, name, worktree_id, before_activation)
    out.setdefault("reason", None)
    return out


# ------------------------------------------------------------------ 그룹

def apply_group(
    tag: str,
    enabled: bool,
    *,
    worktree_id: Optional[str] = None,
    tool: Optional[str] = None,
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
        return {"status": "failed", "reason": tr("그 태그가 붙은 서버가 없다"),
                "tag": tag, "results": [], "changed": 0}

    scan = mcp_scan.scan(worktree_id)
    targets = [s for s in scan["servers"] if s["name"] in names
               and (tool is None or s["tool"] == tool)]
    if not targets:
        # 태그는 있는데 지금 이 워크트리 기준으로는 아무 항목도 안 보인다.
        # 실패가 아니라 "여기엔 없다"이므로 그대로 말한다.
        return {"status": "ok", "reason": tr("이 워크트리에서 보이는 항목이 없다"),
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
        if not s.get("writable", True):
            results.append({**entry, "status": "failed", "changed": False,
                            "reason": tr("읽기 전용 출처는 그룹에서 변경하지 않습니다"), "skipped": False})
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
        if any(p.exists() and not writable and s == scope
               for p, s, writable in mcp_adapters.opencode_sources(worktree_path)):
            raise WriteRefused(tr("OpenCode 추가 설정/JSONC 출처가 있습니다 — 해당 파일은 읽기 전용입니다"))
        if scope == "local":
            if not worktree_path:
                raise WriteRefused(tr("로컬 스코프는 워크트리를 지정해야 한다"))
            return Path(worktree_path) / "opencode.json", "mcp", None
        return mcp_adapters.opencode_paths()["global"], "mcp", None
    if tool == "agy":
        return mcp_adapters.agy_paths()["global"], "mcpServers", None
    if tool == "claude":
        if scope == "global":
            return mcp_adapters.claude_paths()["global"], "mcpServers", None
        if not worktree_path:
            raise WriteRefused(tr("로컬 스코프는 워크트리를 지정해야 한다"))
        if shared:
            return Path(worktree_path) / ".mcp.json", "mcpServers", None
        return mcp_adapters.claude_paths()["global"], "projects", worktree_path
    raise WriteRefused(
        tr("{tool}에는 정의를 심지 않는다 — config.toml에 새 테이블을 만드는 것은 위험이 다르다. 공식 명령을 쓸 것: `{tool} mcp add`", tool=tool)
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
    expected_revision: Optional[str] = None,
    replace_hash: Optional[str] = None,
    initial_enabled: Optional[bool] = None,
    source_guard=None,
) -> dict:
    """서버 정의 하나를 그 도구·스코프에 심는다. **값은 참조로 바꿔서.**

    `env_map`은 `{"env": {"TOKEN": "FSH_MCP_X_TOKEN"}, …}` — 어느 칸을 어느
    환경변수 이름으로 대체할지. 대체 후에도 값이 남아 있으면 **쓰지 않는다**
    (`has_literal_secret`가 마지막 관문이다). 그래야 `.mcp.json`이 git에
    커밋되며 키가 저장소에 박히는 사고가 구조적으로 불가능해진다(§2-2).
    """
    import mcp_catalog

    if tool not in TOOLS:
        return {"status": "failed", "reason": tr("모르는 도구: {tool}", tool=tool), "changed": False}
    if not isinstance(defn, dict) or not defn:
        return {"status": "failed", "reason": tr("정의가 비었다"), "changed": False}

    # §2-5 — OAuth는 복제 대상이 아니다. 만료·갱신·audience 제약이 있어
    # 옮겨봐야 받는 쪽에서 안 먹거나 조용히 만료된다. "옮겼는데 안 된다"보다
    # "못 옮긴다"가 정직하다.
    if defn.get("oauth") or "oauth" in (defn.get("auth") or {}):
        return {"status": "failed", "changed": False,
                "reason": tr("OAuth를 쓰는 서버는 다른 스코프로 복제할 수 없다 — 대상 도구에서 직접 인증할 것")}

    # ADR-46 — 참조를 쓸 방법이 없는 도구(agy, 1.2.7 실측)에 env/headers가 있는 정의를
    # 옮기면 값을 평문으로 적거나 `${…}` 글자를 적는 수밖에 없다. 둘 다 안 된다.
    if mcp_adapters.ref_syntax(tool) == "none" and (
            any(isinstance(defn.get(s), dict) and defn.get(s) for s in ("env", "headers"))
            or any((env_map or {}).get(s) for s in ("env", "headers"))):
        return {"status": "failed", "changed": False,
                "reason": tr("{tool}는 설정의 환경변수 참조를 확장하지 않는다 — 자격증명(env·headers)이 있는 서버는 {tool}로 복제할 수 없다. {tool}에서 직접 등록할 것", tool=tool)}

    # 1) 값 → 참조.
    ready = mcp_adapters.apply_refs(tool, defn, env_map or {})
    if initial_enabled is not None:
        if tool in ("codex", "opencode"):
            ready["enabled"] = initial_enabled
        elif tool == "agy":
            ready["disabled"] = not initial_enabled
        elif tool == "claude" and (scope == "global" or shared or not worktree_path):
            return {"status": "failed", "changed": False,
                    "reason": tr("Claude는 프로젝트별로 꺼짐을 기록합니다 — 이 프로젝트의 비공유 설정을 선택하세요")}

    schema_error = mcp_adapters.validate_definition(tool, ready)
    if schema_error:
        return {"status": "failed", "changed": False, "reason": schema_error}

    # 2) **마지막 관문.** 하나라도 값이 남아 있으면 파일을 열지 않는다.
    leaked = mcp_adapters.has_literal_secret(ready, tool=tool)
    if leaked:
        return {"status": "failed", "changed": False,
                "reason": tr("값이 그대로 남은 칸이 있다: {fields} — 자격증명을 먼저 등록하고 그 칸을 참조로 지정할 것", fields=", ".join(leaked))}

    if tool == "opencode":
        ready, error = mcp_adapters.common_to_opencode(ready)
        if error:
            return {"status": "failed", "reason": error, "changed": False}

    if tool == "codex":
        return _codex_deploy(name, ready, scope=scope, worktree_path=worktree_path,
                             expected_revision=expected_revision, replace_hash=replace_hash, source_guard=source_guard)

    try:
        path, top_key, project_path = _deploy_target_path(
            tool, scope, worktree_path=worktree_path, shared=shared)
    except WriteRefused as e:
        return {"status": "failed", "reason": str(e), "changed": False}

    try:
        with _locked(path):
            if source_guard is not None and not source_guard():
                raise WriteRefused(tr("적용 직전 원본이 바뀌었습니다"))
            revision = mcp_adapters.file_revision(path)
            if expected_revision is not None and revision != expected_revision:
                raise WriteRefused(tr("미리보기 뒤 대상 파일이 바뀌었습니다"))
            before = _load_json_for_write(path)
            after = json.loads(json.dumps(before))

            if project_path:
                projects = after.setdefault("projects", {})
                if not isinstance(projects, dict):
                    raise WriteRefused(tr("projects가 객체가 아니다 — 쓰기를 중단했다"))
                entry = projects.setdefault(project_path, {})
                if not isinstance(entry, dict):
                    raise WriteRefused(tr("projects 항목이 객체가 아니다 — 쓰기를 중단했다"))
                block = entry.setdefault("mcpServers", {})
                if initial_enabled is not None:
                    _set_membership(entry, "disabledMcpServers", name, not initial_enabled)
            else:
                block = after.setdefault(top_key, {})
            if not isinstance(block, dict):
                raise WriteRefused(tr("{key}가 객체가 아니다 — 쓰기를 중단했다", key=top_key))

            if name in block:
                if replace_hash is not None:
                    if mcp_adapters.definition_hash(block[name]) != replace_hash:
                        raise WriteRefused(tr("대상 정의가 직접 수정됐습니다 — 덮어쓰지 않았습니다"))
                elif block[name] != ready:
                    raise WriteRefused(tr("`{name}` 정의가 이미 있다 — 덮어쓰지 않았다", name=name))
            elif replace_hash is not None:
                raise WriteRefused(tr("동기화 대상 정의가 없어졌습니다"))

            block[name] = ready
            if before == after:
                return {"status": "ok", "changed": False, "target": str(path)}

            def verify(d):
                if project_path:
                    e = (d.get("projects") or {}).get(project_path) or {}
                    got = (e.get("mcpServers") or {}).get(name)
                    if initial_enabled is not None and (name in e.get("disabledMcpServers", [])) == initial_enabled:
                        return False
                else:
                    got = (d.get(top_key) or {}).get(name)
                return got == ready

            out = _commit_json(path, before, after,
                               "projects" if project_path else top_key, verify,
                               allow_new_key=True, expected_revision=revision)
    except WriteRefused as e:
        return {"status": "failed", "reason": str(e), "changed": False}
    except WriteUnverified as e:
        return {"status": "unknown", "reason": str(e), "changed": None}
    except OSError as e:
        return {"status": "failed", "reason": tr("파일 오류: {name}", name=e.__class__.__name__),
                "changed": False}

    # 3) 회수용 기록 — 우리가 심은 참조가 어디 있는지. 이름 규칙이 아니라
    #    쓴 사실 자체를 남긴다(§2-5, 사용자가 이름을 덮어써도 추적이 안 끊긴다).
    for section in ("env", "headers"):
        for env_name in (env_map or {}).get(section, {}).values():
            mcp_catalog.record_ref(tool=tool, scope=scope, source=str(path),
                                   server=name, env=env_name)

    out["target"] = str(path)
    return out


def _codex_deploy(name: str, defn: dict, *, scope: str,
                  worktree_path: Optional[str], expected_revision: Optional[str] = None,
                  replace_hash: Optional[str] = None, source_guard=None) -> dict:
    """새 Codex 테이블 하나만 추가한다. TOML 전체를 재직렬화하지 않는다."""
    if scope == "local" and not worktree_path:
        return {"status": "failed", "reason": tr("프로젝트 기준이 없습니다"), "changed": False}
    if not name or len(name) > 200 or any(c in name for c in '\n\r[]'):
        return {"status": "failed", "reason": tr("서버 이름을 사용할 수 없습니다"), "changed": False}
    allowed = {"command", "args", "url", "env_vars", "bearer_token_env_var", "enabled"}
    if any(k not in allowed for k in defn):
        return {"status": "failed", "reason": tr("Codex에서 지원하지 않는 정의 필드가 있습니다"), "changed": False}
    if bool(defn.get("command")) == bool(defn.get("url")):
        return {"status": "failed", "reason": tr("명령 또는 URL 중 하나가 필요합니다"), "changed": False}
    for key in ("command", "url", "bearer_token_env_var"):
        if key in defn and not isinstance(defn[key], str):
            return {"status": "failed", "reason": tr("{key} 형식이 잘못됐습니다", key=key), "changed": False}
    for key in ("args", "env_vars"):
        if key in defn and (not isinstance(defn[key], list)
                            or any(not isinstance(x, str) for x in defn[key])):
            return {"status": "failed", "reason": tr("{key} 형식이 잘못됐습니다", key=key), "changed": False}
    if "enabled" in defn and not isinstance(defn["enabled"], bool):
        return {"status": "failed", "reason": tr("enabled 형식이 잘못됐습니다"), "changed": False}
    global_path = mcp_adapters.codex_paths()["global"]
    path = (Path(worktree_path) / ".codex" / "config.toml") if scope == "local" else global_path
    try:
        with _locked(path):
            if source_guard is not None and not source_guard():
                raise WriteRefused(tr("적용 직전 원본이 바뀌었습니다"))
            revision = mcp_adapters.file_revision(path)
            if expected_revision is not None and revision != expected_revision:
                raise WriteRefused(tr("미리보기 뒤 대상 파일이 바뀌었습니다"))
            stamp = _stamp(path)
            before, err = mcp_adapters._read_toml(path)
            if err:
                raise WriteRefused(f"{path.name}: {err}")
            before = before or {}
            if scope == "local":
                global_config, global_err = mcp_adapters._read_toml(global_path)
                if global_err or not mcp_adapters.codex_trusted(global_config or {}, worktree_path):
                    raise WriteRefused(tr("Codex가 이 프로젝트를 신뢰하지 않아 로컬 설정이 적용되지 않습니다"))
            existing = mcp_adapters._as_mapping(before.get("mcp_servers")).get(name)
            if existing is not None:
                if replace_hash is None or mcp_adapters.definition_hash(existing) != replace_hash:
                    raise WriteRefused(tr("같은 이름의 Codex 서버가 있거나 직접 수정됐습니다"))
            elif replace_hash is not None:
                raise WriteRefused(tr("동기화 대상 정의가 없어졌습니다"))
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            header = f'[mcp_servers.{json.dumps(name, ensure_ascii=False)}]'
            lines = [header]
            for key in ("command", "args", "url", "env_vars", "bearer_token_env_var", "enabled"):
                if key in defn:
                    value = json.dumps(defn[key], ensure_ascii=False)
                    lines.append(f"{key} = {value}")
            new_text = text + ("" if not text or text.endswith("\n") else "\n") + "\n".join(lines) + "\n"
            if replace_hash is not None:
                original = text.splitlines(keepends=True)
                starts = [i for i, line in enumerate(original) if _codex_header_re(name).match(line)]
                if len(starts) != 1:
                    raise WriteRefused(tr("Codex 테이블을 안전하게 특정할 수 없습니다"))
                start = starts[0]
                end = next((i for i in range(start + 1, len(original))
                            if _TOML_TABLE_RE.match(original[i])), len(original))
                new_text = "".join(original[:start]) + "\n".join(lines) + "\n" + "".join(original[end:])
            parsed, perr = mcp_adapters._read_toml_text(new_text)
            expected = json.loads(json.dumps(before, default=str))
            expected.setdefault("mcp_servers", {})[name] = defn
            if perr or json.loads(json.dumps(parsed, default=str)) != expected:
                raise WriteRefused(tr("새 설정을 안전하게 검증하지 못했습니다"))
            if stamp != _stamp(path) or revision != mcp_adapters.file_revision(path):
                raise WriteRefused(tr("설정 파일이 바뀌었습니다"))
            _atomic_write(path, new_text)
            after, aerr = mcp_adapters._read_toml(path)
            if aerr or mcp_adapters._as_mapping((after or {}).get("mcp_servers")).get(name) != defn:
                raise WriteUnverified(tr("쓴 뒤 설정을 확인하지 못했습니다"))
        return {"status": "ok", "changed": True, "target": str(path)}
    except WriteRefused as exc:
        return {"status": "failed", "reason": str(exc), "changed": False}
    except WriteUnverified as exc:
        return {"status": "unknown", "reason": str(exc), "changed": None}
    except OSError as exc:
        return {"status": "failed", "reason": exc.__class__.__name__, "changed": False}


# --------------------------------------------------------------- opencode

def _opencode_set(name: str, enabled: bool, *, scope: str,
                  worktree_path: Optional[str]) -> dict:
    """`enabled: true/false` — codex와 같은 방향, claude의 `disabled*`와 반대.

    JSON이라 codex와 달리 텍스트 수술이 필요 없다(codex는 TOML이라 파서를
    거치면 주석·서식이 날아가서 원문을 직접 고쳤다).
    """
    path, _, _ = _deploy_target_path("opencode", scope, worktree_path=worktree_path, shared=False)

    with _locked(path):
        before = _load_json_for_write(path)
        after = json.loads(json.dumps(before))

        block = after.get("mcp")
        if not isinstance(block, dict) or name not in block:
            raise WriteRefused(tr("opencode({scope})에 `{name}` 서버 정의가 없다", scope=scope, name=name))
        if not isinstance(block[name], dict):
            raise WriteRefused(tr("서버 정의가 객체가 아니다 — 쓰기를 중단했다"))
        _, schema_error = mcp_adapters.opencode_to_common(block[name])
        if schema_error:
            raise WriteRefused(schema_error)

        block[name]["enabled"] = enabled
        if before == after:
            return {"status": "ok", "changed": False}

        def verify(d):
            got = (d.get("mcp") or {}).get(name) or {}
            return (got.get("enabled", True) is not False) == enabled

        return _commit_json(path, before, after, "mcp", verify)


# ------------------------------------------------------------ 플러그인 (4단계)

def _git_ignored(path: Path) -> bool:
    """git이 이 경로를 무시하는가. git 저장소가 아니거나 확인 실패면 True
    (커밋될 위험이 없거나, 모르는 걸 경고로 부풀리지 않는다)."""
    import subprocess
    try:
        r = subprocess.run(
            ["git", "-C", str(path.parent.parent), "check-ignore", "-q", str(path)],
            capture_output=True, timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    # 0 = 무시됨, 1 = 무시 안 됨, 128 = 저장소 아님/오류
    return r.returncode != 1


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
            return {"status": "failed", "reason": tr("Codex 플러그인은 전역 스코프만 지원한다"),
                    "changed": False}
        path = mcp_adapters.codex_paths()["global"]
        # 설치 목록 확인은 락 **밖에서** 한다 — CLI 호출이 길어질 수 있는데 그동안
        # config.toml 락을 쥐고 있으면 다른 쓰기가 전부 막힌다(ADR-48).
        installed, install_err = codex_cli.installed_plugin_ids()
        if install_err:
            return {"status": "failed", "changed": False,
                    "reason": tr("설치 여부를 확인하지 못했다: {err}", err=install_err)}
        if name not in installed:
            return {"status": "failed", "changed": False,
                    "reason": tr("`{name}` 플러그인이 설치 목록에 없다 — 먼저 `codex plugin add`로 설치할 것", name=name)}
        try:
            with _locked(path):
                before, err = mcp_adapters._read_toml(path)
                if err:
                    raise WriteRefused(tr("{file}: {err} — 쓰기를 중단했다", file=path.name, err=err))
                if before is None:
                    raise WriteRefused(tr("{path} 가 없다", path=path))

                plugins = before.get("plugins")
                if plugins is not None and not isinstance(plugins, dict):
                    raise WriteRefused(tr("plugins가 테이블이 아니다 — 쓰기를 중단했다"))
                current = mcp_adapters._as_mapping(plugins).get(name)
                if current is None and enabled:
                    # 설정이 없으면 Codex 기본값이 켜짐이다. 불필요한 키를 만들지 않는다.
                    return {"status": "ok", "changed": False}
                if current is not None and not isinstance(current, dict):
                    raise WriteRefused(tr("플러그인 설정이 테이블이 아니다 — 쓰기를 중단했다"))

                text = path.read_text(encoding="utf-8")
                if current is None:
                    suffix = "" if text.endswith("\n") or not text else "\n"
                    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
                    new_text = f'{text}{suffix}\n[plugins."{escaped}"]\nenabled = false\n'
                else:
                    new_text, found = toml_set_enabled(text, name, enabled, table="plugins")
                    if not found:
                        raise WriteRefused(
                            tr("`{name}` 테이블을 텍스트에서 찾지 못했다 — 쓰기를 중단했다", name=name))
                if new_text == text:
                    return {"status": "ok", "changed": False}

                parsed, perr = mcp_adapters._read_toml_text(new_text)
                if perr or parsed is None:
                    raise WriteRefused(tr("수정 결과가 올바른 TOML이 아니다: {err}", err=perr))
                expected = json.loads(json.dumps(before, default=str))
                expected.setdefault("plugins", {}).setdefault(name, {})["enabled"] = enabled
                if json.loads(json.dumps(parsed, default=str)) != expected:
                    raise WriteRefused(tr("수정이 `enabled` 말고 다른 곳까지 바꾼다 — 쓰기를 중단했다"))

                stamp = _stamp(path)
                if _stamp(path) != stamp:
                    raise WriteRefused(tr("쓰는 사이 외부에서 파일이 바뀌었다 — 다시 시도할 것"))
                _atomic_write(path, new_text)
                reread, rerr = mcp_adapters._read_toml(path)
                if rerr or reread is None:
                    raise WriteUnverified(tr("쓴 뒤 다시 읽지 못했다: {err}", err=rerr or tr(tr("파일 없음"))))
                got = mcp_adapters._as_mapping(reread.get("plugins")).get(name) or {}
                if bool(got.get("enabled", True)) != enabled:
                    raise WriteUnverified(tr("쓴 내용이 다시 읽은 파일과 다르다"))
                return {"status": "ok", "changed": True}
        except WriteRefused as e:
            return {"status": "failed", "reason": str(e), "changed": False}
        except WriteUnverified as e:
            return {"status": "unknown", "reason": str(e), "changed": None}
        except OSError as e:
            return {"status": "failed", "reason": tr("파일 오류: {name}", name=e.__class__.__name__),
                    "changed": False}

    if tool != "claude":
        return {"status": "failed", "changed": False,
                "reason": tr("{tool} 플러그인 토글은 아직 지원하지 않는다 — 공식 명령을 쓸 것", tool=tool)}
    import mcp_plugins

    # project(`<프로젝트>/.claude/settings.json`)는 저장소에 커밋되는 팀 공유
    # 파일이라 읽기만 한다(2026-09-28 확정) — mcp_plugins 머리말 참고.
    if scope == "project":
        return {"status": "failed", "changed": False,
                "reason": tr("프로젝트 공유 설정(.claude/settings.json)은 팀 전체에 적용되는 커밋 파일이라 여기서 바꾸지 않는다 — 저장소에서 직접 고칠 것")}
    if scope not in ("global", "local"):
        return {"status": "failed", "reason": tr("scope는 global/local"), "changed": False}

    wt = mcp_scan.find_worktree(worktree_id)
    if scope == "local" and wt is None:
        return {"status": "failed", "reason": tr("로컬 스코프는 기준 프로젝트를 지정해야 한다"),
                "changed": False}
    wt_path = wt.get("path") if wt else None

    installed, ierr = mcp_plugins.claude_installed(wt_path)
    if ierr:
        return {"status": "failed", "changed": False,
                "reason": tr("설치 여부를 확인하지 못했다: {err}", err=ierr)}
    if name not in installed:
        return {"status": "failed", "changed": False,
                "reason": tr("`{name}` 플러그인이 설치되지 않은 상태다 — 값만 바꿔도 켜지지 않는다. 먼저 `claude plugin install`로 설치할 것", name=name)}

    path = mcp_plugins.claude_settings_paths(wt_path)["user" if scope == "global" else "local"]
    warning = None
    if scope == "local" and not path.exists() and not _git_ignored(path):
        warning = tr("{file}이 git에 무시되지 않는다 — 커밋되지 않게 .gitignore에 추가할 것", file=path.name)

    try:
        with _locked(path):
            before = _load_json_for_write(path)
            after = json.loads(json.dumps(before))
            block = after.setdefault("enabledPlugins", {})
            if not isinstance(block, dict):
                raise WriteRefused(tr("enabledPlugins가 객체가 아니다 — 쓰기를 중단했다"))
            block[name] = enabled
            if before == after:
                return {"status": "ok", "changed": False}

            def verify(d):
                got = (d.get("enabledPlugins") or {}).get(name)
                return isinstance(got, bool) and got == enabled

            out = _commit_json(path, before, after, "enabledPlugins", verify,
                               allow_new_key=True)
            if warning:
                out["warning"] = warning
            return out
    except WriteRefused as e:
        return {"status": "failed", "reason": str(e), "changed": False}
    except WriteUnverified as e:
        return {"status": "unknown", "reason": str(e), "changed": None}
    except OSError as e:
        return {"status": "failed", "reason": tr("파일 오류: {name}", name=e.__class__.__name__),
                "changed": False}
