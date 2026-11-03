"""CLI와 무관한 세션 작업 위치/Git 스냅샷. tmux 목록과 push가 같이 쓴다.

주어진 cwd 하나만 키로 쓰며 생성 시의 워크트리 태그를 섞지 않는다. **어느 cwd를 줄지는 호출자가 정한다** —
세션 목록(`routes/tmux.py`)은 터미널 pane cwd가 아니라 에이전트 CLI가 훅으로 알린 cwd를 우선한다.
Git I/O는 스레드에서 수행하고 같은 cwd 조회는 합친다. 실패는 nonrepo와 구분한다.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from pathlib import Path

import fsguard
from routes.git import _collect_status, _git

TTL = 10.0
RETRY_TTL = 2.0
MAX_ENTRIES = 256
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_lock = threading.Lock()
_workspace_cache: OrderedDict[str, tuple[float, dict, tuple[str, ...]]] = OrderedDict()
_repo_cache: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
_workspace_reading = tuple(threading.Lock() for _ in range(64))
_repo_reading = tuple(threading.Lock() for _ in range(64))
_reading = tuple(threading.Lock() for _ in range(64))


class ContextChanged(OSError):
    pass


def _git_path(top: Path, option: str) -> Path:
    rc, out = _git(top, "rev-parse", "--path-format=absolute", option)
    if rc or not out:
        raise OSError("worktree identity unavailable")
    # git-dir/common-dir 은 `.git` 안을 가리키므로 점 디렉토리 규칙을 끈다(경계·거부 목록은 유지).
    return fsguard.resolve_under_roots(out.removesuffix("\n"), check_hidden=False)


def _repository_root(top: Path, common: Path) -> str | None:
    key = str(common)
    with _repo_reading[hash(key) % len(_repo_reading)]:
        with _lock:
            hit = _repo_cache.get(key)
        if hit and time.monotonic() < hit[0]:
            candidate = hit[1]
        else:
            rc, out = _git(top, "worktree", "list", "--porcelain", "-z")
            if rc:
                raise OSError("worktree list unavailable")
            # Git lists the primary worktree first. NUL fields preserve whitespace/newlines.
            first = out.split("\0\0", 1)[0].split("\0")
            candidate = next((f[9:] for f in first if f.startswith("worktree ")), None)
            if "bare" in first:
                candidate = None
            with _lock:
                _repo_cache[key] = (time.monotonic() + TTL, candidate)
                _repo_cache.move_to_end(key)
                while len(_repo_cache) > MAX_ENTRIES:
                    _repo_cache.popitem(last=False)
        if not candidate:
            return None
        try:
            return str(fsguard.resolve_under_roots(candidate))
        except fsguard.FsDenied:
            # A linked tree may be allowed while its primary workspace is not.
            return None


def _workspace(top: Path) -> dict:
    key = str(top)
    unknown = {"kind": "unknown", "status": "unavailable", "repositoryRoot": None}
    with _workspace_reading[hash(key) % len(_workspace_reading)]:
        with _lock:
            hit = _workspace_cache.get(key)
        if hit and time.monotonic() < hit[0]:
            try:
                for path in hit[2]:
                    fsguard.resolve_under_roots(path, check_hidden=False)
                return dict(hit[1])
            except fsguard.FsDenied:
                return unknown
        try:
            git_dir = _git_path(top, "--git-dir")
            common = _git_path(top, "--git-common-dir")
            kind = "main" if git_dir == common else "linked"
            repository = str(top) if kind == "main" else _repository_root(top, common)
            data = {"kind": kind, "status": "ready", "repositoryRoot": repository}
            bounds = tuple(str(p) for p in (git_dir, common)) + ((repository,) if repository else ())
        except (OSError, ValueError, fsguard.FsDenied):
            data, bounds = unknown, ()
        with _lock:
            _workspace_cache[key] = (time.monotonic() + (TTL if data["status"] == "ready" else RETRY_TTL), data, bounds)
            _workspace_cache.move_to_end(key)
            while len(_workspace_cache) > MAX_ENTRIES:
                _workspace_cache.popitem(last=False)
        return dict(data)


def _with_workspace(data: dict, cwd: Path) -> dict:
    if not data.get("root"):
        return {**data, "worktree": None}
    top = fsguard.resolve_under_roots(data["root"])
    if not cwd.is_relative_to(top):
        raise ContextChanged("cwd changed during Git lookup")
    # Recheck even on a Git-status cache hit: boundary changes must not leak old metadata.
    workspace = _workspace(top)
    return {**data, "worktree": {**workspace, "name": top.name,
                               "relative": str(cwd.relative_to(top)) if cwd != top else ""}}


def _read(cwd: str) -> dict:
    p = fsguard.resolve_under_roots(cwd)
    if not p.is_dir():
        raise OSError("cwd missing")
    rc, out = _git(p, "rev-parse", "--show-toplevel")
    if rc in (124, 127):
        raise OSError("git unavailable")
    if rc != 0:
        # 손상된 저장소를 비저장소로 표시하지 않는다. 허용 경계 안의 Git 표식만 확인한다.
        for ancestor in (p, *p.parents):
            try:
                allowed = fsguard.resolve_under_roots(str(ancestor))
            except fsguard.FsDenied:
                break
            if (allowed / ".git").exists():
                raise OSError("git repository unavailable")
        return {"status": "nonrepo", "cwd": cwd, "root": None,
                "branch": None, "changed": None}
    # Git의 상위 탐색 결과에도 같은 경계를 적용한다.
    top = fsguard.resolve_under_roots(out.removesuffix("\n"))
    data = _collect_status(top, top=top)
    if data.get("error") or not data.get("repo"):
        raise OSError("git status unavailable")
    if _git_path(p, "--show-toplevel") != top:
        raise ContextChanged("Git root changed during lookup")
    return {"status": "ready", "cwd": cwd, "root": str(top),
            "branch": data.get("branch"),
            "changed": {"files": len(data["files"]),
                        "add": data.get("insertions", 0),
                        "del": data.get("deletions", 0)}}


def _context(cwd: str) -> dict:
    if not cwd:
        return {"status": "unavailable", "cwd": "", "root": None,
                "branch": None, "changed": None, "worktree": None}
    reading = _reading[hash(cwd) % len(_reading)]
    with reading:
        now = time.monotonic()
        with _lock:
            hit = _cache.get(cwd)
        try:
            allowed = fsguard.resolve_under_roots(cwd)
            if not allowed.is_dir():
                raise OSError("cwd missing")
            if hit and hit[1].get("root"):
                fsguard.resolve_under_roots(hit[1]["root"])
            if hit and now < hit[0]:
                with _lock:
                    if cwd in _cache:
                        _cache.move_to_end(cwd)
                return _with_workspace(hit[1], allowed)
            data = _with_workspace(_read(cwd), allowed)
        except (fsguard.FsDenied, ContextChanged):
            # 경계나 작업 공간이 바뀌었으면 이전 성공 값도 반환하지 않는다.
            data = {"status": "unavailable", "cwd": cwd, "root": None,
                    "branch": None, "changed": None, "worktree": None}
        except (OSError, ValueError):
            # 같은 cwd의 마지막 성공만 보존. 다른 디렉터리의 브랜치를 가져오지 않는다.
            previous = hit[1] if hit else {"cwd": cwd, "root": None, "branch": None, "changed": None}
            try:
                previous = _with_workspace(previous, allowed)
            except (OSError, ValueError, fsguard.FsDenied):
                previous = {"cwd": cwd, "root": None, "branch": None, "changed": None, "worktree": None}
            data = {**previous, "status": "unavailable"}
        ttl = RETRY_TTL if data["status"] == "unavailable" or (data.get("worktree") or {}).get("status") == "unavailable" else TTL
        with _lock:
            _cache[cwd] = (time.monotonic() + ttl, data)
            _cache.move_to_end(cwd)
            while len(_cache) > MAX_ENTRIES:
                _cache.popitem(last=False)
        return data


async def contexts_for(cwds: list[str]) -> dict[str, dict]:
    """스냅샷당 Git 동시 조회를 제한하고 같은 cwd는 한 번만 읽는다."""
    gate = asyncio.Semaphore(4)

    async def one(cwd):
        async with gate:
            return cwd, await asyncio.to_thread(_context, cwd)

    result = dict(await asyncio.gather(*(one(c) for c in dict.fromkeys(cwds))))
    roots = list(dict.fromkeys(d["root"] for d in result.values() if d.get("root")))
    # Resolve duplicate basenames with the shortest distinct trailing path, not CLI names.
    for cwd, data in result.items():
        if not data.get("worktree"):
            continue
        root = Path(data["root"])
        peers = [Path(r) for r in roots if Path(r).name == root.name]
        count = 1
        while any(p != root and p.parts[-count:] == root.parts[-count:] for p in peers):
            count += 1
        result[cwd] = {**data, "worktree": {**data["worktree"], "name": str(Path(*root.parts[-count:]))}}
    return result
