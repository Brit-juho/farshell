"""파일 열람 + git diff/stage/commit.

P2 코드 뷰어의 백엔드. 열람(tree/file/diff)은 읽기 전용이지만, D16에서 stage/commit
만 최소로 추가했다 — push·브랜치 조작 등은 여전히 범위 밖이다(TODOS.md D16 참고).
경로 판정은 전부 fsguard 에 위임하고, 여기서는 I/O 와 응답 형태만 다룬다.

쓰기 액션(stage/unstage/commit)도 인증(TokenAuthMiddleware)·CSRF 방어(OriginGuardMiddleware)를
main.py의 전역 미들웨어에서 그대로 상속받는다 — 이 파일에서 별도로 구현할 게 없다.
다만 경로 검증(fsguard.resolve_under_roots + 저장소 상대경로 검사)은 diff와 동일하게
반드시 거쳐야 한다.

blocking I/O(파일 읽기, git 호출)는 반드시 asyncio.to_thread 로 offload 한다.
preview.py:91-93 에 같은 교훈이 있다 — 동기 호출 하나가 터미널 WS 전체를 멈춘다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

import file_store
import fsguard
import tmux_target

logger = logging.getLogger(__name__)

router = APIRouter()

# git 명령 타임아웃. 거대 저장소에서도 이 안에 안 끝나면 응답을 포기하는 편이 낫다.
GIT_TIMEOUT = 10.0

# diff 응답 상한. 대규모 리팩터링에서 수십 MB가 나오면 모바일이 죽는다.
MAX_DIFF_BYTES = 1024 * 1024


def _denied(reason: str) -> JSONResponse:
    return JSONResponse({"error": "denied", "reason": reason}, status_code=403)


# --- 루트 --------------------------------------------------------------------


@router.get("/api/fs/roots")
async def fs_roots():
    """프론트가 첫 화면에 띄울 시작 지점. 열람 허용 경계(fsguard.get_roots())보다
    좁을 수 있다 — 기본값이 그 경우로, 시작은 ~/GitHub 이지만 위로 이동하면 경계(홈)까지
    resolve_under_roots 가 계속 허용한다."""
    return {"roots": [str(r) for r in fsguard.get_start_roots()]}


# --- 트리 --------------------------------------------------------------------


def _list_dir(p: Path) -> dict:
    entries = []
    truncated = False
    try:
        with os.scandir(p) as it:
            for de in it:
                if len(entries) >= fsguard.MAX_ENTRIES:
                    truncated = True
                    break
                name = de.name
                try:
                    is_dir = de.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir and name in fsguard.EXCLUDE_DIRS:
                    continue
                # 거부 대상은 목록에서도 감춘다. 이름만으로도 정보가 되기 때문.
                if not is_dir and fsguard._is_denied_name(name):
                    continue
                try:
                    size = de.stat(follow_symlinks=False).st_size if not is_dir else 0
                except OSError:
                    size = 0
                entries.append({"name": name, "dir": is_dir, "size": size})
    except PermissionError:
        raise fsguard.FsDenied("읽기 권한이 없습니다")

    # 디렉토리 우선, 그다음 이름순 — 파일 탐색기의 보편적 정렬.
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    return {"path": str(p), "entries": entries, "truncated": truncated}


# --- 파일명 검색 (N5/N40, 60-settings-palette.md §3 `/` 모드) ----------------

# 트리 조회(MAX_ENTRIES=1000, 한 디렉토리)와 다른 종류의 상한 — 이건 **재귀** 검색이라
# 파일 수가 아니라 "얼마나 많은 디렉토리를 열어봤는가"로 시간을 잘라야 한다. 큰
# 모노레포에서도 팔레트가 200ms 디바운스 감각을 지키려면 스캔 자체가 짧아야 한다.
MAX_SEARCH_SCANNED_DIRS = 4000
MAX_SEARCH_RESULTS = 50


def _fuzzy_score(query: str, name: str) -> Optional[int]:
    """subsequence 매치 — quickopen.js의 프런트 fuzzy(_fuzzyMatch)와 같은 규칙을
    서버에도 둔다(다른 데이터소스라 코드 공유는 못 하지만 "질의 문자가 이름에서
    같은 순서로 다 나오면 매치"라는 규칙은 동일하게 유지). 점수가 낮을수록(문자가
    이름 안에서 더 붙어 있고 더 앞쪽일수록) 좋은 매치 — 정렬 키로 그대로 쓴다."""
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
    return (last - first) + first  # 붙어 있을수록·앞쪽일수록 작은 값


def _search_files(root: Path, query: str) -> tuple[list[dict], bool]:
    results = []
    scanned_dirs = 0
    truncated = False
    stack = [root]
    while stack:
        d = stack.pop()
        if scanned_dirs >= MAX_SEARCH_SCANNED_DIRS:
            truncated = True
            break
        scanned_dirs += 1
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
                        if name in fsguard.EXCLUDE_DIRS:
                            continue
                        subdirs.append(de.path)
                        continue
                    if fsguard._is_denied_name(name):
                        continue
                    score = _fuzzy_score(query, name)
                    if score is None:
                        continue
                    try:
                        size = de.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = 0
                    results.append({"path": de.path, "name": name, "size": size, "_score": score})
                # 얕은 디렉토리부터 보되(체감상 가까운 결과가 먼저), 순서는 크게
                # 중요하지 않다 — 최종 정렬은 스코어 기준으로 한 번 더 한다.
                stack.extend(subdirs)
        except (PermissionError, OSError):
            continue
    results.sort(key=lambda r: (r["_score"], len(r["path"])))
    trimmed = results[:MAX_SEARCH_RESULTS]
    return trimmed, truncated or len(results) > MAX_SEARCH_RESULTS


@router.get("/api/fs/search")
async def fs_search(q: str = Query(...), path: str = Query("")):
    """파일명 fuzzy 검색 — 팔레트 `/` 모드(60-settings-palette.md §3).

    `path`를 주면 그 루트 아래만(quickopen이 "현재 보고 있는 위치 기준"을
    구현할 수 있게), 안 주면 fsguard.get_start_roots() 전부를 훑는다.
    질의가 빈 문자열이면 빈 결과 — 팔레트가 빈 입력에서 트리 전체를 긁지 않게.
    """
    q = q.strip()
    if not q:
        return {"results": [], "truncated": False}
    if path:
        try:
            roots = [fsguard.resolve_under_roots(path)]
        except fsguard.FsDenied as e:
            return _denied(e.reason)
    else:
        roots = fsguard.get_start_roots()

    all_results: list[dict] = []
    truncated = False
    for root in roots:
        r, t = await asyncio.to_thread(_search_files, root, q)
        all_results.extend(r)
        truncated = truncated or t
    all_results.sort(key=lambda r: (r["_score"], len(r["path"])))
    final = all_results[:MAX_SEARCH_RESULTS]
    for r in final:
        del r["_score"]
    return {"results": final, "truncated": truncated or len(all_results) > MAX_SEARCH_RESULTS}


@router.get("/api/fs/tree")
async def fs_tree(path: str = Query(...)):
    try:
        p = fsguard.resolve_under_roots(path)
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    if not p.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=404)
    try:
        return await asyncio.to_thread(_list_dir, p)
    except fsguard.FsDenied as e:
        return _denied(e.reason)


# --- 파일 --------------------------------------------------------------------


def _read_file(p: Path) -> dict:
    size = p.stat().st_size
    with open(p, "rb") as f:
        head = f.read(fsguard.SNIFF_BYTES)
        # [T3] 이미지는 "바이너리라 못 보여준다"로 끝내지 않는다 — 스크린샷·다이어그램은
        # 원격에서 코드만큼 자주 열어보는 파일이다. 바이트는 /api/fs/raw가 준다.
        mime = fsguard.image_type(p, head)
        if mime:
            return {"path": str(p), "size": size, "binary": True, "image": True,
                    "mime": mime, "too_large": size > fsguard.MAX_IMAGE_BYTES,
                    "truncated": False, "content": ""}
        if fsguard.looks_binary(head):
            return {"path": str(p), "size": size, "binary": True, "image": False,
                    "truncated": False, "content": ""}
        rest = b"" if size <= fsguard.SNIFF_BYTES else f.read(
            max(0, fsguard.MAX_BYTES - len(head))
        )
    raw = head + rest
    truncated = size > len(raw)
    # errors="replace" — CP949 등 비UTF-8 파일도 열리게 하되 깨짐을 숨기지 않는다.
    text = raw.decode("utf-8", errors="replace")
    return {"path": str(p), "size": size, "binary": False, "image": False,
            "truncated": truncated, "content": text}


@router.get("/api/fs/file")
async def fs_file(path: str = Query(...)):
    try:
        p = fsguard.resolve_under_roots(path)
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        return await asyncio.to_thread(_read_file, p)
    except PermissionError:
        return _denied("읽기 권한이 없습니다")
    except OSError as e:
        logger.warning(f"fs_file 실패: {e}")
        return JSONResponse({"error": "read failed"}, status_code=500)


@router.get("/api/fs/raw")
async def fs_raw(path: str = Query(...)):
    """[T3] 이미지 원본 바이트 — 인라인 미리보기 전용.

    **이미지가 아니면 아무것도 안 준다.** 열람 경계(fsguard)는 `/api/fs/file`과
    같은 것을 쓰고, 그 위에 타입 화이트리스트를 하나 더 얹는다: 여기서 나간
    바이트는 브라우저가 이 오리진의 리소스로 해석하므로 SVG·HTML을 내보내면
    그 자체가 XSS다(fsguard.IMAGE_TYPES 주석 참고).

    `Content-Disposition: inline` + `X-Content-Type-Options: nosniff`로 "이건
    이 타입이고 다른 걸로 스니핑하지 말라"를 못 박는다.
    """
    try:
        p = fsguard.resolve_under_roots(path)
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            head = f.read(fsguard.SNIFF_BYTES)
            mime = fsguard.image_type(p, head)
            if not mime:
                return _denied("이미지 파일이 아닙니다")
            if size > fsguard.MAX_IMAGE_BYTES:
                return JSONResponse(
                    {"error": "too_large",
                     "reason": f"이미지가 너무 큽니다 (최대 {fsguard.MAX_IMAGE_BYTES // (1024*1024)}MB)"},
                    status_code=413,
                )
            data = head + f.read()
    except PermissionError:
        return _denied("읽기 권한이 없습니다")
    except OSError as e:
        logger.warning(f"fs_raw 실패: {e}")
        return JSONResponse({"error": "read failed"}, status_code=500)
    return Response(content=data, media_type=mime, headers={
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    })


# --- git ---------------------------------------------------------------------


def _git(repo: Path, *args: str) -> tuple[int, str]:
    """git 호출. shell 을 쓰지 않고 인자 배열로만 넘긴다(인젝션 차단)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return 124, ""
    except (OSError, ValueError) as e:
        logger.warning(f"git 호출 실패: {e}")
        return 127, ""
    return proc.returncode, proc.stdout.decode("utf-8", errors="replace")


