"""Trimming rules for stored conversation transcripts — pure functions, no I/O.

These describe what a *lean copy* of a conversation could leave out. Nothing here is applied to stored data yet:
`compact_preview.py` only measures what each rule would save. The rules follow what other tools do to Claude Code
sessions (Cozempic, Claude Code Organizer: drop the duplicated tool-result envelope, swap old images for a note,
shorten huge tool output) and what Anthropic's "context editing" does for the model's view (keep a note that something
was removed), measured against our own files.

A rule only rewrites a line when it changes something; every other line keeps its exact bytes.

Rules (named by what they remove):
  tur    Claude `toolUseResult`: a structured second copy of a tool result that the message already carries. Dropped only
         when a stretch of the message's own tool_result text is found inside it (so structured data that the text does
         not carry, e.g. an edit's diff, is kept).
  img    Embedded base64 images (an `image` block, or a `data:image/...;base64,` string) become a short text note
         `[이미지 생략: image/png, 약 123KB]`. An image block turns into a text block, so the line stays valid JSON that a
         resume can still read.
  big    A single tool output larger than BIG_BYTES keeps its first and last part and a note in between.
Counted but not recommended (they can matter to a resume): `thinking` (reasoning text and its signature).
"""
from __future__ import annotations

import json

IMG_MIN = 2000          # a base64 payload shorter than this is not worth replacing
BIG_BYTES = 32 * 1024   # tool output larger than this is shortened by the `big` rule
BIG_HEAD = 8 * 1024
BIG_TAIL = 2 * 1024

STAT_KEYS = ('tur_dropped_bytes', 'tur_kept_bytes', 'img_count', 'img_bytes', 'big_count', 'big_saved_bytes',
             'thinking_count', 'thinking_bytes')


def new_stats():
    return dict.fromkeys(STAT_KEYS, 0)


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _size(value):
    return len(_dumps(value).encode())


def _note(media, nbytes):
    return f'[이미지 생략: {media or "image"}, 약 {max(1, nbytes // 1024)}KB]'


def _texts(value, out):
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _texts(item, out)
    elif isinstance(value, list):
        for item in value:
            _texts(item, out)


def _shorten(text):
    cut = len(text) - BIG_HEAD - BIG_TAIL
    return f'{text[:BIG_HEAD]}\n[… 도구 출력 {cut:,}글자 생략 …]\n{text[-BIG_TAIL:]}'


# ── Claude ───────────────────────────────────────────────────────────────────────────────────────────────────

def _claude_images(blocks, stats):
    """Replace image blocks in a content list (also inside tool_result blocks). True if anything changed."""
    changed = False
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        source = block.get('source')
        if (block.get('type') == 'image' and isinstance(source, dict) and source.get('type') == 'base64'
                and len(source.get('data') or '') >= IMG_MIN):
            n = len(source['data'])
            blocks[index] = {'type': 'text', 'text': _note(source.get('media_type'), n * 3 // 4)}
            stats['img_count'] += 1
            stats['img_bytes'] += n
            changed = True
        elif block.get('type') == 'tool_result' and isinstance(block.get('content'), list):
            changed |= _claude_images(block['content'], stats)
    return changed


def _tool_result_text(content):
    out = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get('type') == 'tool_result':
            _texts(block.get('content'), out)
    return ''.join(out)


def _is_duplicate(result_text, tur_json):
    """Does the structured copy carry the tool_result text? A middle stretch of the text must appear in it."""
    if len(result_text) < 200:
        return False
    middle = len(result_text) // 2
    probe = result_text[middle:middle + 80]
    return probe in tur_json or probe in tur_json.replace('\\n', '\n')


def _claude_big(blocks, stats):
    changed = False
    for block in blocks:
        if not (isinstance(block, dict) and block.get('type') == 'tool_result'):
            continue
        content = block.get('content')
        if isinstance(content, str) and len(content) > BIG_BYTES:
            block['content'] = _shorten(content)
            stats['big_count'] += 1
            stats['big_saved_bytes'] += len(content) - len(block['content'])
            changed = True
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get('type') == 'text' and len(part.get('text') or '') > BIG_BYTES:
                    before = len(part['text'])
                    part['text'] = _shorten(part['text'])
                    stats['big_count'] += 1
                    stats['big_saved_bytes'] += before - len(part['text'])
                    changed = True
    return changed


def claude_line(line, rules, stats):
    """One transcript line (bytes) → the line with `rules` applied (same bytes when nothing applies)."""
    if not any(rule in line for rule in (b'toolUseResult', b'"image"', b'tool_result', b'"thinking"')):
        return line
    try:
        record = json.loads(line)
    except ValueError:
        return line
    if not isinstance(record, dict):
        return line
    message = record.get('message') if isinstance(record.get('message'), dict) else None
    blocks = message.get('content') if message and isinstance(message.get('content'), list) else None
    changed = False
    if blocks:
        for block in blocks:
            if isinstance(block, dict) and block.get('type') == 'thinking':
                stats['thinking_count'] += 1
                stats['thinking_bytes'] += _size(block)
    if 'tur' in rules and 'toolUseResult' in record:
        tur_json = _dumps(record['toolUseResult'])
        if blocks and _is_duplicate(_tool_result_text(blocks), tur_json):
            stats['tur_dropped_bytes'] += len(tur_json.encode())
            del record['toolUseResult']
            changed = True
        else:
            stats['tur_kept_bytes'] += len(tur_json.encode())
    if 'img' in rules and blocks:
        changed |= _claude_images(blocks, stats)
    if 'big' in rules and blocks:
        changed |= _claude_big(blocks, stats)
    return (_dumps(record) + '\n').encode() if changed else line


# ── Codex ────────────────────────────────────────────────────────────────────────────────────────────────────

def _codex_images(value, stats):
    """Replace data:image URLs anywhere in the record. Returns the (possibly new) value and whether it changed."""
    if isinstance(value, str):
        if value.startswith('data:image/') and ';base64,' in value[:80] and len(value) >= IMG_MIN:
            media = value[5:value.index(';')]
            stats['img_count'] += 1
            stats['img_bytes'] += len(value)
            return _note(media, len(value) * 3 // 4), True
        return value, False
    changed = False
    if isinstance(value, dict):
        for key, item in list(value.items()):
            new, did = _codex_images(item, stats)
            if did:
                value[key] = new
                changed = True
    elif isinstance(value, list):
        for index, item in enumerate(value):
            new, did = _codex_images(item, stats)
            if did:
                value[index] = new
                changed = True
    return value, changed


def codex_line(line, rules, stats):
    if b'data:image/' not in line and b'"output"' not in line:
        return line
    try:
        record = json.loads(line)
    except ValueError:
        return line
    if not isinstance(record, dict):
        return line
    changed = False
    if 'img' in rules and b'data:image/' in line:
        _, changed = _codex_images(record, stats)
    payload = record.get('payload') if isinstance(record.get('payload'), dict) else None
    if 'big' in rules and payload and isinstance(payload.get('output'), str) and len(payload['output']) > BIG_BYTES:
        before = len(payload['output'])
        payload['output'] = _shorten(payload['output'])
        stats['big_count'] += 1
        stats['big_saved_bytes'] += before - len(payload['output'])
        changed = True
    return (_dumps(record) + '\n').encode() if changed else line


def trim_line(provider, line, rules, stats):
    return (claude_line if provider == 'claude' else codex_line)(line, rules, stats)
