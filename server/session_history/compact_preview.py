"""Read-only preview of what trimming the stored conversations would save — `fsh archive-compact preview`.

For every stored conversation file (its latest saved version) this rebuilds the transcript from the chunks, applies each
trimming rule of `compact_rules.py` in memory and measures the size it would have on disk (compressed in 256 KB pieces
like the archive does). It reports the saving per rule, per provider, per "idle for N days" group, and which
conversations shrink the most. It never writes anything: not the database, not a chunk, not a file.

    python -m session_history.compact_preview [--sample N] [--idle-days 0,7,30,90] [--top K] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time

import zstandard

from session_catalog.db import directory
from . import archive, compact_rules

PIECE = 256 * 1024
# (name, rules applied). The first is what is stored today.
VARIANTS = (
    ('now', ()),
    ('tur', ('tur',)),
    ('img', ('img',)),
    ('tur+img', ('tur', 'img')),
    ('tur+img+big', ('tur', 'img', 'big')),
)
LABELS = {
    'now': '지금(정리 안 함)',
    'tur': '중복 도구 결과 제거',
    'img': '이미지 제거',
    'tur+img': '중복 도구 결과 + 이미지 제거',
    'tur+img+big': '위 둘 + 큰 도구 출력 줄이기',
}

QUERY = """
SELECT f.id AS file_id, f.state AS file_state, c.id AS cid, c.title AS title,
       COALESCE(json_extract(c.detail, '$.last'), c.updated) AS last_active, m.detail AS detail
FROM source_files f
JOIN manifests m ON m.id = (SELECT id FROM manifests WHERE file_id=f.id ORDER BY created DESC, id DESC LIMIT 1)
LEFT JOIN conversations c ON c.id = f.conversation_id
"""


def _open_readonly():
    db = sqlite3.connect(f'file:{directory() / "catalog.sqlite3"}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def _lines(detail):
    """The transcript's lines, rebuilt from its chunks (a line may span chunks)."""
    carry = b''
    for part in detail['chunks']:
        data = carry + archive.read_chunk(part['digest'])
        cut = data.rfind(b'\n') + 1
        carry = data[cut:]
        if cut:
            yield from data[:cut].splitlines(keepends=True)
    if carry:
        yield carry


def _packed_size(lines, compressor):
    """What the lines would take on disk: compressed in PIECE-sized pieces, as the archive stores them."""
    total, buffer = 0, bytearray()
    for line in lines:
        buffer += line
        while len(buffer) >= PIECE:
            total += len(compressor.compress(bytes(buffer[:PIECE])))
            del buffer[:PIECE]
    if buffer:
        total += len(compressor.compress(bytes(buffer)))
    return total


def measure_file(provider, lines):
    """Size of one transcript under each variant, plus what the full rule set would remove."""
    compressor = zstandard.ZstdCompressor(level=archive.PACK_LEVEL)
    out = {'raw': sum(len(line) for line in lines)}
    stats = compact_rules.new_stats()
    for name, rules in VARIANTS:
        scratch = stats if name == VARIANTS[-1][0] else compact_rules.new_stats()
        trimmed = (compact_rules.trim_line(provider, line, rules, scratch) for line in lines) if rules else lines
        out[name] = _packed_size(trimmed, compressor)
    out['stats'] = stats
    return out


def scan(*, sample=None, progress=None, now=None):
    now = now or time.time()
    db = _open_readonly()
    rows = [dict(r) for r in db.execute(QUERY)]
    db.close()
    skipped = {'not_transcript': 0, 'not_saved': 0, 'already_compressed': 0, 'unreadable': 0}
    todo = []
    for row in rows:
        detail = json.loads(row['detail'])
        if detail.get('capture_kind') != 'transcript':
            skipped['not_transcript'] += 1
        elif detail.get('state') != 'saved':
            skipped['not_saved'] += 1
        elif detail.get('compressed'):
            skipped['already_compressed'] += 1
        else:
            todo.append((row, detail))
    if sample and sample < len(todo):
        random.Random(1).shuffle(todo)
        todo = todo[:sample]
    files = []
    for index, (row, detail) in enumerate(todo):
        provider = detail.get('provider')
        try:
            lines = list(_lines(detail))
        except (OSError, archive.Refused):
            skipped['unreadable'] += 1
            continue
        result = measure_file(provider, lines)
        result.update(provider=provider, title=row['title'] or os.path.basename(detail.get('relative_path', '')),
                      idle_days=max(0.0, (now - (row['last_active'] or now)) / 86400),
                      original_kept=row['file_state'] != 'source_missing')
        files.append(result)
        if progress and (index + 1) % 50 == 0:
            progress(index + 1, len(todo))
    return {'files': files, 'skipped': skipped, 'total_files': len(rows)}


