"""git 계정 저장소 + 바인딩 API (N30, 40-dock-git.md §1).

두 라우터로 나눈다(R4 — 핸들러마다 승격 검사를 붙이지 않고 라우터 레벨로 묶는다):
- `router`      : 읽기(GET) — 승격 불필요.
- `elevated_router` : 쓰기(계정 등록/삭제, 바인딩 변경) — `auth.require_elevated`가
  `APIRouter(dependencies=[...])`로 전체에 걸린다. 핸들러마다 개별로 호출하지 않는다.

실제 stage/unstage/commit(D16)은 routes/files.py에 이미 있고 이번 범위가 아니다.
push/PR(N32)도 별도 작업 — 여기는 계정·바인딩 인프라만 다룬다.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

import fsguard
import git_account_store as store
import snippet_store
from auth import require_elevated

logger = logging.getLogger(__name__)

router = APIRouter()
elevated_router = APIRouter(dependencies=[Depends(require_elevated)])


def _repo_top(repo: str) -> str | None:
    """repo 원시 문자열 → fsguard 경계 안의 git 저장소 top 경로. 아니면 None.

    snippet_store.resolve_project_key와 정확히 같은 판정(fsguard 검증 +
    `git rev-parse --show-toplevel`)을 재사용한다 — 스니펫의 project 키·git 계정
    바인딩의 repo 키가 서로 다른 값이 되면 어느 한쪽이 사용자 기대와 어긋난다.
    """
    return snippet_store.resolve_project_key(repo)


@router.get("/api/git/accounts")
async def list_accounts():
    return {"accounts": [store.account_public(a) for a in store.list_accounts()]}


@elevated_router.post("/api/git/accounts")
async def add_account(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    provider = str(body.get("provider", "") or "")
    host = str(body.get("host", "") or "")
    token = str(body.get("token", "") or "")

    if provider not in store.PROVIDERS:
        return JSONResponse(
            {"error": "invalid_provider", "reason": f"provider는 {sorted(store.PROVIDERS)} 중 하나"},
            status_code=400,
        )
    if not token:
        return JSONResponse({"error": "missing_token"}, status_code=400)

    # 네트워크 호출(GitHub/GitLab GET /user) — 이벤트 루프를 막지 않도록 스레드로 offload.
    verified = await asyncio.to_thread(store.verify_token, provider, host, token)
    if not verified.get("ok"):
        status = 401 if verified.get("error") == "token_invalid" else 400
        return JSONResponse(verified, status_code=status)

    result = store.add_account(
        provider,
        host or ("github.com" if provider == "github" else "gitlab.com"),
        verified["login"],
        token,
        name=body.get("name") or verified.get("name"),
        email=body.get("email") or verified.get("email"),
        ssh_key=body.get("ssh_key"),
        orgs=body.get("orgs"),
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return {"ok": True, "account": store.account_public(result["account"])}


@elevated_router.delete("/api/git/accounts/{account_id}")
async def delete_account(account_id: str):
    result = store.remove_account(account_id)
    if not result.get("ok"):
        return JSONResponse(result, status_code=404)
    return result


@router.get("/api/git/binding")
async def get_binding(repo: str = Query(...)):
    try:
        top = fsguard.resolve_under_roots(repo)
    except fsguard.FsDenied as e:
        return JSONResponse({"error": "denied", "reason": e.reason}, status_code=403)
    repo_top = _repo_top(str(top))
    remote_url = await asyncio.to_thread(store.git_remote_url, repo_top) if repo_top else None
    account_id = store.resolve_account_id(
        repo_top, remote_url, store.get_bindings(), store.list_accounts()
    )
    return {"repo": repo_top, "account_id": account_id}


@elevated_router.put("/api/git/binding")
async def put_binding(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    repo = str(body.get("repo", "") or "")
    account_id = str(body.get("account_id", "") or "")
    if not repo or not account_id:
        return JSONResponse({"error": "missing_fields"}, status_code=400)
    try:
        top = fsguard.resolve_under_roots(repo)
    except fsguard.FsDenied as e:
        return JSONResponse({"error": "denied", "reason": e.reason}, status_code=403)
    repo_top = _repo_top(str(top))
    if not repo_top:
        return JSONResponse({"error": "not_a_repo"}, status_code=400)
    result = store.set_repo_binding(repo_top, account_id)
    if not result.get("ok"):
        status = 404 if result.get("error") == "account_not_found" else 400
        return JSONResponse(result, status_code=status)
    return result
