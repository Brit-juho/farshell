"""MCP 서버 조회·토글 API (97번 1단계 3/n).

읽기와 쓰기를 라우터 두 개로 나눈다(R4 — 핸들러마다 승격 검사를 붙이면
하나라도 빠뜨린다). git 계정 API와 같은 구조다:

- `router`          : `GET /api/mcp` — 조회
- `elevated_router` : `POST /api/mcp/toggle`·`/api/mcp/group` — **끄고 켜는 것은
  승격이 필요하다.** MCP 서버를 켜고 끄는 건 곧 에이전트가 어떤 도구를 부를 수
  있는지를 바꾸는 일이고, 이 UI는 터널 너머 공개 인터넷에 노출된다.

2단계(그룹 태그)에서 늘어난 것:

- `POST /api/mcp/tags`  — 서버 하나의 태그 교체. **승격을 요구하지 않는다.**
  태그는 FarShell 화면 안에만 있는 라벨이라 에이전트의 능력을 바꾸지 않는다.
  여기에 승격을 걸면 칩 하나 붙일 때마다 비밀번호를 묻게 된다.
- `POST /api/mcp/group` — 그룹 전체를 목표 상태로. **승격을 요구한다** —
  이건 토글을 여러 번 하는 것과 정확히 같은 일이다.

두 핸들러 모두 **스레드로 offload** 한다. `~/.claude.json`은 이 맥에서만
238KB고(프로젝트 69개) 워크트리 탐색은 git을 부른다 — 이벤트 루프에서
그대로 돌리면 전체 서버가 멈춘다(과거 E2E 스모크가 실제로 이렇게 멈췄다).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

import mcp_catalog
import mcp_plugins
import mcp_scan
import mcp_write
from auth import require_elevated

logger = logging.getLogger(__name__)

router = APIRouter()
elevated_router = APIRouter(dependencies=[Depends(require_elevated)])


@router.get("/api/mcp")
async def list_mcp(worktree: str | None = Query(default=None)):
    """워크트리 하나(+ 전역)를 기준으로 정의된 MCP 서버를 모은다.

    저장된 것을 돌려주는 게 아니라 **부를 때마다 실제 파일을 읽는다** — 그래서
    사용자가 터미널에서 직접 고친 것도 항상 최신으로 보인다(97번 §1-1).
    """
    try:
        result = await asyncio.to_thread(mcp_scan.scan, worktree)
    except Exception:
        logger.exception("mcp scan 실패")
        return JSONResponse({"error": "scan_failed"}, status_code=500)

    result["groups"] = mcp_scan.group_by_name(result["servers"])
    # 2단계 — 태그는 우리 저장소에 있고 CLI 파일엔 없다. 스캔 결과와 합쳐서
    # 내려보내되 **서버 목록 안에 섞지 않는다**(`servers[].tags`로 넣으면
    # "CLI 파일에서 읽은 사실"과 "우리가 붙인 라벨"이 한 덩어리로 보인다).
    try:
        tags = await asyncio.to_thread(mcp_catalog.get_tags)
    except Exception:
        logger.exception("MCP 태그 읽기 실패")
        tags = {}
    result["tags"] = tags
    result["allTags"] = sorted({t for vals in tags.values() for t in vals}, key=str.casefold)
    return result


@elevated_router.post("/api/mcp/group")
async def apply_group(request: Request):
    """태그가 붙은 서버 전부를 한 번에 켜거나 끈다(97번 §1-3).

    **뒤집기가 아니라 목표 상태 지정**이라, 섞인 상태에서 눌러도 결과가
    결정적이고 일부 실패 뒤 다시 눌러도 안전하다. 응답의 `results`에 항목별
    결과가 그대로 들어 있어 화면이 무엇이 실패했는지 말할 수 있다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    tag = mcp_catalog.normalize_tag(body.get("tag"))
    enabled = body.get("enabled")
    worktree_id = body.get("worktree") or None

    if tag is None:
        return JSONResponse({"error": "invalid_tag"}, status_code=400)
    if not isinstance(enabled, bool):
        return JSONResponse(
            {"error": "invalid_enabled", "reason": "enabled는 true/false 여야 한다"},
            status_code=400,
        )

    result = await asyncio.to_thread(
        mcp_write.apply_group, tag, enabled, worktree_id=worktree_id,
    )
    if result["status"] == "failed":
        return JSONResponse({"error": "group_failed", **result}, status_code=409)
    return result


