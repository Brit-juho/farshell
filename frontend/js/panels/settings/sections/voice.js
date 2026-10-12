import { renderSessionVoice, renderVoiceDefaults } from './voice-session.js';
import { vtFetch } from '../../../core/api.js';
import { activeSessionId, getSession } from '../../../core/store.js';
import { mountClients } from '../../../layout/clients.js';
import { row, statusLine, toast, renderItems, group } from '../controls.js';
import { renderVoiceInput, cleanupVoiceModels } from './voice-models.js';
import { togglePush, refreshPushLabel } from '../../../pushui.js';
import { getAction } from '../../../core/dom.js';

// ── 「음성」 (N42 · 알림·음성 진단) ──────────────────────────────────────
// (다른 탭 클릭 · 패널 닫기) 반드시 cleanup을 불러야 폴링 타이머가 안 샌다.
let _voiceClientsCleanup = null;
let _voiceSessionCleanup = null;

/** 이 섹션을 떠날 때(다른 탭·패널 닫기) 반드시 불러야 폴링 타이머가 안 샌다.
 *  settings.js의 rerender/onClose가 부른다. */
export function cleanupVoiceClients() {
  if (_voiceClientsCleanup) { _voiceClientsCleanup(); _voiceClientsCleanup = null; }
  _voiceSessionCleanup?.(); _voiceSessionCleanup = null;
  cleanupVoiceModels();  // 모델 받기 진행 폴링
}

export function renderVoiceSection() {
  const frag = document.createDocumentFragment();
  const notif = document.createDocumentFragment();  // 「알림」 구역
  const desk = document.createDocumentFragment();   // 「데스크톱 음성」 구역

  // 1. 웹 푸시
  // 2026-09-18 — 구독 켜기/끄기(pushui.js)는 예전 ⋯ 메뉴의 마지막 남은 자리인
  // legacy 레일 플라이아웃 안에만 있었다. 그 플라이아웃을 여는 ⚙가 새 레일
  // (Rail.tsx)로 대체되며 사라져 도달 불가능해졌다 — id(`push-btn`/`push-label`)는
  // pushui.js가 그대로 찾으므로 유지한다.
  const subBtn = document.createElement('button');
  subBtn.type = 'button'; subBtn.id = 'push-btn'; subBtn.className = 'vt-btn sm quiet vt-set-reset';
  const subLabel = document.createElement('span');
  subLabel.id = 'push-label';
  subLabel.textContent = '구독';
  subBtn.appendChild(subLabel);
  // togglePush()가 끝에서 스스로 refreshPushLabel을 다시 부른다(pushui.js).
  subBtn.addEventListener('click', () => togglePush());

  const pushBtn = document.createElement('button');
  pushBtn.type = 'button'; pushBtn.className = 'vt-btn sm quiet vt-set-reset'; pushBtn.textContent = '테스트 발송';
  const pushBtns = document.createElement('div');
  pushBtns.className = 'vt-set-btns';
  pushBtns.append(subBtn, pushBtn);
  const pushRow = row('웹 푸시', pushBtns);
  const pushStatus = statusLine('확인 중…');
  pushRow.querySelector('.vt-set-label').appendChild(pushStatus);
  notif.appendChild(pushRow);
  refreshPushLabel();
  vtFetch('/api/push/status').then((r) => {
    if (!r) return;
    pushStatus.textContent = r.available
      ? `구독 ${r.subscriptions}대 · VAPID ${r.configured ? '확인됨' : '미설정'}`
      : '사용 불가 (pywebpush 미설치)';
  }).catch(() => { pushStatus.textContent = '상태를 확인할 수 없습니다.'; });
  pushBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/api/push/test', { method: 'POST' });
      toast(r.ok ? `발송됨 · 구독 ${r.sent}건` : '발송 실패');
    } catch (e) { toast(e.message || '발송 실패', 'error'); }
  });

  _voiceSessionCleanup?.();
  const sessionVoice = renderSessionVoice();
  const defaults = renderVoiceDefaults();
  _voiceSessionCleanup = () => { sessionVoice.cleanup(); defaults.cleanup(); };

  // 3. 음성 입력 + 받아 둔 항목 관리 — voice-models.js
  const vm = renderVoiceInput();
  const statusBtn = document.createElement('button');
  statusBtn.type = 'button'; statusBtn.className = 'vt-btn sm quiet';
  statusBtn.textContent = '음성 상태 열기';
  statusBtn.addEventListener('click', () => getAction('voice.status')?.());
  vm.input.prepend(row('입력 현황', statusBtn));

  // 5. 음성 전용 모드 — 실제 토글 로직·시각 상태(.active)는
  // voice/media-session.js의 toggleVoiceOnly()가 갖고 있다(그 파일은 별도
  // voice.js 번들이라 여기서 직접 import하지 않는다 — 상단 주석 참고).
  // core/dom.js(공유 registry)로 등록된 액션을 직접 불러서 호출한다 —
  // data-action 위임에 맡기면 이 버튼의 텍스트 갱신 리스너와 실행 순서가
  // 보장되지 않는다(버블 단계상 위임 리스너보다 먼저 불려 상태 갱신 전
  // 텍스트를 읽는다). id는 toggleVoiceOnly()가 getElementById로 그대로
  // 찾으므로 유지.
  const voiceOnlyBtn = document.createElement('button');
  voiceOnlyBtn.type = 'button'; voiceOnlyBtn.id = 'voiceonly-btn';
  voiceOnlyBtn.className = 'vt-btn sm quiet vt-set-reset needs-voice';
  const paintVoiceOnly = () => {
    const isOn = document.body.classList.contains('voice-only-mode');
    voiceOnlyBtn.textContent = isOn ? '끄기' : '켜기';
    voiceOnlyBtn.classList.toggle('active', isOn);
  };
  paintVoiceOnly();
  voiceOnlyBtn.addEventListener('click', () => { getAction('voice.only-toggle')?.(); paintVoiceOnly(); });
  desk.appendChild(row('음성 전용 모드', voiceOnlyBtn,
    '화면을 마이크 하나로 채웁니다 — 이어폰만으로 조작할 때 씁니다.'));

  // 6. 연결된 화면 — clients.js의 기존 렌더러를 그대로 이식(중복 구현 금지).
  const clientsHost = document.createElement('div');
  desk.appendChild(clientsHost);
  if (_voiceClientsCleanup) { _voiceClientsCleanup(); _voiceClientsCleanup = null; }
  const activeSess = getSession(activeSessionId());
  const activeTmux = activeSess && (activeSess.tmuxName || activeSess.tmux_name);
  if (activeTmux) _voiceClientsCleanup = mountClients(clientsHost, activeTmux, activeSess && activeSess.remote, activeSessionId());

  frag.append(sessionVoice.element, defaults.element, group('알림', notif), vm.input, vm.manage, group('데스크톱 음성', desk));
  return frag;
}
