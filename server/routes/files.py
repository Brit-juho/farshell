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
import base64
import bisect
import errno
import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

from authz import leases as authz_leases
import file_store
import fs_watch
import fs_read
import fsguard
import tmux_target
from i18n import tr
from resilience import fsops, scan as res_scan
from resilience.scan_worker import fuzzy_score as _fuzzy_score

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
    return {"roots": [str(r) for r in fsguard.get_start_roots()], "home": str(Path.home())}


# --- 트리 --------------------------------------------------------------------


# 페이지 하나의 크기(기본·최대)와 한 디렉토리에서 스캔하는 상한(계획 4.4). 예전에는 scandir 앞 1000개를 **먼저 자르고
# 나서** 정렬해, 1000개를 넘는 폴더는 어느 1000개가 보일지가 디스크 순서에 달렸고 나머지를 볼 길이 없었다.
# 지금은 전체(상한 5만)를 이름만으로 모아 정렬한 뒤 페이지를 자른다. 크기(`stat`)는 그 페이지 항목에만 부른다.
PAGE_ENTRIES = fsguard.MAX_ENTRIES
MAX_SCAN_ENTRIES = 50_000
_CURSOR_MAX = 2048


class BadCursor(ValueError):
    pass


def _sort_key(is_dir: bool, name: str) -> tuple:
    # 폴더 먼저, 그다음 대소문자 무시 이름순, 같으면 원래 이름(커서가 항목 하나를 정확히 가리키게).
    return (0 if is_dir else 1, name.casefold(), name)


