import { vtFetch } from '../../../core/api.js';
import { activeSessionId, getSession, subscribe } from '../../../core/store.js';
import { screenId } from '../../../core/screen-id.js';
import { getAction } from '../../../core/dom.js';
import { numberSpinner } from '../number-spinner.js';
import { row, group, toast } from '../controls.js';
import { t } from '../../../core/i18n.js';

// Rebuilding voice controls after a save must not drop keyboard focus.
async function refreshNumberFocus(host, refresh) {
  const focused = document.activeElement;
  const restore = host.contains(focused) && focused?.matches('.vt-range-number');
  await refresh();
  if (restore && !focused.isConnected && host.isConnected && document.activeElement === document.body) {
    host.querySelector('.vt-range-number')?.focus();
  }
}

export function renderSessionVoice() {
  const host = document.createElement('div');
  let disposed = false;
  let revision = 0;
  let selected = '';
  let inputObserver = null;
  const screen = screenId();
  const button = (text, fn) => {
    const b = document.createElement('button'); b.type = 'button';
    b.className = 'vt-btn sm quiet'; b.textContent = text; b.onclick = fn; return b;
  };
  async function refresh() {
    inputObserver?.disconnect(); inputObserver = null;
    const id = activeSessionId(); selected = id;
    const seq = ++revision;
    const sess = getSession(id);
    host.replaceChildren();
    if (!id || sess?.remote || (window.vtRelayHost?.() && window.vtRelayHost() !== 'local')) {
      host.textContent = t('로컬 tmux 세션을 선택하세요. 원격 호스트 음성은 직접 접속해 사용하세요.'); return;
    }
    try {
      const data = await vtFetch(`/voice/session?session_id=${encodeURIComponent(id)}&screen=${screen}`);
      if (disposed || seq !== revision) return;
      if (!data?.target || !data?.policy) { host.textContent = t('음성 설정을 확인할 수 없습니다'); return; }
      const { target, policy } = data;
      window.voiceRememberSession?.(target.name, target.key, policy, data.owns);
      const title = document.createElement('p');
      title.textContent = `${target.name} · ${data.owns ? t('이 화면에서 듣는 중') : data.claimed ? t('다른 화면에서 듣는 중') : policy.destination === 'server' ? t('서버에서 듣기') : t('재생 화면 미선택')}`;
      host.appendChild(title);
      // 모바일에서는 dock이 숨겨진다. 같은 녹음 액션과 상태를 설정에서도 제공한다.
      const record = button(t('녹음 시작'), () => getAction('voice.record')?.());
      const inputStatus = document.createElement('p'); inputStatus.className = 'vt-set-help';
      const mic = document.getElementById('mic-btn-wrap');
      const source = document.getElementById('mic-status');
      const paintInput = () => {
        record.textContent = mic?.classList.contains('recording') ? t('녹음 종료') : t('녹음 시작');
        inputStatus.textContent = source?.textContent || t('녹음 시작에 입력 대상을 고정합니다.');
      };
      host.appendChild(row(t('음성 입력'), record)); host.appendChild(inputStatus);
      paintInput();
      inputObserver = new MutationObserver(paintInput);
      if (mic) inputObserver.observe(mic, { attributes: true, attributeFilter: ['class'] });
      if (source) inputObserver.observe(source, { childList: true, subtree: true, characterData: true });
      const controls = [];
      const save = async (changes, transfer = false) => {
        for (const el of controls) { if (el.vtSetDisabled) el.vtSetDisabled(true); else el.disabled = true; }
        try {
          await vtFetch('/voice/session', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: id, screen, policy: changes, transfer }) });
          if (!disposed && selected === id) await refreshNumberFocus(host, refresh);
          return true;
        } catch (e) { toast(e.message, 'error'); if (!disposed && selected === id) await refreshNumberFocus(host, refresh); return false; }
      };
      const select = (label, values, key, help) => {
        const el = document.createElement('select'); el.className = 'vt-select';
        for (const [value, text] of Object.entries(values)) {
          const option = document.createElement('option'); option.value = value; option.textContent = text; el.appendChild(option);
        }
        el.value = policy[key]; el.onchange = () => save({ [key]: el.value }); controls.push(el);
        host.appendChild(row(label, el, help));
      };
      select(t('이 세션 완료 알림'), { off: t('끔'), sound: t('완료음만'), speak: t('응답 읽기') }, 'mode',
        t('켜 둔 세션만 읽습니다. 화면을 다른 세션으로 옮겨도 설정은 유지됩니다.'));
      select(t('재생 위치'), { browser: t('이 기기'), server: t('서버 컴퓨터') }, 'destination',
        t('이 기기가 연결되지 않으면 서버에서 대신 읽지 않습니다.'));
      select(t('TTS 엔진'), { auto: t('자동 (edge → 로컬)'), local: t('로컬 음성만'), edge: 'edge-tts' }, 'engine',
        t('edge-tts는 읽을 텍스트를 외부 서비스에 보냅니다. 응답 끝부분 최대 200자를 읽습니다.'));
      const min = numberSpinner({ value: policy.min_sec, min: 0, max: 600, onCommit: (value) => save({ min_sec: value }) });
      controls.push(min.element);
      host.appendChild(row(t('최소 작업 시간 (초)'), min.element));
      for (const [key, label] of [['success_only', t('오류 응답 제외')], ['only_unfocused', t('이 세션을 보고 있으면 서버 소리 끄기')]]) {
        const input = document.createElement('input'); input.type = 'checkbox'; input.checked = policy[key];
        input.onchange = () => save({ [key]: input.checked }); controls.push(input); host.appendChild(row(label, input));
      }
      const buttons = document.createElement('div'); buttons.className = 'vt-set-btns';
      if (policy.destination === 'browser') {
        const move = button(data.owns ? t('이 화면에서 듣기 유지') : t('이 기기로 옮기기'), () => save({}, true));
        buttons.appendChild(move); controls.push(move);
      }
      const test = button(t('소리 듣기'), async () => {
        try {
          const r = await vtFetch('/voice/test', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: id, screen }) });
          if (!r.ok) toast(t('출력 위치를 연결하거나 세션 알림을 켜 주세요'));
        } catch (e) { toast(e.message, 'error'); }
      });
      buttons.appendChild(test); controls.push(test); host.appendChild(row(t('음성 테스트'), buttons));
      const status = document.createElement('p'); status.className = 'vt-set-help';
      const statusLabel = { played: t('재생 완료'), failed: t('재생 실패'), cancelled: t('재생 취소'), no_final_response: t('최종 응답 없음'), queue_full: t('대기열 가득 참') }[data.status];
      status.textContent = t('최종 응답 어댑터: Claude · Codex{status}. 연결이 모호하면 낭독하지 않습니다.', { status: statusLabel ? ` · ${statusLabel}` : '' });
      host.appendChild(status);
      const bind = document.createElement('p'); bind.className = 'vt-set-help';
      bind.textContent = t('같은 폴더의 에이전트를 구분하려면 해당 pane에서 fsh voice-bind <claude|codex> <대화 ID>를 실행하세요.');
      host.appendChild(bind);
    } catch (e) { if (!disposed && seq === revision) host.textContent = e.message; }
  }
  const unsub = subscribe(() => { if (selected !== activeSessionId()) refresh(); });
  const update = () => refresh();
  window.addEventListener('voice-connected', update);
  // 자신의 PUT 응답이 갱신을 수행한다. 타 화면 정책 변경만 이벤트로 갱신한다.
  const policyChanged = (e) => { if (!e.detail?.owns) refresh(); };
  window.addEventListener('voice-policy', policyChanged);
  refresh();
  return { element: group(t('현재 세션 음성'), host), cleanup: () => {
    disposed = true; revision++; inputObserver?.disconnect(); unsub(); window.removeEventListener('voice-connected', update);
    window.removeEventListener('voice-policy', policyChanged);
  } };
}

