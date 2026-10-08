"""세션·기기 격리, 큐·취소, 도구 응답 추출의 계약 검증."""
import asyncio
import json
from pathlib import Path
import pytest
import auth
import voice_completion as completion
import voice_targets as targets
import voice_handler
from voice_sessions import VoiceCoordinator


class WS:
    def __init__(self): self.messages = []
    async def send_json(self, msg): self.messages.append(msg)
    async def close(self, **kw): pass


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(targets, 'valid', lambda t: t.get('live', True))
    async def synth(text, **kw): return voice_handler.SynthesizedAudio(text.encode(), 'audio/mpeg', 'test')
    monkeypatch.setattr(voice_handler, 'synthesize_result', synth)
    return tmp_path


def target(key): return {'key': key, 'name': key, 'pane': '%1', 'live': True}
def done(key='one'): return {'id': key, 'text': key, 'ok': True, 'elapsed': 30}


async def settle():
    for _ in range(8): await asyncio.sleep(0)


def test_only_enabled_session_and_one_owner(env):
    async def run():
        c = VoiceCoordinator(); a, b = WS(), WS()
        c.register('phone', 'screen', a); c.register('mac', 'screen', b)
        await c.update(target('A'), {'mode': 'speak'}, 'phone', 'screen')
        assert await c.enqueue(target('A'), done())
        assert not await c.enqueue(target('B'), done('two'))
        await settle()
        assert len([m for m in a.messages if m['type'] == 'voice_audio']) == 1
        assert not any(m['type'] == 'voice_audio' for m in b.messages)
        with pytest.raises(ValueError):
            await c.update(target('A'), {'mode': 'speak'}, 'mac', 'screen')
        await c.close()
    asyncio.run(run())


def test_fifo_waits_for_ack_and_does_not_overlap(env):
    async def run():
        c = VoiceCoordinator(); ws = WS(); c.register('p', 's', ws)
        for key in ('A', 'B'):
            await c.update(target(key), {'mode': 'speak'}, 'p', 's')
            await c.enqueue(target(key), done(key))
        await settle()
        audio = [m for m in ws.messages if m['type'] == 'voice_audio']
        assert [m['key'] for m in audio] == ['A']
        await c.ack(('p','s'), {'id': audio[0]['id'], 'status': 'blocked'})
        await settle()
        assert len([m for m in ws.messages if m['type'] == 'voice_audio']) == 1
        await c.ack(('p','s'), {'id': audio[0]['id'], 'status': 'played'})
        await settle()
        assert [m['key'] for m in ws.messages if m['type'] == 'voice_audio'] == ['A', 'B']
        await c.close()
    asyncio.run(run())


def test_transfer_cancels_old_audio_and_disconnect_never_plays_server(env):
    async def run():
        c = VoiceCoordinator(); a,b = WS(),WS()
        gen = c.register('p','s',a); c.register('m','s',b)
        await c.update(target('A'), {'mode':'speak'}, 'p','s')
        await c.enqueue(target('A'), done()); await settle()
        await c.update(target('A'), {}, 'm','s', True)
        assert any(m['type'] == 'voice_cancel' for m in a.messages)
        assert not any(m['type'] == 'voice_audio' for m in b.messages)
        await c.disconnect('p','s',gen)
        assert 'server' not in c.queues
        await c.close()
    asyncio.run(run())


def test_deleted_target_and_disabled_session_suppress_late_synthesis(env, monkeypatch):
    async def run():
        gate = asyncio.Event()
        async def synth(*a, **kw):
            await gate.wait()
            return voice_handler.completion_sound()
        monkeypatch.setattr(voice_handler, 'synthesize_result', synth)
        c = VoiceCoordinator(); ws=WS(); c.register('p','s',ws)
        await c.update(target('A'), {'mode':'speak'}, 'p','s')
        await c.enqueue(target('A'), done()); await settle()
        await c.update(target('A'), {'mode':'off'}, 'p','s')
        gate.set(); await settle()
        assert not any(m['type'] == 'voice_audio' for m in ws.messages)
        assert not await c.enqueue({**target('A'),'live':False}, done())
        await c.close()
    asyncio.run(run())


