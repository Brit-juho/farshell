"""Direct-host human archive surface. Never reachable through the broad relay."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, FileResponse
from starlette.background import BackgroundTask

import auth
import fsguard
import mcp_plans
from session_catalog import db, inventory, lifecycle, identity
from session_history import archive, query, restore, replication, backup

router = APIRouter(dependencies=[Depends(auth.require_human_session)])
elevated_router = APIRouter(dependencies=[Depends(auth.require_human_session), Depends(auth.require_elevated)])
peer_router = APIRouter()


def _actor(request):
    return hashlib.sha256(request.cookies.get('vt_session', '').encode()).hexdigest()


async def _body(request, allowed):
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > 32768:
            raise db.Refused('request_too_large', 413)
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        raise db.Refused('invalid_request', 400) from None
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise db.Refused('invalid_request', 400)
    return value


async def _call(fn, *args, **kwargs):
    try:
        result = await asyncio.to_thread(fn, *args, **kwargs)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    except (OSError, fsguard.FsDenied):
        return JSONResponse({'error': 'history_access_denied'}, status_code=403)


@router.get('/api/session-catalog')
async def catalog(limit: int = 50, after: str | None = None, archived: bool = False):
    return await _call(db.snapshot, limit=limit, after=after, archived=archived)


@router.get('/api/session-history')
async def history(search: str = '', provider: str = '', limit: int = 50, after: str | None = None, archived: bool = False):
    return await _call(query.listing, search=search, provider=provider, limit=limit, after=after, archived=archived)


@router.get('/api/session-history/status')
async def history_status():
    return await _call(query.status)


@router.get('/api/session-catalog/sessions/{session_id}')
async def session_detail(session_id: str):
    return await _call(db.session_detail, session_id)


@router.get('/api/session-history/replicas/{origin}/{conversation_id}')
async def replica_detail(origin: str, conversation_id: str, after: int = 0, limit: int = 100):
    return await _call(query.replica_detail, origin, conversation_id, after=after, limit=limit)


@router.get('/api/session-history/conversations/{conversation_id}')
async def conversation(conversation_id: str, after: int = 0, limit: int = 100):
    return await _call(query.detail, conversation_id, after=after, limit=limit)


@router.post('/api/session-catalog/sessions/{session_id}')
async def edit_session(session_id: str, request: Request):
    try:
        body = await _body(request, {'revision', 'title', 'archived'})
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    return await _call(db.edit_session, session_id, body.get('revision'), title=body.get('title'), archived=body.get('archived'))


@router.post('/api/session-catalog/sessions/{session_id}/stop')
async def stop_session(session_id: str, request: Request):
    try:
        body = await _body(request, {'operation_id', 'force'})
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    return await _call(lifecycle.stop, session_id, body.get('operation_id'), force=body.get('force'))


@router.post('/api/session-catalog/reconcile')
async def reconcile_catalog():
    """Re-observe tmux now instead of waiting for the background worker (used by "다시 확인")."""
    return await _call(lambda: {'complete': bool(inventory.reconcile()['complete'])})


@router.post('/api/session-catalog/sessions/{session_id}/resume')
async def resume_session(session_id: str, request: Request):
    try:
        body = await _body(request, {'operation_id'})
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    return await _call(lifecycle.resume_session, session_id, body.get('operation_id'))


@router.post('/api/session-history/conversations/{conversation_id}')
async def edit_conversation(conversation_id: str, request: Request):
    try:
        body = await _body(request, {'archived', 'deleted','delete_scope'})
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    return await _call(query.mutate, conversation_id, archived=body.get('archived'), deleted=body.get('deleted', False),delete_scope=body.get('delete_scope','local'))


@router.post('/api/session-history/conversations/{conversation_id}/resume')
async def resume_conversation(conversation_id: str, request: Request):
    try:
        body = await _body(request, {'operation_id', 'session_id'})
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    return await _call(lifecycle.resume, conversation_id, body.get('operation_id'), sid=body.get('session_id'))


@elevated_router.post('/api/session-history/preview/{action}')
async def preview(action: str, request: Request):
    try:
        body = await _body(request, {'provider', 'root', 'manifest_id'})
        if action == 'source':
            value = await asyncio.to_thread(archive.preview_source, body.get('provider'), body.get('root'))
        elif action == 'identity':
            value = await asyncio.to_thread(identity.preview)
        elif action == 'restore':
            value = await asyncio.to_thread(restore.plan, body.get('manifest_id'))
        else:
            raise db.Refused('unsupported_action', 400)
        token = mcp_plans.issue('history-' + action, {'actor': _actor(request), 'value': value})
        return {'preview': value, **token}
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    except (OSError, fsguard.FsDenied):
        return JSONResponse({'error': 'history_access_denied'}, status_code=403)


def _apply(action, token, actor, selected=None):
    if not isinstance(token, str) or len(token) > 128:
        raise db.Refused('invalid_plan', 400)
    plan = mcp_plans.take(token, 'history-' + action)
    if not plan or plan.get('actor') != actor:
        raise db.Refused('plan_expired')
    if action == 'source':
        value = plan['value']
        current = archive.preview_source(value['provider'], value['root'])
        if current['root_identity'] != value['root_identity']:
            raise db.Refused('source_changed')
        return archive.register_source(value['provider'], value['root'])
    if action == 'identity':
        return identity.apply(plan['value'],selected)
    if action == 'restore':
        return restore.apply(plan['value'])
    raise db.Refused('unsupported_action', 400)


@elevated_router.post('/api/session-history/apply/{action}')
async def apply(action: str, request: Request):
    try:
        body = await _body(request, {'token','selected'})
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    return await _call(_apply, action, body.get('token'), _actor(request),body.get('selected'))


@elevated_router.post('/api/session-history/settings')
async def configure(request: Request):
    try:
        body = await _body(request, {'quota_bytes','replica_quota_bytes', 'source_id', 'enabled', 'revision'})
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    return await _call(query.configure, body)


def _export(mid, origin=None):
    manifest = query.manifest(mid, origin)
    path = db.private_dir(db.directory() / 'exports') / db.uid()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            archive.restore_stream(manifest, stream)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


@elevated_router.post('/api/session-history/manifests/{manifest_id}/export')
async def export_raw(manifest_id: str):
    try:
        path = await asyncio.to_thread(_export, manifest_id)
        return FileResponse(path, filename='conversation-' + manifest_id + '.bin', media_type='application/octet-stream',
                            headers={'Cache-Control': 'no-store'}, background=BackgroundTask(path.unlink, missing_ok=True))
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)


@elevated_router.post('/api/session-history/download')
async def prepare_download(request: Request):
    try:
        body = await _body(request, {'manifest_id', 'origin', 'backup'})
        if body.get('backup') is True:
            if body.get('manifest_id') or body.get('origin'):
                raise db.Refused('invalid_request',400)
            filename = 'conversation-backup.tar'
        else:
            await asyncio.to_thread(query.manifest, body.get('manifest_id'), body.get('origin'))
            filename = 'conversation-' + body['manifest_id'] + '.bin'
        plan = mcp_plans.issue('history-download', {'actor':_actor(request), 'body':body})
        return {'url':'/api/session-history/download/' + plan['token'], 'filename':filename, 'expires_at':plan['expires_at']}
    except db.Refused as error:
        return JSONResponse({'error':error.code},status_code=error.status)


@elevated_router.get('/api/session-history/download/{ticket}')
async def download(ticket: str, request: Request):
    try:
        if len(ticket) > 64:
            raise db.Refused('invalid_plan',400)
        plan = mcp_plans.take(ticket,'history-download')
        if not plan or plan['actor'] != _actor(request):
            raise db.Refused('plan_expired')
        body = plan['body']
        if body.get('backup') is True:
            path = await asyncio.to_thread(backup.export_package)
            filename = 'conversation-backup.tar'
        else:
            path = await asyncio.to_thread(_export,body['manifest_id'],body.get('origin'))
            filename = 'conversation-' + body['manifest_id'] + '.bin'
        return FileResponse(path,filename=filename,media_type='application/octet-stream',headers={'Cache-Control':'no-store'},
                            background=BackgroundTask(path.unlink,missing_ok=True))
    except db.Refused as error:
        return JSONResponse({'error':error.code},status_code=error.status)


@elevated_router.post('/api/session-history/replication/identity')
async def replication_identity():
    return await _call(replication.public_identity)


@elevated_router.post('/api/session-history/replication/settings')
async def replication_settings(request: Request):
    try:
        body = await _body(request, {'peer', 'direction', 'origin', 'public_key', 'enabled', 'revision','sources','rotate_key'})
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    if set(body)=={'rotate_key'} and body['rotate_key'] is True:
        return await _call(replication.rotate_key)
    return await _call(replication.configure, body)


@elevated_router.post('/api/session-history/backup')
async def export_backup():
    try:
        path = await asyncio.to_thread(backup.export_package)
        return FileResponse(path, filename='conversation-backup.tar', media_type='application/x-tar',
                            headers={'Cache-Control': 'no-store'}, background=BackgroundTask(path.unlink, missing_ok=True))
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)


@peer_router.post('/api/peer/session-history/receive')
async def history_receive(request: Request):
    from routes.peer_protocol import _guarded, PeerDenied
    import host_store
    try:
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > replication.MAX_WIRE:
                raise db.Refused('request_too_large', 413)
        if not request.headers.get('x-peer-body') or request.headers.get('x-peer-obo'):
            raise db.Refused('history_peer_only', 403)
        grant = _guarded(request, request.method, request.url.path, request.headers, hashlib.sha256(raw).hexdigest())
        payload = json.loads(raw)
        result = await asyncio.to_thread(replication.receive, grant['id'], payload)
        host_store.audit(grant['id'], 'history.replicate', True, 'committed')
        return result
    except PeerDenied as error:
        return JSONResponse({'error': 'peer_denied'}, status_code=error.status)
    except db.Refused as error:
        return JSONResponse({'error': error.code}, status_code=error.status)
    except (ValueError, UnicodeError, RecursionError):
        return JSONResponse({'error': 'invalid_request'}, status_code=400)
