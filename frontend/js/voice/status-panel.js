// app.js 쪽 상태 UI. voice.js와는 DOM/이벤트로만 연결한다(공유 store 복제 금지).
import { vtFetch } from '../core/api.js';
import { t } from '../core/i18n.js';
import { icon } from '../ui/icons.js';
import { bindPinSelect, fillPinSelect, getPin, resolveInputTarget, pinMissingMessage, onPinChange } from './target-pin.js';

export function describeStt(status, catalog) {
  const model = catalog?.models?.find((m) => m.id === status.model);
  if (!status.available) return { state: t('사용 불가'), detail: t('이 서버에서 지원하는 음성 인식 엔진이 없습니다.'), usable: false };
  if (!status.selected_valid || !model) return { state: t('모델 선택 필요'), detail: t('설정에서 이 서버에 맞는 모델을 선택하세요.'), usable: false };
  const missing = (model.requirements || []).filter((r) => !r.satisfied).map((r) => r.label);
  if (!model.model_present) missing.push(t('음성 모델'));
  if (missing.length) return { state: t('설치 필요'), detail: t('{model} · {missing} 미설치', { model: model.label, missing: missing.join(', ') }), usable: false };
  if (status.loaded && status.loaded_model === status.model) {
    return { state: t('사용 준비됨'), detail: t('{model} · 서버 메모리에 올라가 있음', { model: model.label }), usable: true };
  }
  return { state: t('모델 준비 필요'), detail: t('{model} · 설치됨, 메모리에 올리면 녹음 가능', { model: model.label }), usable: true };
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
  const meter = document.getElementById('voice-meter');
  const partial = document.getElementById('voice-partial');
  const close = document.getElementById('voice-status-close');
  const pinSel = document.getElementById('voice-pin-select');
  const pinNote = document.getElementById('voice-pin-note');
  // 별도 버튼은 두지 않는다 — 마이크 버튼이 녹음 토글이면서 상태 점(data-voice-state)을 겸하고,
  // 길게 누르기·우클릭·팔레트(voice.status)로 이 패널을 연다.
  if (mic) mic.setAttribute('aria-controls', panel.id);
  // 머리 아이콘(마이크)과 닫기(×) — 고정 path라 innerHTML로 넣는다(사용자 글자는 들어가지 않는다).
  const headIc = panel.querySelector('.vt-voice-ic');
  if (headIc && !headIc.firstChild) headIc.innerHTML = icon('mic', 16);
  if (close && !close.firstChild) close.innerHTML = icon('x', 14);
  const head = panel.querySelector('.vt-voice-head');
  // 음량 막대 24개(시안 O16-F). 값은 아래 paintMeter가 오래된 것부터 밀어 넣는다.
  const BARS = 24;
  const levels = new Array(BARS).fill(0);
  if (meter && !meter.querySelector('.vt-voice-bar')) {
    for (let i = 0; i < BARS; i++) { const b = document.createElement('i'); b.className = 'vt-voice-bar'; meter.appendChild(b); }
  }
  const bars = meter ? [...meter.querySelectorAll('.vt-voice-bar')] : [];
  let openedFrom = null;
  let pending = false;
  let refreshAgain = false;
  let phase = 'idle';
  let targetName = '';
  let startedAt = 0;
  let usable = false;
  let failed = false;
  let refreshedAt = 0;
  const remote = () => window.vtRelayHost?.() && window.vtRelayHost() !== 'local';
  // 마이크 버튼의 상태 점: '' 정상 · busy 진행 중 · setup 준비 필요 · error 실패.
  function paintMic() {
    let v = '';
    if (phase === 'error' || failed || resolveInputTarget().missing) v = 'error';
    else if (['preparing', 'starting', 'processing'].includes(phase)) v = 'busy';
    else if (phase !== 'recording' && !usable && !remote()) v = 'setup';
    // 패널 머리의 서버 점도 같은 값 — 초록 준비 · 호박 준비 필요 · 빨강 실패(색만이 아니라 옆 글자가 상태를 말한다).
    if (head) { if (v) head.dataset.voiceState = v; else delete head.dataset.voiceState; }
    if (!mic) return;
    if (v) mic.dataset.voiceState = v; else delete mic.dataset.voiceState;
  }
  // 입력 대상 고정: 선택 목록·안내·마이크 버튼 표시. 목록이 같으면 DOM을 건드리지 않는다(fillPinSelect).
  function paintPin() {
    const pin = getPin();
    if (pinSel) {
      fillPinSelect(pinSel);
      pinSel.disabled = ['preparing', 'starting', 'recording', 'processing'].includes(phase);
    }
    if (pinNote) {
      pinNote.textContent = resolveInputTarget().missing ? pinMissingMessage()
        : pin ? t('{name}에 고정됨 — 보고 있는 세션이 바뀌어도 여기로 입력합니다', { name: pin }) : '';
    }
    if (mic) {
      if (pin) mic.dataset.voicePin = pin; else delete mic.dataset.voicePin;
      paintMicLabel();
    }
  }
  function paintMicLabel() {
    if (!mic || !state.textContent) return;
    const pin = getPin();
    const base = t('음성 입력 (탭하여 녹음) · {state}', { state: state.textContent });
    mic.setAttribute('aria-label', pin ? `${base} · ${t('{name} (고정)', { name: pin })}` : base);
  }
  function position() {
    if (panel.hidden) return;
    const rect = mic?.getBoundingClientRect();
    const width = Math.min(440, Math.max(0, window.innerWidth - 24));
    // 보이는 마이크 옆. dock이 숨은 모바일에서는 화면 아래에 둔다.
    const anchored = window.innerWidth >= 720 && rect?.width > 0 && rect?.height > 0;
    panel.style.width = `${width}px`;
    let left = 12;
    let top = null;
    let bottom = null;
    if (anchored) {
      const h = panel.offsetHeight;
      if (rect.left + rect.width / 2 < window.innerWidth / 2 && rect.right + 8 + width <= window.innerWidth - 12) {
        // 활동 막대(왼쪽 세로 막대)의 마이크 — 바로 오른쪽에, 아래 끝을 마이크 아래 끝에 맞춘다(시안 O16-F).
        // top이 아니라 bottom으로 붙인다: 녹음 중 부분 결과·확인 영역이 붙어 패널이 커지면 위로 자라야 화면 밖으로 안 나간다.
        left = rect.right + 8;
        bottom = Math.max(12, window.innerHeight - rect.bottom);
      } else {
        left = Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12));
        top = Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - h - 12));
        // 입력 대상·확인 영역이 붙어 패널이 커지면 마이크 아래에 다 들어가지 않아 위로 밀리며 마이크를 덮는다.
        // 그러면 다시 눌러 녹음을 끝낼 수 없으므로, 마이크 옆(왼쪽)으로 비켜 놓는다.
        if (top < rect.bottom + 8 && rect.left - width - 8 >= 12) left = rect.left - width - 8;
      }
    }
    panel.style.left = `${left}px`;
    panel.style.top = top === null ? '' : `${top}px`;
    panel.style.bottom = bottom === null ? '' : `${bottom}px`;
    panel.classList.toggle('vt-voice-mobile', !anchored);
  }
  function paintRecord() {
    record.disabled = phase !== 'recording' && (!usable || ['preparing', 'starting', 'processing'].includes(phase));
    // 녹음 중에는 위험 테두리(시안: 「녹음 종료」는 빨간 테두리, 채운 버튼은 「입력」 하나뿐).
    record.classList.toggle('danger', phase === 'recording');
    record.textContent = phase === 'recording' ? t('녹음 종료') : phase === 'processing' ? t('인식 중') : phase === 'preparing' ? t('모델 준비 중') : phase === 'starting' ? t('마이크 확인 중') : state.textContent === t('모델 준비 필요') ? t('모델 준비') : t('녹음 시작');
    const shown = targetName && getPin() === targetName ? t('{name} (고정)', { name: targetName }) : targetName;
    meta.textContent = targetName
      ? (phase === 'recording'
        ? t('입력 대상: {name} · {sec}초 / 60초', { name: shown, sec: Math.min(60, Math.floor((Date.now() - startedAt) / 1000)) })
        : t('입력 대상: {name}', { name: shown }))
      : '';
    if (meter) { meter.hidden = phase !== 'recording'; if (meter.hidden) { levels.fill(0); paintMeter(0, false); } }
  }
  // 음량 미터 — 값은 voice 번들(meter.js)이 계산해 'vt:voice-level'로 보낸다. 여기서는 그리기만 한다.
  // prefers-reduced-motion이면 5단계로 끊고 초당 4번만 갱신한다.
  const reduceMotion = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  let levelAt = -Infinity;
  function paintMeter(level, clip) {
    if (!meter) return;
    const v = Math.max(0, Math.min(1, Number(level) || 0));
    const shown = reduceMotion() ? Math.round(v * 5) / 5 : Number(v.toFixed(2));
    meter.style.setProperty('--lv', String(reduceMotion() ? shown : v.toFixed(2)));
    // 막대: 가장 새 값이 오른쪽 끝. 높이는 transform으로만 바꾼다(레이아웃을 다시 잡지 않는다).
    levels.shift(); levels.push(shown);
    for (let i = 0; i < bars.length; i++) bars[i].style.transform = `scaleY(${Math.max(0.1, levels[i]).toFixed(2)})`;
    meter.setAttribute('aria-valuenow', String(Math.round(v * 100)));
    if (clip) meter.dataset.clip = '1'; else delete meter.dataset.clip;
  }
  // 부분 결과 — 서버가 녹음 중에 인식한 미리보기(voice 번들이 'vt:voice-partial'로 보낸다). 입력되지 않으며
  // 녹음·인식 중에만 보이고, 최종 결과·오류·새 녹음이 오면 지운다. 스크린리더에는 읽히지 않게 aria-live를 두지 않는다
  // (1.5초마다 바뀌어 최종 결과 낭독을 가린다).
  function paintPartial(text, clipped) {
    if (!partial) return;
    const s = typeof text === 'string' ? text.trim() : '';
    partial.textContent = s ? (clipped ? '… ' : '') + s : '';
    partial.hidden = !s;
    if (s) partial.title = t('미리 보기 — 녹음이 끝나면 다시 인식합니다');
    else partial.removeAttribute('title');
  }
  window.addEventListener('vt:voice-partial', (e) => {
    if (phase !== 'recording' && phase !== 'processing') return;
    paintPartial(e.detail?.text, e.detail?.clipped === true);
  });
  window.addEventListener('vt:voice-level', (e) => {
    if (phase !== 'recording') return;
    const now = performance.now();
    if (reduceMotion() && now - levelAt < 250) return;
    levelAt = now;
    paintMeter(e.detail?.level, e.detail?.clip);
  });
  async function refresh() {
    if (pending) { refreshAgain = true; return; }
    pending = true;
    failed = false;
    try {
      if (remote()) {
        state.textContent = t('직접 접속 필요'); detail.textContent = t('원격 호스트의 음성을 쓰려면 해당 호스트에 직접 접속하세요.');
        usable = false;
      } else {
        const [status, catalog] = await Promise.all([vtFetch('/voice/stt/status'), vtFetch('/voice/stt/catalog')]);
        const view = describeStt(status, catalog);
        state.textContent = view.state;
        detail.textContent = view.detail;
        if (status.last_error) detail.textContent += ' · ' + t('마지막 오류: {error}', { error: status.last_error });
        usable = view.usable;
      }
    } catch (e) {
      failed = true;
      state.textContent = t('상태 확인 실패'); detail.textContent = e.status === 401 || e.status === 403 ? t('로그인이 필요합니다.') : t('서버 연결을 확인하세요.');
      usable = false;
    } finally {
      pending = false; refreshedAt = Date.now();
      mic?.setAttribute('data-tip-sub', state.textContent);
      paintPin(); paintMic(); paintRecord(); position();
      if (refreshAgain) { refreshAgain = false; refresh(); }
    }
  }
  function open(from, focus = false) {
    const wasHidden = panel.hidden;
    if (wasHidden) openedFrom = from || document.activeElement;
    paintPin();
    panel.hidden = false; mic?.setAttribute('aria-expanded', 'true');
    position();
    if (wasHidden || Date.now() - refreshedAt >= 15000) refresh();
    if (focus) close.focus();
  }
  function hide(restoreFocus = false) {
    panel.hidden = true; mic?.setAttribute('aria-expanded', 'false');
    if (restoreFocus && openedFrom?.isConnected) openedFrom.focus();
  }
  // 길게 누르기(500ms)·우클릭(터치 길게 누름도 contextmenu로 온다)은 패널을 연다.
  // 길게 누른 뒤 손을 떼면 click이 따라오므로 한 번 삼켜 녹음이 시작되지 않게 한다.
  let pressTimer = null;
  let swallowClick = false;
  const cancelPress = () => { clearTimeout(pressTimer); pressTimer = null; };
  if (mic) {
    mic.addEventListener('pointerdown', (e) => {
      if (e.button > 0) return;
      cancelPress();
      pressTimer = setTimeout(() => { pressTimer = null; swallowClick = true; open(mic, true); }, 500);
    });
    ['pointerup', 'pointerleave', 'pointercancel'].forEach((t) => mic.addEventListener(t, cancelPress));
    mic.addEventListener('contextmenu', (e) => { e.preventDefault(); cancelPress(); swallowClick = false; open(mic, true); });
    mic.addEventListener('click', (e) => {
      if (!swallowClick) return;
      swallowClick = false; e.stopPropagation(); e.preventDefault();
    }, true);
  }
  close.onclick = () => hide(true);
  panel.addEventListener('click', (e) => {
    if (e.target.closest('[data-action="voice.settings"]')) hide();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !panel.hidden) { hide(true); e.preventDefault(); e.stopPropagation(); }
  }, true);
  document.addEventListener('pointerdown', (e) => {
    if (!panel.hidden && !panel.contains(e.target) && !mic?.contains(e.target)) hide();
  });
  window.addEventListener('resize', position);
  window.addEventListener('vt:voice-stage', (e) => {
    phase = e.detail.phase;
    if (e.detail.target !== undefined) targetName = e.detail.target;
    if (phase === 'recording') startedAt = Date.now();
    if (phase !== 'recording' && phase !== 'processing') paintPartial('');
    paintPin(); paintMic(); paintRecord(); open(mic);
    if (['idle', 'ready', 'error', 'done'].includes(phase)) refresh();
  });
  new MutationObserver(() => {
    if (message.textContent.trim()) open(mic);
  }).observe(message, { childList: true, subtree: true, characterData: true });
  if (pinSel) bindPinSelect(pinSel, { onChange: () => { paintPin(); paintMic(); paintRecord(); position(); } });
  onPinChange(() => { paintPin(); paintMic(); paintRecord(); });
  window.registerAction('voice.status', () => open(mic, true));
  refresh();
  if (message.textContent.trim()) open(mic);
  // 서버 상태는 화면이 보일 때만 갱신. 모델 적재나 다운로드는 하지 않는다.
  window.setInterval(() => {
    if (document.hidden) return;
    paintPin(); paintMic();  // 세션이 열리고 닫히면 고정 대상의 유무가 바뀐다
    if (phase === 'recording') paintRecord();
    const dockVisible = (mic?.getBoundingClientRect().width || 0) > 0;
    if ((!panel.hidden || dockVisible) && Date.now() - refreshedAt >= 15000) refresh();
  }, 1000);
}