def _git_toplevel(repo: Path) -> Path | None:
    rc, out = _git(repo, "rev-parse", "--show-toplevel")
    if rc != 0 or not out.strip():
        return None
    return Path(out.strip())


# `git status --branch -z`가 맨 앞에 넣는 헤더 한 줄.
#   ## feat/x...origin/feat/x [ahead 3, behind 1]
#   ## master            (upstream 없음)
#   ## HEAD (no branch)  (detached)
#   ## No commits yet on master
# ahead/behind는 대괄호 안에 하나만 올 수도 있고 둘 다 올 수도 있다.
_BRANCH_RE = re.compile(r"^## (?:No commits yet on )?(?P<branch>[^.\s]\S*?)(?:\.\.\.(?P<upstream>\S+))?(?: \[(?P<track>[^\]]*)\])?$")
_AHEAD_RE = re.compile(r"ahead (\d+)")
_BEHIND_RE = re.compile(r"behind (\d+)")


def _parse_branch_header(line: str) -> dict:
    """`## ...` 헤더 → {branch, upstream, ahead, behind}. 못 읽으면 전부 비운다.

    파싱 실패가 status 전체를 실패시키면 안 된다 — 파일 목록은 dock의 본체고
    브랜치 줄은 머리말이라, 머리말을 못 읽었다고 본체를 못 보여줄 이유가 없다.
    """
    empty = {"branch": None, "upstream": None, "ahead": 0, "behind": 0}
    m = _BRANCH_RE.match(line.strip())
    if not m:
        return empty
    branch = m.group("branch")
    if branch == "HEAD":       # detached — 브랜치 이름이 아니다
        branch = None
    track = m.group("track") or ""
    a = _AHEAD_RE.search(track)
    b = _BEHIND_RE.search(track)
    return {
        "branch": branch,
        "upstream": m.group("upstream"),
        "ahead": int(a.group(1)) if a else 0,
        "behind": int(b.group(1)) if b else 0,
    }


