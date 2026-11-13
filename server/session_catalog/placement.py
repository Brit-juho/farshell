"""저장소 = CLI를 켠 곳 — 대화(conversations)를 저장소(projects)에 놓는다(docs/ref/session-ui.md 「Projects」).

대화의 실행 폴더(`conversations.cwd`)를 `inventory.inspect_workspace`로 풀어 git이면 본 체크아웃 저장소(링크드 워크트리도
본 저장소), 아니면 그 폴더 저장소에 놓고 `conversations.workspace_id`를 채운다. 저장소가 처음 대화를 받으면 `registered`가
찍히고(목록의 저장소가 된다), `last_activity`가 오른다.

새 대화와 옛 기록(백필)이 같은 길을 탄다 — `conversations.placement IS NULL`(아직)인 대화를 최근 것부터 조금씩. 결과는
`placement` 칸에 적어 다시 보지 않는다:

- `placed` 저장소에 놓았다.
- `skip:launch` 터미널 CLI가 아니다(`cli_started` — Claude `entrypoint=cli`, Codex `originator=codex-tui`만).
  데스크톱 앱·`claude -p`·`codex exec`는 「대화 보관함」에만 남는다(P0 실측, 설계서 §3.2).
- `skip:child` 하위 대화(서브에이전트 기록).
- `skip:cwd` 실행 폴더가 없거나 임시 폴더(/tmp·/var/folders)거나 탐색 허용 폴더·거부 경로(fsguard) 밖이다(지운 폴더는 남은 상위 폴더로 확인).
- `skip:gone` 폴더가 지워졌고, 기록의 옛 작업 트리로도 원래 git 저장소를 못 찾았다.
- `skip:git` 폴더는 있는데 git 확인이 GIT_TRIES번 실패했다(느린 볼륨·권한).

시각은 기록 저장소의 created·updated(수집 시각)를 쓰지 않는다 — 수집(archive.capture)이 detail에 `started`(원본 첫 timestamp)·
`last`(원본 파일 수정 시각)를 적고, 이 판 이전에 수집된 옛 대화는 `history_facts`가 보관된 원본에서 읽는다. 이미 놓인 대화의 새
활동은 수집이 `touch_project`로 저장소에 올린다(같은 트랜잭션).

hidden: 0 보임 · 1 사람이 숨김 · 2 오래돼 자동 숨김(처음 등록될 때 마지막 활동이 AUTO_HIDE_DAYS일 넘음 — 새 활동이면 풀린다).
다른 맥에서 복제돼 온 대화(`replicas`)는 여기 오지 않는다 — 경로가 그 맥 것이라 각 맥이 자기 기록으로 만든다.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
from datetime import datetime

import fsguard

from .db import changed, database
from . import inventory

BATCH = 50
BUDGET = 5.0
AUTO_HIDE_DAYS = 90
GIT_TRIES = 2
_TEMP = ('/tmp', '/private/tmp', '/var/folders', '/private/var/folders')
_TS = re.compile(rb'"timestamp"\s*:\s*"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d))"')


def cli_started(provider: str, launch: str) -> bool:
    """터미널에서 켠 CLI 대화인가 — 저장소는 이것만 만든다. 데스크톱 앱을 넣고 싶어지면 여기만 바꾼다."""
    return (provider == 'claude' and launch == 'cli') or (provider == 'codex' and launch == 'codex-tui')


def _epoch(raw: bytes):
    try:
        return datetime.fromisoformat(raw.decode().replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def first_timestamp(head: bytes):
    m = _TS.search(head or b'')
    return _epoch(m.group(1)) if m else None


def _last_timestamp(data: bytes):
    found = _TS.findall(data[-262144:]) if data else []
    return _epoch(found[-1]) if found else None


def _num(v):
    """시각 값 — 유한한 수만. 망가진 값은 없는 것으로."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float('inf') else None


def _obj(text) -> dict:
    """detail JSON — 객체가 아니면(망가짐·null·배열) 빈 것으로. 한 행 때문에 배치 전체가 멈추지 않게."""
    try:
        v = json.loads(text or '{}')
    except (ValueError, TypeError):
        return {}
    return v if isinstance(v, dict) else {}


def _temp(path: str) -> bool:
    p = path.rstrip('/')
    return any(p == t or p.startswith(t + '/') for t in _TEMP)


def _gone_path(cwd: str):
    """지워진 폴더의 경로를 fsguard로 푼다 — 남아 있는 앞부분의 링크를 펼치고, 전체 경로로 탐색 허용 폴더·거부 경로·숨김 폴더를
    검사한다(`resolve_under_roots`는 없는 경로도 받는다). 경계 밖이면 None — 문자열 앞부분만 보고 옛 작업 트리에 붙이지 않는다."""
    try:
        return str(fsguard.resolve_under_roots(cwd, git_meta=True))
    except Exception:
        return None


