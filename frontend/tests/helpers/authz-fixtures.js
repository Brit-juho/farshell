'use strict';
// 승인함 테스트 공용: 항목 모양은 API.md 「Approval inbox, grants and audit」의 응답과 같다.
const item = (id, kind = 'share', over = {}) => ({
  id, kind, state: 'pending',
  subject: { kind: 'agent', id: 'agent:local/$1:1', name: 'agent-a' },
  resource: { id: 'session:local/$2:2', label: 'build' },
  scopes: ['session.read', 'session.view'], conds: {},
  preset: { id: 'view', label: '보기', exact: true },
  elevated: [], lineage: null, created: 1, expires: 99999, age: 120, expiresIn: 3600,
  ...over,
});

const grant = (id, kind = 'share', over = {}) => ({
  id, kind, effect: 'allow', state: 'active',
  subject: { id: 'agent:local/$1:1', name: 'agent-a' }, resource: 'session:local/$2:2',
  scopes: ['session.read', 'session.view'], conds: {}, preset: { id: 'view', label: '보기', exact: true },
  approvedBy: 'user:alice', approvedVia: 'manual', created: 1, expires: null, uses: null, maxUses: null, lineage: null,
  ...over,
});

const ok = (json = { ok: true }) => ({ json });
const promptInput = (document) => document.querySelector('.vt-dialog-input');

module.exports = { item, grant, ok, promptInput };
