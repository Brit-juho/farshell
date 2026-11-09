"""파일 쓰기 라우트(files-overhaul 단계 8) — 저장 `PUT /api/fs/file`, 새 파일·새 폴더, 이름 바꾸기, 휴지통 삭제.

공개 터널로 쓰기가 열리는 첫 경로라서 라우터 전체에 **사람 세션 + 승격**(`require_human_session`,
`require_elevated` — 웹 로그인 비밀번호 재확인, 15분, 결정 D1)을 건다. 기계 토큰·에이전트 주체·peer 서명은 403이고,
에이전트는 자기 도구로 쓴다. 호스트 중계 허용 목록(`relay_policy.py`)에 없어 **이 맥의 파일만** 쓴다(결정 D7).
교차 출처 요청은 `OriginGuardMiddleware`가 앞에서 막는다(계약 3).

판정·쓰기는 `fs_write`(→ `fsguard.resolve_for_write`)가 하고, 여기서는 본문 상한·감사·lease 알림·응답만 다룬다.
- lease는 막는 장치가 아니다(30-security 3.3) — 사람은 남의 lease 안에서도 쓴다. `enforce_write`가 그 사실을 감사에
  `lease.override`로 남기고 알림을 보낸다.
- 감사(`authz/audit.py`)는 쓰기 **전에** `started`를 반드시 남긴다(못 남기면 503, 쓰지 않는다). 끝나면 결과를 남긴다.
  경로·내용은 남기지 않는다 — 경로는 sha8, 크기만.
"""
from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import auth
import fs_write
from authz import audit
from authz import leases as authz_leases
from i18n import tr
from resilience import fsops

logger = logging.getLogger(__name__)

elevated_router = APIRouter(dependencies=[Depends(auth.require_human_session), Depends(auth.require_elevated)])

_SAVE_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_REV_MAX = 128


def _err(e: fs_write.WriteError) -> JSONResponse:
    return JSONResponse(e.body, status_code=e.status)


def _bad(error: str, reason: str) -> JSONResponse:
    return JSONResponse({"error": error, "reason": reason}, status_code=400)