export function renderVoiceDefaults() {
  const host = document.createElement('div');
  let disposed = false;
  async function refresh() {
    try {
      const { policy } = await vtFetch('/voice/defaults');
      if (disposed) return;
      host.replaceChildren();
      const help = document.createElement('p'); help.className = 'vt-set-help';
      help.textContent = t('처음 음성 설정을 확인하는 세션에 적용합니다. 기존 세션은 바뀌지 않으며 이 기기 재생은 화면에서 연결해야 합니다.');
      host.appendChild(help);
      const save = async (changes) => {
        try {
          await vtFetch('/voice/defaults', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ policy: changes }) });
          if (!disposed) await refreshNumberFocus(host, refresh);
          return true;
        } catch (e) { toast(e.message, 'error'); return false; }
      };
      const select = (label, values, key) => {
        const el = document.createElement('select'); el.className = 'vt-select';
        for (const [value, text] of Object.entries(values)) {
          const option = document.createElement('option'); option.value = value; option.textContent = text; el.appendChild(option);
        }
        el.value = policy[key]; el.onchange = () => save({ [key]: el.value }); host.appendChild(row(label, el));
      };
      select(t('새 세션 완료 알림'), { off: t('끔'), sound: t('완료음만'), speak: t('응답 읽기') }, 'mode');
      select(t('새 세션 재생 위치'), { browser: t('이 기기'), server: t('서버 컴퓨터') }, 'destination');
      select(t('새 세션 TTS 엔진'), { auto: t('자동 (edge → 로컬)'), local: t('로컬 음성만'), edge: 'edge-tts' }, 'engine');
      const min = numberSpinner({ value: policy.min_sec, min: 0, max: 600, onCommit: (value) => save({ min_sec: value }) });
      host.appendChild(row(t('새 세션 최소 작업 시간 (초)'), min.element));
      const errors = document.createElement('input'); errors.type = 'checkbox'; errors.checked = policy.success_only;
      errors.onchange = () => save({ success_only: errors.checked }); host.appendChild(row(t('새 세션 오류 응답 제외'), errors));
    } catch (e) { if (!disposed) host.textContent = e.message; }
  }
  refresh();
  return { element: group(t('새 세션 기본값'), host), cleanup: () => { disposed = true; } };
}
