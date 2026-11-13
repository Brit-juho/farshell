"""저장소 = CLI를 켠 곳 — 대화(conversations)를 저장소(projects)에 놓는다(docs/ref/session-ui.md 「Projects」).

대화의 실행 폴더(`conversations.cwd`)를 `inventory.inspect_workspace`로 풀어 git이면 본 체크아웃 저장소(링크드 워크트리도
본 저장소), 아니면 그 폴더 저장소에 놓고 `conversations.workspace_id`를 채운다. 저장소가 처음 대화를 받으면 `registered`가
찍히고(목록의 저장소가 된다), 대화가 올 때마다 `last_activity`가 오른다.

새 대화와 옛 기록(백필)이 같은 길을 탄다 — 아직 놓이지 않은 대화를 최근 것부터 조금씩(한 번에 BATCH개, BUDGET초).
놓을 수 없는 대화는 이유를 `detail.placement`에 적어 다시 보지 않는다:

- `skip:launch` 터미널 CLI가 아니다(`cli_started` — Claude `entrypoint=cli`, Codex `originator=codex-tui`만).
  데스크톱 앱·`claude -p`·`codex exec`는 「대화 보관함」에만 남는다(P0 실측, 설계서 §3.2).
- `skip:cwd` 실행 폴더가 없거나 임시 폴더(/tmp·/var/folders)다.
- `skip:gone` 폴더가 지워졌고, 기록에 남은 옛 작업 트리로도 원래 git 저장소를 못 찾았다.
- `skip:git` 폴더는 있는데 git 확인이 두 번 실패했다(느린 볼륨·권한).

지운 워크트리에서 돈 대화는 기록의 옛 작업 트리(`workspaces.path`)가 그 폴더이거나 조상이면 그 git 저장소로 간다.
다른 맥에서 복제돼 온 대화(`replicas`)는 여기 오지 않는다 — 경로가 그 맥 것이라 각 맥이 자기 기록으로 만든다.

hidden: 0 보임 · 1 사람이 숨김 · 2 오래돼 자동 숨김(처음 등록될 때 마지막 활동이 AUTO_HIDE_DAYS일 넘음 — 새 활동이면 풀린다).
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

from .db import changed, database
from . import inventory

BATCH = 50
BUDGET = 5.0
AUTO_HIDE_DAYS = 90
GIT_TRIES = 2
_TEMP = ('/tmp', '/private/tmp', '/var/folders', '/private/var/folders')


def cli_started(provider: str, launch: str) -> bool:
    """터미널에서 켠 CLI 대화인가 — 저장소는 이것만 만든다. 데스크톱 앱을 넣고 싶어지면 여기만 바꾼다."""
    return (provider == 'claude' and launch == 'cli') or (provider == 'codex' and launch == 'codex-tui')


def _temp(path: str) -> bool:
    p = path.rstrip('/')
    return any(p == t or p.startswith(t + '/') for t in _TEMP)


_TS = re.compile(rb'"timestamp"\s*:\s*"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d))"')


def _epoch(raw: bytes):
    try:
        return datetime.fromisoformat(raw.decode().replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _decoded(info, data: bytes) -> bytes:
    if not info.get('compressed'):
        return data
    import io
    import zstandard
    with zstandard.ZstdDecompressor(max_window_size=64 * 1024**2).stream_reader(io.BytesIO(data)) as z:
        return z.read(1024 * 1024)


def history_facts(db, cid: str, provider: str) -> dict:
    """옛 대화(백필)의 실행 방식·실제 시작·마지막 활동을 원본 기록에서 읽는다. 기록 저장소의 `created`·`updated`는 수집 시각이라
    (처음 수집한 날·다시 읽은 날) 「등록순」·자동 숨김에 쓸 수 없다. 원본 줄의 `timestamp`가 근거다:
    시작 = 보관된 첫 조각의 첫 timestamp, 마지막 = 원본 파일이 남아 있으면 그 수정 시각, 없으면 보관된 마지막 조각의 마지막 timestamp.
    못 읽은 값은 빠진다(실행 방식을 못 읽으면 ''이고 「CLI 아님」으로 다룬다)."""
    from session_history import adapters, archive
    out = {'launch': ''}
    rows = db.execute('SELECT detail FROM manifests WHERE conversation_id=? ORDER BY created', (cid,)).fetchall()
    if not rows:
        return out
    try:
        first, last = json.loads(rows[0]['detail']), json.loads(rows[-1]['detail'])
        if first.get('chunks'):
            head = _decoded(first, archive.read_chunk(first['chunks'][0]['digest']))
            ident = adapters.identity(head, provider, str(first.get('relative_path') or ''))
            out['launch'] = (ident or {}).get('launch') or ''
            m = _TS.search(head)
            if m and _epoch(m.group(1)):
                out['started'] = _epoch(m.group(1))
        src = db.execute('SELECT s.root,f.relative_path FROM source_files f JOIN sources s ON s.id=f.source_id'
                         ' WHERE f.conversation_id=? LIMIT 1', (cid,)).fetchone()
        path = os.path.join(os.path.expanduser(src['root']), src['relative_path']) if src else ''
        if path and os.path.isfile(path):
            out['last'] = os.stat(path).st_mtime
        elif last.get('chunks') and not last.get('compressed'):
            tail = archive.read_chunk(last['chunks'][-1]['digest'])
            stamps = _TS.findall(tail[-262144:])
            if stamps and _epoch(stamps[-1]):
                out['last'] = _epoch(stamps[-1])
    except Exception:   # 손상·형식 차이 — 읽은 데까지만
        pass
    return out


def _gone_workspace(known, cwd: str):
    """지워진 폴더 → 기록의 옛 작업 트리. git 저장소는 그 폴더이거나 조상이면, 폴더 저장소는 같은 경로일 때만."""
    best = None
    for path, wid, kind in known:
        if cwd == path or (kind == 'git' and cwd.startswith(path.rstrip('/') + '/')):
            if best is None or len(path) > len(best[0]):
                best = (path, wid)
    return best[1] if best else None


def place_conversations(now=None, limit=BATCH, budget=BUDGET) -> int:
    """아직 놓이지 않은 대화를 최근 것부터 놓는다. 이번에 결정한(놓았거나 건너뛴) 대화 수를 돌려준다."""
    now = time.time() if now is None else now
    deadline = time.monotonic() + budget
    with database() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT id,provider,cwd,created,updated,detail FROM conversations"
            " WHERE workspace_id IS NULL AND deleted IS NULL AND parent_native_id IS NULL"
            " AND json_extract(detail,'$.placement') IS NULL ORDER BY updated DESC LIMIT ?", (limit,))]
        known = [(r['path'], r['id'], r['kind']) for r in db.execute(
            "SELECT w.path,w.id,p.kind FROM workspaces w JOIN projects p ON p.id=w.project_id")]
    if not rows:
        return 0
    cache: dict[str, object] = {}
    decided = []   # (row, detail, workspace placement | workspace id | None)
    for r in rows:
        if time.monotonic() > deadline:
            break
        try:
            detail = json.loads(r['detail'] or '{}')
        except ValueError:
            detail = {}
        launch = detail.get('launch')
        if not isinstance(launch, str):
            # 실행 방식을 모르는 대화 = 이 판 이전에 수집된 옛 대화(백필). 원본 기록에서 방식·실제 시각을 읽는다.
            with database() as db:
                facts = history_facts(db, r['id'], r['provider'])
            launch = detail['launch'] = facts['launch']
            for k in ('started', 'last'):
                if facts.get(k):
                    detail[k] = facts[k]
        if not cli_started(r['provider'], launch):
            decided.append((r, {**detail, 'placement': 'skip:launch'}, None))
            continue
        cwd = r['cwd'] or ''
        if not cwd.startswith('/') or _temp(cwd):
            decided.append((r, {**detail, 'placement': 'skip:cwd'}, None))
            continue
        if cwd not in cache:
            if os.path.isdir(cwd):
                cache[cwd] = inventory.inspect_workspace(cwd)   # git 2초 제한, 실패는 None
            else:
                wid = _gone_workspace(known, cwd)
                cache[cwd] = ('wid', wid) if wid else 'gone'
        found = cache[cwd]
        if found == 'gone':
            decided.append((r, {**detail, 'placement': 'skip:gone'}, None))
        elif found is None:
            tries = int(detail.get('placement_tries') or 0) + 1
            decided.append((r, {**detail, 'placement_tries': tries, **({'placement': 'skip:git'} if tries >= GIT_TRIES else {})}, None))
        else:
            decided.append((r, {**detail, 'placement': 'placed'}, found))
    if not decided:
        return 0
    old = now - AUTO_HIDE_DAYS * 86400
    with database(write=True) as db:
        for r, detail, found in decided:
            wid = None
            if isinstance(found, tuple):
                wid = found[1]
            elif isinstance(found, dict):
                wid = inventory.workspace(db, found)
            db.execute('UPDATE conversations SET detail=?,workspace_id=COALESCE(?,workspace_id) WHERE id=? AND workspace_id IS NULL',
                       (json.dumps(detail, separators=(',', ':'), sort_keys=True), wid, r['id']))
            if not wid:
                continue
            p = db.execute('SELECT p.id,p.registered,p.removed,p.last_activity,p.hidden FROM projects p'
                           ' JOIN workspaces w ON w.project_id=p.id WHERE w.id=?', (wid,)).fetchone()
            if not p:
                continue
            # 실제 시작·마지막 활동(백필은 원본 기록의 값, 새 대화는 수집 시각 ≈ 실제).
            created = detail.get('started') or r['created'] or now
            act = max(p['last_activity'] or 0, detail.get('last') or r['updated'] or 0)
            first = p['registered'] is None
            registered = created if first else min(p['registered'], created)
            removed = None if p['removed'] is not None and created > p['removed'] else p['removed']
            hidden = p['hidden']
            if hidden == 2 and act >= old:
                hidden = 0          # 자동 숨김은 새 활동이면 풀린다(사람이 숨긴 1은 그대로)
            elif first and hidden == 0 and act < old:
                hidden = 2          # 처음 등록되는 저장소의 마지막 활동이 오래됐다(백필) — 숨겨 둔다
            db.execute('UPDATE projects SET registered=?,removed=?,last_activity=?,hidden=?,revision=revision+1 WHERE id=?',
                       (registered, removed, act, hidden, p['id']))
            changed(db, 'project', p['id'])
    return len(decided)
