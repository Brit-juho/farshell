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
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import fsguard

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
        if fsguard.looks_binary(head):
            return {"path": str(p), "size": size, "binary": True,
                    "truncated": False, "content": ""}
        rest = b"" if size <= fsguard.SNIFF_BYTES else f.read(
            max(0, fsguard.MAX_BYTES - len(head))
        )
    raw = head + rest
    truncated = size > len(raw)
    # errors="replace" — CP949 등 비UTF-8 파일도 열리게 하되 깨짐을 숨기지 않는다.
    text = raw.decode("utf-8", errors="replace")
    return {"path": str(p), "size": size, "binary": False,
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


def _collect_status(repo: Path) -> dict:
    top = _git_toplevel(repo)
    if top is None:
        return {"repo": False, "root": str(repo), "files": []}
    rc, out = _git(top, "status", "--porcelain=v1", "-z")
    if rc != 0:
        return {"repo": True, "root": str(top), "files": []}
    files = []
    # -z 는 NUL 구분이라 공백/한글 파일명이 안전하다.
    # rename/copy(R/C)는 필드가 2개: "XY new_path\0old_path\0" — old_path는
    # 별도 NUL 필드지만 상태 접두사(XY )가 없다. 이걸 그냥 다음 status
    # record로 오인해서 파싱하면(구 코드) rec[:2]가 old_path 앞 두 글자를
    # 가짜 status로 삼켜서 UI에 존재하지 않는 파일/상태가 나타난다.
    parts = out.split("\0")
    it = iter(parts)
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
    return {"repo": True, "root": str(top), "files": files}


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