def touch_project(db, workspace_id: str, last_activity: float, now: float) -> None:
    """이미 놓인 대화에 새 활동 — 저장소의 마지막 활동을 올리고 자동 숨김(2)을 푼다. 수집의 쓰기 트랜잭션 안에서 부른다."""
    p = db.execute('SELECT p.id,p.registered,p.last_activity,p.hidden FROM projects p JOIN workspaces w ON w.project_id=p.id'
                   ' WHERE w.id=?', (workspace_id,)).fetchone()
    if not p or p['registered'] is None:
        return
    act = max(p['last_activity'] or 0, last_activity)
    hidden = 0 if p['hidden'] == 2 and act >= now - AUTO_HIDE_DAYS * 86400 else p['hidden']
    if act != p['last_activity'] or hidden != p['hidden']:
        db.execute('UPDATE projects SET last_activity=?,hidden=?,revision=revision+1 WHERE id=?', (act, hidden, p['id']))
        changed(db, 'project', p['id'])


TAIL = 262144


class _Chunks(io.RawIOBase):
    """보관된 조각들을 필요할 때 하나씩 읽는 스트림(메모리에 조각 하나만)."""

    def __init__(self, chunks):
        self._digests = [c['digest'] for c in chunks]
        self._buf = b''

    def readable(self):
        return True

    def readinto(self, b):
        from session_history import archive
        while not self._buf and self._digests:
            self._buf = archive.read_chunk(self._digests.pop(0))
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def _tail(info) -> bytes:
    """보관된 원본의 끝부분(TAIL바이트). 압축(.zst)이면 처음부터 1MiB씩 흘려 풀며 끝부분만 남긴다 — 메모리는 조각 하나 + 1MiB로
    묶이고(압축 조각 하나가 크게 풀려도), 이어 붙은 여러 프레임도 끝까지 읽는다(read_across_frames)."""
    from session_history import archive
    chunks = info.get('chunks') or []
    if not chunks:
        return b''
    if not info.get('compressed'):
        return archive.read_chunk(chunks[-1]['digest'])[-TAIL:]
    import zstandard
    tail = b''
    with zstandard.ZstdDecompressor(max_window_size=64 * 1024**2).stream_reader(_Chunks(chunks), read_across_frames=True) as z:
        while True:
            block = z.read(1024 * 1024)
            if not block:
                break
            tail = (tail + block)[-TAIL:]
    return tail


def history_facts(db, cid: str, provider: str) -> dict:
    """이 판 이전에 수집된 옛 대화의 실행 방식·실제 시작·마지막 활동을 원본에서 읽는다. 원본 파일이 남아 있으면 그 앞부분과
    수정 시각, 없으면 보관된 원본(첫 조각·마지막 매니페스트 전체). 못 읽은 값은 빠진다(방식을 못 읽으면 '' → CLI 아님)."""
    from session_history import adapters, archive
    out: dict = {'launch': ''}
    try:
        src = db.execute('SELECT s.root,f.relative_path FROM source_files f JOIN sources s ON s.id=f.source_id'
                         ' WHERE f.conversation_id=? LIMIT 1', (cid,)).fetchone()
        rows = db.execute('SELECT detail FROM manifests WHERE conversation_id=? ORDER BY created', (cid,)).fetchall()
        first = json.loads(rows[0]['detail']) if rows else {}
        relative = str(first.get('relative_path') or (src['relative_path'] if src else ''))
        path = os.path.join(os.path.expanduser(src['root']), src['relative_path']) if src else ''
        head = b''
        if path and os.path.isfile(path) and not path.endswith('.zst'):
            with open(path, 'rb') as f:
                head = f.read(1024 * 1024)
            out['last'] = os.stat(path).st_mtime
        elif first.get('chunks'):
            head = archive.read_chunk(first['chunks'][0]['digest'])
            if first.get('compressed'):
                import zstandard
                with zstandard.ZstdDecompressor(max_window_size=64 * 1024**2).stream_reader(io.BytesIO(head)) as z:
                    head = z.read(1024 * 1024)
        ident = adapters.identity(head, provider, relative) if head else None
        out['launch'] = (ident or {}).get('launch') or ''
        if first_timestamp(head):
            out['started'] = first_timestamp(head)
        if 'last' not in out and path and os.path.isfile(path):
            out['last'] = os.stat(path).st_mtime
        if 'last' not in out and rows:
            last = _last_timestamp(_tail(json.loads(rows[-1]['detail'])))
            if last:
                out['last'] = last
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