def _diff_stat(top: Path) -> dict:
    """워킹트리+인덱스 전체의 +/− 합. HEAD가 없는 저장소(커밋 0개)면 0."""
    rc, out = _git(top, "diff", "--numstat", "HEAD")
    if rc != 0:
        return {"insertions": 0, "deletions": 0}
    ins = dele = 0
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        # 바이너리는 "-\t-\tpath"로 나온다 — 숫자가 아니면 건너뛴다.
        if cols[0].isdigit():
            ins += int(cols[0])
        if cols[1].isdigit():
            dele += int(cols[1])
    return {"insertions": ins, "deletions": dele}


def _collect_status(repo: Path) -> dict:
    top = _git_toplevel(repo)
    if top is None:
        return {"repo": False, "root": str(repo), "files": []}
    # --branch: dock 소스컨트롤 머리말(브랜치 · ↑ahead ↓behind)을 위해. 별도
    # git 호출이 아니라 같은 status 호출의 헤더 한 줄이라 비용이 0이다.
    rc, out = _git(top, "status", "--porcelain=v1", "-z", "--branch")
    if rc != 0:
        return {"repo": True, "root": str(top), "files": [],
                **_parse_branch_header(""), "insertions": 0, "deletions": 0}
    files = []
    # -z 는 NUL 구분이라 공백/한글 파일명이 안전하다.
    # rename/copy(R/C)는 필드가 2개: "XY new_path\0old_path\0" — old_path는
    # 별도 NUL 필드지만 상태 접두사(XY )가 없다. 이걸 그냥 다음 status
    # record로 오인해서 파싱하면(구 코드) rec[:2]가 old_path 앞 두 글자를
    # 가짜 status로 삼켜서 UI에 존재하지 않는 파일/상태가 나타난다.
    parts = out.split("\0")
    it = iter(parts)
    # 헤더는 항상 첫 레코드다(-z라서 NUL로 끊긴다).
    branch_info = {"branch": None, "upstream": None, "ahead": 0, "behind": 0}
    first = next(it, "")
    if first.startswith("## "):
        branch_info = _parse_branch_header(first)
    else:
        it = iter(parts)   # 헤더가 없으면(구 git 등) 처음부터 다시 읽는다
    for rec in it:
        if len(rec) < 4:
            continue
        # XY 두 글자를 그대로 보존한다(strip 금지) — X는 인덱스(스테이지) 상태,
        # Y는 워킹트리 상태다. "M "(스테이지만 수정)과 " M"(워킹트리만 수정)을
        # strip()으로 뭉개면 둘 다 "M"이 되어 stage/unstage UI가 상태를 구분할 수 없다.
        status = rec[:2]
        entry = {
            "status": status.strip(),
            "index_status": status[0] if status[0] != " " else "",
            "worktree_status": status[1] if status[1] != " " else "",
            "file": rec[3:],
        }
        if status.strip() and status[0] in ("R", "C"):
            try:
                entry["orig_file"] = next(it)
            except StopIteration:
                pass
        files.append(entry)
    return {"repo": True, "root": str(top), "files": files, **branch_info, **_diff_stat(top)}