def test_codex_response_item_and_payload(env, monkeypatch):
    root = env / '.codex'; records = root / 'sessions'; records.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME',str(root))
    path = records / 'test.jsonl'
    items = [
        {'type':'event_msg','timestamp':'2026-01-01T00:00:00Z','payload':{'type':'user_message','message':'질문'}},
        {'type':'response_item','payload':{'type':'message','role':'assistant','channel':'analysis','content':[{'type':'output_text','text':'분석은 낭독하지 않음'}]}},
        {'type':'response_item','payload':{'type':'message','role':'assistant','channel':'final','content':[{'type':'output_text','text':'완료했습니다'}]}},
    ]
    path.write_text('\n'.join(json.dumps(x) for x in items))
    result = completion.extract('codex',{'session_id':'run','transcript_path':str(path)})
    assert result['text'] == '완료했습니다'
    assert completion.extract('codex',{'session_id':'run','last_assistant_message':'직접 응답'})['text'] == '직접 응답'
    assert completion.extract('unknown',{'session_id':'run','last_assistant_message':'불명'}) is None
    assert completion.extract('codex',{'session_id':'run','last_assistant_message':'중단','stop_reason':'interrupted'}) is None


def test_transcript_path_cannot_escape_registered_root(env,monkeypatch):
    monkeypatch.setenv('CODEX_HOME', str(env / 'codex'))
    path=env/'secret.jsonl'; path.write_text('{"role":"assistant","content":"비밀"}')
    assert completion.extract('codex',{'session_id':'x','transcript_path':str(path)}) is None


def test_local_engine_never_uses_edge_and_auto_reports_fallback_mime(monkeypatch):
    import sys, types
    import shutil
    class Broken:
        def __init__(self,*a): pass
        async def stream(self):
            raise RuntimeError('offline')
            yield
    monkeypatch.setitem(sys.modules,'edge_tts',types.SimpleNamespace(Communicate=Broken))
    monkeypatch.setattr(shutil,'which',lambda n:'/usr/bin/say' if n=='say' else None)
    async def say(text): return b'RIFF-wav'
    monkeypatch.setattr(voice_handler,'_macos_say',say)
    monkeypatch.setattr(voice_handler,'_tts_engine','edge-tts')
    result=asyncio.run(voice_handler.synthesize_result('테스트'))
    assert result.media_type=='audio/wav' and result.engine=='macos-say'
    result=asyncio.run(voice_handler.synthesize_result('테스트',engine='local'))
    assert result.data==b'RIFF-wav'


def test_fixed_input_target_and_body_collision(env, monkeypatch):
    from routes import voice
    class Req:
        headers = {'x-voice-id': 'new', 'x-voice-target': 'token'}
        query_params = {'session_id': 'A'}
        cookies = {}
        async def body(self): return b'audio'
    async def stt(*a,**k): return '명령', {}
    monkeypatch.setattr(voice_handler,'transcribe_timed',stt)
    monkeypatch.setattr(targets,'get',lambda *a:target('A'))
    sent=[]
    monkeypatch.setattr('tmux_target.send_to_tmux',lambda pane,text:sent.append((pane,text)) or True)
    voice._voice_done.clear(); voice._voice_inflight.clear(); voice._voice_fingerprints.clear()
    result=asyncio.run(voice.voice_input(Req()))
    assert result['injected'] and sent==[('%1','명령')]
    class Collision(Req):
        async def body(self): return b'different'
    result=asyncio.run(voice.voice_input(Collision()))
    assert result.status_code==409 and len(sent)==1


