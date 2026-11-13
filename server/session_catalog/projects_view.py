"""저장소·대화 목록 API의 본체(routes/session_catalog.py). 저장소 = CLI를 켠 곳(placement.py가 만든 registered 저장소).

- `project_list` 목록의 저장소(등록됨·제거 안 됨, 숨김은 설정 화면에서만), 메인 브랜치, 끝난 대화 수.
- `conversation_list` 저장소의 「종료 N」(끝난 대화)과 보관함(보관한 대화) — 최근 활동순, 커서 쪽 넘김, 검색.
- `update_project` 숨김·제거.
- `conversation_resume_preview` 대화를 이어서 열 폴더와 tmux 이름(아무것도 만들지 않는다).

「끝난 대화」 = 저장소에 놓였고(placement='placed') 지운 것이 아니며 지금 돌고 있지 않은 대화. 돌고 있는지는 이 맥의 에이전트
상태(훅)의 대화 id로 본다 — 돌고 있는 대화는 실행 중 세션 칸이 보여 준다. 활동 시각은 수집이 원본에서 적은 `detail.last`
(없으면 수집 시각). 모두 이 맥 전용(중계 안 함).
"""
from __future__ import annotations

import re
import time

from .db import Refused, changed, database, host_id, identifier

PAGE_MAX = 100
_CURSOR = re.compile(r'^(-?\d+(?:\.\d+)?):([A-Za-z0-9_-]{1,64})$')
_ACTIVITY = "COALESCE(CAST(json_extract(c.detail,'$.last') AS REAL),c.updated)"


def _running_native_ids() -> set[str]:
    try:
        import agent_status
        return {e.get('session_id') for e in agent_status.visible_state().values()
                if not e.get('released') and isinstance(e.get('session_id'), str)}
    except Exception:  # noqa: BLE001 — 에이전트 상태가 없으면 아무것도 돌고 있지 않은 것으로
        return set()


def _not_running(running):
    """지금 돌고 있는 대화를 SQL에서 뺀다(파이썬에서 거르면 쪽 넘김이 짧아지거나 건너뛴다)."""
    ids = sorted(i for i in running if isinstance(i, str))[:500]
    return (f" AND c.native_id NOT IN ({','.join('?' * len(ids))})", ids) if ids else ('', [])


def project_list(include_hidden: bool = False) -> dict:
    running = _running_native_ids()
    with database() as db:
        host = host_id(db)
        rows = db.execute(
            "SELECT p.id,p.name,p.path,p.kind,p.hidden,p.registered,p.last_activity,"
            " (SELECT w.branch FROM workspaces w WHERE w.project_id=p.id AND w.kind='checkout' AND w.branch IS NOT NULL"
            "  ORDER BY w.rowid DESC LIMIT 1) AS branch"
            " FROM projects p WHERE p.host=? AND p.registered IS NOT NULL AND p.removed IS NULL"
            + ('' if include_hidden else ' AND p.hidden=0'), (host,)).fetchall()
        ids = [r['id'] for r in rows]
        counts = {}
        if ids:
            not_running, run_args = _not_running(running)
            counts = {r['pid']: r['n'] for r in db.execute(
                "SELECT w.project_id AS pid,count(*) AS n FROM conversations c JOIN workspaces w ON w.id=c.workspace_id"
                " WHERE c.placement='placed' AND c.deleted IS NULL AND c.archived IS NULL AND c.parent_native_id IS NULL"
                f" AND w.project_id IN ({','.join('?' * len(ids))}){not_running} GROUP BY w.project_id", (*ids, *run_args))}
    return {'projects': [{'id': r['id'], 'name': r['name'], 'path': r['path'], 'kind': r['kind'],
                          'hidden': r['hidden'], 'registered': r['registered'], 'last_activity': r['last_activity'],
                          'branch': r['branch'], 'ended_count': counts.get(r['id'], 0)} for r in rows]}


