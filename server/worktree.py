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
import threading
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
    parse_git_config_origin,
    parse_remote_url,
    parse_shortstat,
    parse_worktree_porcelain,
    substitute_env_ports,
)

logger = logging.getLogger(__name__)

# 폴링 주기(Rail.tsx WORKTREES_POLL_MS)보다 **길어야** 한다. 짧으면 모든 폴링이
# 캐시를 빗나가 매번 전체 탐색이 돈다 — 5초 TTL에 8초 폴링이라 적중률이 0이었다.
CACHE_TTL_SEC = 20.0
# 이 나이를 넘은 값은 더 이상 그대로 내주지 않고 호출자를 기다리게 한다.
# 서버가 오래 idle이었다가 처음 열린 화면이 2분 전 상태를 보면 안 된다.
CACHE_STALE_SEC = 120.0
GIT_TIMEOUT = 10.0
MAX_SCAN_DEPTH = 3

# 98-rail-repos-2.1.6.md §1 — 탐색 경계가 `$HOME`이면 깊이 3으로는 진짜 프로젝트가
# 밀려난다(`~/GitHub/side_project/tools/farshell`이 깊이 4라 통째로 빠졌다).
# 모든 루트의 깊이를 올리면 `~/Library`까지 파고들므로, **컨테이너로 흔히 쓰는
# 이름 밑에서만** 한 단계를 더 준다. 루트 자신이 아니라 루트 **바로 밑**의
# 디렉터리 이름으로 판정한다 — 경계가 `$HOME`이어도 `GitHub/` 밑이 깊어진다.
SCAN_CONTAINER_NAMES = {"github", "projects", "project", "src", "repos", "work", "dev", "workspace"}
SCAN_DEPTH_BONUS = 1

# 저장소 탐색에서만 건너뛸 디렉터리. **`fsguard.EXCLUDE_DIRS`에 넣지 않는다** —
# 그쪽은 파일 브라우저(routes/files.py)도 같이 쓰므로, 거기에 넣으면
# `~/Downloads`를 열람조차 못 하게 된다. 여기는 "git 저장소가 있을 리 없는 곳"
# 목록이지 "보면 안 되는 곳" 목록이 아니다.
SCAN_EXCLUDE_DIRS = {
    "Library", "Applications", "Downloads", "Pictures", "Music", "Movies",
    "Public", "Desktop", "go", ".cargo", ".rustup", "Parallels", "VirtualBox VMs",
}

# 한 번의 탐색이 모을 저장소 상한(§6-3 확정). 넘으면 멈추고 응답에
# `truncated: true`를 싣는다 — 무한히 자라는 목록은 레일이 감당하지 못한다.
MAX_REPOS = 200

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


def _root_depth(root: Path, base_depth: int) -> int:
    """루트 자체가 컨테이너 이름이면 한 단계 더 준다(`~/GitHub` 경계일 때)."""
    if root.name.lower() in SCAN_CONTAINER_NAMES:
        return base_depth + SCAN_DEPTH_BONUS
    return base_depth


def _find_git_entrypoints(
    roots: list[Path], max_depth: int = MAX_SCAN_DEPTH, limit: int = MAX_REPOS,
) -> tuple[list[Path], bool]:
    """git 저장소 진입점 목록과 "상한에 걸렸는가"를 돌려준다.

    깊이는 루트마다 다르다(§1). 루트 이름이 컨테이너면 +1, 그리고 탐색 중에
    만난 디렉터리 이름이 컨테이너면 그 아래로 다시 +1을 준다 — 경계가
    `$HOME`이어도 `GitHub/side_project/tools/farshell`(깊이 4)이 들어오고,
    `~/Library` 쪽은 보너스를 못 받아 예전 깊이 그대로다.
    """
    found: list[Path] = []
    truncated = False

    def walk(d: Path, depth_left: int) -> None:
        nonlocal truncated
        if truncated or len(found) >= limit:
            truncated = truncated or len(found) >= limit
            return
        try:
            # `with`로 닫는다 — routes/files.py와 같은 규칙. 예외 경로에서
            # 디렉터리 fd가 GC까지 남지 않도록.
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            return
        if any(e.name == ".git" for e in entries):
            found.append(d)
            if len(found) >= limit:
                truncated = True
            return
        if depth_left <= 0:
            return
        for e in entries:
            if truncated:
                return
            if not e.is_dir(follow_symlinks=False):
                continue
            if e.name in fsguard.EXCLUDE_DIRS or e.name in SCAN_EXCLUDE_DIRS:
                continue
            if e.name.startswith(".") and e.name != ".worktrees":
                continue
            bonus = SCAN_DEPTH_BONUS if e.name.lower() in SCAN_CONTAINER_NAMES else 0
            walk(Path(e.path), depth_left - 1 + bonus)

    for root in roots:
        if truncated:
            break
        walk(root, _root_depth(root, max_depth))
    return found, truncated