def test_target_deleted_during_recognition_never_falls_back_to_other_pane(env,monkeypatch):
    from routes import voice
    class Req:
        headers={'x-voice-target':'gone'}; query_params={'session_id':'B'}; cookies={}
        async def body(self): return b'audio'
    async def stt(*a,**kw): return '결과',{}
    monkeypatch.setattr(voice_handler,'transcribe_timed',stt)
    monkeypatch.setattr(targets,'get',lambda *a:{**target('A'),'live':False})
    sent=[]; monkeypatch.setattr('tmux_target.send_to_tmux',lambda *a:sent.append(a))
    result=asyncio.run(voice.voice_input(Req()))
    assert not result['injected'] and result['text']=='결과' and result['error']
    assert not sent


def test_codex_observed_phase_and_task_complete_schema(env, monkeypatch):
    root=env/'codex'; records=root/'sessions'; records.mkdir(parents=True)
    monkeypatch.setenv('CODEX_HOME',str(root))
    path=records/'phase.jsonl'
    items=[
        {'type':'event_msg','payload':{'type':'task_started','turn_id':'turn-1'}},
        {'type':'response_item','payload':{'type':'message','role':'assistant','phase':'commentary','content':[{'type':'output_text','text':'作業中'}]}},
        {'type':'response_item','payload':{'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'최종 답변'}]}},
        {'type':'event_msg','payload':{'type':'task_complete','turn_id':'turn-1','last_agent_message':'최종 답변'}},
    ]
    path.write_text('\n'.join(json.dumps(x) for x in items))
    r=completion.extract('codex',{'session_id':'r','transcript_path':str(path)})
    assert r['text']=='최종 답변' and r['turn']=='turn-1'
    items.append({'type':'event_msg','payload':{'type':'task_started','turn_id':'turn-2'}})
    items.append({'type':'response_item','payload':{'type':'message','role':'assistant','phase':'commentary','content':[{'type':'output_text','text':'중간 업데이트'}]}})
    path.write_text('\n'.join(json.dumps(x) for x in items))
    assert completion.extract('codex',{'session_id':'r','transcript_path':str(path)}) is None


def test_http_bearer_and_websocket_query_have_same_principal():
    class HTTP:
        cookies={}; headers={'authorization':'Bearer same-token'}; query_params={}
    class WS:
        cookies={}; headers={}; query_params={'token':'same-token'}
    assert targets.principal(HTTP()) == targets.principal(WS())


def test_tmux_session_target_uses_exact_session_separator(monkeypatch):
    calls=[]
    monkeypatch.setattr('tmux_runner.run_text',lambda args: calls.append(args) or '$1\t'+'a'*32+'\t%2\t123\twork\n')
    result=targets.snapshot('work')
    assert calls[0][calls[0].index('-t')+1]=='=work:'
    assert result['pane']=='%2'


def test_defaults_are_copied_once_and_do_not_claim_browser(env):
    c = VoiceCoordinator()
    assert c.policy('existing')['mode'] == 'off'
    c.update_defaults({'mode': 'speak', 'engine': 'local', 'min_sec': 10})
    assert c.policy('existing')['mode'] == 'off'
    assert c.policy('new')['mode'] == 'speak'
    c.update_defaults({'mode': 'off'})
    assert c.policy('new')['mode'] == 'speak'
    assert not c.leases


def test_codex_success_after_an_aborted_turn(env, monkeypatch):
    records = env / 'custom-codex' / 'sessions'; records.mkdir(parents=True)
    monkeypatch.setenv('VT_CODEX_HOME', str(records.parent))
    path = records / 'turns.jsonl'
    def event(kind, **kw):
        return {'type': 'event_msg', 'payload': {'type': kind, **kw}}
    items = [event('task_started', turn_id='old'), event('turn_aborted'),
             event('task_started', turn_id='new'), event('task_complete', turn_id='new', last_agent_message='정상 완료')]
    path.write_text('\n'.join(json.dumps(x) for x in items))
    assert completion.extract('codex', {'session_id': 'run', 'transcript_path': str(path)})['text'] == '정상 완료'
    items.append(event('turn_aborted'))
    path.write_text('\n'.join(json.dumps(x) for x in items))
    assert completion.extract('codex', {'session_id': 'run', 'transcript_path': str(path)}) is None
