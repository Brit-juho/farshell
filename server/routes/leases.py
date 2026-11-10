"""폴더 lease API — 본체는 `authz/leases.py`(규칙·강제·한계는 그 머리말).

  POST   /api/leases              lease 잡기 `{glob, mode?, exclusive?, ttl?, reason?}` → 201. 충돌 409 `lease_conflict`(보유자 닉네임·만료만)
  POST   /api/leases/{id}/renew   갱신 `{ttl?}` — 만든 때부터 최대 8시간
  DELETE /api/leases/{id}         놓기(보유자 또는 사람)
  GET    /api/leases              에이전트는 자기 것만, 사람·터미널(fsh)은 전부
  POST   /api/leases/check        순수 조회 `{paths, holder?}` — pre-commit guard 가 쓴다. 아무것도 바꾸지 않는다

호출자는 unix socket 의 연결(peer)로 정해진다(에이전트·터미널의 fsh). 토큰 경로에서는 `X-Fsh-*` 헤더로 pane 을 확인한다.
사람은 로그인 세션. 사람 전용 목록·회수는 `routes/authz.py`(`/api/authz/leases`). relay 허용 목록에 없다 — 다른 맥에서 쓸 수 없다.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from authz import leases
from authz import principals as P
from authz.errors import ShareError
from i18n import tr

logger = logging.getLogger(__name__)

router = APIRouter()


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class LeaseResponse(_Loose):
    ok: bool
    lease: dict


class LeasesResponse(_Loose):
    leases: list[dict]
    enforced: bool


class CheckResponse(_Loose):
    ok: bool
    checked: int
    conflicts: list[dict]


class ReleaseResponse(_Loose):
    ok: bool
    id: str
    released: int


def _err(e: ShareError) -> JSONResponse:
    return JSONResponse(e.body(), status_code=e.status)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return b if isinstance(b, dict) else {}


async def _caller(request: Request) -> P.Principal:
    """lease 를 다룰 수 있는 호출자: 로그인한 사람, 확인된 에이전트, 터미널의 fsh(cli — 조회·check 만 의미가 있다).
    훅·서비스 데몬·peer 는 아니다."""
    if request.scope.get("fsh_sock") is True:
        p = getattr(request.state, "fsh_principal", None)
        if p is not None and (p.kind == P.AGENT or P.is_cli(p)) and not P.is_hook(p):
            return p
        raise leases.LeaseError("request_not_allowed", tr("폴더 점유를 다룰 수 없는 호출자입니다"), 403)
    p = await asyncio.to_thread(leases.identify, request)
    if p is not None and (p.kind in (P.HUMAN, P.AGENT)):
        return p
    raise leases.LeaseError("identity_unverified", tr("호출자를 확인하지 못했습니다 — 터미널의 fsh 또는 에이전트 세션에서 부르세요"), 403)


def _sees_all(p: P.Principal) -> bool:
    return p.kind == P.HUMAN or P.is_cli(p)


@router.post("/api/leases", response_model=LeaseResponse, status_code=201)
async def create(request: Request):
    """lease 를 잡는다. write 는 `fs.write`(ops=[write]), read 는 `fs.read` 권한이 그 경로에 있어야 한다(없으면 403 + 필요한 것)."""
    body = await _body(request)
    try:
        p = await _caller(request)
        v = await asyncio.to_thread(
            leases.acquire, p, str(body.get("glob") or body.get("path") or ""), mode=str(body.get("mode") or "write"),
            exclusive=bool(body.get("exclusive")), ttl=body.get("ttl"), reason=str(body.get("reason") or ""))
    except ShareError as e:
        return _err(e)
    return JSONResponse({"ok": True, "lease": v}, status_code=201)


@router.post("/api/leases/check", response_model=CheckResponse)
async def check(request: Request):
    """paths(절대 경로 목록)에서 이 호출자를 막는 lease. 에이전트는 자기 기준, 터미널(cli)은 `holder`(주체 id)를 줄 수 있다."""
    body = await _body(request)
    try:
        p = await _caller(request)
        return await asyncio.to_thread(leases.check, p, body.get("paths"), body.get("holder") if not p.kind == P.AGENT else None)
    except ShareError as e:
        return _err(e)


@router.post("/api/leases/{lease_id}/renew", response_model=LeaseResponse)
async def renew(lease_id: str, request: Request):
    body = await _body(request)
    try:
        p = await _caller(request)
        v = await asyncio.to_thread(leases.renew, p, lease_id, body.get("ttl"))
    except ShareError as e:
        return _err(e)
    return {"ok": True, "lease": v}


@router.delete("/api/leases/{lease_id}", response_model=ReleaseResponse)
async def release(lease_id: str, request: Request):
    try:
        p = await _caller(request)
        return await asyncio.to_thread(leases.release, p, lease_id)
    except ShareError as e:
        return _err(e)


@router.get("/api/leases", response_model=LeasesResponse)
async def list_leases(request: Request):
    try:
        p = await _caller(request)
        return {"leases": await asyncio.to_thread(leases.list_for, p, everyone=_sees_all(p)), "enforced": leases.enforced()}
    except ShareError as e:
        return _err(e)
