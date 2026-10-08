"""도구별 최종 응답 추출. 임의 도구의 기록을 Claude로 추측하지 않는다."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import fsguard

SUPPORTED = ('claude', 'codex')


def clean(text: str) -> str:
    text = text[-4000:]
    text = re.sub(r'```[\s\S]*?```', ' 코드 블록 생략. ', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'[*_`#]+', '', text).strip()
    return text[-200:]


def _text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return '\n'.join(p.get('text', '') for p in content
                         if isinstance(p, dict) and p.get('type') in ('text', 'output_text', 'input_text'))
    return ''


def extract(tool: str, payload: dict) -> dict | None:
    if tool not in SUPPORTED:
        return None
    outcome = payload.get('stop_reason', '')
    if outcome in ('interrupted', 'cancelled', 'aborted', 'permission', 'plan'):
        return None
    text = payload.get('last_assistant_message') or ''
    stamp = None
    aborted = False
    turn = str(payload.get('turn_id') or '')
    ok = not payload.get('isApiErrorMessage', False)
    path = payload.get('transcript_path')
    if path:
        resolved = fsguard.voice_transcript_path(tool, path)
        if resolved:
            # 읽기·메모리 상한. 너무 큰 파일은 마지막 4MiB의 완전한 줄만 읽는다.
            with resolved.open('rb') as f:
                size = resolved.stat().st_size
                f.seek(max(0, size - 4 * 1024 * 1024))
                if size > 4 * 1024 * 1024:
                    f.readline()
                lines = f.read(4 * 1024 * 1024).decode('utf-8', errors='replace').splitlines()
            for line in lines:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get('isSidechain'):
                    continue
                msg = obj.get('message') or obj
                if tool == 'codex':
                    if obj.get('type') == 'response_item':
                        msg = obj.get('payload', {})
                        if msg.get('type') != 'message':
                            continue
                    elif obj.get('type') == 'event_msg':
                        ev = obj.get('payload', {})
                        if ev.get('type') == 'task_started':
                            text = ''; aborted = False; turn = str(ev.get('turn_id') or turn)
                            stamp = obj.get('timestamp')
                            continue
                        if ev.get('type') in ('turn_aborted', 'task_aborted'):
                            aborted = True; text = ''
                            continue
                        if ev.get('type') == 'user_message':
                            msg = {'role': 'user', 'content': ev.get('message', '')}
                        elif ev.get('type') == 'task_complete':
                            text = ev.get('last_agent_message') or text
                            turn = str(ev.get('turn_id') or turn)
                            continue
                        else:
                            continue
                    else:
                        continue
                role = msg.get('role') or obj.get('type')
                if role == 'user' and _text(msg.get('content')) and not obj.get('isMeta'):
                    stamp = obj.get('timestamp')
                    turn = str(stamp or turn)
                    text = ''; aborted = False
                elif role == 'assistant' and msg.get('channel') not in ('analysis', 'commentary') and msg.get('phase') not in ('commentary', 'analysis'):
                    candidate = _text(msg.get('content'))
                    if candidate:
                        text = candidate
                        ok = not obj.get('isApiErrorMessage', False)
    if aborted:
        return None
    # payload는 transcript보다 명시적인 최종 결과다.
    text = payload.get('last_assistant_message') or text
    if not isinstance(text, str) or not text.strip():
        return None
    elapsed = None
    if stamp:
        try:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp.replace('Z', '+00:00'))).total_seconds()
        except (ValueError, TypeError):
            pass
    sid = str(payload.get('session_id') or payload.get('thread_id') or '')
    if not sid:
        return None
    digest = hashlib.sha256((tool + '\0' + sid + '\0' + turn + '\0' + text).encode()).hexdigest()
    return {'id': digest, 'tool': tool, 'run': sid, 'turn': turn, 'text': clean(text),
            'ok': ok, 'elapsed': elapsed}
