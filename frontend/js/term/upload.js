// R6 — 파일 업로드의 **유일한 구현**(전송·대기열·취소는 이 파일, 드롭 판별·창 기본 동작 차단·브리지 스텁은 upload-lite.js).
// 이 파일은 `upload` 청크라 첫 업로드 때 받는다. 진입점은 셋이다:
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
import { API_BASE } from '../core/env.js';
import { relayUrl } from '../core/host-route.js';
import { noteNetworkError, noteResponse } from '../core/reachability.js';
import { sendPaste } from './clipboard.js';
import { shellQuotePath } from '../lib/shell-quote.js';
import { showToast } from '../ui/toast.js';
import { hasFiles, filesFromDataTransfer } from './upload-lite.js';
import { t } from '../core/i18n.js';

function _uploadClientId() {
  return (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : `up-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// ── 업로드 작업 목록 (2026-09-23) ────────────────────────────────────────
// 예전엔 진행 상태를 이벤트로만 흘려보내고 아무도 안 들고 있었다 — 첨부
// 패널(files.js)이 자기 인스턴스마다 `Map`을 따로 채웠는데, 업로드 도중에
// 패널을 열면 다음 progress 이벤트가 올 때까지 빈 화면이었다. 이제 상태의
// 주인은 여기 하나다 — 패널은 그릴 때마다 `uploadJobs()`로 스냅샷을 읽는다.
// 순서 보존이 필요해 Map을 쓴다(clientId 삽입 순서 = 대기열 순서).
//
// phase: 'queued'(차례 대기) → 'uploading'(전송 중) → 'saving'(바디는 다
// 갔고 서버 응답 대기 — 취소하면 화면·서버가 어긋날 수 있어 UI가 취소를
// 끈다) → 'done' | 'error' | 'cancelled'(각각 잠깐 남았다가 지워진다).
const _jobs = new Map();   // clientId → job

// vt:upload-progress — "다시 그려라"라는 신호일 뿐, 페이로드는 없다. 상태
// 자체는 항상 `_jobs`/`uploadJobs()`에서 읽는다 — 이벤트에 스냅샷을 실으면
// 한쪽만 놓친 리스너가 낡은 값을 들고 있게 된다.
function _emitProgress() {
  document.dispatchEvent(new CustomEvent('vt:upload-progress'));
}

// DataTransfer 판별·폴더 거르기는 드롭 이벤트 안에서 **동기로** 해야 해서 core의 upload-lite.js에 있다. 여기서도 같은 이름으로 내보낸다.
export { hasFiles, filesFromDataTransfer };

/** 지금 떠 있는 업로드 작업들 — 첨부 패널(panels/files/files.js)이 그릴
 * 때마다 부른다. 살아있는 상태(찍힌 클라이언트가 아직 못 본 done/error도
 * 잠깐 포함)만 얕은 사본으로 준다 — 내부 xhr은 취소용 참조라 노출 안 한다. */
export function uploadJobs() {
  return [..._jobs.values()].map((j) => ({
    clientId: j.clientId, name: j.name, size: j.size, loaded: j.loaded, phase: j.phase,
  }));
}

/** 업로드 작업을 취소한다. clientId 하나 또는 'all'(취소 가능한 것 전부).
 * 'saving' 단계는 취소 대상에서 뺀다(설계 2번 — 서버가 이미 파일을 만들고
 * 있을 수 있는 구간이라, 취소를 받아 주면 화면은 사라졌는데 서버엔 파일이
 * 남는 불일치가 생긴다). 반환값은 실제로 취소된 개수. */
export function cancelUpload(clientId) {
  const targets = clientId === 'all'
    ? [..._jobs.values()].filter((j) => j.phase === 'queued' || j.phase === 'uploading')
    : [_jobs.get(clientId)].filter((j) => j && (j.phase === 'queued' || j.phase === 'uploading'));
  for (const j of targets) {
    j.cancelled = true;
    if (j.phase === 'uploading' && j.xhr) j.xhr.abort();   // abort 핸들러가 phase를 정리한다
    else { j.phase = 'cancelled'; _jobs.delete(j.clientId); }
  }
  if (targets.length) _emitProgress();
  return targets.length;
}

/** 파일 하나를 올린다. 성공하면 서버가 준 레코드를, 실패/취소면 null을
 *  반환한다. **던지지 않는다** — 여러 개를 순차로 올리는 중에 하나가
 *  실패해도 나머지는 계속 가야 한다.
 *
 *  fetch가 아니라 XMLHttpRequest를 쓰는 이유: fetch는 업로드(요청 바디 전송)
 *  방향 진행률을 표준적으로 못 준다 — `xhr.upload.onprogress`만 그걸 준다.
 *  같은 이유로 취소도 `xhr.abort()`가 있어야 가능하다(fetch AbortController는
 *  업로드 진행률과 조합이 안 된다). */
function _uploadOne(job, sessionId) {
  return new Promise((resolve) => {
    if (job.cancelled) { resolve(null); return; }
    const { file } = job;
    const formData = new FormData();
    formData.append('file', file, job.name);

    const xhr = new XMLHttpRequest();
    const url = relayUrl(`${API_BASE}/api/upload?session_id=${sessionId || ''}`);
    xhr.open('POST', url);
    xhr.withCredentials = true;
    // apiFetch와 같은 인증 계약: API_BASE로 가는 요청에만 토큰을 붙인다.
    const token = window.VT_TOKEN;
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);

    xhr.upload.addEventListener('progress', (e) => {
      if (!e.lengthComputable) return;
      job.loaded = e.loaded;
      // 바디를 다 보냈으면(loaded===total) 서버 응답을 기다리는 구간이다 —
      // xhr는 여기서 더 진행률을 안 주므로 'load'/'error'/'abort' 중 하나가
      // 올 때까지 이 phase로 남는다.
      job.phase = e.loaded >= e.total ? 'saving' : 'uploading';
      _emitProgress();
    });

    const fail = (why, silent = false) => {
      job.phase = 'error';
      _jobs.delete(job.clientId);
      _emitProgress();
      if (!silent) showToast(t('{name} 업로드 실패 — {reason}', { name: job.name, reason: why }), 'error');
      resolve(null);
    };

    xhr.addEventListener('error', () => { noteNetworkError(); fail(t('네트워크 오류')); });
    xhr.addEventListener('abort', () => {
      // 사용자가 직접 취소한 것과 진짜 실패를 갈라야 한다 — 취소는 오류가
      // 아니고, 취소 요약은 배치가 끝날 때 한 번에 말한다(uploadFiles).
      job.phase = 'cancelled';
      _jobs.delete(job.clientId);
      _emitProgress();
      resolve(null);
    });
    xhr.addEventListener('load', () => {
      noteResponse(xhr.status);
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch (_) { data = null; }
      // 413(용량 초과)은 서버가 max_mb를 실어 준다 — 왜 실패했는지 말해 준다.
      if (xhr.status < 200 || xhr.status >= 300 || !data || !data.ok) {
        fail(data && data.error === 'file too large'
          ? t('{max}MB를 넘습니다', { max: data.max_mb })
          : (data && data.error) || t('알 수 없는 오류'));
        return;
      }
      job.phase = 'done';
      _jobs.delete(job.clientId);
      _emitProgress();
      resolve(data);
    });

    job.xhr = xhr;
    job.phase = 'uploading';
    _emitProgress();
    // WHATWG 스펙 — `abort()`를 UNSENT/전송 전 상태에서 부르면 이벤트 없이
    // 조용히 끝난다. 위 emit을 들은 리스너가 바로 cancelUpload()를 불러도
    // (실사용에선 없지만 테스트처럼 동기 리스너면 있을 수 있다) 그 abort()는
    // 씹히므로, 여기서 `cancelled` 플래그를 직접 한 번 더 확인하고 전송 자체를
    // 걸러낸다 — cancelUpload가 세운 `j.cancelled`가 유일하게 신뢰할 수 있는 신호다.
    if (job.cancelled) {
      job.phase = 'cancelled';
      _jobs.delete(job.clientId);
      _emitProgress();
      resolve(null);
      return;
    }
    xhr.send(formData);
  });
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
 * @param {string?}  opts.name        1개짜리 업로드일 때 서버에 보낼 파일
 *   이름을 덮어쓴다(이미지 붙여넣기가 `pasted-<ts>.ext`를 쓸 때처럼). 여러
 *   개면 무시한다 — 이름 하나로 여러 파일을 겹쳐 쓸 수는 없다.
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

  // 전부 'queued'로 먼저 등록한다 — 아직 첫 파일도 안 올라갔어도 첨부 패널을
  // 열면 대기열 전체가 바로 보인다.
  const jobs = files.map((file, i) => ({
    clientId: _uploadClientId(), file,
    name: (files.length === 1 && opts.name) ? opts.name : file.name,
    size: file.size, loaded: 0, phase: 'queued', xhr: null, cancelled: false,
  }));
  for (const j of jobs) _jobs.set(j.clientId, j);
  _emitProgress();

  const done = [];
  let cancelled = 0;
  for (const j of jobs) {
    if (j.cancelled) { cancelled++; continue; }
    const data = await _uploadOne(j, sessionId);
    if (data) done.push(data);
    else if (j.phase === 'cancelled') cancelled++;
  }
  if (!done.length) return done;

  if (insertPath && sessionId) {
    // 화면에 찍기만 하면(term.write) 드래그 선택 말고는 경로를 집어낼 수 없다.
    // 이미지 붙여넣기(pasteImageUpload)와 동일하게 경로를 명령줄에 실제로
    // 타이핑해 Claude 등에 그대로 넘길 수 있게 한다.
    // N24 — 실행되지 않고 사용자가 이어서 완성할 텍스트라 붙여넣기 경로.
    // 여러 개면 한 번에 붙인다 — 한 줄씩 보내면 셸이 중간에 실행해 버린다.
    // 경로는 셸 인용을 거친다 — 이름에 `$(…)`·백틱·`;`가 있어도 Enter에 실행되지 않는다(lib/shell-quote.js).
    sendPaste(sessionId, done.map((d) => shellQuotePath(d.path)).join(' ') + ' ');
  }

  const n = done.length;
  const failed = files.length - n - cancelled;
  const what = n === 1 ? done[0].path.split('/').pop() : t('{n}개', { n });
  const tail = insertPath && sessionId ? t(' — 경로 삽입됨') : '';
  const notes = [failed ? t('{n}개 실패', { n: failed }) : null, cancelled ? t('{n}개 취소', { n: cancelled }) : null].filter(Boolean);
  const suffix = notes.length ? ` (${notes.join(' · ')})` : '';
  showToast(t('{what} 업로드 완료{suffix}{tail}', { what, suffix, tail }), 'success');
  return done;
}

// 지연 청크(panels/files/files.js)가 쓰는 브리지. 파일 머리말 참고.
if (typeof window !== 'undefined') {
  window.vtUploadFiles = uploadFiles;
  window.vtUploadJobs = uploadJobs;
  window.vtCancelUpload = cancelUpload;
}