@router.get("/api/git/status")
async def git_status(repo: str = Query(...)):
    try:
        p = fsguard.resolve_under_roots(repo)
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    if not p.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=404)
    return await asyncio.to_thread(_collect_status, p)


# diff 섹션 헤더: "diff --git a/path b/path" — b/ 쪽 경로를 실제 대상 파일로 취급한다
# (rename의 경우도 b/ 가 결과 경로). a/, b/ 접두사는 git이 항상 붙이므로 그대로 벗긴다.
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")

REDACTION_PLACEHOLDER = "[내용 가려짐 — 보호된 경로]"


def _diff_path_denied(path: str) -> bool:
    """git diff 헤더에 나온 저장소 상대경로가 fsguard 거부 목록에 걸리는지 확인.

    fsguard.resolve_under_roots()는 루트 경계 검사 + 존재 확인까지 하므로 여기서는
    쓸 수 없다(diff에 나온 경로는 루트 기준 절대경로가 아니고, 삭제된 파일은 더는
    존재하지 않을 수도 있다). 순수 이름 판정 로직(_check_denylist)만 재구현 없이
    그대로 재사용한다 — 거부 목록은 fsguard.py 한 곳에만 있어야 한다.
    """
    return fsguard._check_denylist(Path(path)) is not None


def _redact_diff(diff_text: str, is_denied=_diff_path_denied) -> str:
    """거부 목록에 걸리는 파일의 diff 본문을 플레이스홀더로 치환한다.

    /api/fs/file 은 .env·*.pem·id_rsa 등을 절대 내보내지 않는데, 같은 파일이 git diff에는
    그대로 실려 나가면 그 방어가 무의미해진다. 파일 경로(헤더 줄)는 "뭐가 바뀌었는지"
    UI에 보여줘야 하므로 남기고, 실제 내용(hunk 본문)만 가린다.
    """
    if not diff_text:
        return diff_text
    lines = diff_text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        m = _DIFF_HEADER_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        path_b = m.group(2)
        section_start = i
        i += 1
        while i < n and not lines[i].startswith("diff --git a/"):
            i += 1
        section = lines[section_start:i]
        if is_denied(path_b):
            # "diff --git", "index", "---", "+++" 같은 헤더 줄만 남기고 hunk(@@ ...)는 버린다.
            kept = []
            for hl in section:
                kept.append(hl)
                if hl.startswith("+++ "):
                    break
            out.extend(kept)
            out.append(REDACTION_PLACEHOLDER)
        else:
            out.extend(section)
    return "\n".join(out)


