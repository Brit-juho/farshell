// 파일 본문 받기 — `/api/fs/file` 2판(구간·tail·rev·304·인코딩, API.md)을 쓰는 순수 로직. DOM을 만지지 않는다.
// 그리기는 file.js가 한다. 계약은 파일 개편 계획 4.1~4.3과 docs/ref/files.md 「Reading files」.
//
// 규칙
// - 처음 열 때 1MB 구간을 이어 받아 앞 4MB까지 연다(OPEN_BUDGET). 더 크면 「더 보기」가 4MB씩 더 받는다.
//   받은 양의 상한은 MAX_LOADED — 그 뒤는 「끝부분 보기」(tail)로 본다.
// - 이어 받는 구간은 첫 응답의 인코딩을 고정해 보낸다(`auto`는 응답마다 판정하므로 구간마다 달라질 수 있다).
//   첫 응답이 `unknown`이면 서버가 UTF-8(replace)로 풀었으므로 `utf-8`로 고정한다.
// - 이어 받는 구간은 `expected_rev`를 붙인다. 409(그 사이 파일이 바뀜)면 처음부터 다시 받고, 3번 연속이면
//   「계속 바뀌는 중」으로 멈춘다(ChangingError).
// - 다시 읽기는 첫 구간만 조건부로 묻는다(ETag는 rev+인코딩+구간). 304면 파일 전체가 그대로다.
// - 받아 둔 양이 큰 칸(INCREMENTAL_MIN 초과)의 다시 읽기는 받은 끝 바로 앞의 작은 창(PROBE_BYTES)만 묻는다.
//   그 창이 화면의 끝과 같고 파일이 커지기만 했으면(growthFrom) 받은 끝에서부터만 이어 받는다 — 자라는 로그를
//   볼 때 바뀔 때마다 몇 MB를 다시 받지 않게. 아니면 처음부터 다시 읽는다.
import { vtFetchConditional } from '../../core/api.js';
import { relayHost } from '../../core/host-route.js';

export const CHUNK = 1024 * 1024;           // 서버 구간 상한(1,048,576)
export const OPEN_BUDGET = 4 * CHUNK;        // 처음 여는 양, 「더 보기」 한 번의 양
export const MAX_LOADED = 32 * CHUNK;        // 한 뷰어가 받아 두는 상한(폰 메모리)
export const TAIL_BYTES = CHUNK;             // 끝부분 보기의 양
export const MAX_CHANGED = 3;                // 연속 409 허용 횟수
export const PROBE_BYTES = 8 * 1024;         // 커지기만 했는지 볼 때 묻는 창(받은 끝 바로 앞)
export const INCREMENTAL_MIN = 256 * 1024;   // 이보다 많이 받아 둔 칸만 창으로 묻는다(작으면 통째 다시 읽기가 싸다)

/** 「파일이 계속 바뀌는 중」 — 409가 MAX_CHANGED번 연속. */
export class ChangingError extends Error {
  constructor() {
    super('changing');
    this.name = 'ChangingError';
    this.changing = true;
  }
}

/**
 * 파일을 어느 호스트에서 읽을지(계획 3.5 — 터미널 링크는 **그 세션의 호스트**로 연다). host가 없거나 `local`이거나
 * 지금 화면이 보고 있는 호스트면 평소 경로(vtFetch가 지금 호스트로 중계한다). 다른 호스트면 그 호스트 중계 경로.
 */
export function hostApiPath(path, host) {
  if (!host || host === 'local' || host === relayHost()) return path;
  return `/api/hosts/${encodeURIComponent(host)}/relay${path}`;
}

/** 지금 화면 호스트와 다른 호스트인가 — 그런 파일은 감시·공유·페인 열기를 하지 않는다(file.js·overlay.js). */
export function isCrossHost(host) {
  return !!host && host !== 'local' && host !== relayHost();
}