def conversation_list(project: str | None = None, archived: bool = False, q: str | None = None,
                      cursor: str | None = None, limit: int = 20) -> dict:
    if not 1 <= limit <= PAGE_MAX:
        raise Refused('invalid_limit', 400)
    if project is not None:
        identifier(project)
    if q is not None and len(q) > 200:
        raise Refused('invalid_query', 400)
    where = ["c.placement='placed'", 'c.deleted IS NULL', 'c.parent_native_id IS NULL',
             'c.archived IS NOT NULL' if archived else 'c.archived IS NULL']
    args: list = []
    if project:
        where.append('w.project_id=?')
        args.append(project)
    if q and q.strip():
        like = '%' + q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where.append("(c.title LIKE ? ESCAPE '\\' OR c.cwd LIKE ? ESCAPE '\\' OR l.last_user_text LIKE ? ESCAPE '\\')")
        args += [like, like, like]
    if cursor:
        m = _CURSOR.match(cursor)
        if not m:
            raise Refused('invalid_cursor', 400)
        where.append(f'({_ACTIVITY}<? OR ({_ACTIVITY}=? AND c.id<?))')
        args += [float(m.group(1)), float(m.group(1)), m.group(2)]
    if not archived:
        not_running, run_args = _not_running(_running_native_ids())
        where.append(not_running.removeprefix(' AND ') or '1')
        args += run_args
    with database() as db:
        rows = db.execute(
            f"SELECT c.id,c.provider,c.native_id,c.title,c.cwd,c.archived,{_ACTIVITY} AS activity,"
            " CAST(json_extract(c.detail,'$.started') AS REAL) AS started, w.project_id,"
            " l.last_user_text,l.user_message_count,"
            " (SELECT r.branch_at_end FROM conversation_executions e JOIN runtimes r ON r.id=e.runtime_id"
            "  WHERE e.conversation_id=c.id AND r.branch_at_end IS NOT NULL ORDER BY r.started DESC LIMIT 1) AS branch"
            " FROM conversations c JOIN workspaces w ON w.id=c.workspace_id"
            " LEFT JOIN conversation_local l ON l.conversation_id=c.id"
            f" WHERE {' AND '.join(where)} ORDER BY activity DESC,c.id DESC LIMIT ?",
            (*args, limit + 1)).fetchall()
        counts = {}
        if archived and not cursor:
            for r in db.execute("SELECT w.project_id AS pid,count(*) AS n FROM conversations c JOIN workspaces w ON w.id=c.workspace_id"
                                " WHERE c.placement='placed' AND c.deleted IS NULL AND c.archived IS NOT NULL GROUP BY w.project_id"):
                counts[r['pid']] = r['n']
    page, more = rows[:limit], len(rows) > limit
    out = {'conversations': [{'id': r['id'], 'native_id': r['native_id'], 'provider': r['provider'], 'title': r['title'] or '', 'cwd': r['cwd'],
                              'project_id': r['project_id'], 'activity': r['activity'], 'started': r['started'],
                              'archived': r['archived'], 'branch': r['branch'], 'last_user_text': r['last_user_text'] or '',
                              'user_message_count': r['user_message_count'] or 0} for r in page],
           'next': f"{page[-1]['activity']}:{page[-1]['id']}" if more and page else None}
    if archived and not cursor:
        out['project_counts'] = counts
    return out


def update_project(pid: str, value: dict) -> dict:
    identifier(pid)
    hidden, removed = value.get('hidden'), value.get('removed')
    if (hidden is not None and type(hidden) is not bool) or (removed is not None and removed is not True) or (hidden is None and removed is None):
        raise Refused('invalid_request', 400)
    with database(write=True) as db:
        row = db.execute('SELECT id,path,hidden FROM projects WHERE id=? AND host=? AND registered IS NOT NULL AND removed IS NULL',
                         (pid, host_id(db))).fetchone()
        if not row:
            raise Refused('project_not_found', 404)
        if removed:
            # 제거: 목록·설정에서 빠진다. 대화 기록은 지우지 않는다(보관함·대화 보관함). 그 자리에서 CLI를 다시 켜면 되살아난다.
            db.execute('UPDATE projects SET removed=?,revision=revision+1 WHERE id=?', (time.time(), pid))
        else:
            db.execute('UPDATE projects SET hidden=?,revision=revision+1 WHERE id=?', (1 if hidden else 0, pid))
        changed(db, 'project', pid)
    return {'ok': True}


def conversation_resume_preview(cid: str) -> dict:
    """대화를 이어서 열기 전에: 열릴 폴더와 tmux 이름(지금 tmux에 없는 첫 이름 — 누르는 사이 바뀔 수 있다)."""
    from pathlib import Path
    import fsguard
    import session_names
    identifier(cid)
    with database() as db:
        c = db.execute('SELECT id,title,provider,cwd,archived FROM conversations WHERE id=? AND deleted IS NULL', (cid,)).fetchone()
    if not c:
        raise Refused('conversation_not_found', 404)
    # 실제 이어서 시작(lifecycle.resume)과 같은 경로 검사 + 폴더가 있어야 한다 — 미리보기가 「된다」고 하고 누르면 거절되지 않게.
    try:
        resolved = fsguard.resolve_under_roots(c['cwd'])
        cwd = str(resolved) if resolved.is_dir() else None
    except (fsguard.FsDenied, TypeError, OSError):
        cwd = None
    base = session_names.base_for(cwd) if cwd else None
    return {'resumable': bool(cwd), 'conversation_id': c['id'], 'conversation_title': c['title'] or '', 'provider': c['provider'],
            'folder': Path(cwd).name if cwd else None, 'name': session_names.allocate(base) if base else None}
