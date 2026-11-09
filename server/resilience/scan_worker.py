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
- `grep`: 파일 **내용** 검색(파일 개편 단계 6) — `fs_grep.py`가 부른다. 후보 목록(저장소 안이면
  `git ls-files`, 밖이면 순회) → 거부 규칙으로 먼저 빼기 → 남은 파일만 읽어 찾는다. 찾은 줄은 파일마다
  `{"hits": …}` 한 줄로 바로 내보낸다 — 부모가 기한에 이 프로세스를 죽여도 그때까지 찾은 결과는 남는다.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import time


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


# --- 내용 검색(grep) ----------------------------------------------------------------------------------

def _glob_re(pat: str) -> str:
    """glob 하나 → 정규식 본문. `**`는 `/`를 넘고, `*`·`?`는 한 조각 안에서만, `{a,b}`는 고르기, `[...]`는 문자 묶음."""
    out = []
    i, n = 0, len(pat)
    depth = 0
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                i += 2
                if i < n and pat[i] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pat.find("]", i + 2)
            if j < 0:
                out.append(re.escape(c))
            else:
                body = pat[i + 1:j]
                neg = body.startswith("!")
                body = re.sub(r"([\\\]\[^])", r"\\\1", body[1:] if neg else body)
                out.append("[" + ("^" if neg else "") + body + "]")
                i = j
        elif c == "{":
            depth += 1
            out.append("(?:")
        elif c == "}" and depth:
            depth -= 1
            out.append(")")
        elif c == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(c))
        i += 1
    out.append(")" * depth)
    return "".join(out)


class GlobSet:
    """포함·제외 glob 목록. 경로는 검색 루트 기준 상대경로(`/` 구분).

    - `/`가 없는 패턴(`*.ts`, `docs`)은 경로의 **어느 조각**과 맞아도 걸린다 — 파일 이름이든 폴더 이름이든.
    - `/`가 있는 패턴(`frontend/**`, `src/app`)은 루트 기준이고, 경로 전체나 그 **앞부분 폴더**와 맞으면 걸린다
      (`frontend/js`는 그 아래 전부). 앞의 `./`·`/`는 뗀다.
    """

    def __init__(self, patterns):
        self.any_part = []
        self.anchored = []
        for raw in patterns or []:
            p = raw.strip()
            while p.startswith("./"):
                p = p[2:]
            p = p.lstrip("/")
            if not p:
                continue
            if p.endswith("/"):
                p = p + "**"
            rx = re.compile(r"\A(?:" + _glob_re(p) + r")\Z", re.DOTALL)
            (self.anchored if "/" in p else self.any_part).append(rx)

    def __bool__(self):
        return bool(self.any_part or self.anchored)

    def match(self, rel: str) -> bool:
        parts = rel.split("/")
        for part in parts:
            for rx in self.any_part:
                if rx.match(part):
                    return True
        if self.anchored:
            for k in range(1, len(parts) + 1):
                prefix = "/".join(parts[:k])
                for rx in self.anchored:
                    if rx.match(prefix):
                        return True
        return False


def _u16(s: str) -> int:
    """JS 문자열 길이(UTF-16 단위). 화면의 열 번호·강조 범위가 이 단위다."""
    if s.isascii():
        return len(s)
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


class _Rules:
    """후보 경로 거르기 — `fsguard`의 거부 이름·거부 폴더·점 폴더·제외 폴더 규칙(값은 부모가 넘긴다).

    경로는 검색 루트 기준이다. 루트 자신과 그 위는 부모가 `resolve_under_roots`로 이미 판정했다."""

    def __init__(self, req: dict):
        self.exclude = set(req.get("exclude_dirs", []))
        self.allowed_dot = set(req.get("allowed_dot_dirs", []))
        self.deny_names = {n.lower() for n in req.get("deny_names", [])}
        self.deny_prefixes = tuple(p.lower() for p in req.get("deny_prefixes", []))
        self.deny_suffixes = tuple(s.lower() for s in req.get("deny_suffixes", []))
        self.deny_dirs = set(req.get("deny_dir_parts", []))
        self.skip = _Skip(req.get("skip_prefixes", []))

    def name_denied(self, name: str) -> bool:
        low = name.lower()
        return low in self.deny_names or low.startswith(self.deny_prefixes) or low.endswith(self.deny_suffixes)

    def dir_denied(self, name: str) -> bool:
        return (name in self.exclude or name in self.deny_dirs
                or (name.startswith(".") and name not in self.allowed_dot))

    def path_denied(self, rel: str) -> bool:
        parts = rel.split("/")
        if any(p in ("", ".", "..") for p in parts):
            return True
        dirs = parts[:-1]
        for i, part in enumerate(dirs):
            if self.dir_denied(part):
                return True
            if i + 1 < len(dirs) and f"{part}/{dirs[i + 1]}" in self.deny_dirs:
                return True
        return self.name_denied(parts[-1])


