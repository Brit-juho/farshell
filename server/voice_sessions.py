"""세션 정책·브라우저 재생 소유권·기기별 직렬 TTS 큐. 서버 자동 폴백은 없다."""
from __future__ import annotations
import asyncio
import base64
import logging
import os
import secrets
import shutil
import tempfile
import time
from collections import deque
from pathlib import Path

import auth
import voice_targets as targets
import voice_handler

log = logging.getLogger(__name__)
DEFAULT = {'mode': 'off', 'destination': 'browser', 'min_sec': 0, 'success_only': True,
           'only_unfocused': False, 'engine': 'auto'}


class VoiceCoordinator:
    def __init__(self):
        self.clients = {}
        self.leases = {}
        self.queues = {}
        self.workers = {}
        self.current = {}
        self.paused = {}
        self.seen = {}
        self.runs = {}
        self.diagnostics = {}
        self.pending = set()

    def defaults(self):
        data = auth._read_json(auth.STATE_DIR / 'voice-sessions.json', {})
        return {**DEFAULT, **data.get('_defaults', {})}

    def policy(self, key):
        data = auth._read_json(auth.STATE_DIR / 'voice-sessions.json', {})
        if key not in data:
            # 최초 확인 시 기본값을 복사한다. 이후 기본값 변경은 기존 세션에 적용하지 않는다.
            data[key] = {**DEFAULT, **data.get('_defaults', {})}
            auth._write_json_secure(auth.STATE_DIR / 'voice-sessions.json', data)
        return {**DEFAULT, **data[key]}

    @staticmethod
    def validated(cfg, changes):
        cfg = {**cfg, **{k: changes[k] for k in DEFAULT if k in changes}}
        if cfg['mode'] not in ('off', 'sound', 'speak') or cfg['destination'] not in ('browser', 'server'):
            raise ValueError('음성 설정 값이 잘못됐습니다')
        if cfg['engine'] not in ('auto', 'edge', 'local'):
            raise ValueError('음성 엔진 값이 잘못됐습니다')
        cfg['min_sec'] = max(0, min(600, int(cfg['min_sec'])))
        cfg['success_only'] = cfg['success_only'] is not False
        cfg['only_unfocused'] = cfg['only_unfocused'] is True
        return cfg

    def update_defaults(self, changes):
        cfg = self.validated(self.defaults(), changes)
        data = auth._read_json(auth.STATE_DIR / 'voice-sessions.json', {})
        data['_defaults'] = cfg
        auth._write_json_secure(auth.STATE_DIR / 'voice-sessions.json', data)
        return cfg

    async def update(self, target, changes, owner, screen, transfer=False):
        key = target['key']
        cfg = self.validated(self.policy(key), changes)
        client = (owner, screen)
        old = self.leases.get(key)
        if cfg['mode'] != 'off' and cfg['destination'] == 'browser':
            if client not in self.clients:
                raise ValueError('이 화면의 음성 연결을 기다려 주세요')
            if old and old[:2] != client and not transfer:
                raise ValueError('다른 화면에서 듣고 있습니다. 이 기기로 옮기기를 누르세요')
        await self.cancel_session(key)
        data = auth._read_json(auth.STATE_DIR / 'voice-sessions.json', {})
        data[key] = cfg
        auth._write_json_secure(auth.STATE_DIR / 'voice-sessions.json', data)
        if cfg['mode'] != 'off' and cfg['destination'] == 'browser':
            self.leases[key] = (*client, self.clients[client]['generation'])
        else:
            self.leases.pop(key, None)
        await self.publish_policy(key)
        return cfg

    def register(self, owner, screen, ws):
        key = (owner, screen)
        old = self.clients.get(key)
        generation = secrets.token_hex(12)
        self.clients[key] = {'ws': ws, 'generation': generation, 'visible': set()}
        # 같은 화면의 새 연결만 다시 소유한다. 이전 오디오는 재전송하지 않는다.
        for session, lease in list(self.leases.items()):
            if lease[:2] == key:
                self.leases[session] = (*key, generation)
        if old:
            asyncio.create_task(self.cancel_current(key))
            self.queues[key] = deque()
            asyncio.create_task(old['ws'].close(code=4000))
        return generation

    async def disconnect(self, owner, screen, generation):
        client = (owner, screen)
        if self.clients.get(client, {}).get('generation') != generation:
            return
        self.clients.pop(client, None)
        self.paused.pop(client, None)
        for key, lease in list(self.leases.items()):
            if lease[:2] == client:
                await self.cancel_session(key)
        worker = self.workers.pop(client, None)
        if worker:
            worker.cancel()
        self.queues.pop(client, None)

    async def publish_policy(self, key):
        cfg = self.policy(key)
        lease = self.leases.get(key)
        for client, entry in list(self.clients.items()):
            try:
                await entry['ws'].send_json({'type': 'voice_policy', 'key': key, 'policy': cfg,
                                            'owns': bool(lease and lease[:2] == client)})
            except Exception:
                pass

    async def cancel_session(self, key):
        for destination, queue in self.queues.items():
            self.queues[destination] = deque(x for x in queue if x['target']['key'] != key)
        for dest, job in list(self.current.items()):
            if job['target']['key'] == key:
                await self.cancel_current(dest)

    async def cancel_current(self, dest):
        job = self.current.get(dest)
        if not job:
            return
        job['cancelled'] = True
        proc = job.get('process')
        if proc and proc.returncode is None:
            proc.terminate()
        if isinstance(dest, tuple) and dest in self.clients:
            try:
                await self.clients[dest]['ws'].send_json({'type': 'voice_cancel', 'id': job['id']})
            except Exception:
                pass
        event = job.get('ack')
        if event:
            event.set()

    def allowed(self, job):
        if job.get('cancelled') or time.monotonic() - job['created'] > 120:
            return False
        if not targets.valid(job['target']):
            return False
        cfg = self.policy(job['target']['key'])
        if not job.get('test') and cfg['mode'] == 'off':
            return False
        if job['destination'] == 'server':
            return cfg['destination'] == 'server' and not (cfg['only_unfocused'] and any(
                job['target']['key'] in c['visible'] for c in self.clients.values()))
        lease = self.leases.get(job['target']['key'])
        return (cfg['destination'] == 'browser' and lease == job['lease']
                and self.clients.get(lease[:2], {}).get('generation') == lease[2]) if lease else False

    async def enqueue(self, target, completion, test=False):
        cfg = self.policy(target['key'])
        if not test and (cfg['mode'] == 'off' or (cfg['success_only'] and not completion['ok'])
                         or (completion['elapsed'] is not None and completion['elapsed'] < cfg['min_sec'])):
            return False
        dest = 'server' if cfg['destination'] == 'server' else None
        lease = self.leases.get(target['key'])
        if dest is None:
            if not lease:
                return False
            dest = lease[:2]
        job = {'id': secrets.token_hex(16), 'completion_id': completion['id'], 'target': target,
               'text': completion['text'], 'mode': 'speak' if test else cfg['mode'],
               'engine': cfg['engine'], 'destination': dest, 'lease': lease, 'test': test,
               'created': time.monotonic(), 'ack': asyncio.Event()}
        if not self.allowed(job):
            return False
        queue = self.queues.setdefault(dest, deque())
        if len(queue) >= 10:
            self.diagnostics[target['key']] = 'queue_full'
            return False
        queue.append(job)
        if dest not in self.workers or self.workers[dest].done():
            self.workers[dest] = asyncio.create_task(self._worker(dest))
        return True

    async def _worker(self, dest):
        try:
            while self.queues.get(dest):
                if dest in self.paused:
                    if time.monotonic() - self.paused[dest] > 120:
                        self.paused.pop(dest, None)
                    else:
                        await asyncio.sleep(.2)
                        continue
                job = self.queues[dest].popleft()
                self.current[dest] = job
                try:
                    if not self.allowed(job):
                        continue
                    if job['mode'] == 'sound':
                        audio = voice_handler.completion_sound()
                    else:
                        audio = await asyncio.wait_for(voice_handler.synthesize_result(job['text'], engine=job['engine']), 40)
                    if not self.allowed(job):
                        continue
                    if dest == 'server':
                        await self._play_server(job, audio)
                    else:
                        ws = self.clients[dest]['ws']
                        await ws.send_json({'type': 'voice_audio', 'id': job['id'],
                            'completion_id': job['completion_id'], 'key': job['target']['key'],
                            'name': job['target']['name'], 'generation': job['lease'][2],
                            'media_type': audio.media_type, 'audio': base64.b64encode(audio.data).decode()})
                        await asyncio.wait_for(job['ack'].wait(), 120)
                    self.diagnostics[job['target']['key']] = 'cancelled' if job.get('cancelled') else job.get('result', 'played')
                except asyncio.CancelledError:
                    await self.cancel_current(dest)
                    raise
                except Exception as e:
                    await self.cancel_current(dest)
                    self.diagnostics[job['target']['key']] = 'failed'
                    log.warning('음성 재생 실패: %s', type(e).__name__)
                finally:
                    self.current.pop(dest, None)
        finally:
            self.workers.pop(dest, None)

    async def _play_server(self, job, audio):
        player = shutil.which('afplay') or shutil.which('ffplay')
        if not player:
            raise RuntimeError('서버 오디오 재생 프로그램이 없습니다')
        fd, path = tempfile.mkstemp(suffix='.aiff' if audio.media_type == 'audio/aiff' else '.wav' if audio.media_type == 'audio/wav' else '.mp3')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(audio.data)
            args = [player, path] if Path(player).name == 'afplay' else [player, '-nodisp', '-autoexit', '-loglevel', 'quiet', path]
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            job['process'] = proc
            try:
                await asyncio.wait_for(proc.wait(), 60)
            finally:
                if proc.returncode is None:
                    proc.terminate()
                    await proc.wait()
        finally:
            Path(path).unlink(missing_ok=True)

    async def ack(self, client, msg):
        job = self.current.get(client)
        if job and job['id'] == msg.get('id'):
            status = msg.get('status')
            job['result'] = status
            if status in ('played', 'failed', 'cancelled'):
                job['ack'].set()

    async def barge(self, client, pause):
        if pause:
            self.paused[client] = time.monotonic()
            await self.cancel_current(client)
        else:
            self.paused.pop(client, None)

    def observe(self, body, session):
        """완료 전부터 실행 ID와 정확한 pane의 연결을 보존한다."""
        import pane_resolve
        tool = body.get('agent') or 'claude'
        payload = body.get('payload') or {}
        run = str(payload.get('session_id') or payload.get('thread_id') or '')
        if not run:
            return
        key = (tool, run)
        if body.get('event') == 'session_end':
            self.runs.pop(key, None)
            return
        if key in self.runs and targets.valid(self.runs[key]['target']):
            if body.get('event') in ('prompt', 'user_prompt_submit'):
                self.runs[key]['turn'] = secrets.token_hex(12)
            return
        pane = body.get('pane') if tool != 'codex' and pane_resolve.is_our_socket(body.get('tmux')) else None
        if not pane:
            matches = [p for p in __import__('tmux_runner').get_all_panes()
                       if payload.get('cwd') and p.path == payload['cwd']]
            if len(matches) == 1:
                pane = matches[0].pane_id
        if not pane:
            return
        try:
            target = targets.snapshot(session or '', pane)
            self.runs[key] = {'target': target, 'turn': secrets.token_hex(12)}
        except ValueError:
            pass

    def completion(self, body, session):
        self.observe(body, session)
        if body.get('event') != 'stop':
            return
        task = asyncio.create_task(self._complete(body))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def _complete(self, body):
        import voice_completion
        import agent_status
        payload = body.get('payload') or {}
        tool = body.get('agent') or 'claude'
        run = str(payload.get('session_id') or payload.get('thread_id') or '')
        binding = self.runs.get((tool, run))
        if not binding:
            return
        # Plan Stop 뒤 오버레이가 도착할 시간을 둔다. 새 턴도 낭독하지 않는다.
        turn = binding['turn']
        await asyncio.sleep(1)
        status = agent_status.get_state(run)
        if status and (status.get('status') != agent_status.DONE):
            return
        if self.runs.get((tool, run), {}).get('turn') != turn:
            return
        cfg = self.policy(binding['target']['key'])
        if cfg['mode'] == 'off':
            return
        completion = await asyncio.to_thread(voice_completion.extract, tool, payload)
        if not completion:
            self.diagnostics[binding['target']['key']] = 'no_final_response'
            return
        completion['id'] += ':' + turn
        now = time.monotonic()
        self.seen = {k: ts for k, ts in self.seen.items() if now - ts < 600}
        if completion['id'] in self.seen:
            return
        self.seen[completion['id']] = now
        await self.enqueue(binding['target'], completion)

    async def close(self):
        for dest in list(self.current):
            await self.cancel_current(dest)
        workers = list(self.workers.values()) + list(self.pending)
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self.clients.clear()
        self.leases.clear()
        self.queues.clear()


coordinator = VoiceCoordinator()
