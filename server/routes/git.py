"""git status/diff/log/show + stage/unstage/commit.

P2 코드 뷰어의 git 절반. 원래 routes/files.py 한 파일에 파일 탐색(`/api/fs/*`)·
git·파일 저장소(`/api/files/*`) 세 도메인이 같이 있어 961줄이었다 — **이 분리는
순수 이동이고 동작은 한 줄도 바뀌지 않는다.**

경로 판정은 전부 fsguard 에 위임하고, 여기서는 git 호출과 응답 형태만 다룬다.
쓰기 액션(stage/unstage/commit)도 인증(TokenAuthMiddleware)·CSRF 방어
(OriginGuardMiddleware)를 main.py의 전역 미들웨어에서 그대로 상속받는다.

blocking I/O(git 호출)는 반드시 asyncio.to_thread 로 offload 한다 —
동기 호출 하나가 터미널 WS 전체를 멈춘다(preview.py:91-93의 교훈).
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

import fsguard
import git_safe
from authz import leases as authz_leases
from i18n import tr

logger = logging.getLogger(__name__)

router = APIRouter()

# git 명령 타임아웃. 거대 저장소에서도 이 안에 안 끝나면 응답을 포기하는 편이 낫다.
GIT_TIMEOUT = 10.0

# diff 응답 상한. 대규모 리팩터링에서 수십 MB가 나오면 모바일이 죽는다.
MAX_DIFF_BYTES = 1024 * 1024


def _denied(reason: str) -> JSONResponse:
    """files.py에도 같은 두 줄이 있다 — 공유 모듈을 새로 만들 만큼의 로직이
    아니고, 두 라우터가 서로를 import하지 않는 편이 낫다."""
    return JSONResponse({"error": "denied", "reason": reason}, status_code=403)


# --- git ---------------------------------------------------------------------


def _git(repo: Path, *args: str, hooks: bool = False, readable_paths: bool = False) -> tuple[int, str]:
    """git 호출. shell 을 쓰지 않고 인자 배열로만 넘긴다(인젝션 차단).

    저장소 설정이 프로그램을 띄우지 못하게 `git_safe`를 거친다 — 레일 배지는 pane cwd 마다 이 함수로
    `status`를 부르므로, 안 그러면 악성 저장소에 `cd`만 해도 fsmonitor가 실행된다. `hooks=True`는
    사용자가 누른 git 동작(commit)에만 쓴다."""
    from resilience import proc as res_proc
    try:
        proc = res_proc.run(
            git_safe.argv(*args, cwd=repo, hooks=hooks, readable_paths=readable_paths),
            timeout=GIT_TIMEOUT, env=git_safe.env(),
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
    rc, out = _git(top, "diff", *git_safe.DIFF_FLAGS, "--numstat", "HEAD")
    if rc != 0:
        if rc in (124, 127):
            return {"insertions": 0, "deletions": 0, "error": "git_diff_unavailable"}
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


def _collect_status(repo: Path, *, top: Path | None = None) -> dict:
    top = top or _git_toplevel(repo)
    if top is None:
        return {"repo": False, "root": str(repo), "files": []}
    # --branch: dock 소스컨트롤 머리말(브랜치 · ↑ahead ↓behind)을 위해. 별도
    # git 호출이 아니라 같은 status 호출의 헤더 한 줄이라 비용이 0이다.
    rc, out = _git(top, "status", "--porcelain=v1", "-z", "--branch")
    if rc != 0:
        return {"repo": True, "root": str(top), "files": [],
                "error": "git_status_unavailable",
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


def _repo_dir_sync(repo: str):
    p = fsguard.resolve_under_roots(repo)
    return p if p.is_dir() else None


async def _repo_dir(repo: str):
    """경로 검증(resolve)·is_dir 를 `fs` 풀에서 기한을 두고 한다. 예전엔 이벤트 루프 위에서 불러, 원격
    마운트 경로 하나가 서버 전체를 멈출 수 있었다. 성공하면 Path, 아니면 그대로 돌려줄 응답."""
    from resilience import fsops
    try:
        p = await fsops.call(_repo_dir_sync, repo, path=repo, op="git.repo_dir")
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    except fsops.UNAVAILABLE as e:
        return fsops.unavailable_response(e)
    if p is None:
        return JSONResponse({"error": "not a directory"}, status_code=404)
    return p


@router.get("/api/git/status")
async def git_status(repo: str = Query(...)):
    p = await _repo_dir(repo)
    if not isinstance(p, Path):
        return p
    return await asyncio.to_thread(_collect_status, p)


# diff 섹션 헤더. git 은 `diff --git a/<경로> b/<경로>`(병합 충돌·병합 커밋은 `diff --cc <경로>` /
# `diff --combined <경로>`)로 시작하고, 비ASCII·`"`·역슬래시·제어 문자가 든 경로는 C 방식으로 따옴표를 씌운다
# (`diff --git "a/\354\204\234.pem" "b/..."`). 예전 정규식은 따옴표 헤더를 못 읽어 본문이 그대로 나갔고, 그 헤더를
# 새 섹션으로도 치지 않아 앞 섹션에 흡수됐다. 지금은 (1) 모든 시작 줄을 섹션으로 쪼개고, (2) 따옴표를 풀고,
# (3) 공백 때문에 경로가 모호하면 가능한 모든 분할을 검사하고, (4) 읽지 못하면 **가린다**(열린 채로 실패하지 않는다).
_DIFF_STARTS = ("diff --git ", "diff --cc ", "diff --combined ")

REDACTION_PLACEHOLDER = "[내용 가려짐 — 보호된 경로]"

_C_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, '"': 34, "\\": 92}


def _unquote_c(text: str) -> tuple[str, str] | None:
    """`"..."`로 시작하는 git C 인용 하나를 풀어 (경로, 나머지)를 돌려준다. 닫는 따옴표가 없으면 None."""
    if not text.startswith('"'):
        return None
    out = bytearray()
    i, n = 1, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            return out.decode("utf-8", errors="replace"), text[i + 1:]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in "01234567":
                j = i + 1
                while j < n and j < i + 4 and text[j] in "01234567":
                    j += 1
                out.append(int(text[i + 1:j], 8) & 0xFF)
                i = j
                continue
            if nxt in _C_ESCAPES:
                out.append(_C_ESCAPES[nxt])
                i += 2
                continue
            return None
        out += ch.encode("utf-8")
        i += 1
    return None


def _strip_prefix(path: str) -> str:
    return path[2:] if path[:2] in ("a/", "b/") else path


def _header_paths(line: str) -> list[str] | None:
    """섹션 시작 줄에서 나올 수 있는 모든 경로(a/·b/ 접두사 제거). 읽지 못하면 None.

    인용되지 않은 `a/X b/Y`는 X나 Y에 공백이 있으면 모호하다 — ` b/`가 나오는 자리마다 분할해 그 후보를
    전부 돌려주므로 호출부는 하나라도 거부 대상이면 가린다(과하게 가리는 쪽으로만 틀린다).
    """
    for prefix in _DIFF_STARTS:
        if line.startswith(prefix):
            rest = line[len(prefix):]
            break
    else:
        return None
    if prefix != "diff --git ":
        q = _unquote_c(rest)
        path = q[0] if q and not q[1] else rest
        return [path] if path else None
    paths: list[str] = []
    if rest.startswith('"'):
        first = _unquote_c(rest)
        if first is None:
            return None
        paths.append(_strip_prefix(first[0]))
        tail = first[1].lstrip(" ")
        if tail.startswith('"'):
            second = _unquote_c(tail)
            if second is None:
                return None
            paths.append(_strip_prefix(second[0]))
        elif tail:
            paths.append(_strip_prefix(tail))
        return paths or None
    if not rest.startswith("a/"):
        return None
    pos = rest.find(" b/")
    while pos != -1:
        paths += [rest[2:pos], rest[pos + 3:]]
        pos = rest.find(" b/", pos + 1)
    # `a/X "b/Y"` — 이름이 바뀐 파일은 한쪽만 인용될 수 있다. 인용 시작(` "`)마다 뒤를 풀어 본다.
    pos = rest.find(' "')
    while pos != -1:
        q = _unquote_c(rest[pos + 1:])
        if q is not None and not q[1]:
            paths += [rest[2:pos], _strip_prefix(q[0])]
        pos = rest.find(' "', pos + 1)
    return paths or None


def _section_paths(section: list[str]) -> list[str] | None:
    """헤더 줄 + 그 아래 `rename from/to`·`copy from/to`·`---`/`+++` 줄(첫 `@@` 전)의 모든 경로."""
    paths = _header_paths(section[0])
    if paths is None:
        return None
    for line in section[1:]:
        if line.startswith("@"):
            break
        for key in ("rename from ", "rename to ", "copy from ", "copy to "):
            if line.startswith(key):
                value = line[len(key):]
                q = _unquote_c(value)
                paths.append(q[0] if q else value)
        if line.startswith(("--- ", "+++ ")):
            value = line[4:].split("\t", 1)[0]
            if value == "/dev/null":
                continue
            q = _unquote_c(value)
            paths.append(_strip_prefix(q[0] if q else value))
    return paths


def _diff_path_denied(path: str) -> bool:
    """git diff 헤더에 나온 저장소 상대경로가 열람 규칙에 걸리는지 확인.

    fsguard.resolve_under_roots()는 루트 경계 검사 + 존재 확인까지 하므로 여기서는
    쓸 수 없다(diff에 나온 경로는 루트 기준 절대경로가 아니고, 삭제된 파일은 더는
    존재하지 않을 수도 있다). 순수 이름 판정 로직을 재구현 없이 그대로 재사용한다 —
    규칙은 fsguard.py 한 곳에만 있어야 한다:
    - 거부 목록(`_check_denylist`): .env·*.pem·id_rsa·.ssh/ …
    - 점 디렉터리(`ALLOWED_DOT_DIRS` 밖): 뷰어가 `.sometool/state.json`을 거부하는데 diff 가 내주면 그 방어가
      무의미하다. 마지막 구성요소(파일)는 점 파일(`.gitignore`)이라도 열람 대상이므로 보지 않는다.
    """
    p = Path(path)
    if fsguard._check_denylist(p) is not None:
        return True
    return any(fsguard.is_hidden_state_dir_name(part) for part in p.parts[:-1])


def _redact_diff(diff_text: str, is_denied=_diff_path_denied) -> str:
    """열람 규칙에 걸리는 파일의 diff 본문을 플레이스홀더로 치환한다.

    /api/fs/file 은 .env·*.pem·id_rsa 등을 절대 내보내지 않는데, 같은 파일이 git diff에는
    그대로 실려 나가면 그 방어가 무의미해진다. 파일 경로(헤더 줄)는 "뭐가 바뀌었는지"
    UI에 보여줘야 하므로 남기고, 실제 내용(hunk 본문)만 가린다. 경로를 읽지 못한 섹션도 가린다.
    """
    if not diff_text:
        return diff_text
    lines = diff_text.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if not lines[i].startswith(_DIFF_STARTS):
            out.append(lines[i])
            i += 1
            continue
        section_start = i
        i += 1
        while i < n and not lines[i].startswith(_DIFF_STARTS):
            i += 1
        section = lines[section_start:i]
        paths = _section_paths(section)
        if paths is None or any(is_denied(p) for p in paths):
            # "diff --git", "index", "---", "+++" 같은 헤더 줄만 남기고 hunk(@@ ...)는 버린다.
            kept = []
            for hl in section:
                if hl.startswith("@"):
                    break
                kept.append(hl)
            out.extend(kept)
            out.append(tr("[내용 가려짐 — 보호된 경로]"))
        else:
            out.extend(section)
    return "\n".join(out)


def _collect_diff(repo: Path, file: str, staged: bool) -> dict:
    top = _git_toplevel(repo)
    if top is None:
        return {"repo": False, "diff": "", "truncated": False}
    args = ["diff", "--no-color", *git_safe.DIFF_FLAGS]
    if staged:
        args.append("--cached")
    if file:
        # '--' 뒤에 두면 파일명이 옵션으로 해석되지 않는다.
        args += ["--", file]
    rc, out = _git(top, *args, readable_paths=True)
    if rc == 124:
        return {"repo": True, "diff": "", "truncated": False,
                "error": tr("git diff 시간 초과")}
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
    p = await _repo_dir(repo)
    if not isinstance(p, Path):
        return p
    # file 은 저장소 기준 상대경로다. 절대경로/상위 탈출을 여기서 잘라낸다.
    if file and _bad_repo_relpath(file):
        return _denied(tr("잘못된 파일 경로입니다"))
    return await asyncio.to_thread(_collect_diff, p, file, staged)


# --- git 로그/커밋 diff (읽기 전용) --------------------------------------------
#
# stage/commit(D16)과 달리 log/show는 읽기 전용이라 "코드 뷰어는 읽기 전용" 전제와
# 충돌하지 않는다. status/diff와 같은 fsguard 검증 + _redact_diff 재사용.

# 커밋 필드 구분자. 저자명/제목에 나올 일이 없는 제어문자(Unit Separator)를 쓴다.
_LOG_FIELD_SEP = "\x1f"
_LOG_FMT = _LOG_FIELD_SEP.join(("%H", "%h", "%an", "%ad", "%P", "%D", "%s"))

# 형식이 명백한 hex sha만 git 인자로 넘긴다 — 그 외 문자열(옵션처럼 보이는 값 등)은 거부.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")

MAX_LOG_LIMIT = 200
DEFAULT_LOG_LIMIT = 30


def _list_branches(top: Path) -> list[str]:
    rc, out = _git(top, "for-each-ref", "--format=%(refname:short)",
                   "--sort=-committerdate", "refs/heads", "refs/remotes")
    if rc != 0:
        return []
    return [ln for ln in out.split("\n") if ln and not ln.endswith("/HEAD")]


def _collect_log(repo: Path, file: str, skip: int, limit: int, ref: str = "") -> dict:
    top = _git_toplevel(repo)
    if top is None:
        return {"repo": False, "commits": [], "has_more": False}
    # ref 는 실제 존재하는 브랜치 이름과 대조한 값만 git 인자로 넘긴다(옵션 주입 차단).
    if ref and ref not in _list_branches(top):
        return {"repo": True, "root": str(top), "commits": [], "has_more": False,
                "error": tr("알 수 없는 브랜치입니다")}
    # has_more 판정을 위해 하나 더 요청한다.
    args = ["log", f"--pretty=format:{_LOG_FMT}", "--date=iso-strict",
            f"--skip={skip}", f"-n{limit + 1}"]
    if ref:
        args.append(ref)
    if file:
        args += ["--", file]
    rc, out = _git(top, *args)
    if rc == 124:
        return {"repo": True, "commits": [], "has_more": False, "error": tr("git log 시간 초과")}
    if rc != 0:
        # 커밋이 아예 없는 새 저장소 등 — 빈 로그로 취급한다.
        return {"repo": True, "root": str(top), "commits": [], "has_more": False}
    lines = [ln for ln in out.split("\n") if ln]
    has_more = len(lines) > limit
    lines = lines[:limit]
    commits = []
    for ln in lines:
        parts = ln.split(_LOG_FIELD_SEP)
        if len(parts) != 7:
            continue
        h, short, author, date, parents, refs, subject = parts
        commits.append({"hash": h, "short": short, "author": author,
                         "date": date, "subject": subject,
                         "parents": parents.split(),
                         "refs": [r.strip() for r in refs.split(",") if r.strip()]})
    return {"repo": True, "root": str(top), "commits": commits, "has_more": has_more}


@router.get("/api/git/log")
async def git_log(
    repo: str = Query(...),
    file: str = Query(""),
    ref: str = Query(""),
    skip: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LOG_LIMIT, ge=1, le=MAX_LOG_LIMIT),
):
    p = await _repo_dir(repo)
    if not isinstance(p, Path):
        return p
    if file and _bad_repo_relpath(file):
        return _denied(tr("잘못된 파일 경로입니다"))
    return await asyncio.to_thread(_collect_log, p, file, skip, limit, ref)


def _collect_branches(repo: Path) -> dict:
    top = _git_toplevel(repo)
    if top is None:
        return {"repo": False, "branches": []}
    return {"repo": True, "root": str(top), "branches": _list_branches(top)}


@router.get("/api/git/branches")
async def git_branches(repo: str = Query(...)):
    p = await _repo_dir(repo)
    if not isinstance(p, Path):
        return p
    return await asyncio.to_thread(_collect_branches, p)


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
    # _LOG_FMT은 7칸(H h an ad P D s)이고 본문(%b)이 구분자 하나 뒤에 붙는다 — 앞 7칸 + 본문으로 자른다.
    # (예전에는 5칸으로 잘라 제목 자리에 부모 해시가, 본문 자리에 ref·제목·본문이 섞여 들어갔다.)
    parts = out.rstrip("\n").split(_LOG_FIELD_SEP, 7)
    if len(parts) < 7:
        return None
    h, short, author, date, parents, _refs, subject = parts[:7]
    body = parts[7].strip() if len(parts) > 7 else ""
    return {"hash": h, "short": short, "author": author, "date": date,
            "parents": parents.split() if parents.strip() else [],
            "subject": subject, "body": body}


def _collect_commit_file_diff(repo: Path, sha: str, file: str) -> dict:
    args = ["show", "--no-color", *git_safe.DIFF_FLAGS, "--format="]
    args.append(sha)
    if file:
        args += ["--", file]
    rc, out = _git(repo, *args, readable_paths=True)
    if rc == 124:
        return {"diff": "", "truncated": False, "error": tr("git show 시간 초과")}
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
    p = await _repo_dir(repo)
    if not isinstance(p, Path):
        return p
    if not _SHA_RE.match(sha):
        return _denied(tr("잘못된 커밋 해시입니다"))
    if file and _bad_repo_relpath(file):
        return _denied(tr("잘못된 파일 경로입니다"))
    top = await asyncio.to_thread(_git_toplevel, p)
    if top is None:
        return JSONResponse({"error": tr("git 저장소가 아닙니다")}, status_code=404)

    if file:
        diff = await asyncio.to_thread(_collect_commit_file_diff, top, sha, file)
        return {"repo": True, "root": str(top), "sha": sha, **diff}

    meta = await asyncio.to_thread(_collect_commit_meta, top, sha)
    if meta is None:
        return JSONResponse({"error": tr("커밋을 찾을 수 없습니다")}, status_code=404)
    files = await asyncio.to_thread(_collect_commit_files, top, sha)
    return {"repo": True, "root": str(top), "commit": meta, "files": files}


# --- git 쓰기(stage/commit) ---------------------------------------------------
#
# D16: 코드 뷰어의 "읽기 전용" 방어 전제를 stage/commit 만큼만 좁게 깬다.
# push·브랜치 조작·강제 옵션(-f 등)은 절대 추가하지 않는다.

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
        return None, JSONResponse({"error": tr("git 저장소가 아닙니다")}, status_code=404)
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
    # `_resolve_repo_top`은 안쪽에서 `git rev-parse --show-toplevel`을 돈다
    # (_git_toplevel → _git → subprocess). 3겹 아래라 눈으로는 안 보인다.
    top, err = await asyncio.to_thread(_resolve_repo_top, str(body.get("repo", "")))
    if err:
        return err
    files = _validate_files(body.get("files"))
    if files is None:
        return _denied(tr("잘못된 파일 목록입니다"))
    blocked = await authz_leases.enforce_write(request, [str(top / f) for f in files], op="git.stage")
    if blocked is not None:
        return blocked
    rc, _ = await asyncio.to_thread(_git, top, "add", "--", *files)
    if rc != 0:
        return JSONResponse({"error": tr("git add 실패")}, status_code=500)
    return await asyncio.to_thread(_collect_status, top)


@router.post("/api/git/unstage")
async def git_unstage(request: Request):
    body = await _read_json_body(request)
    # `_resolve_repo_top`은 안쪽에서 `git rev-parse --show-toplevel`을 돈다
    # (_git_toplevel → _git → subprocess). 3겹 아래라 눈으로는 안 보인다.
    top, err = await asyncio.to_thread(_resolve_repo_top, str(body.get("repo", "")))
    if err:
        return err
    files = _validate_files(body.get("files"))
    if files is None:
        return _denied(tr("잘못된 파일 목록입니다"))
    blocked = await authz_leases.enforce_write(request, [str(top / f) for f in files], op="git.unstage")
    if blocked is not None:
        return blocked
    # reset(HEAD 없이) -- <paths> 는 워킹트리를 건드리지 않고 인덱스만 되돌린다.
    rc, _ = await asyncio.to_thread(_git, top, "reset", "-q", "--", *files)
    if rc != 0:
        return JSONResponse({"error": tr("git reset 실패")}, status_code=500)
    return await asyncio.to_thread(_collect_status, top)


def _has_staged_changes(repo: Path) -> bool:
    rc, out = _git(repo, "diff", "--cached", "--name-only")
    return rc == 0 and bool(out.strip())


def _staged_paths(repo: Path) -> list[str]:
    """스테이지된 경로 전부(이름 바꾸기도 옛 이름·새 이름 둘 다) — `-z` 로 NUL 구분."""
    try:
        proc = subprocess.run(git_safe.argv("diff", "--cached", "--name-only", "-z", "--no-renames", cwd=repo),
                              capture_output=True, timeout=GIT_TIMEOUT, env=git_safe.env())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    return [x for x in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if x]


def _commit(repo: Path, message: str) -> tuple[int, str]:
    return _git(repo, "commit", "-m", message, hooks=True)


@router.post("/api/git/commit")
async def git_commit(request: Request):
    body = await _read_json_body(request)
    # `_resolve_repo_top`은 안쪽에서 `git rev-parse --show-toplevel`을 돈다
    # (_git_toplevel → _git → subprocess). 3겹 아래라 눈으로는 안 보인다.
    top, err = await asyncio.to_thread(_resolve_repo_top, str(body.get("repo", "")))
    if err:
        return err
    message = body.get("message", "")
    if not isinstance(message, str) or not message.strip():
        return JSONResponse({"error": tr("커밋 메시지가 비었습니다")}, status_code=400)
    if len(message.encode("utf-8")) > MAX_COMMIT_MSG_BYTES:
        return JSONResponse({"error": tr("커밋 메시지가 너무 깁니다")}, status_code=400)
    if not await asyncio.to_thread(_has_staged_changes, top):
        return JSONResponse({"error": tr("커밋할 스테이지 변경사항이 없습니다")}, status_code=400)
    if authz_leases.enforced():            # 스테이지된 파일 전부가 대상이다(NUL 로 나눠 받는다 — 이름에 공백·개행이 있어도 안전)
        staged = await asyncio.to_thread(_staged_paths, top)
        blocked = await authz_leases.enforce_write(request, [str(top / f) for f in staged], op="git.commit")
        if blocked is not None:
            return blocked
    rc, out = await asyncio.to_thread(_commit, top, message)
    if rc != 0:
        logger.warning(f"git commit 실패: {out[:500]}")
        return JSONResponse({"error": tr("git commit 실패")}, status_code=500)
    status = await asyncio.to_thread(_collect_status, top)
    status["committed"] = True
    return status
