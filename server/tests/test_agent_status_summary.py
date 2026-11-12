"""도구 입력 원문은 내보내지 않고 요약만(세션 로직 점검 단계 0, B1·A1·A2).

훅의 `tool_input`에는 Write의 파일 내용 전체, Edit의 바꿀 문자열, Bash 명령(토큰이 섞이는 자리)이 그대로
들어 있다. 상태 API·/ws-agent·peer 중계로 나가는 `public_entry`에는 그 원문이 없어야 한다.
"""

import json

import pytest

import agent_status


@pytest.fixture(autouse=True)
def _reset_state():
    for table in (agent_status._state, getattr(agent_status, '_owners', {}), getattr(agent_status, '_retired', {})):
        table.clear()
    yield
    for table in (agent_status._state, getattr(agent_status, '_owners', {}), getattr(agent_status, '_retired', {})):
        table.clear()


SECRET = 'ghp_TOPSECRET123'


def _public(state):
    return json.dumps(agent_status.public_entry(state), ensure_ascii=False)


def test_bash_command_is_summarised_to_the_program_name_only():
    state = agent_status.on_event('pre', {'session_id': 's1', 'cwd': '/repo', 'tool_name': 'Bash',
                                          'tool_input': {'command': f'GH_TOKEN={SECRET} curl -H "Authorization: Bearer {SECRET}" https://x'}})
    out = agent_status.public_entry(state)
    assert out['summary'] == 'curl'
    assert SECRET not in _public(state)
    assert 'input' not in out


def test_write_content_never_leaves_and_path_is_relative_to_cwd():
    state = agent_status.on_event('pre', {'session_id': 's1', 'cwd': '/repo', 'tool_name': 'Write',
                                          'tool_input': {'file_path': '/repo/server/app.py', 'content': SECRET * 50}})
    assert agent_status.public_entry(state)['summary'] == 'server/app.py'
    assert SECRET not in _public(state)


def test_path_outside_cwd_shows_only_the_file_name():
    state = agent_status.on_event('pre', {'session_id': 's1', 'cwd': '/repo', 'tool_name': 'Edit',
                                          'tool_input': {'file_path': '/home/alice/.ssh/config', 'old_string': SECRET, 'new_string': SECRET}})
    assert agent_status.public_entry(state)['summary'] == 'config'
    assert SECRET not in _public(state)


def test_permission_keeps_question_but_not_raw_input():
    state = agent_status.on_event('permission', {'session_id': 's1', 'cwd': '/repo', 'tool_name': 'Bash',
                                                 'tool_input': {'command': f'deploy --token {SECRET}'}})
    out = agent_status.public_entry(state)
    assert out['status'] == agent_status.WAITING
    assert out['summary'] == 'deploy'
    assert 'input' not in out and SECRET not in _public(state)


def test_unknown_tools_have_no_summary():
    state = agent_status.on_event('pre', {'session_id': 's1', 'cwd': '/repo', 'tool_name': 'apply_patch',
                                          'tool_input': {'patch': SECRET}}, agent='codex')
    assert agent_status.public_entry(state).get('summary') is None
    assert SECRET not in _public(state)


def test_new_prompt_clears_the_previous_turns_tool():
    agent_status.on_event('pre', {'session_id': 's1', 'cwd': '/repo', 'tool_name': 'Bash', 'tool_input': {'command': 'ls'}})
    agent_status.on_event('post', {'session_id': 's1'})
    state = agent_status.on_event('prompt', {'session_id': 's1', 'prompt': 'next'})
    out = agent_status.public_entry(state)
    assert out['status'] == agent_status.WORKING
    assert out.get('tool') is None and out.get('last_tool') is None and out.get('summary') is None


def test_subagent_permission_does_not_take_over_the_main_tool():
    agent_status.on_event('pre', {'session_id': 's1', 'cwd': '/repo', 'tool_name': 'Edit',
                                  'tool_input': {'file_path': '/repo/a.py'}})
    state = agent_status.on_event('permission', {'session_id': 's1', 'agent_id': 'a' + '1' * 16, 'tool_name': 'Bash',
                                                 'tool_use_id': 't9', 'tool_input': {'command': 'rm -rf build'}})
    assert state['status'] == agent_status.WAITING
    assert state['tool'] == 'Edit' and state['summary'] == 'a.py'
    state = agent_status.on_event('post', {'session_id': 's1', 'agent_id': 'a' + '1' * 16, 'tool_name': 'Bash', 'tool_use_id': 't9'})
    assert state['status'] == agent_status.WORKING
    assert state['tool'] == 'Edit'


@pytest.mark.parametrize('tool,tool_input,expected', [
    ('Bash', {'command': '  A=1 B=2 /usr/bin/git status'}, 'git'),
    ('Bash', {'command': ''}, None),
    ('Bash', {'command': 5}, None),
    ('Read', {'file_path': 'relative/x.md'}, 'x.md'),
    ('NotebookEdit', {'notebook_path': '/repo/n.ipynb'}, 'n.ipynb'),
    ('Write', {'file_path': '/repo/a\x1b[31m.py'}, 'a[31m.py'),
    ('Write', 'not-a-dict', None),
])
def test_tool_summary_edges(tool, tool_input, expected):
    assert agent_status.tool_summary(tool, tool_input, '/other') == expected