def _collect_diff(repo: Path, file: str, staged: bool) -> dict:
    top = _git_toplevel(repo)
    if top is None:
        return {"repo": False, "diff": "", "truncated": False}
    args = ["diff", "--no-color"]
    if staged:
        args.append("--cached")
    if file:
        # '--' 뒤에 두면 파일명이 옵션으로 해석되지 않는다.
        args += ["--", file]
    rc, out = _git(top, *args)
    if rc == 124:
        return {"repo": True, "diff": "", "truncated": False,
                "error": "git diff 시간 초과"}
    out = _redact_diff(out)
    truncated = len(out.encode()) > MAX_DIFF_BYTES
    if truncated:
        out = out.encode()[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
    return {"repo": True, "root": str(top), "diff": out, "truncated": truncated}


def _bad_repo_relpath(file: str) -> bool:
    """저장소 기준 상대경로 검증. 절대경로/상위 탈출을 걸러낸다.

    diff의 file 파라미터에서 쓰던 검사를 stage/unstage 파일 목록에도 그대로 재사용한다 —
    같은 판정을 두 곳에 따로 구현하면 한쪽만 고치고 잊어버리는 사고가 난다.
    """
    return file.startswith("/") or ".." in Path(file).parts


@router.get("/api/git/diff")
async def git_diff(
    repo: str = Query(...),
    file: str = Query(""),
    staged: bool = Query(False),
):
    try:
        p = fsguard.resolve_under_roots(repo)
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    if not p.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=404)
    # file 은 저장소 기준 상대경로다. 절대경로/상위 탈출을 여기서 잘라낸다.
    if file and _bad_repo_relpath(file):
        return _denied("잘못된 파일 경로입니다")
    return await asyncio.to_thread(_collect_diff, p, file, staged)


# --- git 로그/커밋 diff (읽기 전용) --------------------------------------------
#
# stage/commit(D16)과 달리 log/show는 읽기 전용이라 "코드 뷰어는 읽기 전용" 전제와
# 충돌하지 않는다. status/diff와 같은 fsguard 검증 + _redact_diff 재사용.

# 커밋 필드 구분자. 저자명/제목에 나올 일이 없는 제어문자(Unit Separator)를 쓴다.
_LOG_FIELD_SEP = "\x1f"
_LOG_FMT = _LOG_FIELD_SEP.join(("%H", "%h", "%an", "%ad", "%s"))

# 형식이 명백한 hex sha만 git 인자로 넘긴다 — 그 외 문자열(옵션처럼 보이는 값 등)은 거부.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")

MAX_LOG_LIMIT = 200
DEFAULT_LOG_LIMIT = 30


def _collect_log(repo: Path, file: str, skip: int, limit: int) -> dict:
    top = _git_toplevel(repo)
    if top is None:
        return {"repo": False, "commits": [], "has_more": False}
    # has_more 판정을 위해 하나 더 요청한다.
    args = ["log", f"--pretty=format:{_LOG_FMT}", "--date=iso-strict",
            f"--skip={skip}", f"-n{limit + 1}"]
    if file:
        args += ["--", file]
    rc, out = _git(top, *args)
    if rc == 124:
        return {"repo": True, "commits": [], "has_more": False, "error": "git log 시간 초과"}
    if rc != 0:
        # 커밋이 아예 없는 새 저장소 등 — 빈 로그로 취급한다.
        return {"repo": True, "root": str(top), "commits": [], "has_more": False}
    lines = [ln for ln in out.split("\n") if ln]
    has_more = len(lines) > limit
    lines = lines[:limit]
    commits = []
    for ln in lines:
        parts = ln.split(_LOG_FIELD_SEP)
        if len(parts) != 5:
            continue
        h, short, author, date, subject = parts
        commits.append({"hash": h, "short": short, "author": author,
                         "date": date, "subject": subject})
    return {"repo": True, "root": str(top), "commits": commits, "has_more": has_more}


@router.get("/api/git/log")
async def git_log(
    repo: str = Query(...),
    file: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LOG_LIMIT, ge=1, le=MAX_LOG_LIMIT),
):
    try:
        p = fsguard.resolve_under_roots(repo)
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    if not p.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=404)
    if file and _bad_repo_relpath(file):
        return _denied("잘못된 파일 경로입니다")
    return await asyncio.to_thread(_collect_log, p, file, skip, limit)


def _collect_commit_files(repo: Path, sha: str) -> list[dict]:
    rc, out = _git(repo, "show", "--no-color", "--name-status", "--format=", sha)
    if rc != 0:
        return []
    files = []
    for ln in out.split("\n"):
        if not ln.strip():
            continue
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        # rename/copy: "R100\told\tnew" — 마지막 필드가 결과 경로.
        file = parts[-1]
        entry = {"status": status[0], "file": file}
        if status[0] in ("R", "C") and len(parts) >= 3:
            entry["orig_file"] = parts[1]
        files.append(entry)
    return files


