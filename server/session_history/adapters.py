"""Bounded provider decoders; data never becomes a command or file authority."""
from __future__ import annotations

import json
import re

SECRET = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~-]+|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})")


def text(value):
    if isinstance(value, str):
        return SECRET.sub(lambda m: (m.group(1) or '') + '[REDACTED]', value)
    if isinstance(value, list):
        return '\n'.join(text(b.get('text', '')) for b in value if isinstance(b, dict)
                         and b.get('type') in ('text', 'input_text', 'output_text'))
    return ''


def clean_arguments(value):
    if isinstance(value,dict):
        return {k:clean_arguments(v) for k,v in value.items() if not re.search('password|secret|token|credential|api.?key',k,re.I)}
    if isinstance(value,list):
        return [clean_arguments(v) for v in value]
    return value


def records(record, provider):
    if not isinstance(record, dict):
        return []
    if provider == 'codex':
        p = record.get('payload')
        if record.get('type') != 'response_item' or not isinstance(p, dict):
            return []
        kind = p.get('type')
        if kind == 'message' and p.get('role') in ('user', 'assistant'):
            return [(p['role'], text(p.get('content')))]
        if kind in ('function_call', 'custom_tool_call'):
            args = p.get('arguments', p.get('input', ''))
            if isinstance(args,str):
                try:
                    args=json.dumps(clean_arguments(json.loads(args)),ensure_ascii=False)
                except (ValueError,RecursionError):
                    pass
            return [('tool_call', text(str(p.get('name', '')) + '\n' + (args if isinstance(args, str) else '')))]
        if kind in ('function_call_output', 'custom_tool_call_output'):
            return [('tool_result', text(p.get('output')))]
        return []
    message = record.get('message')
    if record.get('type') not in ('user', 'assistant') or not isinstance(message, dict):
        return []
    role, content = message.get('role', record['type']), message.get('content')
    if role not in ('user', 'assistant'):
        return []
    out = []
    if isinstance(content, str):
        return [(role, text(content))]
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        if b.get('type') == 'text':
            out.append((role, text(b.get('text'))))
        elif b.get('type') == 'tool_use':
            value = b.get('input', {})
            # Explicit secrets are never part of the public projection.
            out.append(('tool_call', text(str(b.get('name', '')) + '\n' + json.dumps(clean_arguments(value), ensure_ascii=False))))
        elif b.get('type') == 'tool_result':
            out.append(('tool_result', text(b.get('content'))))
    return out


def identity(data, provider, relative):
    native, cwd, parent, title = '', '', None, ''
    for raw in data.splitlines()[:256]:
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if not isinstance(row, dict):
            continue
        if provider == 'codex' and row.get('type') == 'session_meta' and isinstance(row.get('payload'), dict):
            p = row['payload']
            native, cwd = p.get('id', ''), p.get('cwd', '')
            source = p.get('source')
            if isinstance(source, dict):
                sub = source.get('subagent', {})
                spawn = sub.get('thread_spawn', {}) if isinstance(sub, dict) else {}
                parent = spawn.get('parent_thread_id') if isinstance(spawn, dict) else None
        elif provider == 'claude':
            native = native or row.get('sessionId', '')
            cwd = cwd or row.get('cwd', '')
            if row.get('type') in ('summary', 'custom-title', 'ai-title'):
                title = row.get('customTitle', row.get('title', row.get('summary', '')))
    # Claude child sessionId can be the parent's. Path-owned child ID stays distinct.
    parts = relative.split('/')
    if provider == 'claude' and 'subagents' in parts:
        index = parts.index('subagents')
        if index >= 2:
            parent = parts[index-1]
            native = parts[-1].removeprefix('agent-').removesuffix('.jsonl')
    if not isinstance(native, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', native):
        return None
    return {'native_id': native, 'cwd': cwd if isinstance(cwd, str) else '',
            'parent_native_id': parent if isinstance(parent, str) else None,
            'title': title[:128] if isinstance(title, str) else ''}
