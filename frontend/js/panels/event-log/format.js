// 기록 패널의 순수 함수들 — DOM 없이 시험한다. 「이슈용 복사」 텍스트를 만든다.
import { t } from '../../core/i18n.js';

const pad = (n) => String(n).padStart(2, '0');

export function fmtDateTime(ts) {
  const d = new Date(ts);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function fmtClock(ts) {
  const d = new Date(ts);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** `PUT /api/fs/file → 403 · 182ms` */
export function reqLine(r) {
  if (!r) return '';
  const parts = [`${r.method || ''} ${r.path || ''}`.trim()];
  const tail = [];
  if (r.status != null) tail.push(r.status === 0 ? t('응답 없음') : String(r.status));
  if (r.ms != null) tail.push(`${r.ms}ms`);
  return tail.length ? `${parts[0]} → ${tail.join(' · ')}` : parts[0];
}

/** 이슈에 붙이는 마크다운 한 건. rel 은 관련 항목(있으면 한 줄로 덧붙인다). */
export function eventToText(e, rel) {
  const out = [`### [${e.level}] ${e.msg}${e.count > 1 ? ` ×${e.count}` : ''}`];
  out.push(`- ${t('시각')}: ${fmtDateTime(e.ts)}${e.last !== e.ts ? ` (${t('마지막')} ${fmtDateTime(e.last)})` : ''}`);
  out.push(`- ${t('출처')}: ${e.source}`);
  if (e.req) {
    const extra = [e.req.code && `code=${e.req.code}`, e.req.host && `host=${e.req.host}`].filter(Boolean).join(' · ');
    out.push(`- ${t('요청')}: ${reqLine(e.req)}${extra ? ` · ${extra}` : ''}`);
    if (e.req.reason) out.push(`- ${t('사유')}: ${e.req.reason}`);
  }
  if (rel) out.push(`- ${t('관련')}: ${rel.req ? reqLine(rel.req) : rel.msg}${e.relGuess ? ` (${t('추정')})` : ''}`);
  if (e.ctx) {
    const c = [e.ctx.view, e.ctx.session && `session=${e.ctx.session}`, e.ctx.screen && `screen=${String(e.ctx.screen).slice(0, 8)}`].filter(Boolean).join(' · ');
    if (c) out.push(`- ${t('화면')}: ${c}`);
  }
  if (e.detail) out.push('', '```', e.detail, '```');
  return out.join('\n');
}

/** 머리말(환경) + 항목들. env: { ua, page, lang, now }. */
export function reportText(events, env, relOf) {
  const head = [`**FarShell ${t('기록')}** — ${fmtDateTime(env.now || Date.now())}`, `- UA: ${env.ua || ''}`, `- ${t('언어')}: ${env.lang || ''}`, ''];
  return head.concat(events.map((e) => eventToText(e, relOf ? relOf(e) : null)).join('\n\n')).join('\n');
}