def _collect_commit_meta(repo: Path, sha: str) -> dict | None:
    rc, out = _git(repo, "show", "-s", f"--format={_LOG_FMT}%x1f%b", "--date=iso-strict", sha)
    if rc != 0 or not out.strip():
        return None
    parts = out.rstrip("\n").split(_LOG_FIELD_SEP, 5)
    if len(parts) < 5:
        return None
    h, short, author, date, subject = parts[:5]
    body = parts[5].strip() if len(parts) > 5 else ""
    return {"hash": h, "short": short, "author": author, "date": date,
            "subject": subject, "body": body}


def _collect_commit_file_diff(repo: Path, sha: str, file: str) -> dict:
    args = ["show", "--no-color", "--format="]
    args.append(sha)
    if file:
        args += ["--", file]
    rc, out = _git(repo, *args)
    if rc == 124:
        return {"diff": "", "truncated": False, "error": "git show 시간 초과"}
    out = _redact_diff(out)
    truncated = len(out.encode()) > MAX_DIFF_BYTES
    if truncated:
        out = out.encode()[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
    return {"diff": out, "truncated": truncated}


@router.get("/api/git/show")
async def git_show(
    repo: str = Query(...),
    sha: str = Query(...),
    file: str = Query(""),
):
    try:
        p = fsguard.resolve_under_roots(repo)
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    if not p.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=404)
    if not _SHA_RE.match(sha):
        return _denied("잘못된 커밋 해시입니다")
    if file and _bad_repo_relpath(file):
        return _denied("잘못된 파일 경로입니다")
    top = await asyncio.to_thread(_git_toplevel, p)
    if top is None:
        return JSONResponse({"error": "git 저장소가 아닙니다"}, status_code=404)

    if file:
        diff = await asyncio.to_thread(_collect_commit_file_diff, top, sha, file)
        return {"repo": True, "root": str(top), "sha": sha, **diff}

    meta = await asyncio.to_thread(_collect_commit_meta, top, sha)
    if meta is None:
        return JSONResponse({"error": "커밋을 찾을 수 없습니다"}, status_code=404)
    files = await asyncio.to_thread(_collect_commit_files, top, sha)
    return {"repo": True, "root": str(top), "commit": meta, "files": files}


# --- git 쓰기(stage/commit) ---------------------------------------------------
#
# D16: 코드 뷰어의 "읽기 전용" 방어 전제를 stage/commit 만큼만 좁게 깬다.
# push·브랜치 조작·강제 옵션(-f 등)은 절대 추가하지 않는다 — TODOS.md D16 참고.

# 커밋 메시지 상한. 실수로 파일 전체를 붙여넣는 등의 사고를 막는 정도의 느슨한 상한.
MAX_COMMIT_MSG_BYTES = 8192


async def _read_json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _resolve_repo_top(repo_raw: str) -> tuple[Path, JSONResponse | None]:
    """repo 쿼리를 fsguard로 검증하고 실제 git 저장소 루트를 찾는다.

    stage/unstage/commit 모두 "저장소 루트에서만 동작"이 전제라 공통화했다.
    """
    try:
        p = fsguard.resolve_under_roots(repo_raw)
    except fsguard.FsDenied as e:
        return None, _denied(e.reason)
    if not p.is_dir():
        return None, JSONResponse({"error": "not a directory"}, status_code=404)
    top = _git_toplevel(p)
    if top is None:
        return None, JSONResponse({"error": "git 저장소가 아닙니다"}, status_code=404)
    return top, None


def _validate_files(files) -> list[str] | None:
    """요청 본문의 files 필드를 검증. 문제가 있으면 None."""
    if not isinstance(files, list) or not files:
        return None
    out = []
    for f in files:
        if not isinstance(f, str) or not f.strip() or _bad_repo_relpath(f):
            return None
        out.append(f)
    return out


@router.post("/api/git/stage")
async def git_stage(request: Request):
    body = await _read_json_body(request)
    top, err = _resolve_repo_top(str(body.get("repo", "")))
    if err:
        return err
    files = _validate_files(body.get("files"))
    if files is None:
        return _denied("잘못된 파일 목록입니다")
    rc, _ = await asyncio.to_thread(_git, top, "add", "--", *files)
    if rc != 0:
        return JSONResponse({"error": "git add 실패"}, status_code=500)
    return await asyncio.to_thread(_collect_status, top)


@router.post("/api/git/unstage")
async def git_unstage(request: Request):
    body = await _read_json_body(request)
    top, err = _resolve_repo_top(str(body.get("repo", "")))
    if err:
        return err
    files = _validate_files(body.get("files"))
    if files is None:
        return _denied("잘못된 파일 목록입니다")
    # reset(HEAD 없이) -- <paths> 는 워킹트리를 건드리지 않고 인덱스만 되돌린다.
    rc, _ = await asyncio.to_thread(_git, top, "reset", "-q", "--", *files)
    if rc != 0:
        return JSONResponse({"error": "git reset 실패"}, status_code=500)
    return await asyncio.to_thread(_collect_status, top)


