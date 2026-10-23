"""Server-owned collection, independent of browser visibility and terminal polling."""
from __future__ import annotations

import asyncio
import logging
import time

from session_catalog import inventory
from session_catalog.db import database, set_setting, Refused
from . import archive

logger = logging.getLogger(__name__)

# Bound GIL-held parsing/commit work while interactive terminal streams are active.
CAPTURE_BYTES = 256 * 1024


async def _offload(fn,*,on_cancel=None):
    task = asyncio.create_task(asyncio.to_thread(fn))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if on_cancel:
            on_cancel()
        # Cancellation must not leave a writer running after server shutdown or
        # after a test's private state/environment has been released.
        try:
            await task
        except Exception:
            pass
        raise


class Collector:
    def __init__(self):
        self.sources = {}
        self.last_inventory = 0
        self.last_discovery = 0

    def step(self):
        now = time.monotonic()
        if now - self.last_inventory >= 5:
            inventory.reconcile()
            self.last_inventory = now
        with database(write=True) as db:
            sources = [dict(r) for r in db.execute('SELECT * FROM sources WHERE enabled=1')]
        updated, failures, limited = 0, 0, False
        for source in sources:
            state = self.sources.get(source['id'])
            if not state or now - state['scanned'] >= 60 or state['revision'] != source['revision']:
                try:
                    root, files, truncated = archive.source_files(source)
                    state = {'scanned': now, 'files': files, 'index': 0, 'revision': source['revision'], 'truncated': truncated}
                    self.sources[source['id']] = state
                    if not truncated:
                        with database(write=True) as db:
                            seen = set(files)
                            for row in db.execute('SELECT id,relative_path FROM source_files WHERE source_id=?', (source['id'],)):
                                if row['relative_path'] not in seen:
                                    db.execute("UPDATE source_files SET state='source_missing' WHERE id=?", (row['id'],))
                except (OSError, ValueError, Refused):
                    failures += 1
                    continue
            limited |= state['truncated']
            deadline = time.monotonic() + 1
            processed = 0
            while state['files'] and processed < min(len(state['files']), 32):
                relative = state['files'][state['index'] % len(state['files'])]
                state['index'] += 1
                processed += 1
                try:
                    updated += bool(archive.capture(source, relative, max_bytes=CAPTURE_BYTES))
                except (OSError, ValueError, Refused):
                    failures += 1
                if time.monotonic() > deadline:
                    break
        # Verification observes accepted agent state, never raw unauthenticated hook data.
        self.bind_conversations()
        with database(write=True) as db:
            set_setting(db, 'worker', {'state': 'degraded' if failures else 'running', 'checked_at': time.time(),
                                     'updated': updated, 'errors': failures, 'truncated': limited})
        from . import backup
        backup.maybe_snapshot()

    @staticmethod
    def bind_conversations():
        import agent_status
        import agent_config_context
        from session_catalog.db import uid, encode, changed
        entries = list(agent_status.visible_state().values())
        candidates = []
        with database() as db:
            for e in entries:
                native,name,tool=e.get('session_id'),e.get('tmux_session'),e.get('agent')
                if not native or not name or tool not in ('claude','codex'):
                    continue
                runtimes=db.execute('SELECT * FROM runtimes WHERE name=? AND ended IS NULL',(name,)).fetchall()
                conversations=db.execute('SELECT c.*,s.root FROM conversations c JOIN sources s ON s.scope=c.scope WHERE c.native_id=? AND c.provider=? AND c.deleted IS NULL',(native,tool)).fetchall()
                if len(runtimes)==1 and len(conversations)==1:
                    candidates.append((dict(runtimes[0]),dict(conversations[0]),agent_status.public_entry(e)))
        verified=[]
        for runtime,conversation,e in candidates:
            try:
                ctx=agent_config_context.identify(runtime['name'])
                from pathlib import Path
                if ctx['tool'] != conversation['provider'] or Path(ctx['root']).resolve()!=Path(conversation['root']).resolve():
                    continue
                verified.append((runtime,conversation,e,ctx))
            except (ValueError,OSError):
                continue
        with database(write=True) as db:
            for runtime,conversation,e,ctx in verified:
                current=db.execute('SELECT ended,epoch,physical_key FROM runtimes WHERE id=?',(runtime['id'],)).fetchone()
                if not current or current['ended'] is not None or current['epoch']!=runtime['epoch'] or current['physical_key']!=runtime['physical_key']:
                    continue
                db.execute('INSERT INTO conversation_executions VALUES(?,?,?,1) ON CONFLICT(conversation_id,runtime_id,pane_id) DO UPDATE SET verified=1',(conversation['id'],runtime['id'],ctx['pane']))
                children=e.get('subagents',{}).get('items',[])
                for child in children:
                    if not isinstance(child,dict) or not isinstance(child.get('id'),str):
                        continue
                    execution=runtime['id']+':'+ctx['pane']
                    detail=encode(child)
                    old=db.execute('SELECT detail FROM native_children WHERE conversation_id=? AND execution=? AND native_id=?',(conversation['id'],execution,child['id'])).fetchone()
                    if old and old['detail']==detail:
                        continue
                    db.execute('INSERT INTO native_children VALUES(?,?,?,?,?,?) ON CONFLICT(conversation_id,execution,native_id) DO UPDATE SET detail=excluded.detail,observed=excluded.observed',
                               (uid(),conversation['id'],execution,child['id'],detail,time.time()))
                    changed(db,'native_child',conversation['id'])
        # The delegation ledger remains the authority; only durable display edges are projected.
        import delegation
        try:
            from session_catalog.identity import visible_ledger
            delegations=visible_ledger(delegation.list_all())
        except (OSError,ValueError):
            return
        with database(write=True) as db:
            live={r['physical_key']:r['session_id'] for r in db.execute('SELECT * FROM runtimes WHERE ended IS NULL')}
            for item in delegations:
                parent=live.get(item.get('parent',{}).get('key'))
                child=live.get(item.get('child',{}).get('key'))
                source=item.get('id')
                if not parent or not child or parent==child or not isinstance(source,str):
                    continue
                prior=db.execute("SELECT * FROM relations WHERE kind='delegation' AND source_id=?",(source,)).fetchone()
                detail=encode({'state':item.get('state'),'outcome':item.get('outcome')})
                if prior and (prior['parent_id']!=parent or prior['child_id']!=child):
                    continue
                if prior:
                    db.execute('UPDATE relations SET detail=? WHERE id=?',(detail,prior['id']))
                else:
                    db.execute('INSERT INTO relations VALUES(?,?,?,?,?,?)',(uid(),'delegation',parent,child,source,detail))



