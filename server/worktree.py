"""워크트리 발견·생성·삭제 — N8/N44 (30-worktree.md). ADR-20: 정보구조의 주어는 워크트리다.

지켜야 할 것:
  1. 모든 git 호출은 subprocess **list** 인자 + cwd + timeout=10. 셸 문자열 금지
     (30-worktree.md §2).
  2. 경로 검증은 전부 `fsguard.resolve_under_roots`를 통과시킨다 — 여기서 새
     경계 검사 로직을 만들지 않는다(90-verification.md §4-3, 검사 위치 단일 원칙).
  3. `.env`는 fsguard 거부 목록에 있어 열람 API로는 못 보지만, 이 모듈이 직접
     복사하는 것은 허용된다(§3) — 단, 내용을 API 응답에 절대 싣지 않는다.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import agents
import fsguard
import mcp_env
import tmux_runner
# 순수 파서는 worktree_parse.py로 옮겼다. **이름은 여기 그대로 남긴다** —
# tests/test_worktree.py와 create_worktree가 `worktree.parse_*`로 쓰고 있고,
# 상태를 안 읽는 순수 함수라 재수출해도 monkeypatch와 어긋날 여지가 없다.
from worktree_ports import (  # noqa: F401
    DEFAULT_PORT_BASE,
    PORT_STEP,
    _load_ports_map,
    _remove_ports_entry,
    _update_ports_map,
    next_port_base,
)
from worktree_parse import (  # noqa: F401
    _PORT_KEYS,
    _branch_from_block,
    parse_shortstat,
    parse_worktree_porcelain,
    substitute_env_ports,
)

logger = logging.getLogger(__name__)

CACHE_TTL_SEC = 5.0
GIT_TIMEOUT = 10.0
MAX_SCAN_DEPTH = 3

_LOCKFILE_NAMES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")

_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")


class WorktreeError(Exception):
    """서버 라우트가 그대로 status+payload로 매핑하는 예외."""

    def __init__(self, status: int, payload: dict):
        super().__init__(payload.get("error", "error"))
        self.status = status
        self.payload = payload


# --- git 실행 헬퍼 ------------------------------------------------------------


def _git(args: list[str], cwd: Path, timeout: float = GIT_TIMEOUT) -> tuple[int, bytes, bytes]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, timeout=timeout, check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, b"", b"git not found"
    except subprocess.TimeoutExpired:
        return 124, b"", b"timeout"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        return child == parent or child.is_relative_to(parent)
    except (AttributeError, ValueError):
        return False


# --- diff 요약 / ahead-behind --------------------------------------------------
#
# 파싱(worktree_parse.py)과 달리 이 둘은 **git을 실행한다** — 순수 모듈에
# 두면 그 파일의 "부작용 없음"이라는 성질이 깨진다.

# --- diff 요약 / ahead-behind --------------------------------------------------


def _ahead_behind(path: Path) -> tuple[int, int]:
    rc, out, _ = _git(["rev-list", "--left-right", "--count", "@{u}...HEAD"], cwd=path)
    if rc != 0:
        return 0, 0
    parts = out.decode("utf-8", errors="replace").split()
    if len(parts) != 2:
        return 0, 0
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0
    return ahead, behind


def _changed_stat(path: Path) -> dict:
    rc, out, _ = _git(["diff", "HEAD", "--shortstat"], cwd=path)
    if rc != 0:
        return {"files": 0, "add": 0, "del": 0}
    return parse_shortstat(out.decode("utf-8", errors="replace"))


# --- 세션 매핑 -----------------------------------------------------------------


def _sessions_for_path(path: Path, all_panes) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for pane in all_panes:
        if not pane.path:
            continue
        try:
            pane_path = Path(pane.path).resolve()
        except OSError:
            continue
        if _is_within(pane_path, path) and pane.session not in seen:
            seen.add(pane.session)
            names.append(pane.session)
    return names


# --- 저장소 탐색 ---------------------------------------------------------------


def _find_git_entrypoints(roots: list[Path], max_depth: int = MAX_SCAN_DEPTH) -> list[Path]:
    found: list[Path] = []

    def walk(d: Path, depth_left: int) -> None:
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        if any(e.name == ".git" for e in entries):
            found.append(d)
            return
        if depth_left <= 0:
            return
        for e in entries:
            if not e.is_dir(follow_symlinks=False):
                continue
            if e.name in fsguard.EXCLUDE_DIRS or (e.name.startswith(".") and e.name != ".worktrees"):
                continue
            walk(Path(e.path), depth_left - 1)

    for root in roots:
        walk(root, max_depth)
    return found


def _build_entry(block: dict, repo_top: Path, all_panes, ports_map: dict) -> dict | None:
    raw_path = block.get("path")
    if not raw_path:
        return None
    try:
        path = fsguard.resolve_under_roots(raw_path)
    except fsguard.FsDenied:
        return None

    branch = _branch_from_block(block)
    head = (block.get("head") or "")[:8]
    is_main = path == repo_top
    repo_name = repo_top.name
    wt_id = hashlib.sha1(f"{repo_top}{path}".encode()).hexdigest()[:12]
    ahead, behind = _ahead_behind(path)
    changed = _changed_stat(path)
    sessions = _sessions_for_path(path, all_panes)
    ports = ports_map.get(wt_id, {}).get("ports")

    return {
        "id": wt_id,
        "repo": str(repo_top),
        "repoName": repo_name,
        "path": str(path),
        "branch": branch,
        "isMain": is_main,
        "head": head,
        "ahead": ahead,
        "behind": behind,
        "changed": changed,
        "sessions": sessions,
        "ports": ports,
        "host": "local",
    }


def _discover_all() -> list[dict]:
    roots = fsguard.get_roots()
    entrypoints = _find_git_entrypoints(roots)
    visited: set[str] = set()
    all_panes = tmux_runner.get_all_panes()
    ports_map = _load_ports_map()
    out: list[dict] = []

    for entry in sorted(entrypoints, key=str):
        try:
            entry_resolved = str(entry.resolve())
        except OSError:
            continue
        if entry_resolved in visited:
            continue

        rc, out_b, _ = _git(["worktree", "list", "--porcelain"], cwd=entry)
        if rc != 0:
            continue
        blocks = parse_worktree_porcelain(out_b.decode("utf-8", errors="replace"))
        if not blocks:
            continue

        try:
            repo_top = fsguard.resolve_under_roots(blocks[0]["path"])
        except fsguard.FsDenied:
            continue

        for b in blocks:
            try:
                visited.add(str(Path(b["path"]).resolve()))
            except OSError:
                pass

        for b in blocks:
            item = _build_entry(b, repo_top, all_panes, ports_map)
            if item:
                out.append(item)
    return out


_cache: dict = {"at": 0.0, "data": None}


def list_worktrees(force: bool = False) -> list[dict]:
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["at"] < CACHE_TTL_SEC:
        return _cache["data"]
    data = _discover_all()
    _cache["at"] = now
    _cache["data"] = data
    return data


def invalidate_cache() -> None:
    _cache["at"] = 0.0
    _cache["data"] = None


def find_by_id(wt_id: str, *, force: bool = True) -> dict | None:
    for w in list_worktrees(force=force):
        if w["id"] == wt_id:
            return w
    return None


# --- lockfile 배너 판정 (precheck + create 공용) --------------------------------


def _lockfile_mismatch(repo_top: Path, base: str) -> bool:
    """본 저장소의 현재 package.json(+락파일)과 base 커밋의 그것이 다른가.

    심링크로 node_modules를 공유하면 base 커밋 기준이 아니라 "본 저장소 워킹트리
    지금 상태" 기준의 node_modules가 붙는다 — 그게 base와 다르면 깨질 수 있다는
    경고(§3 배너)의 근거.
    """
    pkg_current = repo_top / "package.json"
    if not pkg_current.is_file():
        return False
    try:
        current_bytes = pkg_current.read_bytes()
    except OSError:
        return False

    lock_name = None
    lock_current_bytes = b""
    for name in _LOCKFILE_NAMES:
        p = repo_top / name
        if p.is_file():
            lock_name = name
            try:
                lock_current_bytes = p.read_bytes()
            except OSError:
                lock_current_bytes = b""
            break
    hash_current = hashlib.sha256(current_bytes + lock_current_bytes).hexdigest()

    rc, base_pkg, _ = _git(["show", f"{base}:package.json"], cwd=repo_top)
    if rc != 0:
        return False  # base에 package.json이 없음 — 비교 불가, mismatch로 취급하지 않는다.
    base_lock = b""
    if lock_name:
        rc2, out2, _ = _git(["show", f"{base}:{lock_name}"], cwd=repo_top)
        if rc2 == 0:
            base_lock = out2
    hash_base = hashlib.sha256(base_pkg + base_lock).hexdigest()
    return hash_current != hash_base


def precheck(repo: str, base: str) -> dict:
    repo_path = fsguard.resolve_under_roots(repo)
    warnings = []
    if _lockfile_mismatch(repo_path, base):
        warnings.append("lockfile_mismatch")
    return {"warnings": warnings}


# --- 에이전트 기동 --------------------------------------------------------------


def _session_name_for(repo_name: str, branch: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{repo_name}-{branch}").strip("-")
    return (f"wt-{slug}"[:64]) or "wt-session"


def _open_agent(path: Path, repo_name: str, branch: str, agent_name: str) -> dict:
    spec = agents.get(agent_name)
    if not spec:
        return {"ok": False, "error": "지원하지 않는 agent"}
    tmux_name = _session_name_for(repo_name, branch)
    if not tmux_runner.has_session(tmux_name):
        rc, _, err = tmux_runner.run(
            ["new-session", "-d", "-s", tmux_name, "-c", str(path)], timeout=GIT_TIMEOUT,
        )
        if rc != 0:
            return {"ok": False, "error": err.decode("utf-8", errors="replace")}

    # 97번 3단계 §2-4 — MCP 설정에는 참조(`${VAR}`)만 심으므로 값은 여기서
    # 채운다. **값이 아니라 파일 경로만** 명령줄에 실린다(mcp_env 머리말:
    # `tmux set-environment`에 값을 직접 넘기면 `ps`로 다른 계정에 보인다).
    # MCP를 못 읽어도 세션은 열려야 하므로 실패는 삼킨다.
    prefix = ""
    try:
        wt = next((w for w in list_worktrees() if w.get("path") == str(path)), None)
        env_file = mcp_env.prepare(tmux_name, wt.get("id") if wt else None)
        prefix = mcp_env.source_prefix(env_file)
        if env_file:
            tmux_runner.run(
                ["set-environment", "-t", tmux_name, mcp_env.ENV_POINTER, str(env_file)],
                timeout=GIT_TIMEOUT,
            )
    except Exception:
        logger.exception("MCP 환경 준비 실패 — 키 없이 세션을 연다")

    tmux_runner.run(["send-keys", "-t", tmux_name, prefix + spec.command, "Enter"],
                    timeout=GIT_TIMEOUT)
    return {"ok": True, "tmux_session": tmux_name}


# --- 만들기 (§3) --------------------------------------------------------------


def _validate_create_body(body: dict) -> tuple[str, str, str, str, dict, str, str, str | None]:
    repo_raw = str(body.get("repo") or "").strip()
    base = str(body.get("base") or "").strip()
    name = str(body.get("name") or "").strip()
    branch = str(body.get("branch") or "").strip() or f"feat/{name}"
    ports_cfg = body.get("ports") if isinstance(body.get("ports"), dict) else {}
    node_modules_mode = body.get("nodeModules", "symlink")
    env_mode = body.get("env", "inherit")
    agent_name = body.get("agent")

    if not repo_raw or not base or not name:
        raise WorktreeError(400, {"error": "repo/base/name이 필요합니다"})
    if not _SLUG_RE.fullmatch(name):
        raise WorktreeError(400, {"error": "name은 영숫자/-/_ 만 가능합니다"})
    if branch.startswith("-") or not _BRANCH_RE.fullmatch(branch):
        raise WorktreeError(400, {"error": "유효하지 않은 브랜치 이름입니다"})
    if node_modules_mode not in ("symlink", "copy", "none"):
        raise WorktreeError(400, {"error": "nodeModules 값이 유효하지 않습니다"})
    if env_mode not in ("inherit", "empty", "none"):
        raise WorktreeError(400, {"error": "env 값이 유효하지 않습니다"})
    if agent_name is not None and not agents.is_supported(agent_name):
        raise WorktreeError(400, {"error": f"지원하지 않는 agent: {agent_name}"})

    return repo_raw, base, name, branch, ports_cfg, node_modules_mode, env_mode, agent_name


def create_worktree(body: dict) -> dict:
    (repo_raw, base, name, branch, ports_cfg,
     node_modules_mode, env_mode, agent_name) = _validate_create_body(body)

    try:
        repo_path = fsguard.resolve_under_roots(repo_raw)
    except fsguard.FsDenied as e:
        raise WorktreeError(403, {"error": e.reason}) from e
    if not (repo_path / ".git").exists():
        raise WorktreeError(400, {"error": "git 저장소가 아닙니다"})

    repo_name = repo_path.name
    dest = Path.home() / ".worktrees" / repo_name / name
    try:
        dest_resolved = fsguard.resolve_under_roots(str(dest))
    except fsguard.FsDenied as e:
        raise WorktreeError(403, {"error": e.reason}) from e
    if dest_resolved.exists():
        raise WorktreeError(409, {"error": "이미 존재하는 경로입니다", "path": str(dest_resolved)})

    warnings: list[str] = []
    if _lockfile_mismatch(repo_path, base):
        warnings.append("lockfile_mismatch")

    # 1) git worktree add — 실패하면 아무것도 안 만들어졌으니 롤백 불필요.
    dest_resolved.parent.mkdir(parents=True, exist_ok=True)
    rc, _, err = _git(["worktree", "add", "-b", branch, str(dest_resolved), base], cwd=repo_path)
    if rc != 0:
        raise WorktreeError(400, {"error": "git worktree add 실패", "detail": err.decode("utf-8", errors="replace")})

    created_node_modules: Path | None = None
    opened: dict | None = None
    try:
        # 2) node_modules
        src_nm = repo_path / "node_modules"
        dest_nm = dest_resolved / "node_modules"
        if node_modules_mode == "symlink" and src_nm.exists():
            os.symlink(src_nm, dest_nm)
            created_node_modules = dest_nm
        elif node_modules_mode == "copy" and src_nm.exists():
            shutil.copytree(src_nm, dest_nm, symlinks=True)
            created_node_modules = dest_nm

        # 3) .env
        ports_enabled = bool(ports_cfg.get("enabled"))
        port_base = ports_cfg.get("base")
        if ports_enabled and not isinstance(port_base, int):
            port_base = next_port_base()

        if env_mode == "empty":
            (dest_resolved / ".env").write_text("", encoding="utf-8")
        elif env_mode == "inherit":
            src_env = repo_path / ".env"
            if src_env.is_file():
                text = src_env.read_text(encoding="utf-8", errors="replace")
                if ports_enabled and isinstance(port_base, int):
                    text = substitute_env_ports(text, port_base)
                (dest_resolved / ".env").write_text(text, encoding="utf-8")
        # env_mode == "none" → 아무 것도 하지 않는다.

        # 4) 포트 대역 기록
        wt_id = hashlib.sha1(f"{repo_path}{dest_resolved}".encode()).hexdigest()[:12]
        if ports_enabled and isinstance(port_base, int):
            _update_ports_map(wt_id, port_base)

        # 5) 에이전트
        if agent_name:
            opened = _open_agent(dest_resolved, repo_name, branch, agent_name)
    except Exception as e:
        try:
            if created_node_modules and created_node_modules.exists():
                if created_node_modules.is_symlink():
                    created_node_modules.unlink()
                else:
                    shutil.rmtree(created_node_modules, ignore_errors=True)
        except OSError:
            pass
        _git(["worktree", "remove", "--force", str(dest_resolved)], cwd=repo_path)
        _git(["branch", "-D", branch], cwd=repo_path)
        raise WorktreeError(500, {"error": "워크트리 생성 실패", "detail": str(e)}) from e

    invalidate_cache()
    entry = find_by_id(hashlib.sha1(f"{repo_path}{dest_resolved}".encode()).hexdigest()[:12], force=True)
    resp: dict = {"ok": True, "worktree": entry, "warnings": warnings}
    if agent_name:
        resp["opened"] = opened
    return resp


# --- 열기/삭제 ----------------------------------------------------------------


def open_worktree(wt_id: str) -> dict:
    wt = find_by_id(wt_id, force=True)
    if not wt:
        raise WorktreeError(404, {"error": "워크트리를 찾을 수 없습니다"})
    if wt["sessions"]:
        return {"ok": True, "tmux_session": wt["sessions"][0], "created": False}
    tmux_name = _session_name_for(wt["repoName"], wt["branch"])
    if not tmux_runner.has_session(tmux_name):
        rc, _, err = tmux_runner.run(
            ["new-session", "-d", "-s", tmux_name, "-c", wt["path"]], timeout=GIT_TIMEOUT,
        )
        if rc != 0:
            raise WorktreeError(500, {"error": "tmux 세션 생성 실패", "detail": err.decode("utf-8", errors="replace")})
    invalidate_cache()
    return {"ok": True, "tmux_session": tmux_name, "created": True}


def delete_worktree(wt_id: str, *, force: bool = False, kill_sessions: bool = False) -> dict:
    wt = find_by_id(wt_id, force=True)
    if not wt:
        raise WorktreeError(404, {"error": "워크트리를 찾을 수 없습니다"})
    if wt["isMain"]:
        raise WorktreeError(400, {"error": "메인 워크트리는 삭제할 수 없습니다"})

    path = Path(wt["path"])
    repo = Path(wt["repo"])

    if not force:
        rc, out, _ = _git(["status", "--porcelain"], cwd=path)
        if rc == 0 and out.strip():
            raise WorktreeError(409, {"error": "워크트리에 변경사항이 있습니다", "dirty": True})

    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    rc, _, err = _git(args, cwd=repo)
    if rc != 0:
        raise WorktreeError(500, {"error": "git worktree remove 실패", "detail": err.decode("utf-8", errors="replace")})

    if kill_sessions:
        for s in wt["sessions"]:
            tmux_runner.run(["kill-session", "-t", s], timeout=GIT_TIMEOUT)

    _remove_ports_entry(wt_id)
    invalidate_cache()
    return {"ok": True, "removed": str(path)}


# --- CLI (bin/fsh 가 서버 없이 직접 호출 — queue_store.py 와 같은 방식) ------------


def _cli_find(name: str) -> dict | None:
    items = list_worktrees(force=True)
    matches = [w for w in items if Path(w["path"]).name == name or w["branch"].endswith(name)]
    return matches[0] if len(matches) == 1 else None


def _cli(argv: list[str]) -> int:
    import sys

    cmd = argv[0] if argv else "list"
    rest = argv[1:]

    if cmd in ("list", "ls"):
        items = list_worktrees(force=True)
        print()
        if not items:
            print("  워크트리가 없습니다")
        else:
            for w in items:
                mark = "★" if w["isMain"] else " "
                sess = f" [{','.join(w['sessions'])}]" if w["sessions"] else ""
                print(f"  {mark} {w['repoName']}/{w['branch']}  {w['path']}{sess}")
        print()
        return 0

    if cmd == "add":
        if not rest:
            print("사용법: fsh worktree add <name> [--base b] [--ports] [--copy-modules] [--agent claude]", file=sys.stderr)
            return 2
        name = rest[0]
        opts = rest[1:]
        base = "HEAD"
        ports_enabled = False
        node_mode = "symlink"
        agent_name = None
        i = 0
        while i < len(opts):
            o = opts[i]
            if o == "--base" and i + 1 < len(opts):
                i += 1
                base = opts[i]
            elif o == "--ports":
                ports_enabled = True
            elif o == "--copy-modules":
                node_mode = "copy"
            elif o == "--agent" and i + 1 < len(opts):
                i += 1
                agent_name = opts[i]
            i += 1
        # bin/fsh cmd_worktree()가 server/ 로 cd한 뒤 이 스크립트를 부르므로,
        # os.getcwd()는 항상 server/다 — 호출자의 실제 cwd는 VT_WORKTREE_CWD로 받는다.
        repo_cwd = os.environ.get("VT_WORKTREE_CWD") or os.getcwd()
        body = {
            "repo": repo_cwd, "base": base, "name": name, "branch": f"feat/{name}",
            "ports": {"enabled": ports_enabled, "base": next_port_base() if ports_enabled else None},
            "nodeModules": node_mode, "env": "inherit", "agent": agent_name,
        }
        try:
            r = create_worktree(body)
        except WorktreeError as e:
            print(f"  ✗ {e.payload.get('error')}", file=sys.stderr)
            return 1
        print(f"  ✓ 생성됨: {r['worktree']['path']}")
        if r.get("warnings"):
            print(f"  ⚠ {', '.join(r['warnings'])}")
        return 0

    if cmd == "rm":
        if not rest:
            print("사용법: fsh worktree rm <name> [--force]", file=sys.stderr)
            return 2
        name = rest[0]
        force = "--force" in rest[1:]
        wt = _cli_find(name)
        if not wt:
            print(f"  ✗ '{name}' 을 찾을 수 없습니다(또는 여러 개와 일치)", file=sys.stderr)
            return 1
        try:
            r = delete_worktree(wt["id"], force=force)
        except WorktreeError as e:
            print(f"  ✗ {e.payload.get('error')}", file=sys.stderr)
            return 1
        print(f"  ✓ 삭제됨: {r['removed']}")
        return 0

    if cmd == "open":
        if not rest:
            print("사용법: fsh worktree open <name>", file=sys.stderr)
            return 2
        name = rest[0]
        wt = _cli_find(name)
        if not wt:
            print(f"  ✗ '{name}' 을 찾을 수 없습니다(또는 여러 개와 일치)", file=sys.stderr)
            return 1
        try:
            r = open_worktree(wt["id"])
        except WorktreeError as e:
            print(f"  ✗ {e.payload.get('error')}", file=sys.stderr)
            return 1
        suffix = " (신규)" if r["created"] else ""
        print(f"  ✓ {r['tmux_session']}{suffix}")
        return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
