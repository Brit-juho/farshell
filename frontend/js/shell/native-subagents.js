import { t } from '../core/i18n.js';

export function normalizeSubagents(value) {
  const items = Array.isArray(value?.items) ? value.items : [];
  const seen = new Set();
  return {
    parentId: typeof value?.parent_id === 'string' ? value.parent_id : '',
    limited: value?.limited === true,
    items: items.slice(0, 32).filter((r) => {
      if (!r || typeof r.id !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(r.id)
          || seen.has(r.id) || !['working', 'idle', 'done', 'failed'].includes(r.state)) return false;
      seen.add(r.id); return true;
    }).map((r) => ({ id: r.id, state: r.state,
      label: typeof r.label === 'string' ? r.label.slice(0, 200) : r.id,
      tool: typeof r.tool === 'string' ? r.tool.slice(0, 200) : '',
      started_at: Number.isFinite(r.started_at) ? r.started_at : null,
      stale: r.stale === true,
    })),
  };
}

export function nativeStateLabel(state) {
  return { working: t('작업 중'), idle: t('유휴'), done: t('완료'), failed: t('실패') }[state] || t('유휴');
}

// 같은 이름(label)이 둘 이상이면 시작 시각 순으로 1부터 번호를 매긴다(id → 번호).
// current·ended 를 합친 전체 목록에서 한 번 계산해, 끝난 쪽과 번호가 일관되게 한다.
export function sameLabelOrdinals(items) {
  const groups = new Map();
  items.forEach((r, i) => {
    if (!groups.has(r.label)) groups.set(r.label, []);
    groups.get(r.label).push({ r, i });
  });
  const out = new Map();
  for (const list of groups.values()) {
    if (list.length < 2) continue;
    list.sort((a, b) => (a.r.started_at ?? Infinity) - (b.r.started_at ?? Infinity) || a.i - b.i)
      .forEach((x, n) => out.set(x.r.id, n + 1));
  }
  return out;
}
