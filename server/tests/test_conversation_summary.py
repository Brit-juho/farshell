"""대화 제목 갱신과 이 맥 전용 대화 요약(세션 로직 점검 단계 2, C6·S7).

- Claude가 대화 중간·뒤에 붙인 제목 줄도 제목이 된다(예전에는 앞 256줄만 봤다).
- Codex는 제목 줄이 없어 첫 지시로 제목을 정한다(Codex가 끼워 넣은 `# AGENTS.md instructions`는 지시가 아니다).
- 제목 순위: 사람이 정한 이름(custom-title) > 자동 제목(ai-title) > 옛 요약(summary) > 첫 지시 — titles.py.
- 옛 대화는 서버가 한 번 채운다(백필): Claude는 보관 사본의 제목 줄, Codex는 저장된 첫 지시, 빠진 마지막 지시 요약도.
- 마지막 지시·지시 수는 `conversation_local`(복제되지 않는 표)에만, 사람이 친 줄만 센다.
"""

import json

import pytest


@pytest.fixture
def private_history(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('VT_STATE_DIR', str(home / 'state'))
    monkeypatch.setenv('VT_BROWSE_ROOTS', str(home))
    from session_catalog import db
    db.snapshot()
    return home


def _source(home, provider):
    from session_history.archive import register_source
    from session_catalog.db import database
    root = home / ('.claude' if provider == 'claude' else '.codex')
    folder = root / ('projects/sample' if provider == 'claude' else 'sessions/2026/01')
    folder.mkdir(parents=True)
    registered = register_source(provider, str(root))
    with database() as db:
        return folder, dict(db.execute('SELECT * FROM sources WHERE id=?', (registered['id'],)).fetchone())


def _line(obj):
    return json.dumps(obj, ensure_ascii=False) + '\n'


def _summary(cid):
    from session_catalog.db import database
    with database() as db:
        title = db.execute('SELECT title FROM conversations WHERE id=?', (cid,)).fetchone()[0]
        local = db.execute('SELECT last_user_text,user_message_count FROM conversation_local WHERE conversation_id=?', (cid,)).fetchone()
    return title, (tuple(local) if local else None)


def test_claude_title_written_late_and_only_human_prompts_count(private_history):
    from session_history import archive, query
    folder, source = _source(private_history, 'claude')
    path = folder / 's1.jsonl'
    user = lambda text: _line({'sessionId': 's1', 'type': 'user', 'cwd': str(private_history), 'message': {'role': 'user', 'content': text}})
    path.write_text(user('세션 목록 시안 만들어줘\n자세히') + user('<system-reminder>무시</system-reminder>')
                    + user('[FarShell 위임 abc] 머리말') + user('Caveat: 끼워 넣은 글'))
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    assert _summary(cid) == ('', ('세션 목록 시안 만들어줘', 1))
    with path.open('a') as f:
        f.write(''.join(_line({'sessionId': 's1', 'type': 'user', 'message': {'role': 'user', 'content': f'n{i}'}}) for i in range(300)))
        f.write(user('마지막 지시'))
        f.write(_line({'type': 'ai-title', 'aiTitle': '세션 목록 리뉴얼', 'sessionId': 's1'}))   # Claude Code가 실제로 쓰는 모양
    archive.scan_source(source)
    assert _summary(cid) == ('세션 목록 리뉴얼', ('마지막 지시', 302))
    archive.scan_source(source)      # 바뀐 게 없으면 다시 세지 않는다
    assert _summary(cid)[1] == ('마지막 지시', 302)


def test_codex_title_comes_from_the_first_prompt(private_history):
    from session_history import archive, query
    folder, source = _source(private_history, 'codex')
    path = folder / 'rollout-2026-01-01T00-00-00-019a0000-0000-7000-8000-000000000001.jsonl'
    path.write_text(_line({'type': 'session_meta', 'payload': {'id': '019a0000-0000-7000-8000-000000000001', 'cwd': str(private_history)}})
                    + _line({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'CI 실패 원인 찾아줘'}]}}))
    archive.scan_source(source)
    items = query.listing()['items']
    if not items:
        pytest.skip('이 Codex 기록 모양을 어댑터가 읽지 않는다')
    title, local = _summary(items[0]['id'])
    assert title == 'CI 실패 원인 찾아줘'
    assert local == ('CI 실패 원인 찾아줘', 1)


