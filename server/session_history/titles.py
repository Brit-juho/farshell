"""대화 제목 — Claude 제목 줄 읽기, 우선순위, 옛 대화 한 번 채우기(백필).

왜 있나(2026-11-26): 종료 목록·대화 기록의 거의 모든 대화가 「제목 없음」이었다. 원인 셋.
1. Claude Code는 자동 제목을 `{"type":"ai-title","aiTitle":…}`로 적는데 `customTitle`·`title`·`summary`만 읽었다.
2. 수집은 파일에서 새로 붙은 줄만 읽으므로, 제목 기능(2026-11-12) 전에 읽은 대화와 끝난 대화는 고쳐도 계속 비어 있다.
3. Codex 제목(첫 지시)에 Codex가 끼워 넣은 `# AGENTS.md instructions…`가 잡혔다(human_prompt.py에서 거른다).

우선순위: 사람이 정한 이름(`custom-title`, /rename) > 자동 제목(`ai-title`) > 옛 요약(`summary`) > 첫 지시(Codex).
같은 순위면 나중 것이 이긴다. Claude는 이름을 바꾼 뒤에도 자동 제목 줄을 계속 적으므로(실측), 순위 없이 「마지막 줄」을
쓰면 사람이 정한 이름이 덮인다. 어디서 온 제목인지는 `conversations.detail.title_source`에 둔다.

백필은 이 맥이 직접 수집한 대화만 다룬다. Claude는 FarShell이 보관한 사본(manifests·chunks)에서 제목 줄만 골라 읽는다 —
원본 파일이 지워졌어도 된다. Codex는 저장된 사용자 메시지에서 첫 지시를 찾는다. 「마지막 지시」 요약(conversation_local)이
없는 옛 대화는 같은 메시지로 채운다(화면이 제목 대신 보여 준다). 진행 위치를 meta에 적어 재시작해도 이어 가고, 끝나면 다시
돌지 않는다. 사용자 원문은 로그에 남기지 않는다.
"""
from __future__ import annotations

import json
import logging

from human_prompt import human_prompt

logger = logging.getLogger(__name__)

MAX_TITLE = 128
RANK = {'prompt': 0, 'summary': 1, 'ai': 2, 'custom': 3}
_KINDS = {'custom-title': ('custom', 'customTitle'), 'ai-title': ('ai', 'aiTitle'), 'summary': ('summary', 'summary')}
# 제목 줄만 JSON으로 읽는다 — 긴 기록을 줄마다 해석하지 않으려는 바이트 거름망.
_NEEDLES = (b'"custom-title"', b'"ai-title"', b'"type":"summary"', b'"type": "summary"')

BACKFILL_KEY = 'conversation_titles_backfill'
BACKFILL_VERSION = 1
BATCH = 20
# 사본 하나에서 읽을 상한 — 비정상적으로 큰 기록 하나가 백필을 붙잡지 않게.
SCAN_LIMIT = 256 * 1024**2


def claude_title(row):
    """Claude 기록 한 줄이 제목 줄이면 (제목, 종류), 아니면 None."""
    if not isinstance(row, dict):
        return None
    kind = _KINDS.get(row.get('type'))
    if not kind:
        return None
    value = row.get(kind[1])
    if not isinstance(value, str) or not value.strip():
        value = row.get('title')
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:MAX_TITLE], kind[0]


def better(current, candidate):
    """candidate(제목, 종류)가 current(제목, 종류 또는 None)를 대신해야 하나. 같은 순위면 나중 것이 이긴다."""
    if candidate is None:
        return False
    if current is None or current[1] not in RANK:
        return True
    return RANK.get(candidate[1], -1) >= RANK[current[1]]


def title_source(detail_text):
    try:
        detail = json.loads(detail_text or '{}')
    except (TypeError, ValueError):
        return None
    source = detail.get('title_source') if isinstance(detail, dict) else None
    return source if source in RANK else None


def apply(db, cid, candidate):
    """대화 제목을 candidate로 바꿀지 순위로 정해 쓴다. 바꿨으면 True."""
    if candidate is None:
        return False
    row = db.execute('SELECT title,detail FROM conversations WHERE id=?', (cid,)).fetchone()
    if row is None:
        return False
    source = title_source(row['detail'])
    current = (row['title'], source) if row['title'] and source else None
    if not better(current, candidate):
        return False
    title, kind = candidate
    if row['title'] == title and source == kind:
        return False
    db.execute("UPDATE conversations SET title=?,detail=json_set(CASE WHEN json_valid(detail) THEN detail ELSE '{}' END,"
               "'$.title_source',?) WHERE id=?", (title, kind, cid))
    return True


def scan_lines(chunks):
    """바이트 덩어리들을 줄로 이어 읽으며 가장 좋은 제목을 고른다."""
    best, carry, seen = None, b'', 0
    for data in chunks:
        seen += len(data)
        lines = (carry + data).split(b'\n')
        carry = lines.pop()
        for line in lines:
            best = _pick(best, line)
        if seen >= SCAN_LIMIT:
            carry = b''
            break
    return _pick(best, carry)


