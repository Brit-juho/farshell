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