def _git_candidates(root: str, req: dict, limit: int):
    """저장소 안이면 `git ls-files`(추적 + 무시되지 않은 새 파일, `.gitignore`를 따른다). 경로는 root 기준.
    저장소가 아니거나 git이 없으면 None(→ 순회). 돌려주는 것: (경로 목록, 상한에 닿았나)."""
    argv = req.get("git_argv")
    if not argv:
        return None
    root_n = root.rstrip("/") or "/"
    inside = []
    for pre in req.get("skip_prefixes") or []:
        pre = (pre or "").rstrip("/")
        if not pre:
            continue
        if _within(root_n, pre):
            return [], False                    # 루트 전체가 건너뛸 자리 — git을 띄우지도 않는다
        if _within(pre, root_n):
            inside.append(os.path.relpath(pre, root_n))
    if inside:
        # 건너뛸 자리(위험한 마운트·File Provider·iCloud·격리된 폴더)를 **git이 걷기 전에** 뺀다. 목록을 받은 뒤
        # 거르면 git 자신이 그 아래를 이미 걸었다(홈이 저장소면 ~/Library 전체). 제외 pathspec이면 git이 그 폴더에
        # 들어가지 않는다. `literal`: 이름의 `*`·`?`를 글롭으로 보지 않는다.
        argv = list(argv) + ["--"] + [f":(exclude,literal){rel}" for rel in sorted(inside)]
    env = dict(os.environ)
    env.update(req.get("git_env") or {})
    try:
        p = subprocess.Popen(list(argv), cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=env)
    except OSError:
        return None
    out = []
    buf = b""
    over = False
    try:
        while True:
            chunk = p.stdout.read1(65536) if hasattr(p.stdout, "read1") else p.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            *items, buf = buf.split(b"\0")
            out.extend(os.fsdecode(item) for item in items if item)
            if len(out) >= limit:
                over = True
                break
    finally:
        if over:
            p.kill()
        p.stdout.close()
        rc = p.wait()
    if not over and rc != 0:
        return None
    return sorted(set(out))[:limit], over


def _walk_candidates(root: str, rules: _Rules, max_dirs: int, limit: int):
    """저장소 밖 — 파일명 검색과 같은 순회(제외·점·거부 폴더는 들어가지 않고, 폴더 심링크는 따라가지 않는다)."""
    out = []
    stack = [""]
    scanned = 0
    over = False
    while stack:
        if scanned >= max_dirs or len(out) >= limit:
            over = True
            break
        rel_dir = stack.pop()
        d = os.path.join(root, rel_dir) if rel_dir else root
        if rules.skip(d):
            continue
        scanned += 1
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        subdirs = []
        for de in entries:
            rel = f"{rel_dir}/{de.name}" if rel_dir else de.name
            try:
                if de.is_dir(follow_symlinks=False):
                    if not rules.dir_denied(de.name):
                        subdirs.append(rel)
                    continue
            except OSError:
                continue
            out.append(rel)
        stack.extend(reversed(subdirs))
    return out[:limit], over