def _decide(r, cache, known):
    """(placement, detail 고칠 값, 저장소 근거) — DB 밖에서(git·파일 읽기)."""
    patch: dict = {}
    if r['parent_native_id']:
        return 'skip:child', patch, None
    detail = _obj(r['detail'])
    launch = detail.get('launch')
    if not isinstance(launch, str) or 'started' not in detail:
        with database() as db:
            facts = history_facts(db, r['id'], r['provider'])
        if not isinstance(launch, str):
            launch = patch['launch'] = facts['launch']
        for k in ('started', 'last'):
            if facts.get(k) and k not in detail:
                patch[k] = facts[k]
    if not cli_started(r['provider'], launch):
        return 'skip:launch', patch, None
    cwd = os.path.normpath(r['cwd']) if (r['cwd'] or '').startswith('/') else ''
    if not cwd or _temp(cwd):
        return 'skip:cwd', patch, None
    if cwd not in cache:
        if os.path.isdir(cwd):
            # 있는 폴더는 inspect_workspace가 fsguard(탐색 허용 폴더·거부 경로)로 확인한다. 경계 밖이면 None → git 실패와 구분 못 하므로
            # 먼저 경계를 본다.
            try:
                fsguard.resolve_under_roots(cwd, git_meta=True)
                cache[cwd] = inventory.inspect_workspace(cwd)   # git 2초 제한, 실패는 None
            except Exception:
                cache[cwd] = 'outside'
        else:
            safe = _gone_path(cwd)
            wid = _gone_workspace(known, safe) if safe else None
            cache[cwd] = ('wid', wid) if wid else ('gone' if safe else 'outside')
    found = cache[cwd]
    if found == 'outside':
        return 'skip:cwd', patch, None
    if found == 'gone':
        return 'skip:gone', patch, None
    if found is None:
        tries = int(detail.get('placement_tries') or 0) + 1
        patch['placement_tries'] = tries
        return ('skip:git' if tries >= GIT_TRIES else None), patch, None
    return 'placed', patch, found


def place_conversations(now=None, limit=BATCH, budget=BUDGET) -> int:
    """아직 놓이지 않은 대화를 최근 것부터 놓는다. 이번에 결정한(놓았거나 건너뛴) 대화 수를 돌려준다."""
    now = time.time() if now is None else now
    with database() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT id,provider,cwd,created,updated,detail,parent_native_id FROM conversations"
            " WHERE placement IS NULL AND deleted IS NULL ORDER BY updated DESC LIMIT ?", (limit,))]
        known = [(r['path'], r['id'], r['kind']) for r in db.execute(
            "SELECT w.path,w.id,p.kind FROM workspaces w JOIN projects p ON p.id=w.project_id")]
    if not rows:
        return 0
    deadline = time.monotonic() + budget
    cache: dict = {}
    decided = []
    for r in rows:
        if time.monotonic() > deadline:
            break
        decided.append((r, *_decide(r, cache, known)))
    old = now - AUTO_HIDE_DAYS * 86400
    done = 0
    with database(write=True) as db:
        for r, placement, patch, found in decided:
            # git·파일 읽기 동안 수집이 이 대화를 고쳤을 수 있다 — 쓰기 트랜잭션 안에서 다시 읽어 합친다(옛 스냅샷으로 덮지 않는다).
            cur = db.execute('SELECT detail,placement FROM conversations WHERE id=?', (r['id'],)).fetchone()
            if not cur or cur['placement'] is not None:
                continue
            d = _obj(cur['detail'])
            for k, v in patch.items():
                if k == 'last':
                    d['last'] = max(_num(d.get('last')) or 0, v)
                elif k == 'placement_tries' or k not in d:
                    d[k] = v
            wid = found[1] if isinstance(found, tuple) else (inventory.workspace(db, found) if isinstance(found, dict) else None)
            db.execute('UPDATE conversations SET placement=?,workspace_id=COALESCE(?,workspace_id),detail=? WHERE id=?',
                       (placement, wid, json.dumps(d, separators=(',', ':'), sort_keys=True), r['id']))
            if patch:
                changed(db, 'conversation', r['id'])   # 복제: 바뀐 detail을 다른 맥에도
            if placement:
                done += 1
            if not wid:
                continue
            p = db.execute('SELECT p.id,p.registered,p.removed,p.last_activity,p.hidden FROM projects p'
                           ' JOIN workspaces w ON w.project_id=p.id WHERE w.id=?', (wid,)).fetchone()
            if not p:
                continue
            started = _num(d.get('started')) or r['created'] or now
            act = max(p['last_activity'] or 0, _num(d.get('last')) or r['updated'] or 0)
            first = p['registered'] is None
            registered = started if first else min(p['registered'], started)
            removed = None if p['removed'] is not None and started > p['removed'] else p['removed']
            hidden = p['hidden']
            if hidden == 2 and act >= old:
                hidden = 0          # 자동 숨김은 새 활동이면 풀린다(사람이 숨긴 1은 그대로)
            elif first and hidden == 0 and act < old:
                hidden = 2          # 처음 등록되는 저장소의 마지막 활동이 오래됐다(백필) — 숨겨 둔다
            db.execute('UPDATE projects SET registered=?,removed=?,last_activity=?,hidden=?,revision=revision+1 WHERE id=?',
                       (registered, removed, act, hidden, p['id']))
            changed(db, 'project', p['id'])
    return done
