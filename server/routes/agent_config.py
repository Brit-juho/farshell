"""Human management surface and target-approved cross-host requests.

No generic relay exemptions: a peer can inspect or submit a request, never apply
a settings write. Only an elevated human logged into the target can approve it.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

import agent_config as manager
import agent_config_maintenance as maintenance
import agent_config_restart as restart
import auth
import host_store
import peer_client
from authz import host_gate
from i18n import tr
from routes.peer_protocol import capability

router = APIRouter(dependencies=[Depends(auth.require_human_session)])
elevated_router = APIRouter(dependencies=[Depends(auth.require_human_session), Depends(auth.require_elevated)])
peer_router = APIRouter()
_requests = {}
_plans = {}
_lock = threading.RLock()
_READ = {"inventory", "environment", "sync-status", "restart-status", "restart-jobs", "requests"}
_REVIEW = {"preview", "restart-preview", "edit-preview"}
_REMOTE_REVIEW = {"preview", "restart-preview"}
_WRITE = {"apply", "metadata", "metadata-category", "preset-save", "preset-delete", "environment-set", "restart-start", "restart-cancel", "edit-apply", "sync-run", "update-check", "editor"}


async def _body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > 512 * 1024:
        raise ValueError(tr("요청이 너무 큽니다"))
    data = json.loads(raw)
    return _validate(data)


def _validate(data):
    if not isinstance(data, dict):
        raise ValueError(tr("잘못된 요청입니다"))
    for key in ("session", "id", "token", "preset", "revision", "inventory_revision", "name", "scope", "job"):
        if key in data and data[key] is not None and (not isinstance(data[key], str) or len(data[key]) > 256):
            raise ValueError(tr("잘못된 요청입니다"))
    if not isinstance(data.get("view", "all"), str) or data.get("view", "all") not in ("all", "session"):
        raise ValueError(tr("잘못된 관리 범위입니다"))
    for key, expected, limit in (("changes", list, 64), ("ids", list, 64), ("values", dict, 8), ("content", str, 256 * 1024), ("payload", dict, 32)):
        if key == "changes" and data.get(key) is None and isinstance(data.get("preset"), str):
            continue
        if key in data and (not isinstance(data[key], expected) or len(data[key]) > limit):
            raise ValueError(tr("잘못된 요청입니다"))
    if "value" in data and data["value"] is not None and (not isinstance(data["value"], str) or len(data["value"]) > 8192):
        raise ValueError(tr("잘못된 요청입니다"))
    if "plugins" in data and type(data["plugins"]) is not bool:
        raise ValueError(tr("잘못된 요청입니다"))
    if "offset" in data and (type(data["offset"]) is not int or not 0 <= data["offset"] <= manager.MAX_CONTEXTS):
        raise ValueError(tr("잘못된 요청입니다"))
    return data


def _operate(action, body):
    body = _validate(body)
    session, view = body.get("session"), body.get("view", "all")
    if action == "inventory":
        return manager.inventory(session, view, plugins=body.get("plugins") is True, offset=body.get("offset", 0), inventory_revision=body.get("inventory_revision"))
    if action == "metadata":
        return manager.metadata(body.get("id"), body.get("values"), session, view)
    if action == "metadata-category":
        return manager.metadata_category(body.get("ids"), body.get("category"), session, view)
    if action == "preset-save":
        return manager.save_preset(body.get("name"), body.get("changes"), session, view)
    if action == "preset-delete":
        return manager.delete_preset(body.get("id"))
    if action == "preview":
        return manager.preview(body.get("changes"), session, view, body.get("preset"))
    if action == "apply":
        return manager.apply(body.get("token"))
    if action == "environment":
        return manager.environment(session)
    if action == "environment-set":
        return manager.set_environment(session, body.get("scope"), body.get("name"), body.get("value"), body.get("revision"))
    if action == "restart-preview":
        return restart.preview(session)
    if action == "restart-start":
        return restart.start(body.get("token"))
    if action == "restart-status":
        return restart.status(body.get("job"))
    if action == "restart-jobs":
        return restart.list_jobs()
    if action == "restart-cancel":
        return restart.cancel(body.get("job"))
    if action == "editor":
        return maintenance.editor(body.get("id"), session, view)
    if action == "edit-preview":
        return maintenance.preview_edit(body.get("id"), session, view, body.get("revision"), body.get("content"))
    if action == "edit-apply":
        return maintenance.apply_edit(body.get("token"))
    if action == "update-check":
        return maintenance.check_update(body.get("id"), session, view)
    if action == "sync-status":
        return maintenance.peer_sync_status()
    if action == "sync-run":
        return maintenance.run_peer_sync(body.get("id"))
    if action == "requests":
        return {"requests": pending_requests()}
    raise ValueError(tr("지원하지 않는 관리 작업입니다"))


def _error(exc):
    reason = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else tr("요청을 처리하지 못했습니다. 원본을 확인하세요")
    return JSONResponse({"error": "agent_config_refused", "reason": reason}, status_code=409)


@router.post("/api/agent-config/read/{action}", response_model=dict)
async def read_config(action: str, request: Request):
    try:
        if action not in _READ | _REVIEW:
            raise ValueError(tr("지원하지 않는 관리 작업입니다"))
        return await asyncio.to_thread(_operate, action, await _body(request))
    except (ValueError, OSError, RuntimeError) as exc:
        return _error(exc)


@elevated_router.post("/api/agent-config/write/{action}", response_model=dict)
async def write_config(action: str, request: Request):
    try:
        if action not in _WRITE:
            raise ValueError(tr("지원하지 않는 관리 작업입니다"))
        return await asyncio.to_thread(_operate, action, await _body(request))
    except (ValueError, OSError, RuntimeError) as exc:
        return _error(exc)


def _prune():
    now = time.time()
    for store in (_requests, _plans):
        for key in list(store):
            if store[key]["expires_at"] <= now:
                del store[key]


def pending_requests():
    with _lock:
        _prune()
        return [{k: row[k] for k in ("id", "peer", "action", "created_at", "expires_at", "summary")}
                for row in _requests.values() if row["state"] == "pending"]


def _remote(action, body, peer, grant_revision=None):
    if not isinstance(action, str):
        raise ValueError(tr("잘못된 요청입니다"))
    body = _validate(body)
    if action in {"inventory", "sync-status", "restart-status"} | _REMOTE_REVIEW:
        result = _operate(action, body)
        # Configuration values never cross hosts. A peer may review only masked
        # plans; private skill content and environment values stay on the owner.
        if action in _REVIEW and "token" in result:
            with _lock:
                _prune()
                if len(_plans) >= 256:
                    raise ValueError(tr("검토 중인 요청이 너무 많습니다"))
                _plans[result["token"]] = {"peer": peer, "result": result, "action": action,
                                           "grant_revision": grant_revision,
                                           "expires_at": result["expires_at"]}
        return result
    if action == "request":
        target_action = body.get("action")
        payload = body.get("payload")
        # Remote writes are deliberately limited to exact reviewed plans.
        if not isinstance(target_action, str) or target_action not in {"apply", "restart-start"} or not isinstance(payload, dict):
            raise ValueError(tr("원격 변경은 대상 호스트에서 검토한 계획이 필요합니다"))
        token = payload.get("token")
        if not isinstance(token, str):
            raise ValueError(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
        with _lock:
            _prune()
            plan = _plans.get(token)
            expected = {"apply": "preview", "restart-start": "restart-preview"}[target_action]
            if not plan or plan["peer"] != peer or plan["action"] != expected:
                raise ValueError(tr("변경 계획이 만료됐습니다. 다시 검토하세요"))
            if len(_requests) >= 64:
                raise ValueError(tr("검토 중인 요청이 너무 많습니다"))
            existing = next((r for r in _requests.values() if r["token"] == token and r["peer"] == peer), None)
            if existing:
                return {"status": existing["state"], "request_id": existing["id"]}
            key = secrets.token_hex(16)
            _requests[key] = {"id": key, "peer": peer, "action": target_action,
                "grant_revision": plan.get("grant_revision"),
                "token": token, "state": "pending", "created_at": time.time(),
                "expires_at": plan["expires_at"], "summary": plan["result"]}
            return {"status": "pending", "request_id": key,
                    "note": tr("대상 호스트의 FarShell에서 승인해야 적용됩니다")}
    raise ValueError(tr("이 원격 작업은 대상 호스트에서 직접 수행하세요"))


@peer_router.post("/api/peer/agent-config", response_model=dict)
@capability("agent-config", level=host_store.LEVEL_CONTROL, body=True, strip=())
async def peer_config(request: Request, grant: dict, raw_body: bytes):
    # IDs and paths are intentionally owner-host values, never used locally.
    try:
        if len(raw_body) > 512 * 1024:
            raise ValueError(tr("요청이 너무 큽니다"))
        body = json.loads(raw_body)
        if not isinstance(body, dict) or not isinstance(body.get("payload"), dict):
            raise ValueError(tr("잘못된 요청입니다"))
        return await asyncio.to_thread(_remote, body.get("action"), body["payload"], grant["id"], manager._id(grant["secret"]))
    except (ValueError, OSError, RuntimeError) as exc:
        return _error(exc)


@elevated_router.post("/api/agent-config/requests/{request_id}/{decision}", response_model=dict)
async def decide_request(request_id: str, decision: str):
    try:
        with _lock:
            _prune()
            pending = _requests.get(request_id)
            if not pending or pending["state"] != "pending" or decision not in ("approve", "deny"):
                raise ValueError(tr("승인 요청이 만료되었거나 처리됐습니다"))
            grant = host_store.find_grant(pending["peer"])
            from authz import peer_svc
            if (grant is None or (peer_svc.enforced() and not peer_svc.decide(grant, "agent-config").allow)
                    or (not peer_svc.enforced() and grant.get("level") != host_store.LEVEL_CONTROL)):
                raise ValueError(tr("호스트 연결 권한이 회수됐습니다"))
            if pending.get("grant_revision") and pending["grant_revision"] != manager._id(grant["secret"]):
                raise ValueError(tr("호스트 연결 권한이 회수됐습니다"))
            pending["state"] = "denied" if decision == "deny" else "applying"
            action, token = pending["action"], pending["token"]
        if decision == "deny":
            host_store.audit(pending["peer"], "agent-config/deny", True, action)
            return {"status": "denied"}
        result = await asyncio.to_thread(_operate, action, {"token": token})
        with _lock:
            pending["state"] = result.get("status", "requested")
        host_store.audit(pending["peer"], "agent-config/approve", True, action)
        return result
    except (ValueError, OSError, RuntimeError) as exc:
        return _error(exc)


@router.post("/api/hosts/{host_id}/agent-config/{action}", response_model=dict)
async def remote_config(host_id: str, action: str, request: Request):
    try:
        if host_gate.enforced():
            permission = await asyncio.to_thread(host_gate.decide, request, host_id, "relay", {"group": "mcp"})
            if not permission.allow:
                return JSONResponse(host_gate.denied_body(), status_code=403)
        peer = host_store.find_peer(host_id)
        if peer is None:
            raise ValueError(tr("등록되지 않은 호스트입니다"))
        payload = await _body(request)
        if action not in {"inventory", "sync-status", "restart-status", "request"} | _REMOTE_REVIEW:
            raise ValueError(tr("이 원격 작업은 대상 호스트에서 직접 수행하세요"))
        if action == "request":
            auth.require_elevated(request)
        return await peer_client.call(peer, "POST", "/api/peer/agent-config", {"action": action, "payload": payload})
    except peer_client.PeerError:
        return JSONResponse({"error": "peer_unavailable", "reason": tr("상대 호스트가 연결되지 않았거나 이 기능을 지원하지 않습니다")}, status_code=502)
    except (ValueError, OSError, RuntimeError) as exc:
        return _error(exc)
