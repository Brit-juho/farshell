// 파일 업로드의 core 쪽 얇은 층 — 전송·대기열·취소 본체(term/upload.js, 10KB)는 `upload` 청크라 첫 업로드 때 받는다.
// 여기 남는 것은 **이벤트 안에서 동기로** 해야 하거나 부팅 때 있어야 하는 것뿐이다:
//   1. hasFiles / filesFromDataTransfer — 드롭 이벤트가 끝나면 DataTransfer가 비므로 await 전에 읽어야 한다.
//   2. 창 전체 기본 동작 차단 — 없으면 드롭 대상 밖에 떨어뜨린 파일로 브라우저가 이동해 터미널이 날아간다.
//   3. window.vtUploadFiles / vtUploadJobs / vtCancelUpload 브리지 스텁 — 지연 청크(panels/files)와 mobile-nav가
//      부른다. 본체가 올라오면(upload.js 끝) 진짜 함수로 덮어쓴다. 아직 안 올라왔다면 작업이 없다는 뜻이라
//      Jobs는 [], Cancel은 0이 정확한 답이다.
import { showToast } from '../ui/toast.js';
import { t } from '../core/i18n.js';

/** DataTransfer에 OS 파일이 실려 있는가. 앱 내부 드래그(세션·dock 탭)와
 *  구분하는 유일한 기준이다 — 그쪽은 자기 mime을 싣고 'Files'는 안 싣는다. */
export function hasFiles(dataTransfer) {
  const types = dataTransfer && dataTransfer.types;
  return !!types && Array.prototype.includes.call(types, 'Files');
}

/** 드롭된 항목에서 **파일만** 고른다.
 *
 * 폴더를 끌어다 놓으면 `dataTransfer.files`에 크기 0짜리 항목으로 들어오는데,
 * 그대로 올리면 서버에 빈 파일이 생긴다(이름만 폴더명). `webkitGetAsEntry()`는
 * 세 엔진 모두 지원하므로 그걸로 걸러내고, 없는 환경에서는 그냥 통과시킨다 —
 * 못 거르는 것보다 낫고, 그 경우에도 업로드 자체는 실패하지 않는다. */
export function filesFromDataTransfer(dataTransfer) {
  if (!dataTransfer) return [];
  const items = dataTransfer.items;
  if (items && items.length && typeof items[0].webkitGetAsEntry === 'function') {
    const out = [];
    let sawDir = false;
    for (const item of items) {
      if (item.kind !== 'file') continue;
      let entry = null;
      try { entry = item.webkitGetAsEntry(); } catch (_) { entry = null; }
      if (entry && entry.isDirectory) { sawDir = true; continue; }
      const f = item.getAsFile();
      if (f) out.push(f);
    }
    if (sawDir) showToast(t('폴더는 올릴 수 없습니다 — 파일만 골라 놓으세요'), 'error');
    return out;
  }
  return Array.from(dataTransfer.files || []);
}

let _heavy = null;
const loadUpload = () => (_heavy ||= import('./upload.js').catch((e) => { _heavy = null; throw e; }));

/** 업로드 시작. DataTransfer가 오면 **지금 동기로** 파일 목록으로 바꾼 뒤 본체를 받는다. */
export function uploadFiles(list, opts) {
  const files = (list && typeof list.getData === 'function') ? filesFromDataTransfer(list) : Array.from(list || []);
  if (!files.length) return Promise.resolve([]);
  return loadUpload().then((m) => m.uploadFiles(files, opts)).catch((e) => {
    console.error('[FarShell upload]', e);
    showToast(t('업로드 모듈을 불러오지 못했습니다 — 새로고침 후 다시 시도하세요'), 'error');
    return [];
  });
}

/** 부팅 뒤 유휴 시간에 본체를 미리 받는다 — 첫 드롭·붙여넣기가 네트워크를 기다리지 않게. */
export function prefetchUpload() {
  const go = () => { loadUpload().catch(() => {}); };
  if (typeof requestIdleCallback === 'function') requestIdleCallback(go, { timeout: 8000 });
  else setTimeout(go, 4000);
}

if (typeof window !== 'undefined') {
  window.vtUploadFiles = uploadFiles;
  window.vtUploadJobs = () => [];
  window.vtCancelUpload = () => 0;

  // ── 창 전체 기본 동작 차단 ────────────────────────────────────────────
  // 이게 없으면 드롭 대상 **밖에** 파일을 떨어뜨렸을 때 브라우저가 그 파일로
  // 이동해 버린다 — 터미널 화면이 통째로 날아가고 뒤로 가기로 돌아와도
  // 세션은 다시 붙어야 한다. R6 이전부터 있던 구멍이라 여기서 같이 막는다.
  //
  // drop이 발생하려면 직전 dragover에서 기본 동작이 막혀 있어야 하므로 둘 다
  // 건다. 실제로 받아 처리하는 곳(터미널 칸·파일 패널)은 자기 핸들러에서
  // stopPropagation()을 불러 여기까지 올라오지 않게 한다 — 그래서 아래
  // 안내는 "아무도 안 받은 드롭"에만 뜬다.
  window.addEventListener('dragover', (e) => { if (hasFiles(e.dataTransfer)) e.preventDefault(); });
  window.addEventListener('drop', (e) => {
    if (!hasFiles(e.dataTransfer)) return;
    e.preventDefault();
    showToast(t('터미널 칸이나 파일 패널에 놓으면 올라갑니다'), 'info');
  });
}