def _pick(best, line):
    if not line or not any(n in line for n in _NEEDLES):
        return best
    try:
        candidate = claude_title(json.loads(line))
    except (ValueError, UnicodeError, RecursionError):
        return best
    return candidate if better(best, candidate) else best


def _claude_from_archive(db, cid, native):
    from .archive import read_chunk
    manifest = db.execute(
        "SELECT detail FROM manifests WHERE conversation_id=? AND json_valid(detail)"
        " AND json_extract(detail,'$.capture_kind')='transcript' AND NOT coalesce(json_extract(detail,'$.compressed'),0)"
        " AND json_extract(detail,'$.relative_path') LIKE ? ESCAPE '\\' ORDER BY created DESC LIMIT 1",
        (cid, '%' + native.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '.jsonl')).fetchone()
    if manifest is None:
        return None
    parts = json.loads(manifest['detail']).get('chunks') or []

    def chunks():
        for part in parts:
            yield read_chunk(part['digest'])
    return scan_lines(chunks())


def _prompts(db, cid, limit=None):
    sql = "SELECT body FROM messages WHERE conversation_id=? AND role='user' ORDER BY position"
    rows = db.execute(sql + (' LIMIT ?' if limit else ''), (cid, limit) if limit else (cid,))
    return [p for p in (human_prompt(r['body']) for r in rows) if p]


def _plan_one(db, row):
    """읽기만 해서 이 대화에 쓸 것(제목 후보, 비우기, 마지막 지시 요약)을 정한다."""
    cid, plan = row['id'], {'id': row['id']}
    if row['provider'] == 'claude':
        if title_source(row['detail']) != 'custom':
            try:
                plan['title'] = _claude_from_archive(db, cid, row['native_id'])
            except Exception:
                # 사본이 깨졌거나 없으면(chunk_corrupt 등) 이 대화의 제목만 건너뛴다.
                logger.info('conversation title backfill skipped one conversation')
    elif row['provider'] == 'codex':
        bad = (row['title'] or '').startswith('# AGENTS.md instructions')
        if not row['title'] or bad:
            first = _prompts(db, cid, limit=200)
            if first:
                plan['title'] = (first[0][:60], 'prompt')
            elif bad:
                plan['clear_bad'] = True
    if not db.execute('SELECT 1 FROM conversation_local WHERE conversation_id=?', (cid,)).fetchone():
        prompts = _prompts(db, cid)
        if prompts:
            plan['local'] = (prompts[-1], len(prompts))
    return plan


def _write_one(db, plan, now):
    cid, changed = plan['id'], False
    title = plan.get('title')
    if title and title[1] == 'prompt':
        # 첫 지시는 제목이 비었거나 Codex가 끼워 넣은 글일 때만 — 그사이 다른 제목이 생겼으면 두지 않는다.
        row = db.execute('SELECT title FROM conversations WHERE id=?', (cid,)).fetchone()
        if row is None or (row['title'] and not row['title'].startswith('# AGENTS.md instructions')):
            title = None
        elif row['title']:
            db.execute("UPDATE conversations SET title='' WHERE id=?", (cid,))
    changed |= apply(db, cid, title)
    if plan.get('clear_bad'):
        changed |= db.execute("UPDATE conversations SET title='' WHERE id=? AND title LIKE '# AGENTS.md instructions%'",
                              (cid,)).rowcount > 0
    if plan.get('local'):
        # 그사이 수집이 요약을 만들었으면 그대로 둔다(두 번 세지 않는다).
        changed |= db.execute('INSERT INTO conversation_local(conversation_id,last_user_text,user_message_count,updated)'
                              ' VALUES(?,?,?,?) ON CONFLICT(conversation_id) DO NOTHING',
                              (cid, plan['local'][0], plan['local'][1], now)).rowcount > 0
    return changed


def backfill_batch(limit=BATCH):
    """옛 대화 한 묶음을 채운다. 읽기(사본 훑기)는 쓰기 잠금 밖에서 하고, 쓰기는 짧게. 돌려주는 값: (처리한 수, 끝났나)."""
    import time
    from session_catalog.db import database, host_id, setting, set_setting
    with database() as db:
        state = setting(db, BACKFILL_KEY, {}) or {}
        if state.get('version') == BACKFILL_VERSION and state.get('done'):
            return 0, True
        cursor = state.get('cursor', '') if state.get('version') == BACKFILL_VERSION else ''
        rows = db.execute(
            "SELECT id,provider,native_id,title,detail FROM conversations WHERE host=? AND deleted IS NULL AND id>?"
            " AND provider IN ('claude','codex') ORDER BY id LIMIT ?", (host_id(db), cursor, limit)).fetchall()
        plans = [_plan_one(db, row) for row in rows]
    now, filled = time.time(), 0
    with database(write=True) as db:
        for plan in plans:
            filled += _write_one(db, plan, now)
        done = len(rows) < limit
        before = int(state.get('filled', 0)) if cursor else 0
        set_setting(db, BACKFILL_KEY, {'version': BACKFILL_VERSION, 'cursor': rows[-1]['id'] if rows else cursor,
                                       'done': done, 'filled': before + filled})
    return len(rows), done
