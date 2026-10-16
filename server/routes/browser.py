"""브라우저 broker API — 본체·규칙·한계는 `browser/broker.py`(머리말). 마스터 스위치 `VT_AUTHZ_ENFORCE` 의 `browser` 가 꺼져 있으면 모든 라우트가 «없는 라우트»와 같은 404.

  GET    /api/browser/adapters                         어댑터 목록·사용 가능 여부와 이유·켜짐. 사람·터미널 또는 browser 읽기 권한이 있는 에이전트
  POST   /api/browser/adapters/{name}/enable           `{enabled?: bool, consent?: bool}` 켜기/끄기. 사람·터미널(fsh)만. 처음 켤 때 consent=true 필요
  POST   /api/browser/tabs                             `{url?, adapter?, profile?}` 격리 탭 열기. `{attach}`(사용자 탭 붙기)는 판정 뒤 501
  GET    /api/browser/tabs                             에이전트는 자기 탭, 사람·터미널은 전부(소유자·상태 포함)
  POST   /api/browser/tabs/{id}/navigate               `{url}`
  POST   /api/browser/tabs/{id}/snapshot|screenshot    요소 번호(e1…)·본문 / PNG(base64)
  POST   /api/browser/tabs/{id}/click|type             `{ref}` / `{ref, text, submit?}` — ref 는 snapshot 이 준 번호만
  POST   /api/browser/tabs/{id}/eval                   `{script}` — `browser.js` 가 있을 때만
  POST   /api/browser/tabs/{id}/cookies|storage|network  `{values?}` — 값은 grant 조건 `values` 가 있을 때만, 기본은 이름·플래그만
  POST   /api/browser/tabs/{id}/handoff                소유자가 탭을 사람에게 넘긴다(소유권 해제, 닫지 않음)
  DELETE /api/browser/tabs/{id}                        탭 닫기(소유자, 사람·터미널은 아무 탭)

호출자: unix socket 의 에이전트·터미널 fsh, 웹의 로그인한 사람, 다른 맥의 relay(`browser` 그룹을 grant 가 이름으로 적은 peer — eval·쿠키·storage·network 는 relay 로 못 한다).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from authz import leases
from authz import principals as P
from authz.errors import ShareError
from browser import broker
from i18n import tr

logger = logging.getLogger(__name__)

router = APIRouter()


def _off():
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def _err(e: ShareError) -> JSONResponse:
    return JSONResponse(e.body(), status_code=e.status)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return b if isinstance(b, dict) else {}


async def _caller(request: Request) -> tuple[P.Principal, bool]:
    """(주체, relay 여부). relay 로 들어온 요청은 서버 코드만 심을 수 있는 `vt_relay` 로 알아본다 — 그 peer 의 grant 만 효력이 있다."""
    rel = request.scope.get("vt_relay")
    if isinstance(rel, dict) and rel.get("peer"):
        return P.peer(str(rel["peer"])), True
    if request.scope.get("fsh_sock") is True:
        p = getattr(request.state, "fsh_principal", None)
        if p is not None and not P.is_hook(p) and not P.is_svc(p) and p.kind in (P.AGENT, P.LOCAL):
            return p, False
        raise broker._err("request_not_allowed", tr("브라우저를 다룰 수 없는 호출자입니다"), 403)
    p = await asyncio.to_thread(leases.identify, request)
    if p is not None and p.authenticated and p.kind in (P.HUMAN, P.AGENT, P.LEGACY):
        return p, False
    raise broker._err("identity_unverified", tr("호출자를 확인하지 못했습니다"), 403)


@router.get("/api/browser/adapters")
async def adapters(request: Request):
    if not broker.enabled():
        return _off()
    try:
        p, _rel = await _caller(request)
        return await asyncio.to_thread(broker.list_adapters, p)
    except ShareError as e:
        return _err(e)


@router.post("/api/browser/adapters/{name}/enable")
async def enable_adapter(name: str, request: Request):
    if not broker.enabled():
        return _off()
    body = await _body(request)
    try:
        p, rel = await _caller(request)
        if rel:
            raise broker._err("relay_forbidden", tr("이 조작은 다른 맥을 거쳐 할 수 없습니다"), 403)
        return await asyncio.to_thread(broker.set_adapter, p, name, body)
    except ShareError as e:
        return _err(e)


@router.post("/api/browser/tabs")
async def open_tab(request: Request):
    if not broker.enabled():
        return _off()
    body = await _body(request)
    try:
        p, rel = await _caller(request)
        return JSONResponse({"ok": True, "tab": await asyncio.to_thread(broker.open_tab, p, body, via_relay=rel)}, status_code=201)
    except ShareError as e:
        return _err(e)


@router.get("/api/browser/tabs")
async def list_tabs(request: Request):
    if not broker.enabled():
        return _off()
    try:
        p, _rel = await _caller(request)
        return await asyncio.to_thread(broker.list_tabs, p)
    except ShareError as e:
        return _err(e)


@router.delete("/api/browser/tabs/{tab_id}")
async def close_tab(tab_id: str, request: Request):
    if not broker.enabled():
        return _off()
    try:
        p, _rel = await _caller(request)
        return {"ok": True, **await asyncio.to_thread(broker.close_tab, p, tab_id)}
    except ShareError as e:
        return _err(e)


async def _act(tab_id: str, request: Request, action: str):
    if not broker.enabled():
        return _off()
    body = await _body(request)
    try:
        p, rel = await _caller(request)
        return await asyncio.to_thread(broker.op, p, tab_id, action, body, via_relay=rel)
    except ShareError as e:
        return _err(e)


@router.post("/api/browser/tabs/{tab_id}/navigate")
async def tab_navigate(tab_id: str, request: Request):
    return await _act(tab_id, request, "navigate")


@router.post("/api/browser/tabs/{tab_id}/snapshot")
async def tab_snapshot(tab_id: str, request: Request):
    return await _act(tab_id, request, "snapshot")


@router.post("/api/browser/tabs/{tab_id}/screenshot")
async def tab_screenshot(tab_id: str, request: Request):
    return await _act(tab_id, request, "screenshot")


@router.post("/api/browser/tabs/{tab_id}/click")
async def tab_click(tab_id: str, request: Request):
    return await _act(tab_id, request, "click")


@router.post("/api/browser/tabs/{tab_id}/type")
async def tab_type(tab_id: str, request: Request):
    return await _act(tab_id, request, "type")


@router.post("/api/browser/tabs/{tab_id}/eval")
async def tab_eval(tab_id: str, request: Request):
    return await _act(tab_id, request, "eval")


@router.post("/api/browser/tabs/{tab_id}/cookies")
async def tab_cookies(tab_id: str, request: Request):
    return await _act(tab_id, request, "cookies")


@router.post("/api/browser/tabs/{tab_id}/storage")
async def tab_storage(tab_id: str, request: Request):
    return await _act(tab_id, request, "storage")


@router.post("/api/browser/tabs/{tab_id}/network")
async def tab_network(tab_id: str, request: Request):
    return await _act(tab_id, request, "network")


@router.post("/api/browser/tabs/{tab_id}/handoff")
async def tab_handoff(tab_id: str, request: Request):
    if not broker.enabled():
        return _off()
    try:
        p, rel = await _caller(request)
        if rel:
            raise broker._err("relay_forbidden", tr("이 조작은 다른 맥을 거쳐 할 수 없습니다"), 403)
        return {"ok": True, "tab": await asyncio.to_thread(broker.handoff, p, tab_id)}
    except ShareError as e:
        return _err(e)