function _url(path, host, params) {
  const q = new URLSearchParams({ path });
  for (const [k, v] of Object.entries(params)) if (v != null) q.set(k, String(v));
  return hostApiPath(`/api/fs/file?${q.toString()}`, host);
}

/**
 * 이어 붙일 조각 정리. CRLF·CR을 LF로 바꾼다(VTDiffLex.normalize와 같은 규칙). 구간이 `\r`에서 끝나고 다음 구간이
 * `\n`으로 시작하면 줄이 하나 더 생기므로, 끝의 `\r`은 다음 조각으로 넘긴다(`carry`).
 */
export function joinPiece(carry, content, eof) {
  let s = (carry || '') + String(content == null ? '' : content);
  let next = '';
  if (!eof && s.endsWith('\r')) { next = '\r'; s = s.slice(0, -1); }
  return { text: s.replace(/\r\n?/g, '\n'), carry: next };
}

/** 줄 끝 종류. `crlf`·`lf`·`cr`(옛 맥) 중 둘 이상이면 섞인 파일이다(D9). */
export function eolKinds(raw, into = { crlf: false, lf: false, cr: false }) {
  const s = String(raw || '');
  if (!into.crlf && s.includes('\r\n')) into.crlf = true;
  if (!into.lf && /(^|[^\r])\n/.test(s)) into.lf = true;
  if (!into.cr && /\r(?!\n)/.test(s.endsWith('\r') ? s.slice(0, -1) : s)) into.cr = true;
  return into;
}

export function mixedEol(k) {
  return [k.crlf, k.lf, k.cr].filter(Boolean).length > 1;
}

function _pin(d, requested) {
  if (requested && requested !== 'auto') return requested;
  return !d.encoding || d.encoding === 'unknown' ? 'utf-8' : d.encoding;
}

function _isChanged(e) {
  return !!e && e.status === 409;
}

// 첫 응답 모양을 뷰어가 쓰는 메타로.
function _meta(d, mode, requested) {
  return {
    mode,
    size: d.size,
    rev: d.rev || null,
    image: !!d.image,
    binary: !!d.binary,
    mime: d.mime,
    tooLarge: !!d.too_large,
    requested: requested || 'auto',
    encoding: d.encoding || null,
    pinned: _pin(d, requested),
    confident: d.encoding_confident !== false,
    reason: d.encoding_reason || null,
    suggested: d.suggested || null,
    eof: !!d.eof,
    nextOffset: d.next_offset == null ? null : d.next_offset,
    lineTruncated: !!d.line_truncated,
    eol: eolKinds(d.content),
  };
}

// 같은 rev로 이어 받는다. 조각을 붙일 때마다 onAppend(text)를 부른다(첫 그리기를 기다리지 않게).
async function _continue(fetchFn, path, host, meta, carry, target, onAppend) {
  let c = carry;
  while (!meta.eof && meta.nextOffset != null && meta.nextOffset < target) {
    const r = await fetchFn(_url(path, host, {
      offset: meta.nextOffset, length: CHUNK, encoding: meta.pinned, expected_rev: meta.rev,
    }));
    const d = r.data || {};
    if (d.encoding_confident === false) {
      meta.confident = false;
      meta.reason = meta.reason || d.encoding_reason || 'decode_errors';
    }
    eolKinds((c || '') + (d.content || ''), meta.eol);   // 넘겨받은 `\r` 뒤의 `\n`을 LF로 세지 않게
    meta.eof = !!d.eof;
    meta.nextOffset = d.next_offset == null ? null : d.next_offset;
    meta.lineTruncated = !!d.line_truncated;
    const piece = joinPiece(c, d.content, meta.eof);
    c = piece.carry;
    if (piece.text) await onAppend(piece.text);
  }
  return c;
}