def _has_staged_changes(repo: Path) -> bool:
    rc, out = _git(repo, "diff", "--cached", "--name-only")
    return rc == 0 and bool(out.strip())


def _commit(repo: Path, message: str) -> tuple[int, str]:
    return _git(repo, "commit", "-m", message)


@router.post("/api/git/commit")
async def git_commit(request: Request):
    body = await _read_json_body(request)
    top, err = _resolve_repo_top(str(body.get("repo", "")))
    if err:
        return err
    message = body.get("message", "")
    if not isinstance(message, str) or not message.strip():
        return JSONResponse({"error": "커밋 메시지가 비었습니다"}, status_code=400)
    if len(message.encode("utf-8")) > MAX_COMMIT_MSG_BYTES:
        return JSONResponse({"error": "커밋 메시지가 너무 깁니다"}, status_code=400)
    if not await asyncio.to_thread(_has_staged_changes, top):
        return JSONResponse({"error": "커밋할 스테이지 변경사항이 없습니다"}, status_code=400)
    rc, out = await asyncio.to_thread(_commit, top, message)
    if rc != 0:
        logger.warning(f"git commit 실패: {out[:500]}")
        return JSONResponse({"error": "git commit 실패"}, status_code=500)
    status = await asyncio.to_thread(_collect_status, top)
    status["committed"] = True
    return status


# --- 파일 저장소 (N19) ---------------------------------------------------------
#
# 코드 뷰어(fs/*)·git 섹션과 달리 이쪽은 fsguard 경계와 무관하다 — 접근 판정은
# file_store.py의 id 기반 조회 하나뿐이라 여기서 별도 경로 검사를 할 게 없다.

EXPIRING_SOON_SECONDS = 3 * 86400  # dock 칩 "만료 임박" 기준(50-files-share.md §4)


def _file_public(item: dict) -> dict:
    """클라이언트에 내려줄 필드만 — 실 경로(디스크 절대경로)는 노출하지 않는다."""
    now_ = time.time()
    expires_at = item.get("created", 0) + file_store.TTL_SECONDS
    return {
        "id": item["id"],
        "name": item["name"],
        "size": item.get("size", 0),
        "mime": item.get("mime"),
        "created": item.get("created"),
        "session": item.get("session"),
        "worktree": item.get("worktree"),
        "host": item.get("host", "local"),
        # pinHash/pinSalt는 **절대** 내려보내지 않는다. 해시라도 목록 API로
        # 새어 나가면 오프라인에서 4자리 PIN을 그냥 맞춰볼 수 있다(5회 제한은
        # 서버 쪽 시도에만 걸린다). 목록이 필요로 하는 건 모드·만료·1회용뿐이다.
        "shares": [
            {k: s.get(k) for k in ("shareId", "mode", "exp", "once", "views", "lastAccess")}
            for s in item.get("shares", []) if isinstance(s, dict)
        ],
        "pin": bool(item.get("pin")),
        "expires_at": None if item.get("shares") or item.get("pin") else expires_at,
        "expiring_soon": bool(
            not item.get("shares") and not item.get("pin")
            and (expires_at - now_) <= EXPIRING_SOON_SECONDS
        ),
    }


