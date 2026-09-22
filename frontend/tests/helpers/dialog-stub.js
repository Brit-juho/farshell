'use strict';
// core/dialog.js(confirmDialog/promptDialog/alertDialog)가 여는 실제 다이얼로그에
// 응답하는 테스트 헬퍼. window.confirm/prompt 목(mock)은 그 함수들이 더 이상
// bare global을 안 부르므로(대신 openPanel() 기반 실제 DOM을 그려 Promise로
// 기다린다) 효과가 없다 — 열린 카드에서 진짜 버튼을 눌러야 한다.
//
// confirmDialog/promptDialog는 Promise executor 안에서 openPanel()을
// **동기적으로** 부르므로(내부에 await 없음), 호출부가 `deleteWorktreeRow(...)`
// 같은 async 함수를 호출한 시점에 이미 카드가 DOM에 떠 있다 — 첫 다이얼로그는
// flush 없이 바로 찾아도 된다. 단, 같은 함수 안에서 fetch 등 진짜 비동기 작업
// 뒤에 두 번째 다이얼로그가 뜨는 경우(예: 409 재확인)는 그 사이 마이크로태스크가
// 도는 동안 카드가 아직 없을 수 있어 폴링한다.
async function _flush() { return new Promise((r) => setImmediate(r)); }

/** document에서 열려 있는 .vt-dialog 카드를 폴링해서 찾는다. */
async function waitForDialog(document, { timeoutMs = 500 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const el = document.querySelector('.vt-dialog');
    if (el) return el;
    if (Date.now() > deadline) throw new Error('waitForDialog: 열린 .vt-dialog를 못 찾음(타임아웃)');
    await _flush();
  }
}

/**
 * 열린 confirmDialog/alertDialog에 응답한다.
 * @param {Document} document
 * @param {{accept?: boolean}} opts accept=false면 취소 버튼(alertDialog에는 취소가 없다 — 확인만 누른다)
 */
async function answerConfirm(document, { accept = true } = {}) {
  const dialog = await waitForDialog(document);
  const sel = accept || !dialog.querySelector('.vt-dialog-cancel') ? '.vt-dialog-ok' : '.vt-dialog-cancel';
  dialog.querySelector(sel).click();
}

/**
 * 열린 promptDialog에 응답한다. accept=false면 취소(반환값 null).
 * @param {Document} document
 * @param {{value?: string, accept?: boolean}} opts
 */
async function answerPrompt(document, { value, accept = true } = {}) {
  const dialog = await waitForDialog(document);
  if (accept) {
    if (value !== undefined) {
      const input = dialog.querySelector('.vt-dialog-input');
      input.value = value;
    }
    dialog.querySelector('.vt-dialog-ok').click();
  } else {
    dialog.querySelector('.vt-dialog-cancel').click();
  }
}

module.exports = { waitForDialog, answerConfirm, answerPrompt };
