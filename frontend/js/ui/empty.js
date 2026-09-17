// 빈 상태 · 에러 상태 컴포넌트 (2.1.6).
//
// 왜 만들었나: 화면마다 빈 상태를 제각각 한 줄짜리 <p>·<div>로 그리고 있었다.
//   panels/usage.js   → <p class="vt-usage-none">사용량 소스가 없습니다.</p>
//   panels/files/*    → <div class="vt-vw-loading">목록을 불러오지 못했습니다: unauthorized</div>
// 둘 다 "지금 화면이 비어 있다"만 말하고 **다음에 뭘 할 수 있는지는 말하지
// 않는다**. 게다가 아래쪽은 서버 예외 문자열(unauthorized)을 제목 자리에 그대로
//놓아서, 그게 이 화면의 이름처럼 읽혔다.
//
// 계약: 제목은 사람 말, 원문은 detail, 할 수 있는 일이 있으면 action.
// 스타일은 styles/layers/components.css의 .vt-empty.
import { icon } from './icons.js';

/**
 * @param {object} o
 * @param {string} o.title   한 줄. 사람이 읽는 말("아직 업로드한 파일이 없습니다")
 * @param {string} [o.desc]  한두 줄 보충. 왜 비어 있는지 / 어떻게 채우는지
 * @param {string} [o.detail] 서버 원문·예외 문자열 등 기계가 뱉은 것
 * @param {string} [o.icon]  ui/icons.js의 키. 없으면 아이콘을 안 그린다
 * @param {boolean} [o.error] 에러 변형(아이콘이 에러색)
 * @param {{label:string, onClick:Function}} [o.action] 있으면 버튼 하나
 */
export function emptyState(o) {
  const el = document.createElement('div');
  el.className = 'vt-empty' + (o.error ? ' error' : '');
  // 에러는 스크린리더가 즉시 읽어야 하고, 빈 상태는 화면의 한 상태일 뿐이다.
  el.setAttribute('role', o.error ? 'alert' : 'status');

  if (o.icon) el.insertAdjacentHTML('beforeend', icon(o.icon, 20, 1.75));

  const title = document.createElement('div');
  title.className = 'vt-empty-title';
  title.textContent = o.title;
  el.appendChild(title);

  if (o.desc) {
    const d = document.createElement('div');
    d.className = 'vt-empty-desc';
    d.textContent = o.desc;
    el.appendChild(d);
  }
  if (o.detail) {
    const d = document.createElement('div');
    d.className = 'vt-empty-detail';
    d.textContent = o.detail;
    el.appendChild(d);
  }
  if (o.action) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vt-btn sm';
    b.textContent = o.action.label;
    b.addEventListener('click', o.action.onClick);
    el.appendChild(b);
  }
  return el;
}

// 에러에서 가장 흔한 모양 — 제목은 사람 말, 원문은 detail, [다시 시도] 하나.
// retry가 없으면 버튼을 만들지 않는다(누르면 아무 일도 안 나는 버튼을 두지 않는다).
export function errorState(title, err, retry) {
  return emptyState({
    error: true,
    icon: 'alert-triangle',
    title,
    detail: err == null ? '' : String(err && err.message ? err.message : err),
    action: retry ? { label: '다시 시도', onClick: retry } : undefined,
  });
}