_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class _Opener:
    """검색 루트 안의 파일을 **한 조각씩** `O_NOFOLLOW`로 연다(`fsguard.open_resolved`와 같은 걸음 — 이 프로세스는
    표준 라이브러리만 쓰므로 그 규칙을 여기 둔다). 루트 fd에서 시작해 중간 폴더는 `O_DIRECTORY|O_NOFOLLOW`, 마지막
    조각은 `O_NOFOLLOW|O_NONBLOCK`. 저장소 색인을 꾸며 `sub/secret` 같은 경로를 넣고 `sub`를 바깥 폴더로 가는
    심링크로 두어도 `ELOOP`/`ENOTDIR`로 열리지 않는다 — 마지막 조각에만 건 `O_NOFOLLOW`로는 이 틈을 못 막는다.
    후보는 정렬돼 있으므로 직전 폴더의 fd 하나만 붙들어 둔다."""

    _DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOEXEC

    def __init__(self, root: str):
        # 루트 자신은 부모가 resolve해 판정한 경로다(심링크가 이미 펼쳐져 있다).
        self.root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | _CLOEXEC)
        self.dir_key = None
        self.dir_fd = None

    def _dir(self, parts: list) -> int:
        key = tuple(parts)
        if key == self.dir_key:
            return self.dir_fd
        self._drop_dir()
        fd = os.dup(self.root_fd)
        try:
            for part in parts:
                nxt = os.open(part, self._DIR, dir_fd=fd)
                os.close(fd)
                fd = nxt
        except BaseException:
            os.close(fd)
            raise
        self.dir_key, self.dir_fd = key, fd
        return fd

    def _drop_dir(self) -> None:
        if self.dir_fd is not None:
            os.close(self.dir_fd)
        self.dir_key = self.dir_fd = None

    def open(self, rel: str) -> int:
        parts = rel.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise OSError("bad path")
        d = self._dir(parts[:-1])
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | _CLOEXEC, dir_fd=d)

    def close(self) -> None:
        self._drop_dir()
        os.close(self.root_fd)


def _read_file(opener: _Opener, rel: str, max_bytes: int):
    """일반 파일만, 경로의 어느 조각에 있는 심링크도 따라가지 않고(`_Opener`) 연다. (바이트, 사유) — 사유는
    None·"large"·"unreadable". 루트 안의 심링크 파일·폴더가 경계 밖(`~/.ssh/id_rsa` 등)을 가리켜도 열리지 않는다."""
    try:
        fd = opener.open(rel)
    except OSError:
        return None, "unreadable"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, "unreadable"
        if st.st_size > max_bytes:
            return None, "large"
        chunks = []
        left = max_bytes + 1
        while left > 0:
            b = os.read(fd, min(left, 1 << 20))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        data = b"".join(chunks)
        if len(data) > max_bytes:
            return None, "large"
        return data, None
    except OSError:
        return None, "unreadable"
    finally:
        os.close(fd)


