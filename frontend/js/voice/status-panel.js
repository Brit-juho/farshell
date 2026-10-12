// app.js 쪽 상태 UI. voice.js와는 DOM/이벤트로만 연결한다(공유 store 복제 금지).
import { vtFetch } from '../core/api.js';

export function describeStt(status, catalog) {
  const model = catalog?.models?.find((m) => m.id === status.model);
  if (!status.available) return { state: '사용 불가', detail: '이 서버에서 지원하는 음성 인식 엔진이 없습니다.', usable: false };
  if (!status.selected_valid || !model) return { state: '모델 선택 필요', detail: '설정에서 이 서버에 맞는 모델을 선택하세요.', usable: false };
  const missing = (model.requirements || []).filter((r) => !r.satisfied).map((r) => r.label);
  if (!model.model_present) missing.push('음성 모델');
  if (missing.length) return { state: '설치 필요', detail: `${model.label} · ${missing.join(', ')} 미설치`, usable: false };
  if (status.loaded && status.loaded_model === status.model) {
    return { state: '사용 준비됨', detail: `${model.label} · 서버 메모리에 올라가 있음`, usable: true };
  }
  return { state: '모델 준비 필요', detail: `${model.label} · 설치됨, 메모리에 올리면 녹음 가능`, usable: true };
}

