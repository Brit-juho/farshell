import { vtFetch } from '../../../core/api.js';
import { activeSessionId, getSession } from '../../../core/store.js';
import { mountClients } from '../../../layout/clients.js';
import { row, statusLine, toast } from '../controls.js';
import { togglePush, refreshPushLabel } from '../../../pushui.js';
import { getAction } from '../../../core/dom.js';

// ── 「음성」 (N42 · 알림·음성 진단) ──────────────────────────────────────
// (다른 탭 클릭 · 패널 닫기) 반드시 cleanup을 불러야 폴링 타이머가 안 샌다.
let _voiceClientsCleanup = null;

/** 이 섹션을 떠날 때(다른 탭·패널 닫기) 반드시 불러야 폴링 타이머가 안 샌다.
 *  settings.js의 rerender/onClose가 부른다. */
export function cleanupVoiceClients() {
  if (_voiceClientsCleanup) { _voiceClientsCleanup(); _voiceClientsCleanup = null; }
}

export function renderVoiceSection() {
  const frag = document.createDocumentFragment();

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
  frag.appendChild(pushRow);
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

  // 2. 작업 완료 알림 (Stop 훅 TTS 요약)
  const notifyBtn = document.createElement('button');
  notifyBtn.type = 'button'; notifyBtn.className = 'vt-btn sm quiet vt-set-reset'; notifyBtn.textContent = '소리 듣기';
  const notifyRow = row('작업 완료 알림', notifyBtn);
  const notifyStatus = statusLine('확인 중…');
  notifyRow.querySelector('.vt-set-label').appendChild(notifyStatus);
  frag.appendChild(notifyRow);
  vtFetch('/api/hooks/status').then((r) => {
    const events = r && r.events ? Object.entries(r.events) : [];
    if (!events.length) { notifyStatus.textContent = '훅 상태를 확인할 수 없습니다.'; return; }
    const ok = events.filter(([, s]) => s === 'ok').length;
    notifyStatus.textContent = `TTS 요약 · 훅 ${ok}/${events.length} 설치됨`;
  }).catch(() => { notifyStatus.textContent = '훅 상태를 확인할 수 없습니다.'; });
  notifyBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/api/notify/test', { method: 'POST' });
      toast(r.ok ? '알림을 보냈습니다' : (r.configured ? '발송 실패' : 'ntfy/텔레그램이 설정되지 않았습니다'));
    } catch (e) { toast(e.message || '발송 실패', 'error'); }
  });

  // 3. Whisper 모델 (STT 메모리 상주 여부)
  const preloadBtn = document.createElement('button');
  preloadBtn.type = 'button'; preloadBtn.className = 'vt-btn sm quiet vt-set-reset'; preloadBtn.textContent = '미리 적재';
  const unloadBtn = document.createElement('button');
  unloadBtn.type = 'button'; unloadBtn.className = 'vt-btn sm quiet vt-set-reset'; unloadBtn.textContent = '내리기';
  const sttBtns = document.createElement('div');
  sttBtns.className = 'vt-set-btns';
  sttBtns.append(preloadBtn, unloadBtn);
  const sttRow = row('Whisper 모델', sttBtns);
  const sttStatus = statusLine('확인 중…');
  sttRow.querySelector('.vt-set-label').appendChild(sttStatus);
  frag.appendChild(sttRow);
  function refreshStt() {
    vtFetch('/voice/stt/status').then((r) => {
      if (!r) return;
      sttStatus.textContent = !r.available ? '사용 불가' : (r.loaded ? `메모리 상주 · ${r.engine}` : '미적재');
      preloadBtn.disabled = !r.available || r.loaded;
      unloadBtn.disabled = !r.loaded;
    }).catch(() => { sttStatus.textContent = '상태를 확인할 수 없습니다.'; });
  }
  refreshStt();
  preloadBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/voice/stt/preload', { method: 'POST' });
      toast(r.loaded ? `적재됨 · ${r.engine}` : '적재 실패');
    } catch (e) { toast(e.message || '적재 실패', 'error'); }
    refreshStt();
  });
  unloadBtn.addEventListener('click', async () => {
    try {
      const r = await vtFetch('/voice/stt/unload', { method: 'POST' });
      toast(r.unloaded ? '내렸습니다' : '이미 내려가 있습니다');
    } catch (e) { toast(e.message || '내리기 실패', 'error'); }
    refreshStt();
  });

  // 4. 맥에서 음성만 쓰기 (로컬 마이크 — 서버에 상태 조회 API가 없어
  //    버튼 라벨은 클라이언트가 마지막 응답을 기억해 토글한다)
  const localBtn = document.createElement('button');
  localBtn.type = 'button'; localBtn.className = 'vt-btn sm quiet vt-set-reset'; localBtn.textContent = '시작';
  let localRunning = false;
  localBtn.addEventListener('click', async () => {
    try {
      if (!localRunning) {
        const r = await vtFetch('/voice/local/start', { method: 'POST' });
        if (r && r.error) { toast(r.reason || '시작할 수 없습니다', 'error'); return; }
        localRunning = true; localBtn.textContent = '중지';
        toast('맥 로컬 음성 입력을 시작했습니다');
      } else {
        const r = await vtFetch('/voice/local/stop', { method: 'POST' });
        localRunning = false; localBtn.textContent = '시작';
        toast(r && r.text ? `인식됨: ${r.text}` : '중지했습니다');
      }
    } catch (e) { toast(e.message || '실패', 'error'); }
  });
  frag.appendChild(row('맥에서 음성만 쓰기', localBtn,
    '터미널 화면 없이 맥 마이크만 켭니다 — 이어폰으로 조작할 때 씁니다.'));

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
  frag.appendChild(row('음성 전용 모드', voiceOnlyBtn,
    '화면을 마이크 하나로 채웁니다 — 이어폰만으로 조작할 때 씁니다.'));

  // 6. 연결된 화면 — clients.js의 기존 렌더러를 그대로 이식(중복 구현 금지).
  const clientsHost = document.createElement('div');
  frag.appendChild(clientsHost);
  if (_voiceClientsCleanup) { _voiceClientsCleanup(); _voiceClientsCleanup = null; }
  const activeSess = getSession(activeSessionId());
  const activeTmux = activeSess && (activeSess.tmuxName || activeSess.tmux_name);
  if (activeTmux) _voiceClientsCleanup = mountClients(clientsHost, activeTmux, activeSess && activeSess.remote);

  return frag;
}

