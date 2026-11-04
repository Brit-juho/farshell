// "⋯ → 가이드 보기" — 언제든 열고/닫을 수 있는 서비스 전체 사용 가이드(첫 사용자용).
// 본문(섹션 정의와 마크업, 4KB)은 term/guide-view.js로 뺐다 — 열 때만 받는다(`panel` 청크).
// 온보딩(showOnboarding, boot.js)은 첫 실행 시 세션 생성을 강제하는 화면이라 재사용하지 않고 별도로 둔다.
import { registerAction } from '../core/dom.js';

registerAction('guide.show', () => {
  import('./guide-view.js').then((m) => m.showGuide()).catch((e) => console.error('[FarShell guide]', e));
});