async def run():
    collector = Collector()
    while True:
        try:
            await _offload(collector.step)
        except Exception:
            # Never print source paths or transcript contents in operational logs.
            logger.warning('session history collector unavailable')
        await asyncio.sleep(2)


def _replicate_once(stop=None):
    from . import replication
    with database(write=True) as db:
        peers = [r['peer'] for r in db.execute("SELECT peer FROM replication_grants WHERE enabled=1 AND direction='send'")]
    failed=[]
    for peer in peers:
        try:
            replication.push_once(peer,max_chunks=2,should_stop=stop.is_set if stop else lambda:False)
        except Exception:
            failed.append(peer)
    with database(write=True) as db:
        set_setting(db,'replication_worker',{'state':'degraded' if failed else 'running','failed':failed,'checked_at':time.time()})
    if failed:
        raise Refused('replication_delayed',503)


async def replicate():
    """Network backoff cannot stall raw capture or terminal reconciliation."""
    import threading,functools,random
    stop=threading.Event()
    delay = 2
    while True:
        try:
            await _offload(functools.partial(_replicate_once,stop),on_cancel=stop.set)
            delay = 2
        except Exception:
            delay = min(300, delay * 2)
        await asyncio.sleep(delay*random.uniform(.8,1.2))


async def discover():
    """Slow Git discovery never blocks the raw transcript capture schedule."""
    while True:
        try:
            await _offload(inventory.discover_projects)
        except Exception:
            logger.warning('session project discovery unavailable')
        await asyncio.sleep(60)


async def index_history():
    from . import search
    while True:
        try:
            await _offload(search.refresh)
        except Exception:
            logger.warning('session search index unavailable')
        await asyncio.sleep(2)


async def initialize():
    from session_catalog.identity import enable
    await _offload(enable)
