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


# 자동 별칭을 이미 맞춘 실행 → 그때의 CLI 시작 표식. 같은 시작에 두 번 맞추지 않는다(사람이 그사이 이름을 바꿨을 수도).
_NAMED: dict = {}
AUTO_ARCHIVE_FIRST_DELAY = 300


class Collector:
    def __init__(self):
        self.sources = {}
        self.last_inventory = 0
        self.last_discovery = 0
        # 첫 자동 보관은 서버가 뜨고 5분 뒤 — 예전에는 OS 부팅 시각(monotonic) 기준 1시간이라 부팅 직후 띄운
        # 서버는 한 시간 동안 보관을 하지 않았다.
        self.last_auto_archive = time.monotonic() - 3600 + AUTO_ARCHIVE_FIRST_DELAY
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
        self.track_sessions()
        # The heartbeat is committed when it changes or every HEARTBEAT seconds, not on every 2s tick.
        beat = {'state': 'degraded' if failures else 'running', 'updated': updated, 'errors': failures, 'truncated': limited}
        if beat != self.last_beat or now - self.last_beat_at >= HEARTBEAT:
            with database(write=True) as db:
                set_setting(db, 'worker', {**beat, 'checked_at': time.time()})
            self.last_beat, self.last_beat_at = beat, now
        from . import backup
        backup.maybe_snapshot()

    @staticmethod
    def track_sessions():
        """실행 중인 에이전트로 세션의 마지막 활동 시각을 앞당기고(분 단위), CLI가 켜지면 자동 이름 세션의 보이는
        이름(별칭)을 CLI 폴더의 저장소 이름으로 맞춘다. tmux 이름은 바꾸지 않는다 — 이름은 열쇠가 아니라서 바꿔도
        대기 지시·화면·공유가 깨지지 않는다. 사람이 붙인 별칭(`human`)과 설정을 끈 경우는 건드리지 않는다."""
        import agent_status
        import session_names
        from session_catalog.db import touch_activity, auto_name_enabled, changed
        entries = [e for e in agent_status.visible_state().values()
                   if e.get('agent') in ('claude', 'codex') and e.get('tmux_session')]
        if not entries:
            return
        work = []
        with database() as db:
            enabled = auto_name_enabled(db)
            for e in entries:
                uid_, name = e.get('_tmux_uid'), e.get('tmux_session')
                if uid_:
                    runtimes = db.execute("SELECT * FROM runtimes WHERE physical_key LIKE ? AND ended IS NULL", (uid_ + ':%',)).fetchall()
                    runtimes = [r for r in runtimes if r['name'] == name] if len(runtimes) > 1 else runtimes
                else:
                    runtimes = db.execute('SELECT * FROM runtimes WHERE name=? AND ended IS NULL', (name,)).fetchall()
                if len(runtimes) != 1:
                    continue
                runtime = runtimes[0]
                session = db.execute('SELECT id,title,name_source,last_activity FROM sessions WHERE id=?', (runtime['session_id'],)).fetchone()
                if not session:
                    continue
                activity = e.get('updated_at')
                touch = activity if activity and (session['last_activity'] or 0) < activity - 60 else None
                marker = e.get('_session_start_at') or 'seen'
                rename = (enabled and session['name_source'] == 'auto' and e.get('cwd')
                          and _NAMED.get(runtime['id']) != marker)
                if touch or rename:
                    work.append((session['id'], session['title'], runtime['id'], marker, touch, e.get('cwd') if rename else None))
        if not work:
            return
        if len(_NAMED) > 2000:
            _NAMED.clear()
        named = {}
        for sid, title, rid, marker, touch, cwd in work:
            if cwd:
                base = session_names.base_for(cwd)
                # 홈·이름 없는 폴더는 의미 있는 이름이 아니다 — 지금 이름을 둔다.
                named[rid] = base if base not in ('home', 'session') else None
        with database(write=True) as db:
            for sid, title, rid, marker, touch, cwd in work:
                touch_activity(db, sid, touch)
                if cwd:
                    base = named.get(rid)
                    if base and base != title:
                        db.execute("UPDATE sessions SET title=?,updated=?,revision=revision+1 WHERE id=? AND name_source='auto'",
                                   (base, time.time(), sid))
                        changed(db, 'session', sid)
                    _NAMED[rid] = marker

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
                # 런타임은 이름이 아니라 tmux 세션 id로 찾는다(엔트리가 새겨 둔 `$N`) — 이름은 바뀔 수 있고, 같은 이름의
                # 끝나지 않은 런타임이 둘(재부팅 전 세션이 아직 「상태 모름」)이면 영영 연결되지 않았다. id를 아직 못 새긴
                # 엔트리만 이름으로 찾는다.
                tmux_uid=e.get('_tmux_uid')   # `uid`라 부르면 아래에서 쓰는 uid() 함수를 가린다
                if tmux_uid:
                    runtimes=db.execute("SELECT * FROM runtimes WHERE physical_key LIKE ? AND ended IS NULL",(tmux_uid+':%',)).fetchall()
                    if len(runtimes)>1:
                        runtimes=[r for r in runtimes if r['name']==name]
                else:
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
