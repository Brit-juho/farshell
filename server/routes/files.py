"""파일 열람 + git diff (읽기 전용).

P2 코드 뷰어의 백엔드. 쓰기 경로는 의도적으로 만들지 않는다 — 수정은 터미널/에이전트가 한다.
경로 판정은 전부 fsguard 에 위임하고, 여기서는 I/O 와 응답 형태만 다룬다.

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
        status = rec[:2].strip()
        entry = {"status": status, "file": rec[3:]}
        if status and status[0] in ("R", "C"):
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
    if file:
        if file.startswith("/") or ".." in Path(file).parts:
            return _denied("잘못된 파일 경로입니다")
    return await asyncio.to_thread(_collect_diff, p, file, staged)