/**
 * 앞에서부터 `target` 바이트까지 받는다(처음 열기·다시 읽기 공용).
 * - `etag`가 있으면 첫 구간을 조건부로 묻고, 304면 `{notModified: true}`.
 * - 첫 구간이 오면 `onFirst(text, meta)`, 이어지는 구간마다 `onAppend(text)`. 그 사이 파일이 바뀌어(409) 처음부터 다시
 *   받게 되면 `onRestart()`를 먼저 부른다(호출부는 받은 내용을 버린다).
 * - 돌려주는 것: `{meta, etag, carry}` — meta.loaded는 받은 바이트 끝(다음 「더 보기」 시작점).
 */
export async function loadHead({ path, host, encoding, target = OPEN_BUDGET, etag = null,
  onFirst, onAppend, onRestart, fetchFn = vtFetchConditional }) {
  const requested = encoding || 'auto';
  let changed = 0;
  let first = true;
  for (;;) {
    let r;
    try {
      r = await fetchFn(_url(path, host, { offset: 0, length: CHUNK, encoding: requested }), first && etag ? { etag } : {});
    } catch (e) {
      if (!_isChanged(e)) throw e;
      r = null;
    }
    if (r && r.status === 304) return { notModified: true, etag: r.etag };
    if (r) {
      const d = r.data || {};
      const meta = _meta(d, 'head', requested);
      if (meta.image || meta.binary) return { meta, etag: r.etag };
      const piece = joinPiece('', d.content, meta.eof);
      await onFirst(piece.text, meta);
      try {
        const carry = await _continue(fetchFn, path, host, meta, piece.carry, Math.max(target, CHUNK), onAppend);
        meta.loaded = meta.eof ? meta.size : meta.nextOffset;
        return { meta, etag: r.etag, carry };
      } catch (e) {
        if (!_isChanged(e)) throw e;
      }
    }
    changed += 1;
    first = false;
    if (changed >= MAX_CHANGED) throw new ChangingError();
    if (onRestart) await onRestart();
  }
}

/**
 * 「더 보기」 — 받은 끝(meta.nextOffset)부터 OPEN_BUDGET만큼 더(상한 MAX_LOADED). 409면 Changed를 그대로 던진다
 * (호출부가 처음부터 다시 연다 — 붙여 둔 앞부분이 이미 옛 판이다). `target`을 주면 그 바이트까지(커진 끝 이어 받기).
 */
export async function loadMore({ path, host, meta, carry, onAppend, target = null, fetchFn = vtFetchConditional }) {
  const until = target != null ? target : Math.min(MAX_LOADED, (meta.nextOffset || 0) + OPEN_BUDGET);
  const c = await _continue(fetchFn, path, host, meta, carry, until, onAppend);
  meta.loaded = meta.eof ? meta.size : meta.nextOffset;
  return c;
}

/**
 * 받은 끝(`end`) 바로 앞의 작은 창을 묻는다 — 창 [max(start, end-PROBE_BYTES), end). 인코딩은 고정한 것(meta.pinned).
 * `etag`가 같은 창의 것이면 조건부(304면 `{notModified: true}`). 돌려주는 것: `{offset, length, etag, data}`.
 */
export async function loadProbe({ path, host, meta, start, end, etag = null, fetchFn = vtFetchConditional }) {
  const offset = Math.max(start, end - PROBE_BYTES);
  const length = Math.max(1, end - offset);
  const r = await fetchFn(_url(path, host, { offset, length, encoding: meta.pinned }), etag ? { etag } : {});
  if (r.status === 304) return { notModified: true, offset, length, etag: r.etag };
  return { offset, length, etag: r.etag, data: r.data || {} };
}

const _devIno = (rev) => {
  const m = /^(\d+):(\d+):\d+:\d+$/.exec(String(rev || ''));
  return m ? `${m[1]}:${m[2]}` : null;
};

function _utf8Len(s) {
  return new TextEncoder().encode(s).length;
}

