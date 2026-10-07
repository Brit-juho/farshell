"""CLI와 무관한 세션 작업 위치/Git 스냅샷. tmux 목록과 push가 같이 쓴다.

현재 cwd만 키로 쓰며 생성 시의 워크트리 태그나 agent hook cwd를 섞지 않는다.
Git I/O는 스레드에서 수행하고 같은 cwd 조회는 합친다. 실패는 nonrepo와 구분한다.
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict

import fsguard
from routes.git import _collect_status, _git

TTL = 10.0
RETRY_TTL = 2.0
MAX_ENTRIES = 256
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_lock = threading.Lock()
_reading = tuple(threading.Lock() for _ in range(64))


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
    top = fsguard.resolve_under_roots(out.strip())
    data = _collect_status(top, top=top)
    if data.get("error") or not data.get("repo"):
        raise OSError("git status unavailable")
    return {"status": "ready", "cwd": cwd, "root": str(top),
            "branch": data.get("branch"),
            "changed": {"files": len(data["files"]),
                        "add": data.get("insertions", 0),
                        "del": data.get("deletions", 0)}}


def _context(cwd: str) -> dict:
    if not cwd:
        return {"status": "unavailable", "cwd": "", "root": None,
                "branch": None, "changed": None}
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
                return hit[1]
            data = _read(cwd)
        except fsguard.FsDenied:
            # 경계가 바뀌었으면 이전 성공 값도 반환하지 않는다.
            data = {"status": "unavailable", "cwd": cwd, "root": None,
                    "branch": None, "changed": None}
        except (OSError, ValueError):
            # 같은 cwd의 마지막 성공만 보존. 다른 디렉터리의 브랜치를 가져오지 않는다.
            data = {**(hit[1] if hit else {"cwd": cwd, "root": None,
                                          "branch": None, "changed": None}),
                    "status": "unavailable"}
        ttl = RETRY_TTL if data["status"] == "unavailable" else TTL
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

    return dict(await asyncio.gather(*(one(c) for c in dict.fromkeys(cwds))))
