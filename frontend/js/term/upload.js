// R6 — 파일 업로드의 **유일한 구현**. 진입점은 셋이다:
//   1. `#file-input`  — 키바 📎 · 팔레트 · 파일 패널 「올리기」가 공유하는 그 input
//      (picker.js의 `uploadFile()`이 인라인 onchange로 받아 여기로 넘긴다)
//   2. 터미널 칸에 OS 파일 드롭   — layout/dnd.js
//   3. 파일 패널에 OS 파일 드롭   — panels/files/files.js (지연 청크 → window 브리지)
//
// files.js가 이 모듈을 **정적 import 하지 않는 이유**: 여기는 core/store.js를
// import 하는데 files.js는 지연 청크(panels.js)라, 정적으로 끌어오면 Vite가
// 세션 싱글톤을 그 청크 안에 복제해 앱 본체와 다른 객체가 된다(ADR-26).
// 그래서 아래 `window.vtUploadFiles` 브리지를 둔다 — layout/store.js의
// `window.openPanelPane`과 같은 관용구다.
//
// 서버는 **요청 하나에 파일 하나**다(`POST /api/upload`, routes/pty.py). 여러
// 개는 여기서 순차로 보낸다 — 병렬로 쏘면 큰 파일 여러 개가 동시에
// `MAX_UPLOAD_BYTES` 검사를 통과하며 디스크를 함께 밀어낸다.
import { activeSessionId } from '../core/store.js';
import { apiFetch } from '../core/api.js';
import { API_BASE } from '../core/env.js';
import { sendPaste } from './clipboard.js';
import { showToast } from '../ui/toast.js';

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
    if (sawDir) showToast('폴더는 올릴 수 없습니다 — 파일만 골라 놓으세요', 'error');
    return out;
  }
  return Array.from(dataTransfer.files || []);
}

/** 파일 하나를 올린다. 성공하면 서버가 준 레코드를, 실패하면 null을 반환한다.
 *  **던지지 않는다** — 여러 개를 순차로 올리는 중에 하나가 실패해도 나머지는
 *  계속 가야 한다. */
async function _uploadOne(file, sessionId) {
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await apiFetch(`${API_BASE}/api/upload?session_id=${sessionId || ''}`, {
      method: 'POST',
      body: formData,
    });
    const data = await res.json().catch(() => null);
    // 413(용량 초과)은 서버가 max_mb를 실어 준다 — 왜 실패했는지 말해 준다.
    if (!data || !data.ok) {
      const why = data && data.error === 'file too large'
        ? `${data.max_mb}MB를 넘습니다`
        : (data && data.error) || '알 수 없는 오류';
      showToast(`${file.name} 업로드 실패 — ${why}`, 'error');
      return null;
    }
    return data;
  } catch (e) {
    showToast(`${file.name} 업로드 실패 — ${e.message}`, 'error');
    return null;
  }
}

/**
 * 파일 여러 개를 순차로 올린다.
 *
 * @param {FileList|File[]|DataTransfer} list  DataTransfer를 그대로 줘도 된다 —
 *   지연 청크(files.js)는 `filesFromDataTransfer`를 import 할 수 없어서
 *   브리지 하나로 끝나야 한다.
 * @param {object}   opts
 * @param {string?}  opts.sessionId   경로를 꽂을 세션. 생략하면 활성 세션.
 * @param {boolean}  opts.insertPath  업로드한 경로를 그 세션에 붙여넣을지.
 *   드롭한 자리가 정한다(사용자 확정): 터미널 칸에 놓으면 true, 파일 패널에
 *   놓으면 false — 파일을 정리하는 중에 터미널로 글자가 끼어들면 방해가 된다.
 * @returns {Promise<object[]>} 성공한 항목들
 */
export async function uploadFiles(list, opts = {}) {
  // DataTransfer면 폴더 걸러내기까지 여기서 한다(위 @param 참고).
  const files = (list && typeof list.getData === 'function')
    ? filesFromDataTransfer(list)
    : Array.from(list || []);
  if (!files.length) return [];
  const sessionId = opts.sessionId || activeSessionId() || '';
  const insertPath = opts.insertPath !== false;

  if (files.length > 1) showToast(`${files.length}개 업로드 중…`, 'info');

  const done = [];
  for (const f of files) {
    const data = await _uploadOne(f, sessionId);
    if (data) done.push(data);
  }
  if (!done.length) return done;

  if (insertPath && sessionId) {
    // 화면에 찍기만 하면(term.write) 드래그 선택 말고는 경로를 집어낼 수 없다.
    // 이미지 붙여넣기(pasteImageUpload)와 동일하게 경로를 명령줄에 실제로
    // 타이핑해 Claude 등에 그대로 넘길 수 있게 한다.
    // N24 — 실행되지 않고 사용자가 이어서 완성할 텍스트라 붙여넣기 경로.
    // 여러 개면 한 번에 붙인다 — 한 줄씩 보내면 셸이 중간에 실행해 버린다.
    sendPaste(sessionId, done.map((d) => d.path).join(' ') + ' ');
  }

  const n = done.length;
  const failed = files.length - n;
  const what = n === 1 ? done[0].path.split('/').pop() : `${n}개`;
  const tail = insertPath && sessionId ? ' — 경로 삽입됨' : '';
  if (failed) showToast(`${what} 업로드 완료 (${failed}개 실패)${tail}`, 'success');
  else showToast(`${what} 업로드 완료${tail}`, 'success');
  return done;
}

// 지연 청크(panels/files/files.js)가 쓰는 브리지. 파일 머리말 참고.
if (typeof window !== 'undefined') {
  window.vtUploadFiles = uploadFiles;

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
    showToast('터미널 칸이나 파일 패널에 놓으면 올라갑니다', 'info');
  });
}