/**
 * 창(`loadProbe`의 답)으로 "파일이 받은 끝 뒤로 커지기만 했다"를 확인하고, 이어 받을 바이트 위치를 돌려준다. 아니면 null.
 * ctx: `start`·`end`(화면에 든 바이트 구간), `offset`(창 시작), `oldSize`·`oldRev`, `before`(화면 글에서 마지막 줄
 * 조각 앞의 끝부분, PROBE_BYTES 글자면 충분하다), `beforeAll`(before가 그 앞 전부인가), `partial`(줄 끝 없이 끝난
 * 마지막 조각, 없으면 ''), `pinned`·`confident`(인코딩).
 *
 * 확인하는 것
 * - 크기가 늘었고, rev가 `dev:ino:…`이면 같은 파일(이름 바꿔 저장한 새 파일이 아님)이다.
 * - 창은 줄 끝에서 끝난다. 그 끝(offset+length)은 화면 끝에서 마지막 조각의 바이트만큼 앞이어야 한다 — 조각의 바이트
 *   수는 UTF-8로 확신할 때만 셀 수 있다(다른 인코딩이면 조각이 없을 때만).
 * - 창의 첫 줄 끝 뒤(창이 화면 처음에서 시작하면 전부)가 화면 글의 같은 자리와 같다. `\n`은 이 인코딩들에서 다른
 *   글자의 일부가 아니라서, 창이 글자 중간에서 시작해도 첫 줄 끝 뒤부터는 같은 방식으로 풀린다.
 * 보지 못하는 것: 창 앞쪽만 바뀌고 크기도 는 편집(rev는 메타데이터라 서버도 모른다). 그런 편집은 다음 처음부터
 * 다시 읽기(크기가 그대로인 변경, 창이 다른 변경, 새로고침 버튼으로 다시 열기) 때 맞춰진다.
 */
export function growthFrom(d, ctx) {
  if (!d || d.binary || d.image || d.line_truncated) return null;
  if (!(Number(d.size) > Number(ctx.oldSize))) return null;
  const a = _devIno(d.rev);
  const b = _devIno(ctx.oldRev);
  if (a && b && a !== b) return null;
  if (typeof d.offset !== 'number' || typeof d.length !== 'number' || d.offset !== ctx.offset) return null;
  let partialBytes = 0;
  if (ctx.partial) {
    if (ctx.pinned !== 'utf-8' || !ctx.confident) return null;
    partialBytes = _utf8Len(ctx.partial);
  }
  if (d.offset + d.length !== ctx.end - partialBytes) return null;
  let raw = String(d.content == null ? '' : d.content);
  if (ctx.offset > ctx.start) {
    const i = raw.indexOf('\n');
    if (i < 0) return null;
    raw = raw.slice(i + 1);
  }
  if (!raw || !raw.endsWith('\n')) return null;
  const text = raw.replace(/\r\n?/g, '\n');
  const before = String(ctx.before || '');
  if (ctx.offset === ctx.start ? !(ctx.beforeAll && text === before) : !before.endsWith(text)) return null;
  return d.offset + d.length;
}

/** 끝부분 TAIL_BYTES. 다시 읽기는 `etag`로 조건부(304면 `{notModified: true}`). */
export async function loadTail({ path, host, encoding, etag = null, fetchFn = vtFetchConditional }) {
  const requested = encoding || 'auto';
  const r = await fetchFn(_url(path, host, { tail: TAIL_BYTES, encoding: requested }), etag ? { etag } : {});
  if (r.status === 304) return { notModified: true, etag: r.etag };
  const d = r.data || {};
  const meta = _meta(d, 'tail', requested);
  meta.eof = true;
  meta.loaded = meta.size;
  meta.tailOffset = d.offset || 0;
  return { meta, etag: r.etag, text: joinPiece('', d.content, true).text };
}

/** 더 받을 수 있는가(앞부분 보기에서 아직 끝이 아니고 상한 전). */
export function canLoadMore(meta) {
  return !!meta && meta.mode === 'head' && !meta.eof && meta.nextOffset != null && meta.nextOffset < MAX_LOADED;
}