@router.get("/api/files")
async def list_files(filter: str = Query("all")):
    items = await asyncio.to_thread(file_store.list_items)
    items.sort(key=lambda x: x.get("created", 0), reverse=True)
    public = [_file_public(x) for x in items]
    if filter == "shared":
        public = [x for x in public if x["shares"]]
    elif filter == "expiring":
        public = [x for x in public if x["expiring_soon"]]
    elif filter != "all":
        return JSONResponse({"error": "bad_filter"}, status_code=400)
    # dock 파일 탭 푸터의 용량 게이지(50-files-share.md §4의 "412MB / 2GB · 30일 후
    # 자동 삭제"). used는 **필터와 무관하게 전체**다 — 게이지가 칩을 누를 때마다
    # 움직이면 그건 용량이 아니라 필터 결과를 그린 것이 된다.
    used = sum(int(x.get("size", 0) or 0) for x in items)
    return {
        "items": public,
        "quota": {"used": used, "max": file_store.MAX_TOTAL_BYTES,
                  "ttl_days": file_store.TTL_SECONDS // 86400},
    }


@router.get("/api/files/{file_id}/download")
async def download_file(file_id: str):
    fp = await asyncio.to_thread(file_store.real_path_for, file_id)
    if fp is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    item = await asyncio.to_thread(file_store.get_item, file_id)
    resp = FileResponse(
        str(fp),
        filename=item["name"] if item else fp.name,
        media_type=item.get("mime") if item else None,
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@router.delete("/api/files/{file_id}")
async def delete_file(file_id: str):
    ok = await asyncio.to_thread(file_store.delete, file_id)
    if not ok:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"ok": True}


@router.get("/api/files/{file_id}/path")
async def file_path(file_id: str):
    """저장된 파일의 디스크 경로 — dock 파일 탭의 「경로 복사」(50 §4의 5개 동작 중
    하나)가 쓴다.

    `_file_public`이 경로를 빼는 것과 모순처럼 보이지만 성격이 다르다: 목록은
    화면을 그리는 데 경로가 필요 없어서 뺀 것이고, 여기는 **사용자가 명시적으로
    그 값을 달라고 누른** 경로다. 애초에 `insert`가 같은 문자열을 pane에 그대로
    타이핑하므로 새로 열리는 비밀은 없다. 입력은 여전히 id 하나뿐이라 경로를
    받아 파일을 여는 traversal 표면도 생기지 않는다(그게 id 기반 설계의 목적이다).
    """
    fp = await asyncio.to_thread(file_store.real_path_for, file_id)
    if fp is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"id": file_id, "path": str(fp)}


@router.post("/api/files/{file_id}/send")
async def send_file_to_host(file_id: str, request: Request):
    """저장된 파일을 원격 호스트로 보낸다(A2) — JSON: `host`, 선택 `session`.

    **왜 필요한가**: 원격 pane에 파일 경로를 타이핑해봐야 그쪽에는 그 파일이 없다.
    더 나쁜 경우는 같은 경로에 **다른 파일**이 있는 것이다. 그래서 바이트를 먼저
    옮긴 뒤 그쪽 경로를 넣는다. `session`을 같이 주면 상대가 저장 직후 그 pane에
    경로를 타이핑한다(Enter 없음 — 로컬 삽입과 같은 계약).

    이 라우트는 **평소의 로그인 인증**으로 지킨다(peer 서명이 아니다) — 부르는
    쪽이 브라우저이기 때문이다. 나가는 요청에만 peer 서명이 붙는다.
    """
    body = await _read_json_body(request)
    host = str(body.get("host", "")).strip()
    session = str(body.get("session", "")).strip()
    if not host or host == "local":
        return JSONResponse({"error": "bad_host", "reason": "원격 호스트 id가 필요합니다"},
                            status_code=400)
    fp = await asyncio.to_thread(file_store.real_path_for, file_id)
    if fp is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    import host_store
    import peer_client

    peer = await asyncio.to_thread(host_store.find_peer, host)
    if peer is None:
        return JSONResponse({"error": "host_not_found",
                             "reason": f"등록되지 않은 호스트입니다: {host}"}, status_code=404)
    item = await asyncio.to_thread(file_store.get_item, file_id)
    data = await asyncio.to_thread(fp.read_bytes)
    headers = {
        "X-Peer-File-Name": (item or {}).get("name", fp.name),
        "X-Peer-File-Id": file_id,
        **({"X-Peer-File-Session": session} if session else {}),
    }
    try:
        r = await asyncio.to_thread(peer_client.send_bytes_sync, peer, "/api/peer/file", data, headers)
    except peer_client.PeerError as e:
        # 상대의 거부(등급 부족·크기 초과)는 이쪽 서버의 오류가 아니다 —
        # 그대로 상태와 이유를 전달해 화면이 사람 말로 보여줄 수 있게 한다.
        return JSONResponse({"error": "peer_failed", "reason": e.reason},
                            status_code=e.status if 400 <= e.status < 600 else 502)
    return {"ok": True, "host": host, **{k: r.get(k) for k in ("id", "path", "reused", "typed")}}


@router.post("/api/files/{file_id}/insert")
async def insert_file(file_id: str, request: Request):
    """파일 경로를 지정 세션(tmux)의 pane에 타이핑 — Enter는 누르지 않는다
    (기존 클라이언트 사이드 sendToPty 동작의 서버판, N19 §2)."""
    body = await _read_json_body(request)
    session = str(body.get("session", "")).strip()
    if not session:
        return JSONResponse({"error": "missing_session"}, status_code=400)
    fp = await asyncio.to_thread(file_store.real_path_for, file_id)
    if fp is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    pane = tmux_target.session_pane(session)
    if not pane:
        return JSONResponse({"error": "session_not_found"}, status_code=404)
    ok = await asyncio.to_thread(tmux_target.type_to_tmux, pane, str(fp))
    if not ok:
        return JSONResponse({"error": "insert_failed"}, status_code=500)
    return {"ok": True}
