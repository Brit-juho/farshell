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
from resilience import mounts as res_mounts, pools as res_pools
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
# 2026-11-05 — 같은 줄의 잠금을 쥔 스레드가 NFS 위 cwd 에서 멈추면, 해시가 같은 다른 cwd 들까지
# `/api/tmux/sessions`가 같이 멈췄다. 잠금은 이 시간만 기다리고, 한 cwd 조회 전체에도 기한을 둔다.
STRIPE_WAIT = 3.0
CONTEXT_TIMEOUT = 8.0
# 스냅샷 하나가 아니라 서버 전체에서 동시에 도는 cwd 조회 수(예전엔 호출마다 새 Semaphore(4)였다).
_GLOBAL_LIMIT: tuple | None = None


def _unavailable(cwd: str, reason: str) -> dict:
    return {"status": "unavailable", "cwd": cwd, "root": None, "branch": None,
            "changed": None, "worktree": None, "reason": reason}


class ContextChanged(OSError):
    pass


def _resolve(raw: str, **kwargs):
    """이 모듈은 Git 위치 정보만 읽는다 — Claude Code가 만든 워크트리(`.claude/worktrees/<이름>`)도 보이게
    `git_meta`로 경계를 확인한다(fsguard의 좁은 예외, 파일 열람은 그대로 막힌다)."""
    return fsguard.resolve_under_roots(raw, git_meta=True, **kwargs)


def _git_path(top: Path, option: str) -> Path:
    rc, out = _git(top, "rev-parse", "--path-format=absolute", option)
    if rc or not out:
        raise OSError("worktree identity unavailable")
    # git-dir/common-dir 은 `.git` 안을 가리키므로 점 디렉토리 규칙을 끈다(경계·거부 목록은 유지).
    return _resolve(out.removesuffix("\n"), check_hidden=False)


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
            return str(_resolve(candidate))
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
                    _resolve(path, check_hidden=False)
                return dict(hit[1])
            except fsguard.FsDenied:
                return unknown
        try:
            git_dir = _git_path(top, "--git-dir")
            common = _git_path(top, "--git-common-dir")
            kind = "main" if git_dir == common else "linked"
            repository = str(top) if kind == "main" else _repository_root(top, common)
            # gitDir·commonDir: 같은 저장소 판정은 common-dir 비교다(이름·경로 모양으로 추측하지 않는다).
            data = {"kind": kind, "status": "ready", "repositoryRoot": repository,
                    "gitDir": str(git_dir), "commonDir": str(common)}
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
    top = _resolve(data["root"])
    if not cwd.is_relative_to(top):
        raise ContextChanged("cwd changed during Git lookup")
    # Recheck even on a Git-status cache hit: boundary changes must not leak old metadata.
    workspace = _workspace(top)
    return {**data, "worktree": {**workspace, "name": top.name,
                               "relative": str(cwd.relative_to(top)) if cwd != top else ""}}


def _read(cwd: str) -> dict:
    p = _resolve(cwd)
    if not p.is_dir():
        raise OSError("cwd missing")
    rc, out = _git(p, "rev-parse", "--show-toplevel")
    if rc in (124, 127):
        raise OSError("git unavailable")
    if rc != 0:
        # 손상된 저장소를 비저장소로 표시하지 않는다. 허용 경계 안의 Git 표식만 확인한다.
        for ancestor in (p, *p.parents):
            try:
                allowed = _resolve(str(ancestor))
            except fsguard.FsDenied:
                break
            if (allowed / ".git").exists():
                raise OSError("git repository unavailable")
        return {"status": "nonrepo", "cwd": cwd, "root": None,
                "branch": None, "changed": None}
    # Git의 상위 탐색 결과에도 같은 경계를 적용한다.
    top = _resolve(out.removesuffix("\n"))
    data = _collect_status(top, top=top)
    if data.get("error") or not data.get("repo"):
        raise OSError("git status unavailable")
    if _git_path(p, "--show-toplevel") != top:
        raise ContextChanged("Git root changed during lookup")
    return {"status": "ready", "cwd": cwd, "root": str(top),
            "branch": data.get("branch"),
            # 같은 status 호출의 헤더 값이라 추가 비용이 없다(예전에는 계산하고 버렸다).
            "ahead": int(data.get("ahead") or 0), "behind": int(data.get("behind") or 0),
            "changed": {"files": len(data["files"]),
                        "add": data.get("insertions", 0),
                        "del": data.get("deletions", 0)}}