export function mountVoiceStatus() {
  const panel = document.getElementById('voice-status-panel');
  if (!panel || panel.dataset.mounted) return;
  panel.dataset.mounted = 'true';
  const mic = document.getElementById('mic-btn-wrap');
  const message = document.getElementById('mic-status');
  const state = document.getElementById('voice-stt-state');
  const detail = document.getElementById('voice-stt-detail');
  const meta = document.getElementById('voice-record-meta');
  const record = document.getElementById('voice-panel-record');
  const close = document.getElementById('voice-status-close');
  const trigger = document.createElement('button');
  trigger.type = 'button'; trigger.id = 'voice-status-toggle'; trigger.className = 'vt-voice-summary';
  trigger.setAttribute('aria-controls', panel.id); trigger.setAttribute('aria-expanded', 'false');
  const short = document.createElement('span'); short.className = 'vt-voice-short'; short.textContent = 'STT';
  const summary = document.createElement('span'); summary.textContent = '음성 · 확인 중';
  trigger.append(short, summary);
  let openedFrom = null;
  let pending = false;
  let refreshAgain = false;
  let phase = 'idle';
  let targetName = '';
  let startedAt = 0;
  let usable = false;
  let refreshedAt = 0;
  const remote = () => window.vtRelayHost?.() && window.vtRelayHost() !== 'local';
  function placeTrigger() {
    const home = document.getElementById('vt-dock-voice-status');
    if (home && trigger.parentElement !== home) home.appendChild(trigger);
  }
  function position() {
    if (panel.hidden) return;
    const rect = mic?.getBoundingClientRect();
    const width = Math.min(360, Math.max(0, window.innerWidth - 24));
    // 보이는 마이크 바로 아래. dock이 숨은 모바일에서는 화면 아래에 둔다.
    const anchored = window.innerWidth >= 720 && rect?.width > 0 && rect?.height > 0;
    panel.style.width = `${width}px`;
    panel.style.left = anchored ? `${Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12))}px` : '12px';
    panel.style.top = anchored ? `${Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - panel.offsetHeight - 12))}px` : '';
    panel.classList.toggle('vt-voice-mobile', !anchored);
  }
  function paintRecord() {
    record.disabled = phase !== 'recording' && (!usable || ['preparing', 'starting', 'processing'].includes(phase));
    record.textContent = phase === 'recording' ? '녹음 종료' : phase === 'processing' ? '인식 중' : phase === 'preparing' ? '모델 준비 중' : phase === 'starting' ? '마이크 확인 중' : state.textContent === '모델 준비 필요' ? '모델 준비' : '녹음 시작';
    meta.textContent = targetName ? `입력 대상: ${targetName}${phase === 'recording' ? ` · ${Math.min(60, Math.floor((Date.now() - startedAt) / 1000))}초 / 60초` : ''}` : '';
  }
  async function refresh() {
    if (pending) { refreshAgain = true; return; }
    pending = true;
    try {
      if (remote()) {
        state.textContent = '직접 접속 필요'; detail.textContent = '원격 호스트의 음성을 쓰려면 해당 호스트에 직접 접속하세요.';
        usable = false;
      } else {
        const [status, catalog] = await Promise.all([vtFetch('/voice/stt/status'), vtFetch('/voice/stt/catalog')]);
        const view = describeStt(status, catalog);
        state.textContent = view.state;
        detail.textContent = view.detail;
        if (status.last_error) detail.textContent += ` · 마지막 오류: ${status.last_error}`;
        usable = view.usable;
      }
    } catch (e) {
      state.textContent = '상태 확인 실패'; detail.textContent = e.status === 401 || e.status === 403 ? '로그인이 필요합니다.' : '서버 연결을 확인하세요.';
      usable = false;
    } finally {
      pending = false; refreshedAt = Date.now();
      summary.textContent = `음성 · ${state.textContent}`;
      trigger.setAttribute('aria-label', `음성 입력 상태: ${state.textContent}`);
      trigger.dataset.state = state.textContent;
      mic?.setAttribute('data-tip-sub', state.textContent);
      paintRecord(); position();
      if (refreshAgain) { refreshAgain = false; refresh(); }
    }
  }
  function open(from, focus = false) {
    const wasHidden = panel.hidden;
    if (wasHidden) openedFrom = from || document.activeElement;
    panel.hidden = false; trigger.setAttribute('aria-expanded', 'true');
    position();
    if (wasHidden || Date.now() - refreshedAt >= 15000) refresh();
    if (focus) close.focus();
  }
  function hide(restoreFocus = false) {
    panel.hidden = true; trigger.setAttribute('aria-expanded', 'false');
    if (restoreFocus && openedFrom?.isConnected) openedFrom.focus();
  }
  trigger.onclick = () => panel.hidden ? open(trigger, true) : hide(true);
  close.onclick = () => hide(true);
  panel.addEventListener('click', (e) => {
    if (e.target.closest('[data-action="voice.settings"]')) hide();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !panel.hidden) { hide(true); e.preventDefault(); e.stopPropagation(); }
  }, true);
  document.addEventListener('pointerdown', (e) => {
    if (!panel.hidden && !panel.contains(e.target) && !trigger.contains(e.target) && !mic?.contains(e.target)) hide();
  });
  window.addEventListener('resize', position);
  window.addEventListener('vt:mic-placed', placeTrigger);
  window.addEventListener('vt:voice-stage', (e) => {
    phase = e.detail.phase;
    if (e.detail.target !== undefined) targetName = e.detail.target;
    if (phase === 'recording') startedAt = Date.now();
    paintRecord(); open(mic);
    if (['idle', 'ready', 'error', 'done'].includes(phase)) refresh();
  });
  new MutationObserver(() => {
    if (message.textContent.trim()) open(mic);
  }).observe(message, { childList: true, subtree: true, characterData: true });
  window.registerAction('voice.status', () => open(trigger.isConnected ? trigger : null, true));
  placeTrigger(); refresh();
  if (message.textContent.trim()) open(mic);
  // 서버 상태는 화면이 보일 때만 갱신. 모델 적재나 다운로드는 하지 않는다.
  window.setInterval(() => {
    if (document.hidden) return;
    if (phase === 'recording') paintRecord();
    const dockVisible = trigger.getBoundingClientRect().width > 0;
    if ((!panel.hidden || dockVisible) && Date.now() - refreshedAt >= 15000) refresh();
  }, 1000);
}