def _read_origin_remote(repo_top: Path) -> dict | None:
    """저장소의 `origin` 원격 → `{host, owner, name}`. §4.

    `.git`이 디렉터리면 그 안의 config, 파일이면(부가 워크트리·submodule)
    `gitdir:` 를 따라간 뒤 `commondir`로 본체를 찾는다. 어느 쪽도 못 읽으면
    None — 여기서 git을 실행하지는 않는다(서브프로세스를 늘리지 않는 것이
    §4 수용 기준이다).
    """
    git_path = repo_top / ".git"
    try:
        if git_path.is_dir():
            config_path = git_path / "config"
        elif git_path.is_file():
            head = git_path.read_text(encoding="utf-8", errors="replace").strip()
            if not head.startswith("gitdir:"):
                return None
            gitdir = Path(head.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (repo_top / gitdir).resolve()
            common = gitdir / "commondir"
            if common.is_file():
                rel = common.read_text(encoding="utf-8", errors="replace").strip()
                gitdir = (gitdir / rel).resolve() if rel else gitdir
            config_path = gitdir / "config"
        else:
            return None
        text = config_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return parse_remote_url(parse_git_config_origin(text))


def _build_entry(
    block: dict, repo_top: Path, all_panes, ports_map: dict, remote: dict | None = None,
) -> dict | None:
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
        # §4 — 레일 둘째 줄의 `github/fornerds`와 소유자 기반 색 배정의 입력.
        # remote가 없는 저장소는 None이고, 그때 프런트는 예전처럼 이름 해시로
        # 떨어진다.
        "remote": remote,
    }


def _discover_all() -> list[dict]:
    """저장소 목록. 상한에 걸렸으면 `_scan_truncated`에 남긴다 — 반환 모양은
    `list[dict]` 그대로다(캐시·테스트가 이 계약에 걸려 있다)."""
    global _scan_truncated
    roots = fsguard.get_roots()
    entrypoints, truncated = _find_git_entrypoints(roots)
    _scan_truncated = truncated
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

        remote = _read_origin_remote(repo_top)

        for b in blocks:
            item = _build_entry(b, repo_top, all_panes, ports_map, remote)
            if item:
                out.append(item)
    return out


# `_discover_all()`이 MAX_REPOS에 걸렸는지. 라우트가 응답에 실어 레일이
# "…외 더 있음"을 그린다. 캐시된 목록과 짝이라 갱신 시점이 같다.
_scan_truncated = False


def last_scan_truncated() -> bool:
    return _scan_truncated


# `_discover_all()`은 저장소 25개 기준 실측 1.0~1.7초가 걸리는 **동기** 작업이다
# (디렉터리 순회 0.003초 + 저장소마다 git 서브프로세스 여러 번). 잠금이 없으면
# 동시에 들어온 요청이 각자 전체 탐색을 직렬로 반복해 1.3초가 15~20초로 불어난다
# — 실측으로 확인한 정지의 실제 모양이다. 그래서 두 개의 잠금을 둔다:
#   _cache_lock    : 캐시 딕셔너리 읽기/쓰기만 감싸는 짧은 잠금
#   _discover_lock : 탐색은 한 번에 하나(single-flight). 기다렸다 깨어난 쪽은
#                    그 사이 채워진 값을 그대로 쓰고 다시 돌지 않는다
# ⚠ 잠금 순서는 항상 _discover_lock → _cache_lock 이다. 반대로 잡지 말 것.
_cache: dict = {"at": 0.0, "data": None}
_cache_lock = threading.Lock()
_discover_lock = threading.Lock()
_refresh_thread: threading.Thread | None = None


def _refresh_locked(accept_age: float) -> list[dict]:
    """탐색을 한 번만 돌린다. 잠금을 기다리는 사이 다른 호출자가 `accept_age`
    이내로 갱신해 뒀으면 그 결과를 그대로 반환한다(중복 탐색 제거).

    `accept_age=0.0`이면 어떤 값도 받아들이지 않으므로 항상 새로 탐색한다 —
    `force=True`가 이 경로다.
    """
    with _discover_lock:
        with _cache_lock:
            data, at = _cache["data"], _cache["at"]
        if data is not None and time.time() - at < accept_age:
            return data
        data = _discover_all()
        with _cache_lock:
            _cache["at"] = time.time()
            _cache["data"] = data
        return data


def _schedule_background_refresh() -> None:
    """낡은 값을 이미 응답에 실어 보낸 뒤, 다음 호출을 위해 뒤에서 갱신한다.
    이미 도는 중이면 아무것도 하지 않는다 — 폴링 주체가 여럿이라 이 검사가 없으면
    갱신 스레드가 요청 수만큼 생긴다."""
    global _refresh_thread
    with _cache_lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return
        _refresh_thread = threading.Thread(
            target=_background_refresh, name="worktree-refresh", daemon=True
        )
        thread = _refresh_thread
    thread.start()


def _background_refresh() -> None:
    try:
        _refresh_locked(CACHE_TTL_SEC)
    except Exception:
        # 갱신 실패는 조용히 넘긴다 — 캐시에는 직전 값이 그대로 남아 있고,
        # 다음 요청이 같은 경로를 다시 밟는다. 여기서 예외가 새면 스레드만 죽는다.
        logger.debug("워크트리 배경 갱신 실패", exc_info=True)


def list_worktrees(force: bool = False) -> list[dict]:
    """워크트리 목록. **동기·블로킹**이므로 async 핸들러에서는 반드시
    `asyncio.to_thread`로 감싸 부른다(routes/worktree.py). 이벤트 루프 위에서
    직접 부르면 그 1~2초 동안 서버 전체(HTTP·WS·PTY 출력)가 멈춘다.
    """
    if force:
        return _refresh_locked(0.0)

    with _cache_lock:
        data, at = _cache["data"], _cache["at"]

    if data is not None:
        age = time.time() - at
        if age < CACHE_TTL_SEC:
            return data
        if age < CACHE_STALE_SEC:
            # stale-while-revalidate: 낡았어도 즉시 돌려주고 갱신은 뒤에서.
            # 워크트리 목록은 몇 초 늦어도 해가 없는 종류의 정보이고, 호출자를
            # 1초 넘게 붙잡는 비용이 훨씬 크다.
            _schedule_background_refresh()
            return data

    return _refresh_locked(CACHE_TTL_SEC)


def invalidate_cache() -> None:
    with _cache_lock:
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
