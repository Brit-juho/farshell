"""pair API — 본체·규칙·한계는 `authz/pairs.py`(머리말). 마스터 스위치 `VT_AUTHZ_ENFORCE` 의 `pair` 가 꺼져 있으면 모든 라우트가 409 `pair_disabled`.

  POST   /api/pairs                       묶음과 역할로 pair 만들기 `{sessions?, dirs?, leases?, elevated?, role?, with?}`. 사람, 또는 `pair.manage` 를 가진 에이전트.
                                          같은 시작자·같은 묶음의 합류 전 pair 가 있으면 그것을 돌려준다(200), 새로 만들면 201
  POST   /api/pairs/{id}/code             코드 발급(멱등) → `{code, phrase, expires}`. 시작자만
  POST   /api/pairs/join                  `{code}` 로 합류(확인된 에이전트) → 사람의 승인 요청이 생긴다
  GET    /api/pairs/{id}/status           참여자 본인이 결과를 읽는다(합류자는 이것으로 승인 여부를 안다). 참여자 또는 사람
  GET    /api/pairs                       에이전트는 자기 것만, 사람·터미널(fsh)은 전부
  DELETE /api/pairs/{id}                  끝내기(구성원) 또는 회수(사람)

호출자는 unix socket 의 연결(peer)로 정해진다. 토큰 경로에서는 `X-Fsh-*` 헤더로 pane 을 확인한다. 승인은 이 라우터가 아니라 통합 승인함
(`POST /api/authz/approvals/{id}/decision`, 사람 전용)이 한다. relay 허용 목록에 없다 — 다른 맥에서 쓸 수 없다.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from authz import events as authz_events
from authz import leases, pairs
from authz import principals as P
from authz.errors import ShareError
from i18n import tr

logger = logging.getLogger(__name__)

router = APIRouter()


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class PairResponse(_Loose):
    ok: bool
    pair: dict


class CodeResponse(_Loose):
    ok: bool
    code: str
    phrase: str
    expires: float


class PairsResponse(_Loose):
    pairs: list[dict]


def _err(e: ShareError) -> JSONResponse:
    return JSONResponse(e.body(), status_code=e.status)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return b if isinstance(b, dict) else {}


async def _caller(request: Request) -> P.Principal:
    """pair 를 다룰 수 있는 호출자: 로그인한 사람, 확인된 에이전트, 터미널의 fsh(cli — 전체 목록 읽기뿐). 훅·서비스 데몬·peer 는 아니다."""
    if request.scope.get("fsh_sock") is True:
        p = getattr(request.state, "fsh_principal", None)
        if p is not None and (p.kind == P.AGENT or P.is_cli(p)) and not P.is_hook(p):
            return p
        raise pairs.PairError("request_not_allowed", tr("pair 를 다룰 수 없는 호출자입니다"), 403)
    p = await asyncio.to_thread(leases.identify, request)
    if p is not None and p.kind in (P.HUMAN, P.AGENT):
        return p
    raise pairs.PairError("identity_unverified", tr("호출자를 확인하지 못했습니다 — 터미널의 fsh 또는 에이전트 세션에서 부르세요"), 403)


def _sees_all(p: P.Principal) -> bool:
    return p.kind == P.HUMAN or P.is_cli(p)


@router.post("/api/pairs", response_model=PairResponse, status_code=201)
async def create(request: Request):
    body = await _body(request)
    try:
        pairs.require_enabled()
        p = await _caller(request)
        v, fresh = await asyncio.to_thread(pairs.create, p, body)
    except ShareError as e:
        return _err(e)
    return JSONResponse({"ok": True, "pair": v, "reused": not fresh}, status_code=201 if fresh else 200)


@router.post("/api/pairs/join", response_model=PairResponse)
async def join(request: Request):
    body = await _body(request)
    try:
        pairs.require_enabled()
        p = await _caller(request)
        v = await asyncio.to_thread(pairs.join, p, body.get("code"))
    except ShareError as e:
        return _err(e)
    await authz_events.emit(authz_events.request_event(pairs.request_id_of(v["id"]), "pair"))
    return {"ok": True, "pair": v}


@router.post("/api/pairs/{pair_id}/code", response_model=CodeResponse)
async def code(pair_id: str, request: Request):
    try:
        pairs.require_enabled()
        p = await _caller(request)
        out = await asyncio.to_thread(pairs.issue_code, p, pair_id)
    except ShareError as e:
        return _err(e)
    return {"ok": True, **out}


@router.get("/api/pairs/{pair_id}/status", response_model=PairResponse)
async def status(pair_id: str, request: Request):
    try:
        pairs.require_enabled()
        p = await _caller(request)
        return {"ok": True, "pair": await asyncio.to_thread(pairs.status, p, pair_id)}
    except ShareError as e:
        return _err(e)


@router.get("/api/pairs", response_model=PairsResponse)
async def list_pairs(request: Request):
    try:
        pairs.require_enabled()
        p = await _caller(request)
        return {"pairs": await asyncio.to_thread(pairs.list_for, p, everyone=_sees_all(p))}
    except ShareError as e:
        return _err(e)


@router.delete("/api/pairs/{pair_id}", response_model=PairResponse)
async def end(pair_id: str, request: Request):
    try:
        pairs.require_enabled()
        p = await _caller(request)
        out = await asyncio.to_thread(pairs.end, p, pair_id)
    except ShareError as e:
        return _err(e)
    if not out.get("already"):
        await authz_events.emit(authz_events.revoked_event(pair_id, "pair"))
    return {"ok": True, "pair": out, "cleanup": out.pop("cleanup", None)}
