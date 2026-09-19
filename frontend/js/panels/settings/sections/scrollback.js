import { vtFetch } from '../../../core/api.js';
import { row, boolControl, statusLine, fmtBytes } from '../controls.js';

// ── 「스크롤백」 ──────────────────────────────────────────────────────────
// N13(80-multihost-agents.md §3) — 토글 자체는 core/settings.js가 즉시 반영하지만
// (다른 항목과 동일), 디스크 사용량은 서버 상태라 매번 새로 물어봐야 한다.

export function renderScrollbackSection() {
  const frag = document.createDocumentFragment();
  frag.appendChild(row(
    '스크롤백 영속화',
    boolControl('scrollback.persist'),
    '재접속할 때: 최근 256KB만 (현재 — 이 설정과 무관하게 항상 그대로입니다). '
      + '켜면 출력을 서버 디스크에도 이어붙여 "더 불러오기"로 더 과거 출력을 볼 수 있게 됩니다. '
      + '타이핑한 입력은 저장되지 않습니다 — 출력 스트림만 기록합니다. 7일 뒤 자동 삭제.',
    { scope: 'global' },
  ));
  const usageHost = document.createElement('div');
  usageHost.className = 'vt-set-sechost';
  usageHost.appendChild(statusLine('디스크 사용량 확인 중…'));
  frag.appendChild(usageHost);
  vtFetch('/api/scrollback/usage').then((r) => {
    usageHost.innerHTML = '';
    usageHost.appendChild(statusLine(`디스크 사용량: ${fmtBytes(r.bytes || 0)}`));
  }).catch(() => {
    usageHost.innerHTML = '';
    usageHost.appendChild(statusLine('디스크 사용량을 확인할 수 없습니다.'));
  });
  return frag;
}