def _line_hit(line: str, spans, line_no: int, max_chars: int, max_ranges: int) -> dict:
    """한 줄의 결과. `col`은 첫 일치의 1부터 센 열(UTF-16), `ranges`는 보여 주는 `text` 안의 [시작, 끝)(UTF-16).
    줄이 `max_chars`보다 길면 첫 일치 앞쪽 조금부터 `max_chars`만 남기고 `cut`(잘라 낸 앞부분 길이, UTF-16)을 붙인다."""
    first = spans[0][0]
    start = 0
    if len(line) > max_chars:
        start = max(0, min(first - max_chars // 5, len(line) - max_chars))
    shown = line[start:start + max_chars]
    base = _u16(line[:start]) if start else 0
    ranges = []
    for a, b in spans[:max_ranges]:
        a2, b2 = max(a, start), min(b, start + len(shown))
        if a2 >= b2:
            continue
        ranges.append([_u16(line[start:a2]), _u16(line[start:b2])])
    hit = {"line": line_no, "col": _u16(line[:first]) + 1, "text": shown, "ranges": ranges}
    if start or start + len(shown) < len(line):
        hit["cut"] = base
    return hit


def _file_hits(text: str, pat, max_lines: int, max_ranges: int):
    """찾은 줄들: [(줄 번호, 줄 시작, 줄 끝, [(일치 시작, 끝) — 줄 기준])]. 빈 일치는 건너뛴다(부모가 그런 정규식을 막는다).

    한 줄의 일치는 `max_ranges`개까지만 모으고 그 줄의 나머지는 건너뛴다 — 화면(`_line_hit`)은 그만큼만 쓰는데,
    2MB 한 줄에 정규식 `.`이면 일치 200만 개(튜플 수백 MB)를 쌓게 된다. `pattern.search(text, pos)`는 `^`·뒤보기를
    문자열 전체 기준으로 보므로 `finditer`와 같은 일치를 낸다."""
    max_ranges = max(1, max_ranges)
    hits = []
    line_no = 1
    pos = 0          # line_no 줄이 시작하는 위치
    cur = None
    at = 0
    n = len(text)
    while at <= n:
        m = pat.search(text, at)
        if m is None:
            break
        a, b = m.span()
        if a == b:
            at = a + 1
            continue
        at = b
        if a >= pos:
            nl = text.count("\n", pos, a)
            if nl:
                line_no += nl
                pos = text.rfind("\n", 0, a) + 1
        if cur is None or cur[0] != line_no:
            if cur is not None:
                hits.append(cur)
                if len(hits) >= max_lines:
                    return hits
            end = text.find("\n", pos)
            cur = (line_no, pos, len(text) if end < 0 else end, [])
        cur[3].append((a - cur[1], min(b, cur[2]) - cur[1]))
        if len(cur[3]) >= max_ranges:
            at = max(b, cur[2] + 1)              # 이 줄은 찼다 — 다음 줄부터
    if cur is not None:
        hits.append(cur)
    return hits


def grep(req: dict) -> dict:
    started = time.monotonic()
    roots = req["roots"]
    pat = re.compile(req["pattern"], re.MULTILINE | (0 if req.get("case") else re.IGNORECASE))
    # 대소문자를 가리는 리터럴이면 C 수준 `in`으로 먼저 거른다 — 일치가 없는 파일 대부분이 여기서 끝난다.
    needle = req.get("needle") if req.get("case") else None
    include = GlobSet(req.get("include"))
    exclude = GlobSet(req.get("exclude"))
    rules = _Rules(req)
    max_results = int(req.get("max_results", 500))
    max_total = int(req.get("max_total_bytes", 200 * 1024 * 1024))
    max_file = int(req.get("max_file_bytes", 2 * 1024 * 1024))
    max_chars = int(req.get("max_line_chars", 500))
    max_output = int(req.get("max_output_bytes", 1024 * 1024))
    max_files = int(req.get("max_files", 200_000))
    max_dirs = int(req.get("max_dirs", 20_000))
    max_ranges = int(req.get("max_ranges", 20))
    sniff = int(req.get("sniff_bytes", 8192))

    skipped = {"large": 0, "binary": 0, "excluded": 0, "denied": 0, "unreadable": 0}
    engines = []
    plan = []
    total = 0
    files_over = False
    for root in roots:
        listed = _git_candidates(root, req, max_files)
        if listed is None:
            engines.append("walk")
            files, over = _walk_candidates(root, rules, max_dirs, max_files)
        else:
            engines.append("git")
            files, over = listed
        files_over = files_over or over
        kept = []
        for rel in files:
            if rules.path_denied(rel) or rules.skip(os.path.join(root, rel)):
                skipped["denied"] += 1
            elif (include and not include.match(rel)) or (exclude and exclude.match(rel)):
                skipped["excluded"] += 1
            else:
                kept.append(rel)
        plan.append((root, kept))
        total += len(kept)
    _emit({"total": total, "engine": engines})

    searched = read_bytes = results = out_bytes = 0
    matched_files = 0
    limit = None
    for root, files in plan:
        if limit:
            break
        try:
            opener = _Opener(root)
        except OSError:
            skipped["unreadable"] += len(files)
            continue
        try:
            for rel in files:
                if limit:
                    break
                data, why = _read_file(opener, rel, max_file)
                if why:
                    skipped[why] += 1
                    continue
                searched += 1
                read_bytes += len(data)
                if b"\0" in data[:sniff]:
                    skipped["binary"] += 1
                else:
                    text = data.decode("utf-8", errors="replace")
                    if needle is None or needle in text:
                        batch = []
                        for ln, s0, s1, spans in _file_hits(text, pat, max_results - results, max_ranges):
                            line = text[s0:s1]
                            if line.endswith("\r"):
                                line = line[:-1]
                            spans = [(a, min(b, len(line))) for a, b in spans if a < len(line)] or [(0, 0)]
                            hit = _line_hit(line, spans, ln, max_chars, max_ranges)
                            size = len(hit["text"].encode("utf-8", "surrogatepass")) + 64
                            if out_bytes + size > max_output:
                                limit = "output"
                                break
                            out_bytes += size
                            batch.append(hit)
                            results += 1
                            if results >= max_results:
                                limit = "results"
                                break
                        if batch:
                            matched_files += 1
                            _emit({"hits": {"root": root, "path": rel, "lines": batch}})
                if read_bytes >= max_total and not limit:
                    limit = "bytes"
                if searched % 256 == 0:
                    _emit({"progress": {"searched": searched, "results": results}})
        finally:
            opener.close()
    return {
        "total": total, "searched": searched, "results": results, "matched_files": matched_files,
        "skipped": skipped, "limit": limit or ("files" if files_over else None), "engine": engines,
        "read_bytes": read_bytes, "elapsed": round(time.monotonic() - started, 3),
    }


MODES = {"git_entrypoints": git_entrypoints, "search": search, "grep": grep}


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
