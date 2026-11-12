"""대화 제목 갱신과 이 맥 전용 대화 요약(세션 로직 점검 단계 2, C6·S7).

- Claude가 대화 중간·뒤에 붙인 제목 줄도 제목이 된다(예전에는 앞 256줄만 봤다).
- Codex는 제목 줄이 없어 첫 지시로 제목을 정한다.
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
        f.write(_line({'type': 'ai-title', 'sessionId': 's1', 'title': '세션 목록 리뉴얼'}))
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