def encode_cursor(key: tuple) -> str:
    raw = json.dumps([key[0], key[1], key[2]], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple:
    """`after=` 값 → 정렬 키. 모양이 틀리면 BadCursor(400). 커서는 이름만 담는다 — 경로 판정과 무관하다."""
    if not cursor or len(cursor) > _CURSOR_MAX:
        raise BadCursor("after is malformed")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        v = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BadCursor("after is malformed") from None
    if (not isinstance(v, list) or len(v) != 3 or v[0] not in (0, 1)
            or not isinstance(v[1], str) or not isinstance(v[2], str)):
        raise BadCursor("after is malformed")
    return (v[0], v[1], v[2])


def _list_dir(p: Path, after: tuple | None = None, limit: int = PAGE_ENTRIES) -> dict:
    """디렉토리 한 페이지. 응답: `entries`(이 페이지), `total`(스캔한 보이는 항목 수), `next`(다음 페이지 커서 또는
    None), `capped`(스캔 상한에 닿음), `truncated`(이 응답 밖에 항목이 더 있다 — 옛 클라이언트용 뜻 그대로)."""
    found: list[tuple[tuple, os.DirEntry]] = []
    capped = False
    try:
        with os.scandir(p) as it:
            for de in it:
                if len(found) >= MAX_SCAN_ENTRIES:
                    capped = True
                    break
                name = de.name
                if fsguard.is_managed_profile_path(Path(de.path)):
                    continue
                try:
                    is_dir = de.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir and (name in fsguard.EXCLUDE_DIRS
                               or fsguard.is_hidden_state_dir_name(name)):
                    continue
                # 거부 대상은 목록에서도 감춘다. 이름만으로도 정보가 되기 때문.
                if not is_dir and fsguard._is_denied_name(name):
                    continue
                found.append((_sort_key(is_dir, name), de))
    except PermissionError:
        raise fsguard.FsDenied(tr("읽기 권한이 없습니다"))

    found.sort(key=lambda x: x[0])
    start = 0
    if after is not None:
        start = bisect.bisect_right([k for k, _ in found], after)
    page = found[start:start + limit]
    entries = []
    for key, de in page:
        is_dir = key[0] == 0
        size = 0
        mtime = None
        if not is_dir:
            try:
                st = de.stat(follow_symlinks=False)
                size = st.st_size
                mtime = st.st_mtime_ns // 1_000_000
            except OSError:
                pass
        e = {"name": de.name, "dir": is_dir, "size": size}
        if mtime is not None:
            e["mtime"] = mtime
        entries.append(e)
    more = start + len(page) < len(found)
    return {
        "path": str(p), "entries": entries,
        "truncated": more or capped,
        "total": len(found), "next": encode_cursor(page[-1][0]) if more and page else None,
        "capped": capped,
    }


# --- 파일명 검색 (N5/N40, 60-settings-palette.md §3 `/` 모드) ----------------

# 트리 조회(MAX_ENTRIES=1000, 한 디렉토리)와 다른 종류의 상한 — 이건 **재귀** 검색이라
# 파일 수가 아니라 "얼마나 많은 디렉토리를 열어봤는가"로 시간을 잘라야 한다. 큰
# 모노레포에서도 팔레트가 200ms 디바운스 감각을 지키려면 스캔 자체가 짧아야 한다.
MAX_SEARCH_SCANNED_DIRS = 4000
MAX_SEARCH_RESULTS = 50


SEARCH_TIMEOUT = 10.0


def _managed_skip_prefixes() -> list[str]:
    """관리자 백업·카탈로그(`fsguard.is_managed_profile_path`)는 순회에서도 들어가지 않는다."""
    import vt_paths
    out = []
    for name in ("agent-profiles", "session-catalog"):
        try:
            out.append(str((vt_paths.state_dir() / name).resolve()))
        except (OSError, RuntimeError):
            pass
    return out


def _search_files(root: Path, query: str) -> tuple[list[dict], bool]:
    """파일명 퍼지 검색. 순회는 자식 프로세스(`resilience/scan_worker.py` `search`)에서 기한을 두고
    한다 — 원격·가상 마운트·격리 목록은 들어가지 않고, 멈추면 그 디렉터리를 격리한다. 결과는 여기서
    거부 목록·관리 경로로 한 번 더 거른다(심층 방어). **동기** — `fs` 풀에서 부른다."""
    req = {
        "mode": "search", "root": str(root), "query": query,
        "max_dirs": MAX_SEARCH_SCANNED_DIRS, "max_results": MAX_SEARCH_RESULTS,
        "exclude_dirs": sorted(fsguard.EXCLUDE_DIRS),
        "allowed_dot_dirs": sorted(fsguard.ALLOWED_DOT_DIRS),
        "deny_names": sorted(fsguard.DENY_NAMES),
        "deny_prefixes": list(fsguard.DENY_PREFIXES),
        "deny_suffixes": list(fsguard.DENY_SUFFIXES),
        "skip_prefixes": res_scan.skip_prefixes(_managed_skip_prefixes()),
    }
    out = res_scan.run(req, SEARCH_TIMEOUT)
    results = [r for r in out.get("results", [])
               if not fsguard._is_denied_name(r["name"])
               and not fsguard.is_managed_profile_path(Path(r["path"]))]
    results.sort(key=lambda r: (r["_score"], len(r["path"])))
    trimmed = results[:MAX_SEARCH_RESULTS]
    return trimmed, bool(out.get("truncated")) or len(results) > MAX_SEARCH_RESULTS


def _search_all(path: str, q: str) -> dict:
    roots = [fsguard.resolve_under_roots(path)] if path else fsguard.get_start_roots()
    all_results: list[dict] = []
    truncated = False
    for root in roots:
        r, t = _search_files(root, q)
        all_results.extend(r)
        truncated = truncated or t
    all_results.sort(key=lambda r: (r["_score"], len(r["path"])))
    final = all_results[:MAX_SEARCH_RESULTS]
    for r in final:
        del r["_score"]
    return {"results": final, "truncated": truncated or len(all_results) > MAX_SEARCH_RESULTS}


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
    try:
        return await fsops.call(_search_all, path, q, path=path or str(Path.home()), op="fs.search",
                                timeout=SEARCH_TIMEOUT * 2)
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    except res_scan.ScanTimeout:
        return {"results": [], "truncated": True, "timeout": True}
    except fsops.UNAVAILABLE as e:
        return fsops.unavailable_response(e)


# 미리 펼쳐 두는 하위 목록의 상한 — 큰 저장소에서 첫 응답이 커지지 않게 자른다.
MAX_PREFETCH_DEPTH = 2
MAX_PREFETCH_DIRS = 150
MAX_PREFETCH_CHILD_ENTRIES = 200


def _list_dir_deep(p: Path, depth: int, limit: int = PAGE_ENTRIES) -> dict:
    """_list_dir + 하위 디렉토리 목록을 depth 단계까지 `children` 에 미리 담는다.

    권한 없는 하위 디렉토리는 조용히 건너뛰고(펼칠 때 일반 조회가 오류를 보여준다),
    총 디렉토리 수와 항목 수 상한을 넘으면 그 아래는 미리 담지 않는다."""
    root = _list_dir(p, None, limit)
    budget = [MAX_PREFETCH_DIRS]

    def fill(listing: dict, base: Path, left: int) -> None:
        if left <= 0:
            return
        for e in listing["entries"]:
            if not e["dir"] or budget[0] <= 0:
                continue
            budget[0] -= 1
            child = base / e["name"]
            try:
                sub = _list_dir(child)
            except (fsguard.FsDenied, OSError):
                continue
            if sub["total"] > MAX_PREFETCH_CHILD_ENTRIES:
                continue
            e["children"] = sub
            fill(sub, child, left - 1)

    fill(root, p, depth)
    return root


def _tree(path: str, depth: int, after: tuple | None = None, limit: int = PAGE_ENTRIES):
    p = fsguard.resolve_under_roots(path)
    if not p.is_dir():
        return None
    # 다음 페이지(`after`)에는 미리 받기를 붙이지 않는다 — 미리 받기는 처음 열 때만 쓴다(계획 4.4).
    if after is not None or not depth:
        return _list_dir(p, after, limit)
    return _list_dir_deep(p, depth, limit)


@router.get("/api/fs/tree")
async def fs_tree(path: str = Query(...), depth: int = Query(0, ge=0, le=MAX_PREFETCH_DEPTH),
                  after: str | None = Query(None), limit: int = Query(PAGE_ENTRIES, ge=1, le=PAGE_ENTRIES)):
    # 경로 검증(resolve)부터 목록까지 전부 `fs` 풀에서 기한을 두고 한다 — 예전엔 resolve·is_dir 를
    # 이벤트 루프 위에서 불러, NFS 경로 하나가 서버 전체를 멈출 수 있었다.
    try:
        cursor = decode_cursor(after) if after is not None else None
    except BadCursor as e:
        return JSONResponse({"error": "bad_cursor", "reason": str(e)}, status_code=400)
    try:
        out = await fsops.call(_tree, path, depth, cursor, limit, path=path, op="fs.tree")
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    except fsops.UNAVAILABLE as e:
        return fsops.unavailable_response(e)
    if out is None:
        return JSONResponse({"error": "not a directory"}, status_code=404)
    return out


# --- 파일 --------------------------------------------------------------------


def _file(path: str, opts: dict):
    """경로 판정(fsguard)부터 fd 읽기(fs_read)까지 — 전부 `fs` 풀 안에서. 없는 파일·디렉토리는 None."""
    p = fsguard.resolve_under_roots(path)
    try:
        return fs_read.read(p, **opts)
    except fsguard.NotRegularFile:
        # 디렉토리·FIFO·장치 — 옛 응답(`is_file()`이 거짓 → 404)과 같다.
        return None
    except OSError as e:
        # 없는 파일, 그리고 판정 뒤 경로의 어느 조각이 심링크로 바뀐 경우(ELOOP/ENOTDIR) — 경계 밖을 열지
        # 않고 「없음」으로 답한다. 권한 없음 등 다른 오류는 라우트가 나눠 답한다.
        if e.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            return None
        raise


def _int_param(raw: str | None, name: str, lo: int, hi: int | None) -> int | None:
    if raw is None:
        return None
    try:
        v = int(raw)
    except ValueError:
        raise fs_read.BadRange(f"{name} must be an integer") from None
    if v < lo or (hi is not None and v > hi):
        raise fs_read.BadRange(f"{name} must be {lo}..{hi}" if hi is not None else f"{name} must be >= {lo}")
    return v


def _file_headers(etag: str) -> dict:
    # 저장은 하되 매번 서버에 검증한다 — 같은 판이면 304로 본문 없이 끝난다.
    return {"ETag": etag, "Cache-Control": "private, no-cache"}


def _changed(rev: str) -> JSONResponse:
    return JSONResponse(
        {"error": "changed", "rev": rev,
         "reason": tr("읽는 사이 파일이 바뀌었습니다 — 처음부터 다시 받으세요")},
        status_code=409, headers={"Cache-Control": "no-store"})


@router.get("/api/fs/file")
async def fs_file(request: Request, path: str = Query(...), offset: str | None = Query(None),
                  length: str | None = Query(None), tail: str | None = Query(None),
                  expected_rev: str | None = Query(None), encoding: str = Query("auto")):
    """파일 내용 — 판 번호(rev)·조건부 요청(304)·구간 읽기·인코딩(API.md `/api/fs/file`, fs_read.py).

    구간 값(`offset`·`length`·`tail`)이 없으면 옛 응답 모양(앞 512KB, `truncated`)이다. 옛 뷰어가 그대로 돈다."""
    try:
        opts = {
            "offset": _int_param(offset, "offset", 0, None),
            "length": _int_param(length, "length", 1, fs_read.MAX_RANGE_BYTES),
            "tail": _int_param(tail, "tail", 1, fs_read.MAX_RANGE_BYTES),
        }
        if opts["tail"] is not None and (opts["offset"] is not None or opts["length"] is not None):
            raise fs_read.BadRange("tail cannot be combined with offset/length")
        if encoding not in fs_read.REQUEST_ENCODINGS:
            return JSONResponse({"error": "bad_encoding", "reason": tr("지원하지 않는 인코딩입니다: {enc}", enc=encoding[:40]),
                                 "supported": list(fs_read.REQUEST_ENCODINGS)}, status_code=400)
        if expected_rev is not None and (not expected_rev or len(expected_rev) > fs_read.MAX_REV_LEN):
            raise fs_read.BadRange("expected_rev is malformed")
    except fs_read.BadRange as e:
        return JSONResponse({"error": "bad_range", "reason": str(e)}, status_code=400)
    opts.update(encoding=encoding, expected_rev=expected_rev,
                if_none_match=request.headers.get("if-none-match"))
    try:
        out = await fsops.call(_file, path, opts, path=path, op="fs.file")
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    except fs_read.Changed as e:
        return _changed(e.rev)
    except fs_read.BadRange as e:
        return JSONResponse({"error": "bad_range", "reason": str(e)}, status_code=400)
    except fsops.UNAVAILABLE as e:
        return fsops.unavailable_response(e)
    except PermissionError:
        return _denied(tr("읽기 권한이 없습니다"))
    except OSError as e:
        logger.warning(f"fs_file 실패: {e}")
        return JSONResponse({"error": "read failed"}, status_code=500)
    if out is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if out["status"] == 304:
        return Response(status_code=304, headers=_file_headers(out["etag"]))
    return JSONResponse(out["body"], headers=_file_headers(out["etag"]))


def _raw_image(path: str):
    p = fsguard.resolve_under_roots(path)
    if not p.is_file():
        return ("missing",)
    size = p.stat().st_size
    with open(p, "rb") as f:
        head = f.read(fsguard.SNIFF_BYTES)
        mime = fsguard.image_type(p, head)
        if not mime:
            return ("not_image",)
        if size > fsguard.MAX_IMAGE_BYTES:
            return ("too_large",)
        return ("ok", head + f.read(), mime)


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
        out = await fsops.call(_raw_image, path, path=path, op="fs.raw")
    except fsguard.FsDenied as e:
        return _denied(e.reason)
    except fsops.UNAVAILABLE as e:
        return fsops.unavailable_response(e)
    except PermissionError:
        return _denied(tr("읽기 권한이 없습니다"))
    except OSError as e:
        logger.warning(f"fs_raw 실패: {e}")
        return JSONResponse({"error": "read failed"}, status_code=500)
    kind = out[0]
    if kind == "missing":
        return JSONResponse({"error": "not found"}, status_code=404)
    if kind == "not_image":
        return _denied(tr("이미지 파일이 아닙니다"))
    if kind == "too_large":
        return JSONResponse(
            {"error": "too_large",
             "reason": tr("이미지가 너무 큽니다 (최대 {max}MB)", max=fsguard.MAX_IMAGE_BYTES // (1024*1024))},
            status_code=413,
        )
    _, data, mime = out
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


# --- 변경 감시 (코드 뷰어 자동 갱신) -------------------------------------------

# 구독 메시지 한 통의 상한. 구독 64개 × 경로 몇백 바이트면 한참 남는다.
_WATCH_MSG_MAX = 64 * 1024


@router.websocket("/ws-fs-watch")
async def ws_fs_watch(websocket: WebSocket):
    """브라우저 → `{"type":"watch","subs":[{kind,path},…]}`(매번 전체 목록),
    서버 → `{"type":"watching","rejected":[…]}` / `{"type":"changed","subs":[…]}`.
    판정·감시는 fs_watch.py가 한다."""
    from routes.pty import _ws_auth_token
    import auth
    token = _ws_auth_token(websocket)
    if token is None:
        await websocket.close(code=4001)
        return
    await websocket.accept()
    session_watchdog = auth.spawn_session_watchdog(websocket, token)
    from resilience import wsutil
    pinger = wsutil.start_pinger(websocket)
    client_id = id(websocket)
    try:
        while True:
            text = await websocket.receive_text()
            if len(text) > _WATCH_MSG_MAX:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if not isinstance(msg, dict) or msg.get("type") != "watch":
                continue
            rejected = await fs_watch.hub.set_subs(client_id, websocket.send_json, msg.get("subs"))
            await websocket.send_json({"type": "watching", "rejected": rejected})
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001 — 한 연결의 오류가 허브를 흔들면 안 된다
        logger.info(f"ws-fs-watch 종료: {e}")
    finally:
        session_watchdog.cancel()
        pinger.cancel()
        await fs_watch.hub.remove(client_id)


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


@router.post("/api/files/{file_id}/pin")
async def pin_file(file_id: str, request: Request):
    """고정 켜기/끄기 — JSON `{"pin": true|false}`. 고정된 파일은 30일 자동 삭제와 용량 정리에서 빠진다.

    승격은 요구하지 않는다 — 삭제(`DELETE /api/files/{id}`)가 요구하지 않는 것과 같은 급이다(파일을 남에게 열어
    주는 동작이 아니라 내 저장소의 보존 기간만 바꾼다)."""
    body = await _read_json_body(request)
    pinned = body.get("pin")
    if not isinstance(pinned, bool):
        return JSONResponse({"error": "bad_request", "reason": "pin must be a boolean"}, status_code=400)
    item = await asyncio.to_thread(file_store.set_pin, file_id, pinned)
    if item is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {"ok": True, "item": _file_public(item)}


MAX_BULK_DELETE = 500


@router.post("/api/files/delete")
async def delete_files(request: Request):
    """일괄 정리(2026-09-23, 첨부 탭 「선택 삭제」) — 30일 자동 정리와 별개로
    사용자가 직접 조건(필터 칩)으로 골라 한 번에 지우는 경로. `DELETE
    /api/files/{id}`를 N번 부르지 않는 이유는 file_store.delete_many 주석
    참고. 승격(require_elevated)은 **요구하지 않는다** — 단건 삭제가 이미
    요구하지 않는데 일괄에만 걸면 단건 삭제를 반복 호출하는 것으로 그냥
    우회된다(의미가 없다)."""
    body = await _read_json_body(request)
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        return JSONResponse({"error": "bad_request", "reason": "ids must be a non-empty list of strings"},
                             status_code=400)
    if len(ids) > MAX_BULK_DELETE:
        return JSONResponse({"error": "bad_request", "reason": f"max {MAX_BULK_DELETE} ids per request"},
                             status_code=400)
    result = await asyncio.to_thread(file_store.delete_many, ids)
    return {"ok": True, **result}


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
        return JSONResponse({"error": "bad_host", "reason": tr("원격 호스트 id가 필요합니다")},
                            status_code=400)
    fp = await asyncio.to_thread(file_store.real_path_for, file_id)
    if fp is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    import host_store
    import peer_client

    peer = await asyncio.to_thread(host_store.find_peer, host)
    if peer is None:
        return JSONResponse({"error": "host_not_found",
                             "reason": tr("등록되지 않은 호스트입니다: {host}", host=host)}, status_code=404)
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
    # `id`·`path`는 상대(B)가 **의도적으로 안 내려준다**(routes/peer.py의 strip).
    # B의 저장소 키와 절대 경로라 여기서 그대로 흘리면 화면이 그걸 로컬 것으로
    # 오해한다 — 전달할 것은 "어떻게 됐는가"뿐이다.
    return {"ok": True, "host": host, **{k: r.get(k) for k in ("reused", "typed")}}


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
    pane = await asyncio.to_thread(tmux_target.session_pane, session)   # tmux 조회(2초) — 루프 밖에서
    if not pane:
        return JSONResponse({"error": "session_not_found"}, status_code=404)
    if authz_leases.enforced():
        cwd = await asyncio.to_thread(authz_leases.session_cwd, session)
        blocked = await authz_leases.enforce_write(request, [cwd] if cwd else [], op="insert")
        if blocked is not None:
            return blocked
    if tmux_target.has_control_chars(str(fp)):
        # 인용으로는 못 막는다 — tty·readline이 `^U`·`\r`을 셸보다 먼저 처리한다(shell_quote.has_control_chars).
        return JSONResponse({"error": "unsafe_path", "reason": tr("경로에 제어문자가 있어 터미널에 넣지 않았습니다 — 줄바꿈 같은 문자는 셸이 바로 실행할 수 있습니다")}, status_code=400)
    ok = await asyncio.to_thread(tmux_target.type_path_to_tmux, pane, str(fp))
    if not ok:
        return JSONResponse({"error": "insert_failed"}, status_code=500)
    return {"ok": True}