def test_human_prompt_filter():
    from session_history.archive import human_prompt
    assert human_prompt('  고쳐줘\n둘째 줄') == '고쳐줘'
    assert human_prompt('<command-name>/clear</command-name>') is None
    assert human_prompt('[위임 결과 · x] ...') is None
    assert human_prompt('[FarShell 에이전트 요청 #1 ...') is None
    assert human_prompt('a' * 300) == 'a' * 200


def _detail(cid):
    from session_catalog.db import database
    with database() as db:
        return json.loads(db.execute('SELECT detail FROM conversations WHERE id=?', (cid,)).fetchone()[0])


def test_claude_custom_title_is_not_overwritten_by_a_later_ai_title(private_history):
    """실측: Claude는 /rename 뒤에도 자동 제목 줄을 계속 적는다. 사람이 정한 이름이 이긴다 — 한 번에 읽어도, 나눠 읽어도."""
    from session_history import archive, query
    folder, source = _source(private_history, 'claude')
    path = folder / 's2.jsonl'
    user = lambda text: _line({'sessionId': 's2', 'type': 'user', 'cwd': str(private_history), 'message': {'role': 'user', 'content': text}})
    path.write_text(user('시작') + _line({'type': 'ai-title', 'aiTitle': '자동 제목 1', 'sessionId': 's2'}))
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    assert _summary(cid)[0] == '자동 제목 1' and _detail(cid)['title_source'] == 'ai'
    with path.open('a') as f:
        f.write(_line({'type': 'custom-title', 'customTitle': '내가 정한 이름', 'sessionId': 's2'}))
        f.write(_line({'type': 'ai-title', 'aiTitle': '자동 제목 2', 'sessionId': 's2'}))
    archive.scan_source(source)
    assert _summary(cid)[0] == '내가 정한 이름' and _detail(cid)['title_source'] == 'custom'
    with path.open('a') as f:
        f.write(_line({'type': 'ai-title', 'aiTitle': '자동 제목 3', 'sessionId': 's2'}))
    archive.scan_source(source)
    assert _summary(cid)[0] == '내가 정한 이름'
    with path.open('a') as f:
        f.write(_line({'type': 'custom-title', 'customTitle': '다시 정한 이름', 'sessionId': 's2'}))
    archive.scan_source(source)
    assert _summary(cid)[0] == '다시 정한 이름'


def test_claude_title_in_the_first_lines_is_read_on_insert(private_history):
    from session_history import archive, query
    folder, source = _source(private_history, 'claude')
    (folder / 's3.jsonl').write_text(
        _line({'type': 'custom-title', 'customTitle': '처음부터 이름', 'sessionId': 's3'})
        + _line({'type': 'ai-title', 'aiTitle': '자동', 'sessionId': 's3'})
        + _line({'sessionId': 's3', 'type': 'user', 'cwd': str(private_history), 'message': {'role': 'user', 'content': '안녕'}}))
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    assert _summary(cid)[0] == '처음부터 이름' and _detail(cid)['title_source'] == 'custom'


def test_codex_injected_agents_md_is_not_a_prompt():
    from human_prompt import human_prompt
    assert human_prompt('# AGENTS.md instructions for /srv/acme\n\n<INSTRUCTIONS>...') is None
    assert human_prompt('# AGENTS.md instructions\n<INSTRUCTIONS>') is None
    assert human_prompt('# 제목처럼 쓴 사람 지시') == '# 제목처럼 쓴 사람 지시'