@router.post("/api/mcp/tags")
async def set_tags(request: Request):
    """서버 이름 하나의 태그를 통째로 교체한다.

    승격을 요구하지 않는 이유는 이 파일 머리말에 있다 — 태그는 라벨이고,
    CLI 설정 파일은 전혀 건드리지 않는다.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    name = body.get("name")
    tags = body.get("tags")
    if not isinstance(tags, list):
        return JSONResponse({"error": "invalid_tags"}, status_code=400)

    result = await asyncio.to_thread(mcp_catalog.set_tags, name, tags)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/api/mcp/tags/rename")
async def rename_tag(request: Request):
    """태그 이름을 모든 서버에서 한 번에 바꾼다 — 오타 하나 때문에 그룹이
    둘로 갈라진 채 "그룹 켜기"가 절반만 켜는 일을 막는다."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = await asyncio.to_thread(
        mcp_catalog.rename_tag, body.get("from"), body.get("to"),
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@router.post("/api/mcp/tags/delete")
async def delete_tag(request: Request):
    """태그를 모든 서버에서 뗀다. 서버 자체는 건드리지 않는다."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = await asyncio.to_thread(mcp_catalog.delete_tag, body.get("tag"))
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@elevated_router.post("/api/mcp/toggle")
async def toggle_mcp(request: Request):
    """서버 하나를 스코프 하나에서 켜거나 끈다.

    **토글(뒤집기)이 아니라 목표 상태 지정이다** — `enabled`를 그대로 받는다.
    부분 실패 뒤 같은 요청을 다시 보내도 안전하다(멱등).

    응답은 세 가지다(97번 §3-3):
    - 200 `status:"ok"`      — 쓰고 다시 읽어 확인까지 됐다
    - 200 `status:"unknown"` — **썼는데 확인하지 못했다.** 성공이라고도 실패라고도
      말하지 않는다. 화면은 이 상태를 그대로 보여주고 재시도를 권해야 한다.
    - 409 `status:"failed"`  — 안전하게 쓸 수 없어 **파일을 건드리지 않았다**
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    tool = str(body.get("tool", "") or "")
    name = str(body.get("name", "") or "")
    scope = str(body.get("scope", "global") or "global")
    worktree_id = body.get("worktree") or None
    shared = bool(body.get("shared", False))
    enabled = body.get("enabled")

    if tool not in mcp_write.TOOLS:
        return JSONResponse(
            {"error": "invalid_tool", "reason": f"tool은 {sorted(mcp_write.TOOLS)} 중 하나"},
            status_code=400,
        )
    if not name:
        return JSONResponse({"error": "missing_name"}, status_code=400)
    if not isinstance(enabled, bool):
        # 문자열 "false"를 참으로 읽어 서버를 켜버리는 사고를 막는다.
        return JSONResponse(
            {"error": "invalid_enabled", "reason": "enabled는 true/false 여야 한다"},
            status_code=400,
        )
    if scope not in ("global", "local"):
        return JSONResponse({"error": "invalid_scope"}, status_code=400)

    result = await asyncio.to_thread(
        mcp_write.set_enabled,
        tool, name, enabled,
        scope=scope, worktree_id=worktree_id, shared=shared,
    )

    if result["status"] == "failed":
        return JSONResponse({"error": "write_refused", **result}, status_code=409)
    return result


# ------------------------------------------------------ 자격증명 (97번 3단계)
#
# **원문은 어떤 응답에도 실리지 않는다.** 조회는 mcp_catalog.cred_public()이
# 만든 마스킹 형태만 내보낸다 — 이 UI는 터널 너머 공개 인터넷에 노출된다.


@router.get("/api/mcp/creds")
async def list_creds():
    """마스킹된 목록 + 우리가 심어둔 참조의 위치(회수용, §2-5)."""
    creds = await asyncio.to_thread(mcp_catalog.public_creds)
    refs = await asyncio.to_thread(mcp_catalog.list_refs)
    return {"creds": creds, "refs": refs}