async def _read_body(request: Request, limit: int) -> dict | JSONResponse:
    """본문을 상한까지만 읽어 JSON 객체로. 넘으면 더 읽지 않고 413."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return JSONResponse({"error": "too_large", "reason": tr("요청이 너무 큽니다"), "limit": limit}, status_code=413)
    buf = bytearray()
    async for part in request.stream():
        buf.extend(part)
        if len(buf) > limit:
            return JSONResponse({"error": "too_large", "reason": tr("요청이 너무 큽니다"), "limit": limit}, status_code=413)
    try:
        body = json.loads(bytes(buf).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _bad("bad_json", tr("JSON 본문이 필요합니다"))
    if not isinstance(body, dict):
        return _bad("bad_json", tr("JSON 본문이 필요합니다"))
    return body


def _str(body: dict, key: str) -> str | None:
    v = body.get(key)
    return v if isinstance(v, str) and v.strip() else None


def _rev(body: dict, key: str = "expected_rev") -> tuple[bool, str | None]:
    """(올바른가, 값). 없거나 null이면 (True, None)."""
    v = body.get(key)
    if v is None:
        return True, None
    if not isinstance(v, str) or not v or len(v) > _REV_MAX:
        return False, None
    return True, v


def _kind(body: dict) -> str | None:
    """`kind` — 없으면 `file`. 폴더는 `dir`로 **밝혀야** 폴더로 다룬다(파일 요청이 바꿔치기로 폴더 작업이 되지 않게)."""
    v = body.get("kind", "file")
    return v if v in ("file", "dir") else None


def _owner(request: Request) -> str:
    return auth.session_device(request.cookies.get("vt_session", "")) or "-"


def _audit(request: Request, action: str, result: str, paths: list[str], *, must: bool = False,
           reason: str = "", size: int | None = None) -> JSONResponse | None:
    rec = {"principal": f"human:{_owner(request)}", "action": action, "resource": "fs:local", "scopes": ["fs.write"],
           "decision": "allow", "result": result, "reason": reason,
           "detail": {"paths_sha8": [audit.sha8(p) for p in paths]}}
    if size is not None:
        rec["sizes"] = {"bytes": size}
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        if must:
            logger.warning(f"파일 쓰기 감사 기록 실패 — 거부: {type(e).__name__}: {e}")
            return JSONResponse({"error": "audit_unavailable", "reason": tr("감사 기록을 쓸 수 없어 요청을 거부했습니다")},
                                status_code=503)
        logger.warning(f"파일 쓰기 감사 기록 실패: {e}")
    return None


async def _run(request: Request, action: str, paths: list[str], fn, *args, **kwargs) -> JSONResponse:
    """lease 알림 → 감사(started, 필수) → fs 풀에서 실행 → 감사(결과) → 응답."""
    blocked = await authz_leases.enforce_write(request, paths, op=action)
    if blocked is not None:
        return blocked
    refused = await _audit_async(request, action, "started", paths, must=True)
    if refused is not None:
        return refused
    try:
        status, body = await fsops.call(_capture, fn, *args, path=paths[0], op=action, **kwargs)
    except fsops.UNAVAILABLE as e:
        await _audit_async(request, action, "unavailable", paths)
        return fsops.unavailable_response(e)
    await _audit_async(request, action, "ok" if status == 200 else "failed", paths,
                       reason="" if status == 200 else str(body.get("error") or ""),
                       size=body.get("size") if status == 200 else None)
    return JSONResponse(body, status_code=status)


async def _audit_async(request, action, result, paths, **kw):
    import asyncio
    return await asyncio.to_thread(_audit, request, action, result, paths, **kw)


def _capture(fn, *args, **kwargs) -> tuple[int, dict]:
    try:
        return 200, fn(*args, **kwargs)
    except fs_write.WriteError as e:
        return e.status, e.body
    except Exception as e:  # noqa: BLE001 — 내부 오류도 같은 모양으로(경로·내용 없이)
        logger.exception(f"파일 쓰기 실패: {type(e).__name__}")
        return 500, {"error": "write_failed", "reason": tr("파일을 쓰지 못했습니다")}


# --- 저장 ------------------------------------------------------------------------------


def _save_once(save_id: str, owner: str, fp: str, path: str, content: str, encoding: str,
               eol: str | None, expected_rev: str | None) -> dict:
    """save_id 자리를 잡고 한 번만 쓴다. 이미 있던 id면 그 결과를 돌려준다(`replayed`). fs 풀 스레드에서 돈다 —
    라우트가 시간 초과로 떠나도 끝까지 돌고 결과를 남긴다(`fsops` 기한은 쓰기를 멈추지 않는다)."""
    prev = fs_write.SAVES.begin(save_id, owner, fp)
    if prev is not None:
        return _replay(prev)
    result: tuple[int, dict] = (500, {"error": "write_failed", "reason": tr("파일을 쓰지 못했습니다")})
    try:
        result = (200, fs_write.save(path, content, encoding=encoding, eol=eol, expected_rev=expected_rev))
    except fs_write.WriteError as e:
        result = (e.status, e.body)
    finally:
        if result[0] == 503:
            fs_write.SAVES.forget(save_id)     # 잠금 대기 초과는 쓰지 않았다 — 같은 id로 다시 시도할 수 있게
        else:
            fs_write.SAVES.finish(save_id, result)
    if result[0] != 200:
        raise fs_write.WriteError(result[0], result[1].get("error", "write_failed"),
                                  **{k: v for k, v in result[1].items() if k != "error"})
    return result[1]


def _replay(prev: tuple[int, dict]) -> dict:
    status, body = prev
    if status != 200:
        raise fs_write.WriteError(status, body.get("error", "write_failed"),
                                  **{k: v for k, v in body.items() if k != "error"}, replayed=True)
    return {**body, "replayed": True}


@elevated_router.put("/api/fs/file")
async def fs_save(request: Request):
    """저장. 본문 `{path, content, encoding, eol?, expected_rev, save_id}`. 계약은 API.md."""
    body = await _read_body(request, fs_write.MAX_BODY_BYTES)
    if isinstance(body, JSONResponse):
        return body
    path = _str(body, "path")
    content = body.get("content")
    encoding = body.get("encoding")
    eol = body.get("eol")
    save_id = body.get("save_id")
    ok_rev, expected_rev = _rev(body)
    if not path:
        return _bad("bad_path", tr("경로가 비었습니다"))
    if not isinstance(content, str):
        return _bad("bad_content", tr("내용(content)은 문자열이어야 합니다"))
    if not isinstance(encoding, str):
        return _bad("bad_encoding", tr("저장할 인코딩을 골라야 합니다"))
    if eol is not None and not isinstance(eol, str):
        return _bad("bad_eol", tr("줄 끝 값이 잘못됐습니다"))
    if not ok_rev:
        return _bad("bad_rev", tr("expected_rev 값이 잘못됐습니다"))
    if "expected_rev" not in body:
        return _bad("bad_rev", tr("expected_rev가 필요합니다 (새로 만들 때는 null)"))
    if not isinstance(save_id, str) or not _SAVE_ID.fullmatch(save_id):
        return _bad("bad_save_id", tr("save_id가 필요합니다 (8–64자 영숫자·-·_)"))
    owner = _owner(request)
    fp = fs_write.fingerprint(path, content, encoding, eol, expected_rev)
    try:
        done = fs_write.SAVES.lookup(save_id, owner, fp)
    except fs_write.WriteError as e:
        return _err(e)
    if done is not None:
        # 시간 초과 뒤 재시도 — 첫 저장의 결과. 경로 차단기·풀을 거치지 않는다(이미 끝났다).
        try:
            return JSONResponse(_replay(done))
        except fs_write.WriteError as e:
            return _err(e)
    return await _run(request, "fs.save", [path], _save_once, save_id, owner, fp, path, content, encoding, eol,
                      expected_rev)


# --- 새 파일 · 이름 바꾸기 · 삭제 ----------------------------------------------------------

_SMALL_BODY = 64 * 1024


@elevated_router.post("/api/fs/new")
async def fs_new(request: Request):
    """빈 새 파일 `{dir, name}`. 같은 이름이 있으면 409 `exists`."""
    body = await _read_body(request, _SMALL_BODY)
    if isinstance(body, JSONResponse):
        return body
    d = _str(body, "dir")
    name = body.get("name")
    if not d:
        return _bad("bad_path", tr("경로가 비었습니다"))
    try:
        fs_write.check_name(name)
    except fs_write.WriteError as e:
        return _err(e)
    return await _run(request, "fs.new", [d.rstrip("/") + "/" + name], fs_write.create, d, name)


@elevated_router.post("/api/fs/rename")
async def fs_rename(request: Request):
    """이름 바꾸기 `{path, to, kind?, overwrite?, expected_rev?}`. `kind: "dir"`면 폴더(덮어쓰기 없음). 쓰기 경계 안에서만."""
    body = await _read_body(request, _SMALL_BODY)
    if isinstance(body, JSONResponse):
        return body
    src = _str(body, "path")
    dst = _str(body, "to")
    overwrite = body.get("overwrite", False)
    ok_rev, expected_rev = _rev(body)
    kind = _kind(body)
    if not src or not dst:
        return _bad("bad_path", tr("경로가 비었습니다"))
    if kind is None:
        return _bad("bad_kind", tr("kind는 file 또는 dir이어야 합니다"))
    if kind == "dir":
        # 폴더는 덮어쓰지 않는다 — overwrite·expected_rev를 받지 않는다(받으면 무시하지 않고 거부).
        if overwrite is not False or body.get("expected_rev") is not None:
            return _bad("bad_overwrite", tr("폴더 이름 바꾸기에는 overwrite·expected_rev를 쓸 수 없습니다"))
        return await _run(request, "fs.rename_dir", [src, dst], fs_write.rename_dir, src, dst)
    if not isinstance(overwrite, bool):
        return _bad("bad_overwrite", tr("overwrite는 참/거짓이어야 합니다"))
    if not ok_rev:
        return _bad("bad_rev", tr("expected_rev 값이 잘못됐습니다"))
    return await _run(request, "fs.rename", [src, dst], fs_write.rename, src, dst, overwrite=overwrite,
                      expected_rev=expected_rev)


@elevated_router.post("/api/fs/delete")
async def fs_delete(request: Request):
    """휴지통으로 옮기기 `{path, kind?, expected_rev?}`. `kind: "dir"`면 폴더(내용째). 지우지 않는다 — 휴지통을 못 쓰면
    409 `trash_unavailable`."""
    body = await _read_body(request, _SMALL_BODY)
    if isinstance(body, JSONResponse):
        return body
    path = _str(body, "path")
    ok_rev, expected_rev = _rev(body)
    kind = _kind(body)
    if not path:
        return _bad("bad_path", tr("경로가 비었습니다"))
    if not ok_rev:
        return _bad("bad_rev", tr("expected_rev 값이 잘못됐습니다"))
    if kind is None:
        return _bad("bad_kind", tr("kind는 file 또는 dir이어야 합니다"))
    if kind == "dir":
        if expected_rev is not None:
            return _bad("bad_rev", tr("폴더 휴지통 이동에는 expected_rev를 쓸 수 없습니다"))
        return await _run(request, "fs.trash_dir", [path], fs_write.delete_dir, path)
    return await _run(request, "fs.trash", [path], fs_write.delete, path, expected_rev=expected_rev)


@elevated_router.post("/api/fs/mkdir")
async def fs_mkdir(request: Request):
    """빈 새 폴더 `{dir, name}`. 같은 이름이 있으면 409 `exists`."""
    body = await _read_body(request, _SMALL_BODY)
    if isinstance(body, JSONResponse):
        return body
    d = _str(body, "dir")
    name = body.get("name")
    if not d:
        return _bad("bad_path", tr("경로가 비었습니다"))
    try:
        fs_write.check_name(name)
    except fs_write.WriteError as e:
        return _err(e)
    return await _run(request, "fs.mkdir", [d.rstrip("/") + "/" + name], fs_write.mkdir, d, name)