def _context(cwd: str) -> dict:
    if not cwd:
        return {"status": "unavailable", "cwd": "", "root": None,
                "branch": None, "changed": None, "worktree": None}
    # 원격·가상 마운트(NFS 등) 위의 cwd 는 git·resolve 를 하지 않는다 — 그 한 번이 멈출 수 있다.
    skip = res_mounts.skip_for_scan(cwd)
    if skip:
        return _unavailable(cwd, "protected" if skip.startswith("macos-protected") else "remote_mount")
    reading = _reading[hash(cwd) % len(_reading)]
    if not reading.acquire(True, STRIPE_WAIT):
        with _lock:
            hit = _cache.get(cwd)
        if not hit:
            return _unavailable(cwd, "busy")
        # 기다리지 못해 예전 값을 줄 때도 경계는 지금 기준으로 다시 본다 — 그 사이 허용 범위에서 빠진 경로의
        # 브랜치·변경량을 내보내지 않는다(Git은 다시 읽지 않는다).
        try:
            _resolve(cwd)
            for path in (hit[1].get("root"), (hit[1].get("worktree") or {}).get("repositoryRoot")):
                if path:
                    _resolve(path)
        except (fsguard.FsDenied, OSError, ValueError):
            return _unavailable(cwd, "busy")
        return {**hit[1], "status": "unavailable", "reason": "busy"}
    try:
        return _context_locked(cwd)
    finally:
        reading.release()


def _context_locked(cwd: str) -> dict:
    if True:
        now = time.monotonic()
        with _lock:
            hit = _cache.get(cwd)
        try:
            allowed = _resolve(cwd)
            if not allowed.is_dir():
                raise OSError("cwd missing")
            if hit and hit[1].get("root"):
                _resolve(hit[1]["root"])
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


def disambiguate(result: dict[str, dict], shown: list[str] | None = None) -> dict[str, dict]:
    """폴더 이름이 같은 작업 트리끼리 구분되게 `worktree.name`에 위 폴더를 붙인다(가장 짧게 구분되는 꼬리 경로).
    `shown`이 있으면 그 cwd들의 root끼리만 비교한다 — 화면에 없는 root 때문에 이름이 바뀌지 않게."""
    keys = list(dict.fromkeys(shown)) if shown is not None else list(result)
    roots = list(dict.fromkeys(result[k]["root"] for k in keys if k in result and result[k].get("root")))
    out = dict(result)
    for cwd in keys:
        data = result.get(cwd)
        if not data or not data.get("worktree"):
            continue
        root = Path(data["root"])
        peers = [Path(r) for r in roots if Path(r).name == root.name]
        count = 1
        while any(p != root and p.parts[-count:] == root.parts[-count:] for p in peers):
            count += 1
        out[cwd] = {**data, "worktree": {**data["worktree"], "name": str(Path(*root.parts[-count:]))}}
    return out


async def contexts_for(cwds: list[str], *, name_worktrees: bool = True) -> dict[str, dict]:
    """서버 전체에서 Git 동시 조회를 제한하고 같은 cwd는 한 번만 읽는다. cwd 하나가 기한을 넘기면
    그 cwd 만 unavailable 이고 나머지는 그대로 답한다(`git` 풀, `CONTEXT_TIMEOUT`).
    `name_worktrees=False`면 동명 구분을 호출자가 화면에 쓸 것만 골라 `disambiguate`로 한다."""
    global _GLOBAL_LIMIT
    loop = asyncio.get_running_loop()
    if _GLOBAL_LIMIT is None or _GLOBAL_LIMIT[0] is not loop:
        _GLOBAL_LIMIT = (loop, asyncio.Semaphore(4))     # 세마포어는 루프에 묶인다(테스트는 루프를 여럿 쓴다)
    gate = _GLOBAL_LIMIT[1]

    async def one(cwd):
        async with gate:
            try:
                return cwd, await res_pools.run("git", _context, cwd, op="session_context", key=cwd,
                                                timeout=CONTEXT_TIMEOUT)
            except res_pools.OpTimeout:
                return cwd, _unavailable(cwd, "timeout")
            except res_pools.Busy:
                return cwd, _unavailable(cwd, "busy")

    result = dict(await asyncio.gather(*(one(c) for c in dict.fromkeys(cwds))))
    return disambiguate(result) if name_worktrees else result