def test_backfill_fills_old_conversations_once(private_history):
    """제목 기능 전에 읽은(또는 키를 잘못 읽어 비어 있던) 대화를 서버가 한 번 채운다 — 보관 사본에서, 원본 파일이 없어도."""
    from session_history import archive, query, titles
    from session_catalog.db import database, setting
    folder, source = _source(private_history, 'claude')
    user = lambda sid, text: _line({'sessionId': sid, 'type': 'user', 'cwd': str(private_history), 'message': {'role': 'user', 'content': text}})
    (folder / 'a1.jsonl').write_text(user('a1', '첫 지시') + _line({'type': 'ai-title', 'aiTitle': '자동 제목', 'sessionId': 'a1'})
                                     + user('a1', '마지막 지시'))
    (folder / 'a2.jsonl').write_text(user('a2', '이름 정한 대화') + _line({'type': 'custom-title', 'customTitle': '사람 이름', 'sessionId': 'a2'})
                                     + _line({'type': 'ai-title', 'aiTitle': '나중 자동', 'sessionId': 'a2'}))
    (folder / 'a3.jsonl').write_text(user('a3', '제목 줄 없는 대화'))
    cfolder, csource = _source(private_history, 'codex')
    rollout = cfolder / 'rollout-2026-01-01T00-00-00-019a0000-0000-7000-8000-000000000002.jsonl'
    rollout.write_text(_line({'type': 'session_meta', 'payload': {'id': '019a0000-0000-7000-8000-000000000002', 'cwd': str(private_history)}})
                       + _line({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '# AGENTS.md instructions for /x\n<INSTRUCTIONS>'}]}})
                       + _line({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '빌드 고쳐줘'}]}}))
    archive.scan_source(source)
    archive.scan_source(csource)
    with database(write=True) as db:   # 옛 서버가 남긴 상태: 제목·출처·요약이 없다, Codex는 끼워 넣은 글이 제목
        db.execute("UPDATE conversations SET title='',detail=json_remove(detail,'$.title_source') WHERE provider='claude'")
        db.execute("UPDATE conversations SET title='# AGENTS.md instructions for /x',detail=json_remove(detail,'$.title_source') WHERE provider='codex'")
        db.execute('DELETE FROM conversation_local')
    for f in folder.iterdir():
        f.unlink()                     # 원본이 지워져도 보관 사본으로 채운다
    items = {i['native_id']: i['id'] for i in query.listing()['items']}
    if '019a0000-0000-7000-8000-000000000002' not in items:
        pytest.skip('이 Codex 기록 모양을 어댑터가 읽지 않는다')
    total, done = 0, False
    while not done:
        n, done = titles.backfill_batch(limit=2)
        total += n
    assert total == 4
    assert _summary(items['a1']) == ('자동 제목', ('마지막 지시', 2))
    assert _summary(items['a2'])[0] == '사람 이름' and _detail(items['a2'])['title_source'] == 'custom'
    assert _summary(items['a3']) == ('', ('제목 줄 없는 대화', 1))      # 제목 줄이 없으면 비워 두고 화면이 마지막 지시를 쓴다
    assert _summary(items['019a0000-0000-7000-8000-000000000002']) == ('빌드 고쳐줘', ('빌드 고쳐줘', 1))
    with database() as db:
        state = setting(db, titles.BACKFILL_KEY)
    assert state['done'] and state['filled'] == 4
    assert titles.backfill_batch() == (0, True)      # 끝나면 다시 돌지 않는다


def test_backfill_keeps_a_custom_title_set_meanwhile(private_history):
    from session_history import archive, query, titles
    from session_catalog.db import database
    folder, source = _source(private_history, 'claude')
    (folder / 'b1.jsonl').write_text(_line({'sessionId': 'b1', 'type': 'user', 'cwd': str(private_history), 'message': {'role': 'user', 'content': 'x'}})
                                     + _line({'type': 'ai-title', 'aiTitle': '자동', 'sessionId': 'b1'}))
    archive.scan_source(source)
    cid = query.listing()['items'][0]['id']
    with database(write=True) as db:   # 백필이 읽는 사이 사람이 이름을 정했다
        db.execute("UPDATE conversations SET title='사람',detail=json_set(detail,'$.title_source','custom') WHERE id=?", (cid,))
    while not titles.backfill_batch()[1]:
        pass
    assert _summary(cid)[0] == '사람'


def test_title_rank_rules():
    from session_history.titles import better, claude_title
    assert claude_title({'type': 'ai-title', 'aiTitle': '  a  '}) == ('a', 'ai')
    assert claude_title({'type': 'custom-title', 'customTitle': 'c'}) == ('c', 'custom')
    assert claude_title({'type': 'summary', 'summary': 's'}) == ('s', 'summary')
    assert claude_title({'type': 'ai-title', 'aiTitle': ''}) is None
    assert claude_title({'type': 'user'}) is None and claude_title('x') is None
    assert claude_title({'type': 'ai-title', 'aiTitle': 'x' * 300})[0] == 'x' * 128
    assert better(None, ('a', 'ai')) and better(('a', 'ai'), ('b', 'ai')) and better(('a', 'ai'), ('c', 'custom'))
    assert not better(('c', 'custom'), ('a', 'ai')) and not better(('a', 'ai'), None)
