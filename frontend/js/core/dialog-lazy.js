// confirmDialog / promptDialog / alertDialog의 지연 진입점 — ui/select-lazy.js·panels/system-lazy.js와 같은 구조.
// 세 함수는 이미 Promise를 돌려주므로 호출부는 그대로 두고, 본체(core/dialog.js + panels/panel.js, 약 18KB)만
// `panel` 청크로 뺐다. 부팅 때 필요한 쪽(터미널·레일)은 이 스텁을 import하고, 본체는 처음 필요할 때 받는다.
// 본체를 동기로 쓰는 쪽(openDialog·nextId — 파일·포트·기록·공유 화면)은 이미 지연 청크라 core/dialog.js를 직접 import한다.
//
// 청크를 못 받으면(오프라인·배포 직후 해시 불일치) 기능을 잃는 대신 브라우저 기본 대화상자로 물러난다:
// 확인 창을 조용히 "취소"로 처리하면 사용자가 왜 안 되는지 모르고, "확인"으로 처리하면 안전장치가 사라진다.
let pending = null;
const load = () => (pending ||= import('./dialog.js').catch((e) => { pending = null; throw e; }));

/** 부팅 뒤 유휴 시간에 미리 받아, 첫 대화상자가 네트워크를 기다리지 않게 한다. */
export function prefetchDialogs() {
  const go = () => { load().catch(() => {}); };
  if (typeof requestIdleCallback === 'function') requestIdleCallback(go, { timeout: 6000 });
  else setTimeout(go, 3000);
}

export function confirmDialog(message, opts) {
  return load().then((m) => m.confirmDialog(message, opts), () => window.confirm(message));
}

export function promptDialog(message, opts) {
  return load().then((m) => m.promptDialog(message, opts), () => {
    const v = window.prompt(message, opts?.defaultValue ?? '');
    return v === undefined ? null : v;
  });
}

export function alertDialog(message, opts) {
  return load().then((m) => m.alertDialog(message, opts), () => { window.alert(message); });
}