@elevated_router.post("/api/mcp/creds")
async def set_cred(request: Request):
    """자격증명 하나를 넣거나 갱신한다. **승격 필요** — 시크릿을 받는 경로다.

    `env`를 비우면 `FSH_MCP_<서버>_<키>`가 기본값으로 붙는다(2026-09-16 확정:
    기본은 자동, 덮어쓰기 허용).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    result = await asyncio.to_thread(
        mcp_catalog.set_cred,
        str(body.get("server") or ""), str(body.get("key") or ""),
        str(body.get("secret") or ""),
        env=body.get("env"),
        fingerprint_=body.get("fingerprint") or None,
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return result


@elevated_router.post("/api/mcp/creds/delete")
async def delete_cred(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = await asyncio.to_thread(mcp_catalog.delete_cred, str(body.get("id") or ""))
    if not result.get("ok"):
        return JSONResponse(result, status_code=404)
    return result


@elevated_router.post("/api/mcp/deploy")
async def deploy(request: Request):
    """서버 정의 하나를 다른 도구·스코프로 가져온다 — **값이 아니라 참조로**.

    3단계의 핵심 동작이다. 대체 후에도 값이 남아 있으면 파일을 열지 않고
    409로 거절한다(`.mcp.json`은 저장소에 커밋되는 파일이다).
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    name = str(body.get("name") or "")
    defn = body.get("defn")
    tool = str(body.get("tool") or "")
    scope = str(body.get("scope") or "global")
    shared = bool(body.get("shared", False))
    # `or {}`로 기본값을 먼저 씌우면 안 된다 — `[]`가 falsy라 빈 dict로 둔갑해
    # 타입 검사를 통과한다(실측). 받은 그대로 검사하고 나서 기본값을 씌운다.
    env_map = body.get("env_map")
    if env_map is None:
        env_map = {}
    worktree_id = body.get("worktree") or None

    if not name:
        return JSONResponse({"error": "missing_name"}, status_code=400)
    if not isinstance(defn, dict) or not defn:
        return JSONResponse({"error": "invalid_defn"}, status_code=400)
    if tool not in mcp_write.TOOLS:
        return JSONResponse({"error": "invalid_tool"}, status_code=400)
    if scope not in ("global", "local"):
        return JSONResponse({"error": "invalid_scope"}, status_code=400)
    if not isinstance(env_map, dict):
        return JSONResponse({"error": "invalid_env_map"}, status_code=400)

    wt = await asyncio.to_thread(mcp_scan.find_worktree, worktree_id)
    result = await asyncio.to_thread(
        mcp_write.deploy, name, defn,
        tool=tool, scope=scope,
        worktree_path=(wt.get("path") if wt else None),
        shared=shared, env_map=env_map,
    )
    if result["status"] == "failed":
        return JSONResponse({"error": "deploy_refused", **result}, status_code=409)
    return result


# --------------------------------------------------------- 플러그인 (4단계)
#
# MCP와 같은 "이름 + 스코프 + enabled" 메커니즘이다(§0-2) — 플러그인 안에
# MCP가 번들될 수 있어 파일 스키마 차원에서 이미 겹친다. **설치는 하지 않는다:**
# 마켓플레이스 등록이 선행돼야 하고 설치는 네트워크 행위이며, 미설치
# 플러그인은 `enabled` 값만으론 켜지지 않는다.


@router.get("/api/mcp/plugins")
async def list_plugins(worktree: str | None = Query(default=None)):
    try:
        return await asyncio.to_thread(mcp_plugins.scan, worktree)
    except Exception:
        logger.exception("플러그인 scan 실패")
        return JSONResponse({"error": "scan_failed"}, status_code=500)


@elevated_router.post("/api/mcp/plugins/toggle")
async def toggle_plugin(request: Request):
    """설치된 플러그인 하나를 켜거나 끈다. MCP 토글과 같은 이유로 승격 필요 —
    플러그인 안에 MCP가 들어 있을 수 있어 에이전트의 능력이 바뀐다."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    name = str(body.get("name") or "")
    enabled = body.get("enabled")
    if not name:
        return JSONResponse({"error": "missing_name"}, status_code=400)
    if not isinstance(enabled, bool):
        return JSONResponse(
            {"error": "invalid_enabled", "reason": "enabled는 true/false 여야 한다"},
            status_code=400,
        )

    result = await asyncio.to_thread(
        mcp_write.set_plugin_enabled, name, enabled,
        tool=str(body.get("tool") or "claude"),
        scope=str(body.get("scope") or "global"),
        worktree_id=body.get("worktree") or None,
    )
    if result["status"] == "failed":
        return JSONResponse({"error": "toggle_refused", **result}, status_code=409)
    return result
