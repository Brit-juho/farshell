"""디렉터리 순회 자식 프로세스(표준 라이브러리만 쓴다 — `python -I`로 실행된다).

부모(`resilience/scan.py`)가 stdin 으로 JSON 요청을 주면, 디렉터리에 **들어가기 직전마다**
`{"enter": 경로}` 한 줄을 stdout 에 쓰고, 끝나면 `{"result": ...}` 한 줄을 쓴다. 부모는 기한이 지나면
이 프로세스를 죽이고 마지막 `enter`를 "멈춘 디렉터리"로 기록한다. 스레드는 강제로 멈출 수 없지만
프로세스는 죽이고 버릴 수 있다.

macOS 에서는 시작하자마자 File Provider 의 "열면 그 자리에서 내려받기(materialize)"를 끈다
(`setiopolicy_np`, 자식 프로세스에 상속). 클라우드 폴더를 훑다가 내려받기를 기다리며 멈추지 않게 한다.

모드
- `git_entrypoints`: `.git`이 있는 디렉터리 찾기 — `worktree._find_git_entrypoints`와 같은 규칙.
- `search`: 파일명 퍼지 검색 — `routes/files._search_files`와 같은 규칙. 점수 함수(`fuzzy_score`)는 여기가
  정본이고 `routes/files.py`가 이것을 가져다 쓴다.
"""
from __future__ import annotations

import json
import os
import sys


def fuzzy_score(query: str, name: str):
    """subsequence 매치 — quickopen.js 의 `_fuzzyMatch`와 같은 규칙. 낮을수록 좋다. 매치 없으면 None."""
    if not query:
        return 0
    q = query.lower()
    n = name.lower()
    qi = 0
    first = -1
    last = 0
    for i, ch in enumerate(n):
        if qi < len(q) and ch == q[qi]:
            if first < 0:
                first = i
            last = i
            qi += 1
    if qi < len(q):
        return None
    return (last - first) + first


def _materialize_off() -> None:
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.dylib")
        # <sys/resource.h>: IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES=3, IOPOL_SCOPE_PROCESS=0,
        # IOPOL_MATERIALIZE_DATALESS_FILES_OFF=1
        libc.setiopolicy_np(3, 0, 1)
    except Exception:  # noqa: BLE001 — 실패해도 기한과 격리가 남는다
        pass


def _emit(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


class _Skip:
    def __init__(self, prefixes):
        self.prefixes = [p for p in prefixes if p]

    def __call__(self, path: str) -> bool:
        return any(_within(path, p) for p in self.prefixes)


def git_entrypoints(req: dict) -> dict:
    roots = req["roots"]
    max_depth = int(req.get("max_depth", 3))
    limit = int(req.get("limit", 200))
    exclude = set(req.get("exclude_names", []))
    containers = {c.lower() for c in req.get("container_names", [])}
    bonus = int(req.get("bonus", 1))
    skip = _Skip(req.get("skip_prefixes", []))
    found: list[str] = []
    skipped: list[str] = []
    truncated = False

    def walk(d: str, depth_left: int) -> None:
        nonlocal truncated
        if truncated or len(found) >= limit:
            truncated = truncated or len(found) >= limit
            return
        if skip(d):
            skipped.append(d)
            return
        _emit({"enter": d})
        try:
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
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if e.name in exclude:
                continue
            if e.name.startswith(".") and e.name != ".worktrees":
                continue
            b = bonus if e.name.lower() in containers else 0
            walk(e.path, depth_left - 1 + b)

    for root in roots:
        if truncated:
            break
        depth = max_depth + (bonus if os.path.basename(root.rstrip("/")).lower() in containers else 0)
        walk(root, depth)
    return {"found": found, "truncated": truncated, "skipped": skipped}


def search(req: dict) -> dict:
    root = req["root"]
    query = req.get("query", "")
    max_dirs = int(req.get("max_dirs", 4000))
    max_results = int(req.get("max_results", 50))
    exclude = set(req.get("exclude_dirs", []))
    allowed_dot = set(req.get("allowed_dot_dirs", []))
    deny_names = {n.lower() for n in req.get("deny_names", [])}
    deny_prefixes = tuple(p.lower() for p in req.get("deny_prefixes", []))
    deny_suffixes = tuple(s.lower() for s in req.get("deny_suffixes", []))
    skip = _Skip(req.get("skip_prefixes", []))

    def denied(name: str) -> bool:
        low = name.lower()
        return low in deny_names or low.startswith(deny_prefixes) or low.endswith(deny_suffixes)

    results = []
    scanned = 0
    truncated = False
    stack = [root]
    while stack:
        d = stack.pop()
        if scanned >= max_dirs:
            truncated = True
            break
        if skip(d):
            continue
        scanned += 1
        _emit({"enter": d})
        try:
            with os.scandir(d) as it:
                subdirs = []
                for de in it:
                    name = de.name
                    try:
                        is_dir = de.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if name in exclude or (name.startswith(".") and name not in allowed_dot):
                            continue
                        subdirs.append(de.path)
                        continue
                    if denied(name):
                        continue
                    score = fuzzy_score(query, name)
                    if score is None:
                        continue
                    try:
                        size = de.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = 0
                    results.append({"path": de.path, "name": name, "size": size, "_score": score})
                stack.extend(subdirs)
        except OSError:
            continue
    results.sort(key=lambda r: (r["_score"], len(r["path"])))
    over = len(results) > max_results
    # 부모가 거부 목록을 다시 거르므로 여유를 둬서 넘긴다.
    return {"results": results[: max_results * 2], "truncated": truncated or over}


MODES = {"git_entrypoints": git_entrypoints, "search": search}


def main() -> int:
    _materialize_off()
    req = json.loads(sys.stdin.readline() or "{}")
    fn = MODES.get(req.get("mode"))
    if fn is None:
        _emit({"error": "unknown mode"})
        return 2
    _emit({"result": fn(req)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
