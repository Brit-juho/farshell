"""에이전트 턴 체크포인트 읽기·설정 API — **사람 세션만**(기계 토큰·소켓 주체·다른 Mac은 403).

체크포인트는 사람이 에이전트의 작업을 점검하는 도구다. 에이전트는 자기 도구로 파일을 다루면 되므로 처음부터 사람만으로 좁혔다
(기존 git 라우트는 `auth()`라 기계 토큰도 통과한다). 다른 Mac 중계(`relay_policy.py`)·소켓 허용 목록에도 넣지 않는다.

- `GET /api/checkpoints?repo=` — 켜짐 여부, 세션별 체크포인트 목록(턴별 파일 수·+/−). 파일 내용은 없다.
- `GET /api/checkpoints/diff?repo=&id=[&file=][&from=]` — `file` 없으면 그 턴에 바뀐 파일 목록, 있으면 그 파일의 diff(가림·1MB).
- `PUT /api/checkpoints/settings {repo, enabled}` — 이 저장소에서 켜기·끄기. 감사 체인에 `ckpt.settings`(`started`를 못 남기면 503).

`VT_CHECKPOINTS=0`이면 세 라우트 모두 404 `{"error":"disabled"}`. 복원·삭제 라우트는 이후 단계(승격 필요)다.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

import auth
import checkpoint_jobs
import checkpoint_store
import checkpoints
from authz import audit
from i18n import tr
from resilience import mounts as res_mounts, pools as res_pools

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(auth.require_human_session)])

LIST_SUMMARY_LIMIT = 20       # 한 응답에서 파일 수·+/−를 계산해 주는 turn-end 개수(나머지는 null)

_summary_cache: "OrderedDict[tuple, dict]" = OrderedDict()   # 체크포인트 커밋은 바뀌지 않으므로 오래 써도 된다
_summary_lock = threading.Lock()


def _disabled() -> JSONResponse | None:
    if checkpoint_jobs.globally_enabled():
        return None
    return JSONResponse({"error": "disabled", "reason": tr("이 서버에서 체크포인트가 꺼져 있습니다")}, status_code=404)


async def _repo(repo: str):
    from routes.git import _repo_dir
    return await _repo_dir(repo)


def _sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "surrogateescape")).hexdigest()[:8]


# --- 목록 -----------------------------------------------------------------------


def _file_summary(top: Path, e: dict, entries: list[dict]) -> dict | None:
    base = checkpoints.turn_base(entries, e["id"])
    ck = (e["commit"], base["commit"] if base else e["parent"])
    with _summary_lock:
        hit = _summary_cache.get(ck)
        if hit is not None:
            _summary_cache.move_to_end(ck)
            return hit
    t = checkpoints.turn_files(top, e["id"], timeout=10)
    if t is None:
        return None
    out = {"files": len(t["files"]), "added": sum(f["added"] for f in t["files"]),
           "deleted": sum(f["deleted"] for f in t["files"]), "headMoved": t["head_moved"], "from": t["from"]}
    with _summary_lock:
        _summary_cache[ck] = out
        while len(_summary_cache) > 512:
            _summary_cache.popitem(last=False)
    return out


def _collect(top: Path) -> dict:
    info = checkpoints.repo_key(top)
    if info is None:
        return {"repo": False}
    enabled = checkpoint_store.is_enabled(info["key"])
    entries = checkpoints.list_refs(top)
    sessions: dict[str, dict] = {}
    for e in entries:
        if e["restore"]:
            continue
        sess = sessions.setdefault(e["sess"], {"id": e["sess"], "agent": e["agent"], "entries": [], **checkpoint_jobs.labels(e["sess"])})
        sess["entries"].append(e)
    turn_ends = sorted((e for e in entries if e["kind"] == "turn-end" and not e["restore"]), key=lambda e: e["created"], reverse=True)
    summarized = {e["id"] for e in turn_ends[:LIST_SUMMARY_LIMIT]}
    out_sessions = []
    for sess in sessions.values():
        rows = []
        for e in sess["entries"]:
            row = {"id": e["id"], "kind": e["kind"], "turn": e["turn"], "seq": e["seq"], "created": e["created"],
                   "skipped": e["skipped"], "trusted": e["trusted"], "preTrusted": e["pre_trusted"]}
            if e["id"] in summarized:
                row["summary"] = _file_summary(top, e, entries)
            rows.append(row)
        last = max(e["created"] for e in sess["entries"])
        item = {k: v for k, v in sess.items() if k != "entries"}
        item.update({"last": last, "checkpoints": rows})
        out_sessions.append(item)
    out_sessions.sort(key=lambda s: s["last"], reverse=True)
    last_result = checkpoint_jobs.last_result(info["top"])
    return {"repo": True, "enabled": enabled, "sessions": out_sessions, "lastJob": last_result,
            "limits": {"fileMB": checkpoints.max_file_bytes() // (1024 * 1024), "keepTurns": checkpoints.keep_turns(),
                       "keepDays": checkpoints.keep_days()}}


@router.get("/api/checkpoints")
async def list_checkpoints(repo: str = Query(...)):
    if (off := _disabled()) is not None:
        return off
    p = await _repo(repo)
    if not isinstance(p, Path):
        return p
    try:
        body = await res_pools.run("git", _collect, p, op="ckpt.list", key=str(p)[-40:], timeout=checkpoints.timeout_seconds() + 5)
    except res_pools.Busy:
        return JSONResponse({"error": "busy", "reason": tr("지금은 바쁩니다. 잠시 뒤 다시 시도하세요")}, status_code=503)
    except Exception as e:  # noqa: BLE001 — 기한 초과 등
        logger.info("[ckpt] 목록 실패: %s", type(e).__name__)
        return JSONResponse({"error": "unavailable", "reason": tr("체크포인트를 읽지 못했습니다")}, status_code=503)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


# --- diff -----------------------------------------------------------------------


@router.get("/api/checkpoints/diff")
async def checkpoint_diff(repo: str = Query(...), id: str = Query(...), file: str = Query(""), against: str = Query("prev"),
                          from_: str = Query("", alias="from")):
    if (off := _disabled()) is not None:
        return off
    if not checkpoints.ID_RE.match(id) or (from_ and not checkpoints.ID_RE.match(from_)) or against != "prev":
        return JSONResponse({"error": "bad_request", "reason": tr("잘못된 요청입니다")}, status_code=400)
    p = await _repo(repo)
    if not isinstance(p, Path):
        return p
    try:
        if file:
            out = await res_pools.run("git", checkpoints.diff, p, id, from_ or None, file, op="ckpt.diff", key=id,
                                      timeout=checkpoints.timeout_seconds() + 5)
        else:
            out = await res_pools.run("git", checkpoints.turn_files, p, id, from_ or None, op="ckpt.files", key=id,
                                      timeout=checkpoints.timeout_seconds() + 5)
    except res_pools.Busy:
        return JSONResponse({"error": "busy", "reason": tr("지금은 바쁩니다. 잠시 뒤 다시 시도하세요")}, status_code=503)
    except Exception as e:  # noqa: BLE001
        logger.info("[ckpt] diff 실패: %s", type(e).__name__)
        return JSONResponse({"error": "unavailable", "reason": tr("체크포인트를 읽지 못했습니다")}, status_code=503)
    if out is None:
        return JSONResponse({"error": "not_found", "reason": tr("체크포인트를 찾을 수 없습니다")}, status_code=404)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


# --- 설정(켜기·끄기) --------------------------------------------------------------


def _audit(request: Request, detail: dict, result: str, *, must: bool = False) -> JSONResponse | None:
    owner = auth.session_device(request.cookies.get("vt_session", "")) or "-"
    rec = {"principal": f"human:{owner}", "action": "ckpt.settings", "resource": "ckpt:local",
           "scopes": ["ckpt.settings"], "decision": "allow", "result": result, "detail": detail}
    try:
        audit.append(rec)
    except Exception as e:  # noqa: BLE001
        if must:
            logger.warning("체크포인트 설정 감사 기록 실패 — 거부: %s", type(e).__name__)
            return JSONResponse({"error": "audit_unavailable", "reason": tr("감사 기록을 쓸 수 없어 요청을 거부했습니다")}, status_code=503)
        logger.warning("체크포인트 설정 감사 기록 실패: %s", type(e).__name__)
    return None


def _resolve_key(p: Path) -> dict | None:
    if res_mounts.skip_for_scan(str(p)):
        return {"error": "unsupported_location"}
    return checkpoints.repo_key(p)


@router.put("/api/checkpoints/settings")
async def checkpoint_settings(request: Request):
    if (off := _disabled()) is not None:
        return off
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    if (not isinstance(body, dict) or not isinstance(body.get("repo"), str) or type(body.get("enabled")) is not bool
            or set(body) - {"repo", "enabled"}):
        return JSONResponse({"error": "bad_request", "reason": tr("잘못된 요청입니다")}, status_code=400)
    p = await _repo(body["repo"])
    if not isinstance(p, Path):
        return p
    info = await asyncio.to_thread(_resolve_key, p)
    if info is None:
        return JSONResponse({"error": "not_a_repo", "reason": tr("git 저장소가 아니라 체크포인트를 만들 수 없습니다")}, status_code=400)
    if info.get("error"):
        return JSONResponse({"error": "unsupported_location", "reason": tr("이 위치(원격·보호된 폴더)에서는 체크포인트를 만들지 않습니다")},
                            status_code=409)
    enabled = body["enabled"]
    detail = {"enabled": enabled, "repo": _sha8(info["key"])}       # 경로 원문은 감사 기록에 남기지 않는다
    refused = await asyncio.to_thread(_audit, request, detail, "started", must=True)
    if refused is not None:
        return refused
    try:
        res = await asyncio.to_thread(checkpoint_store.set_enabled, info["key"], enabled)
    except Exception as e:  # noqa: BLE001
        await asyncio.to_thread(_audit, request, detail, "failed")
        logger.warning("[ckpt] 설정 저장 실패: %s", type(e).__name__)
        return JSONResponse({"error": "save_failed", "reason": tr("설정을 저장하지 못했습니다")}, status_code=500)
    if not res.get("ok"):
        await asyncio.to_thread(_audit, request, detail, "failed")
        return JSONResponse({"error": res.get("error", "save_failed"), "reason": tr("켠 저장소가 너무 많습니다")}, status_code=409)
    await asyncio.to_thread(_audit, request, detail, "ok")
    await checkpoint_jobs._notify()
    return {"ok": True, "enabled": enabled}
