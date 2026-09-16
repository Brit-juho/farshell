"""파일 열람(`/api/fs/*`) + 파일 저장소(`/api/files/*`).

P2 코드 뷰어의 열람 절반과 N19 파일 저장소. git(`/api/git/*`)은 routes/git.py로
분리했다 — 한 파일에 세 도메인이 961줄로 뭉쳐 있었다.

경로 판정은 전부 fsguard 에 위임하고, 여기서는 I/O 와 응답 형태만 다룬다.
`/api/files/*` 쪽은 fsguard 경계와 무관하다(file_store의 id 기반 조회 하나뿐).

blocking I/O(파일 읽기)는 반드시 asyncio.to_thread 로 offload 한다.
preview.py:91-93 에 같은 교훈이 있다 — 동기 호출 하나가 터미널 WS 전체를 멈춘다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

import file_store
import fsguard
import tmux_target

logger = logging.getLogger(__name__)

router = APIRouter()

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


async def _read_json_body(request: Request) -> dict:
    """git.py에도 같은 대여섯 줄이 있다 — 공유 모듈을 새로 만들 만큼의 로직이
    아니고, 두 라우터가 서로를 import하지 않는 편이 낫다(_denied와 같은 판단)."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


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
