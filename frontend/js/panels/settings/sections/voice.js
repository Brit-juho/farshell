import { renderSessionVoice, renderVoiceDefaults } from './voice-session.js';
import { vtFetch } from '../../../core/api.js';
import { activeSessionId, getSession } from '../../../core/store.js';
import { mountClients } from '../../../layout/clients.js';
import { row, statusLine, toast, renderItems, group, boolControl, selectControl } from '../controls.js';
import { scopeOf } from '../../../core/settings.js';
import { bindPinSelect } from '../../../voice/target-pin.js';
import { renderVoiceInput, cleanupVoiceModels } from './voice-models.js';
import { togglePush, refreshPushLabel } from '../../../pushui.js';
import { getAction } from '../../../core/dom.js';
import { t } from '../../../core/i18n.js';

// ── 「음성」 (N42 · 알림·음성 진단) ──────────────────────────────────────
// (다른 탭 클릭 · 패널 닫기) 반드시 cleanup을 불러야 폴링 타이머가 안 샌다.
let _voiceClientsCleanup = null;
let _voiceSessionCleanup = null;
let _voicePinCleanup = null;

/** 이 섹션을 떠날 때(다른 탭·패널 닫기) 반드시 불러야 폴링 타이머가 안 샌다.
 *  settings.js의 rerender/onClose가 부른다. */
export function cleanupVoiceClients() {
  if (_voiceClientsCleanup) { _voiceClientsCleanup(); _voiceClientsCleanup = null; }
  _voiceSessionCleanup?.(); _voiceSessionCleanup = null;
  _voicePinCleanup?.(); _voicePinCleanup = null;
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
  subLabel.textContent = t('구독');
  subBtn.appendChild(subLabel);
  // togglePush()가 끝에서 스스로 refreshPushLabel을 다시 부른다(pushui.js).
  subBtn.addEventListener('click', () => togglePush());

  const pushBtn = document.createElement('button');
  pushBtn.type = 'button'; pushBtn.className = 'vt-btn sm quiet vt-set-reset'; pushBtn.textContent = t('테스트 발송');
  const pushBtns = document.createElement('div');
  pushBtns.className = 'vt-set-btns';
  pushBtns.append(subBtn, pushBtn);
  const pushRow = row(t('웹 푸시'), pushBtns);
  const pushStatus = statusLine(t('확인 중…'));
  pushRow.querySelector('.vt-set-label').appendChild(pushStatus);
  notif.appendChild(pushRow);
  refreshPushLabel();
  vtFetch('/api/push/status').then((r) => {
    if (!r) return;
    pushStatus.textContent = r.available
      ? t('구독 {n}대 · VAPID {vapid}', { n: r.subscriptions, vapid: r.configured ? t('확인됨') : t('미설정') })
      : t('사용 불가 (pywebpush 미설치)');
  }).catch(() => { pushStatus.textContent = t('상태를 확인할 수 없습니다.'); });
  pushBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/api/push/test', { method: 'POST' });
      toast(r.ok ? t('발송됨 · 구독 {n}건', { n: r.sent }) : t('발송 실패'));
    } catch (e) { toast(e.message || t('발송 실패'), 'error'); }
  });

  _voiceSessionCleanup?.();
  const sessionVoice = renderSessionVoice();
  const defaults = renderVoiceDefaults();
  _voiceSessionCleanup = () => { sessionVoice.cleanup(); defaults.cleanup(); };

  // 3. 음성 입력 + 받아 둔 항목 관리 — voice-models.js
  const vm = renderVoiceInput();
  const statusBtn = document.createElement('button');
  statusBtn.type = 'button'; statusBtn.className = 'vt-btn sm quiet';
  statusBtn.textContent = t('음성 상태 열기');
  statusBtn.addEventListener('click', () => getAction('voice.status')?.());
  // 입력 대상 고정(이 기기만) + 확인 후 전송(서버 전체). 모바일에서는 dock이 없으니 설정에서도 고를 수 있다.
  const pinSel = document.createElement('select');
  pinSel.className = 'vt-select'; pinSel.setAttribute('aria-label', t('입력 대상'));
  const pinWrap = document.createElement('div');
  pinWrap.className = 'vt-select-wrap'; pinWrap.appendChild(pinSel);
  _voicePinCleanup?.();
  _voicePinCleanup = bindPinSelect(pinSel);
  const confirmRow = row(t('확인 후 전송'), selectControl('voice.confirm', {
    off: t('확인 없이 바로 입력'), shell: t('일반 셸이면 확인'), on: t('항상 확인'),
  }), t('확인하면 인식 결과를 고쳐서 입력할 수 있습니다. 「일반 셸이면 확인」은 Claude·Codex 같은 AI 에이전트 화면에는 바로 입력하고, 일반 셸이나 알 수 없는 화면에는 먼저 확인합니다. rm -rf, sudo, curl | sh, git push --force 같은 위험한 명령은 「확인 없이 바로 입력」이 아니면 항상 확인합니다.'),
    { scope: scopeOf('voice.confirm'), key: 'voice.confirm' });
  vm.input.prepend(
    row(t('입력 현황'), statusBtn),
    row(t('입력 대상'), pinWrap, t('이 기기에서 음성이 들어갈 세션입니다. 고정하면 다른 세션을 보고 있어도 그 세션으로 입력하고, 세션이 없어지면 다른 곳으로 보내지 않고 녹음을 막습니다.')),
    confirmRow);
  // 녹음 중 처리 — 서버가 녹음 시작에 읽는다(녹음 중에 바꾸면 다음 녹음부터). 파일 한 번 전송(폴백) 경로에는 없다.
  vm.input.append(
    row(t('자동 종료'), boolControl('voice.autoStop'),
      t('말이 끝나고 1초 동안 조용하면 녹음을 자동으로 끝내고 전송합니다. 말을 시작하지 않으면 끝내지 않습니다.'),
      { scope: scopeOf('voice.autoStop'), key: 'voice.autoStop' }),
    row(t('부분 결과 표시'), boolControl('voice.partialResults'),
      t('녹음하는 동안 인식 중인 문장을 흐리게 미리 보여 줍니다. 입력은 녹음이 끝난 뒤 최종 결과로만 합니다. 서버의 음성 인식을 더 씁니다.'),
      { scope: scopeOf('voice.partialResults'), key: 'voice.partialResults' }),
  );

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
    voiceOnlyBtn.textContent = isOn ? t('끄기') : t('켜기');
    voiceOnlyBtn.classList.toggle('active', isOn);
  };
  paintVoiceOnly();
  voiceOnlyBtn.addEventListener('click', () => { getAction('voice.only-toggle')?.(); paintVoiceOnly(); });
  desk.appendChild(row(t('음성 전용 모드'), voiceOnlyBtn,
    t('화면을 마이크 하나로 채웁니다 — 이어폰만으로 조작할 때 씁니다.')));

  // 6. 연결된 화면 — clients.js의 기존 렌더러를 그대로 이식(중복 구현 금지).
  const clientsHost = document.createElement('div');
  desk.appendChild(clientsHost);
  if (_voiceClientsCleanup) { _voiceClientsCleanup(); _voiceClientsCleanup = null; }
  const activeSess = getSession(activeSessionId());
  const activeTmux = activeSess && (activeSess.tmuxName || activeSess.tmux_name);
  if (activeTmux) _voiceClientsCleanup = mountClients(clientsHost, activeTmux, activeSess && activeSess.remote, activeSessionId());

  frag.append(sessionVoice.element, defaults.element, group(t('알림'), notif), vm.input, vm.manage, group(t('데스크톱 음성'), desk));
  return frag;
}