def summarize(scan_result, idle_days=(0, 7, 30, 90), top=10):
    files = scan_result['files']
    out = {'skipped': scan_result['skipped'], 'providers': {}, 'top': [], 'removed': {}}
    for provider in ('claude', 'codex'):
        mine = [f for f in files if f['provider'] == provider]
        if not mine:
            continue
        block = {'files': len(mine), 'raw': sum(f['raw'] for f in mine),
                 'variants': {name: sum(f[name] for f in mine) for name, _ in VARIANTS},
                 'original_missing': sum(1 for f in mine if not f['original_kept']), 'by_idle': {}}
        for days in idle_days:
            group = [f for f in mine if f['idle_days'] >= days and f['original_kept']]
            block['by_idle'][days] = {'files': len(group), 'now': sum(f['now'] for f in group),
                                      **{name: sum(f[name] for f in group) for name, _ in VARIANTS[1:]}}
        stats = compact_rules.new_stats()
        for f in mine:
            for key, value in f['stats'].items():
                stats[key] += value
        out['providers'][provider] = block
        out['removed'][provider] = stats
    ranked = sorted(files, key=lambda f: f['now'] - f['tur+img'], reverse=True)[:top]
    out['top'] = [{'title': f['title'], 'provider': f['provider'], 'idle_days': int(f['idle_days']),
                   'now': f['now'], 'after': f['tur+img'], 'saved': f['now'] - f['tur+img']} for f in ranked]
    return out


def _mb(n):
    return f'{n / 1024 ** 2:,.0f}MB' if n >= 1024 ** 2 or n == 0 else f'{max(1, n // 1024):,}KB'


def _pct(a, b):
    return f'{a / b:.0%}' if b else '-'


def render(summary):
    lines = ['정리 미리보기 (읽기만 했습니다 — 저장된 대화는 하나도 바뀌지 않았습니다)', '']
    names = {'claude': 'Claude', 'codex': 'Codex'}
    for provider, block in summary['providers'].items():
        now = block['variants']['now']
        lines.append(f"[{names[provider]}] 대화 파일 {block['files']:,}개, 원본 크기 {_mb(block['raw'])}, 지금 저장 크기(압축 후) {_mb(now)}")
        for name, _rules in VARIANTS:
            size = block['variants'][name]
            gap = '' if name == 'now' else f"   {_mb(now - size)} 줄어듦"
            lines.append(f"  {LABELS[name]:<24}{_mb(size):>8} ({_pct(size, now)}){gap}")
        stats = summary['removed'][provider]
        lines.append('  없어지는 내용:')
        if stats['img_count']:
            lines.append(f"    이미지 {stats['img_count']:,}개 (약 {_mb(stats['img_bytes'])})")
        if stats['tur_dropped_bytes'] or stats['tur_kept_bytes']:
            lines.append(f"    도구 결과 중복 사본 약 {_mb(stats['tur_dropped_bytes'])} (겹치지 않아 남기는 것 {_mb(stats['tur_kept_bytes'])})")
        if stats['big_count']:
            lines.append(f"    32KB 넘는 도구 출력 {stats['big_count']:,}건을 줄임 (원본 기준 약 {_mb(stats['big_saved_bytes'])})")
        if stats['thinking_count']:
            lines.append(f"    (참고) AI 생각 과정 {stats['thinking_count']:,}건, 약 {_mb(stats['thinking_bytes'])} — 이어서 대화할 때 필요할 수 있어 정리 대상에서 제외")
        lines.append(f"  원본이 디스크에서 사라진 대화 {block['original_missing']}개는 정리 대상에서 뺐습니다(보관본이 유일한 사본이라서)")
        lines.append('  마지막 활동 후 경과일별로 「중복 + 이미지」를 정리하면 (원본이 남아 있는 대화만):')
        for days, group in block['by_idle'].items():
            label = '전체' if int(days) == 0 else f'{days}일 이상'
            lines.append(f"    {label:<8} 대화 {group['files']:>4}개   지금 {_mb(group['now']):>7} → {_mb(group['tur+img']):>7}   ({_mb(group['now'] - group['tur+img'])} 절약)")
        lines.append('')
    if summary['top']:
        lines.append('가장 크게 줄어드는 대화(중복 + 이미지):')
        for item in summary['top']:
            lines.append(f"  {_mb(item['saved']):>7} 절약  {item['provider']:<6} {item['idle_days']:>3}일 전  {item['title'][:44]}")
        lines.append('')
    s = summary['skipped']
    lines.append(f"제외: 이미 압축된 원본 {s['already_compressed']}개, 저장이 덜 끝난 것 {s['not_saved']}개, 대화가 아닌 파일 {s['not_transcript']}개, 읽지 못한 것 {s['unreadable']}개")
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='fsh archive-compact', description='대화 사본 정리 미리보기(읽기 전용)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('preview')
    p.add_argument('--sample', type=int, default=None, help='N개 파일만 표본으로 재 보기(빠름)')
    p.add_argument('--idle-days', default='0,7,30,90', help='마지막 활동 후 경과일 기준(쉼표로 구분)')
    p.add_argument('--top', type=int, default=10)
    p.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    try:
        os.nice(10)
    except OSError:
        pass
    try:
        days = tuple(sorted({int(x) for x in args.idle_days.split(',') if x.strip()}))
    except ValueError:
        print('--idle-days 는 숫자를 쉼표로 구분해 주세요(예: 0,7,30,90)', file=sys.stderr)
        return 2

    def progress(done, total):
        print(f'  {done:,}/{total:,} 파일 확인', file=sys.stderr, flush=True)

    try:
        summary = summarize(scan(sample=args.sample, progress=None if args.json else progress), days, args.top)
    except KeyboardInterrupt:
        print('중단했습니다. 아무것도 바꾸지 않았습니다.', file=sys.stderr)
        return 130
    print(json.dumps(summary, ensure_ascii=False) if args.json else render(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
