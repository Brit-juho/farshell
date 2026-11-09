"""파일 내용 검색(`GET /api/fs/grep`, 파일 개편 단계 6) — 질의 검사, 동시 실행 자리, 결과 재검사.

엔진은 하나다(계획 2.3): `resilience/scan_worker.py`의 `grep` 모드 자식 프로세스가
1. 후보 목록을 만든다 — 저장소 안이면 `git ls-files -z --cached --others --exclude-standard`(`.gitignore`를 따른다),
   밖이면 파일명 검색과 같은 순회.
2. 목록 단계에서 `fsguard` 규칙(거부 이름·거부 폴더·점 폴더·제외 폴더·위험 마운트)으로 먼저 뺀다 — 비밀 파일은
   **읽기 전에** 빠진다.
3. 남은 파일만 직접 읽어(`O_NOFOLLOW`, 일반 파일만) 찾는다.

여기서 하는 일:
- 질의 검사(`build_spec`) — 정규식은 부모에서 먼저 컴파일해 오류 위치를 400으로 알리고, 빈 글자와 일치하는 정규식은
  막는다(줄마다 빈 일치가 쏟아진다). 파이썬 `re`에는 기한이 없어서 **자식 프로세스의 전체 기한**으로 끊는다.
- 자리(`Slots`) — 클라이언트당 1개(같은 클라이언트의 새 검색이 이전 것을 취소한다), 전체 2개. 꽉 차면 503.
- 결과 재검사(`search`) — 돌려주기 전에 결과 파일마다 `resolve_under_roots` 전체 판정을 한 번 더 하고, resolve된
  경로가 자식이 읽은 경로와 같을 때만 남긴다(판정 뒤 폴더를 심링크로 바꿔치기한 경우를 거른다).
- 자식을 못 띄우면 프로세스 안에서 대신 돌리지 **않는다**(`ScanUnavailable` → 503). 대신 돌리면 기한에 따른 강제
  종료를 잃는다.

blocking — `search`는 `grep` 풀에서 부른다(라우트: `routes/files.py`).
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from pathlib import Path
from typing import Optional

import fsguard
import git_safe
from i18n import tr
from resilience import scan as res_scan

# 자원 상한(계획 6단계 표). 결과 수는 거부 필터 **뒤에** 센다 — 거부 경로는 목록 단계에서 이미 빠진다.
TIMEOUT = 10.0
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_LINE_CHARS = 500
MAX_RESULTS = 500
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_FILES = 200_000
MAX_WALK_DIRS = 20_000
MAX_GLOBAL = 2

MAX_QUERY = 1000
MAX_GLOBS = 32
MAX_GLOB_LEN = 256
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class BadQuery(ValueError):
    """400 — `error` 코드와 사람에게 보일 `reason`. 정규식 오류면 `pos`(0부터 센 글자 위치)."""

    def __init__(self, error: str, reason: str, pos: Optional[int] = None):
        super().__init__(reason)
        self.error = error
        self.reason = reason
        self.pos = pos

    def body(self) -> dict:
        out = {"error": self.error, "reason": self.reason}
        if self.pos is not None:
            out["pos"] = self.pos
        return out


class Busy(RuntimeError):
    """전체 동시 검색 상한에 닿았다(503)."""


class NotADirectory(ValueError):
    pass


def split_globs(raw: str) -> tuple[list[str], list[str]]:
    """`glob` 값 → (포함, 제외). 쉼표·줄바꿈으로 나누되 `{a,b}` 안의 쉼표는 나누지 않는다. `!`로 시작하면 제외."""
    items: list[str] = []
    cur = []
    depth = 0
    for ch in raw or "":
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        if ch in ",\n" and not depth:
            items.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    items.append("".join(cur))
    include, exclude = [], []
    for item in (s.strip() for s in items):
        if not item or item == "!":
            continue
        if len(item) > MAX_GLOB_LEN:
            raise BadQuery("bad_glob", tr("glob 패턴이 너무 깁니다({n}자까지)", n=MAX_GLOB_LEN))
        if "\0" in item:
            raise BadQuery("bad_glob", tr("glob 패턴에 쓸 수 없는 글자가 있습니다"))
        (exclude if item.startswith("!") else include).append(item[1:].strip() if item.startswith("!") else item)
    if len(include) + len(exclude) > MAX_GLOBS:
        raise BadQuery("bad_glob", tr("glob 패턴은 {n}개까지 쓸 수 있습니다", n=MAX_GLOBS))
    return include, exclude


def build_spec(q: str, mode: str = "literal", case: bool = False, word: bool = False, glob: str = "") -> Optional[dict]:
    """질의 → 자식에게 넘길 검색 조건. 빈 질의면 None(빈 결과). 틀리면 `BadQuery`."""
    if mode not in ("literal", "regex"):
        raise BadQuery("bad_mode", tr("mode는 literal 또는 regex입니다"))
    if q is None or not q.strip():
        return None
    if len(q) > MAX_QUERY:
        raise BadQuery("bad_query", tr("검색어가 너무 깁니다({n}자까지)", n=MAX_QUERY))
    if "\0" in q or "\n" in q:
        raise BadQuery("bad_query", tr("검색어에 줄바꿈이나 NUL은 쓸 수 없습니다"))
    body = re.escape(q) if mode == "literal" else q
    if word:
        body = r"\b(?:" + body + r")\b"
    flags = re.MULTILINE | (0 if case else re.IGNORECASE)
    try:
        rx = re.compile(body, flags)
    except re.error as e:
        pos = e.pos if mode == "regex" and e.pos is not None else None
        msg = e.msg if hasattr(e, "msg") else str(e)
        if pos is not None:
            raise BadQuery("bad_regex", tr("정규식 오류 · {pos}번째 글자: {msg}", pos=pos + 1, msg=msg), pos) from None
        raise BadQuery("bad_regex", tr("정규식 오류: {msg}", msg=msg)) from None
    except (OverflowError, RecursionError):
        raise BadQuery("bad_regex", tr("정규식이 너무 복잡합니다")) from None
    if rx.search("") is not None:
        raise BadQuery("empty_match", tr("빈 글자와도 일치하는 정규식은 쓸 수 없습니다 — 찾을 글자를 하나 이상 넣으세요"))
    include, exclude = split_globs(glob)
    return {
        "pattern": body, "case": bool(case),
        # 대소문자를 가리는 글자 그대로 찾기(단어 단위 아님)면 자식이 `in`으로 먼저 거른다.
        "needle": q if (mode == "literal" and case and not word) else None,
        "include": include, "exclude": exclude,
        "mode": mode, "word": bool(word), "q": q,
    }


def client_key(request, sid: str = "") -> str:
    """「클라이언트」 — 인증 자격(쿠키·헤더)의 해시 + 화면이 보낸 `sid`(탭마다 다른 값). 다른 사람의 검색은 이 키를
    알 수 없어 취소할 수 없다. 중계로 온 요청은 상대 서버의 기계 자격이라, `sid`가 화면 하나를 가른다."""
    cookies = getattr(request, "cookies", None) or {}
    headers = getattr(request, "headers", None) or {}
    cred = cookies.get("vt_session", "") or headers.get("authorization", "") or headers.get("x-vt-peer", "")
    if not cred:
        client = getattr(request, "client", None)
        cred = "addr:" + (getattr(client, "host", "") or "")
    s = sid if sid and _SID_RE.match(sid) else "-"
    return hashlib.sha256(cred.encode("utf-8", "replace")).hexdigest()[:24] + ":" + s


class Slots:
    """동시 검색 자리. 같은 키의 새 검색은 이전 검색을 취소하고 자리를 이어받는다(전체 수가 늘지 않는다)."""

    def __init__(self, limit: int = MAX_GLOBAL):
        self.limit = limit
        self._lock = threading.Lock()
        self._active: dict[str, res_scan.Cancel] = {}

    def acquire(self, key: str) -> res_scan.Cancel:
        token = res_scan.Cancel()
        with self._lock:
            old = self._active.get(key)
            if old is None and len(self._active) >= self.limit:
                raise Busy(key)
            self._active[key] = token
        if old is not None:
            old.cancel()
        return token

    def release(self, key: str, token: res_scan.Cancel) -> None:
        with self._lock:
            if self._active.get(key) is token:
                del self._active[key]

    def active(self) -> int:
        with self._lock:
            return len(self._active)


SLOTS = Slots()


def _worker_request(roots: list[Path], spec: dict, skip_prefixes: list[str]) -> dict:
    return {
        "mode": "grep",
        "roots": [str(r) for r in roots],
        "pattern": spec["pattern"], "case": spec["case"], "needle": spec["needle"],
        "include": spec["include"], "exclude": spec["exclude"],
        "max_results": MAX_RESULTS, "max_total_bytes": MAX_TOTAL_BYTES, "max_file_bytes": MAX_FILE_BYTES,
        "max_line_chars": MAX_LINE_CHARS, "max_output_bytes": MAX_OUTPUT_BYTES,
        "max_files": MAX_FILES, "max_dirs": MAX_WALK_DIRS, "sniff_bytes": fsguard.SNIFF_BYTES,
        "exclude_dirs": sorted(fsguard.EXCLUDE_DIRS),
        "allowed_dot_dirs": sorted(fsguard.ALLOWED_DOT_DIRS),
        "deny_names": sorted(fsguard.DENY_NAMES),
        "deny_prefixes": list(fsguard.DENY_PREFIXES),
        "deny_suffixes": list(fsguard.DENY_SUFFIXES),
        "deny_dir_parts": sorted(fsguard.DENY_DIR_PARTS),
        "skip_prefixes": skip_prefixes,
        "git_argv": git_safe.argv("ls-files", "-z", "--cached", "--others", "--exclude-standard"),
        "git_env": {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
    }


def _roots(path: str) -> list[Path]:
    if not path:
        return [fsguard.resolve_under_roots(str(r)) for r in fsguard.get_start_roots()]
    p = fsguard.resolve_under_roots(path)
    if not p.is_dir():
        raise NotADirectory(path)
    return [p]


def _still_allowed(path: str, cache: dict) -> bool:
    """결과 파일을 돌려주기 전 마지막 판정 — 경계·거부 목록·점 폴더 전체(`resolve_under_roots`)와, resolve된 경로가
    자식이 읽은 경로와 같은지(중간 폴더가 심링크로 바뀌었으면 다르다)."""
    if path in cache:
        return cache[path]
    try:
        ok = str(fsguard.resolve_under_roots(path)) == path
    except fsguard.FsDenied:
        ok = False
    cache[path] = ok
    return ok


def search(path: str, spec: dict, skip_prefixes: list[str], cancel: Optional[res_scan.Cancel] = None,
           timeout: float = TIMEOUT) -> dict:
    """검색 한 번(동기). 기한이면 그때까지 찾은 결과와 `timeout: true`. 취소면 `cancelled: true`.
    던지는 것: `fsguard.FsDenied`(403), `NotADirectory`(404), `res_scan.ScanUnavailable`(503)."""
    started = time.monotonic()
    roots = _roots(path)
    hits: list[tuple[str, str, list]] = []
    progress = {"total": None, "searched": 0}

    def on_message(msg: dict) -> None:
        if "hits" in msg:
            h = msg["hits"]
            hits.append((h["root"], h["path"], h["lines"]))
        elif "total" in msg:
            progress["total"] = msg["total"]
            progress["engine"] = msg.get("engine")
        elif "progress" in msg:
            progress["searched"] = msg["progress"].get("searched", 0)

    result, reason = res_scan.stream(_worker_request(roots, spec, skip_prefixes), timeout, on_message, cancel)
    if reason == "cancelled":
        return {"cancelled": True}

    cache: dict = {}
    results = []
    dropped = 0
    for root, rel, lines in hits:
        full = str(Path(root) / rel)
        if not _still_allowed(full, cache):
            dropped += len(lines)
            continue
        for ln in lines:
            item = {"path": full, "rel": rel, "line": ln["line"], "col": ln["col"],
                    "text": ln["text"], "ranges": ln["ranges"]}
            if "cut" in ln:
                item["cut"] = ln["cut"]
            results.append(item)
    stats = result or {}
    files = len({r["path"] for r in results})
    return {
        "root": str(roots[0]) if len(roots) == 1 else None,
        "roots": [str(r) for r in roots],
        "results": results,
        "files": files,
        "total": stats.get("total", progress["total"]),
        "searched": stats.get("searched", progress["searched"]),
        "skipped": stats.get("skipped", {}),
        "limit": stats.get("limit"),
        "truncated": bool(stats.get("limit")) or reason == "timeout",
        "timeout": reason == "timeout",
        "dropped": dropped,
        "engine": stats.get("engine", progress.get("engine")),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
