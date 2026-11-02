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
HEARTBEAT = 15  # seconds between unchanged `worker` status commits
_replication_beat = {'last': None, 'at': 0}


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
        self.last_auto_archive = 0
        self.last_beat = None
        self.last_beat_at = 0

    def step(self):
        now = time.monotonic()
        if now - self.last_inventory >= 5:
            inventory.reconcile()
            self.last_inventory = now
        if now - self.last_auto_archive >= 3600:
            from session_catalog import db as catalog_db
            catalog_db.auto_archive()
            archive.prune_manifests()
            self.last_auto_archive = now
        with database() as db:
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
        # The heartbeat is committed when it changes or every HEARTBEAT seconds, not on every 2s tick.
        beat = {'state': 'degraded' if failures else 'running', 'updated': updated, 'errors': failures, 'truncated': limited}
        if beat != self.last_beat or now - self.last_beat_at >= HEARTBEAT:
            with database(write=True) as db:
                set_setting(db, 'worker', {**beat, 'checked_at': time.time()})
            self.last_beat, self.last_beat_at = beat, now
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
                    known=db.execute('SELECT pane_id FROM conversation_executions WHERE conversation_id=? AND runtime_id=? AND verified=1 ORDER BY pane_id LIMIT 1',
                                     (conversations[0]['id'],runtimes[0]['id'])).fetchone()
                    candidates.append((dict(runtimes[0]),dict(conversations[0]),agent_status.public_entry(e),known['pane_id'] if known else None))
        verified=[]
        for runtime,conversation,e,pane in candidates:
            if pane is not None:
                # Already verified for this runtime (same tmux session incarnation). identify() spawns tmux,
                # ps and git; repeating it every 2s for every live agent was most of the server's process
                # spawning. A different CLI or profile in that session is a different conversation and is
                # verified on its own first.
                verified.append((runtime,conversation,e,{'pane':pane}))
                continue
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
                # An upsert of an already-verified row still rewrites pages (and checkpoints the WAL) every tick.
                if not db.execute('SELECT 1 FROM conversation_executions WHERE conversation_id=? AND runtime_id=? AND pane_id=? AND verified=1',(conversation['id'],runtime['id'],ctx['pane'])).fetchone():
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
            delegations=visible_ledger(delegation.list_all(),cached=True)
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
                    if prior['detail']!=detail:
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
        deadline = time.monotonic() + 1
        def should_stop():
            return bool(stop and stop.is_set())
        try:
            # Drain small events without sleeping two seconds after each one.
            # In-flight requests keep their timeout and shared byte-rate budget.
            for _ in range(32):
                # A slow first request must still be allowed to advance this
                # bounded event. The scheduling deadline applies between events;
                # only explicit cancellation interrupts an event already begun.
                if should_stop() or time.monotonic() >= deadline:
                    break
                if not replication.push_once(peer, max_chunks=2, should_stop=should_stop):
                    break
        except Exception:
            failed.append(peer)
    beat = {'state': 'degraded' if failed else 'running', 'failed': failed}
    now = time.monotonic()
    if beat != _replication_beat['last'] or now - _replication_beat['at'] >= HEARTBEAT:
        with database(write=True) as db:
            set_setting(db,'replication_worker',{**beat,'checked_at':time.time()})
        _replication_beat.update(last=beat, at=now)
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
    from session_catalog.db import hold_wal
    await _offload(enable)
    try:
        await _offload(hold_wal)
    except Exception:
        # Without the anchor the catalog still works; it only checkpoints on every close again.
        logger.warning('session catalog WAL anchor unavailable')


def shutdown():
    from session_catalog.db import release_wal
    release_wal()
