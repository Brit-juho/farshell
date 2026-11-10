import { row, group, statusLine } from '../controls.js';
import { t } from '../../../core/i18n.js';
import { icon } from '../../../ui/icons.js';
import { isRelayed } from '../../../core/host-route.js';
import { loadAccounts } from '../../../term/accounts.js';
import { TOOL_LABEL } from '../../../term/accounts-data.js';
import { authLabel, headroom, usageText, sourceShort, updatedText } from '../../../term/accounts-text.js';
import { setDefaultAccount, ACCOUNTS_CHANGED } from '../../../term/accounts-ui.js';
import { showMcp } from '../../mcp-open.js';

// ── 「계정」 ─────────────────────────────────────────────────────────────────
// 에이전트(Claude·Codex)용 계정 목록과 도구별 **기본 계정**을 정하는 곳이다. 기본 계정은 새 세션을 만들 때 평소처럼
// 쓰이는 계정이라 세션 만들기 메뉴가 아니라 설정이다(데스크톱은 상태 막대의 계정 칩에서도 바꾼다). 폰에는 상태 막대가 없어
// 이 화면이 기본 계정을 바꾸는 유일한 곳이다. 계정을 만들고 로그인하는 일은 「실행 프로필」 관리에서 한다 — 여기서는 링크만 둔다.
// 값은 서버 선택기 목록(/api/agent-profiles/read/picker)을 그대로 보인다. 값이 없으면 「알 수 없음」이고, 사용률로 계정을 자동으로 고르지 않는다.

// 가장 많이 쓴 창의 사용률로 줄 색을 정한다 — 상태 막대 칩과 같은 기준(75% 주의, 90% 경고).
function usageTone(p) {
  const left = headroom(p);
  if (left === null) return '';
  const pct = 100 - left;
  return pct >= 90 ? 'err' : pct >= 75 ? 'warn' : '';
}

export function renderAccountsSection() {
  const frag = document.createDocumentFragment();
  const host = document.createElement('div');
  host.className = 'vt-set-sechost vt-set-accounts';
  frag.appendChild(host);

  const refresh = document.createElement('button');
  refresh.type = 'button'; refresh.className = 'vt-btn sm quiet';
  refresh.innerHTML = icon('refresh-cw', 14, 2);
  refresh.append(document.createTextNode(t('새로고침')));

  const paint = (acc) => {
    host.replaceChildren();
    if (isRelayed()) {
      host.appendChild(statusLine(t('다른 맥을 보는 중에는 계정을 바꿀 수 없습니다. 그 맥의 화면에서 직접 여세요.')));
      return;
    }
    const tools = ['claude', 'codex'].filter((tool) => acc.profiles.some((p) => p.tool === tool));
    if (!acc.ok || !tools.length) {
      host.appendChild(statusLine(acc.ok
        ? t('등록된 계정이 없습니다. 「실행 프로필」에서 프로필을 만들고 로그인하세요.')
        : t('계정 목록을 불러올 수 없습니다.')));
    }
    tools.forEach((tool, i) => {
      const body = document.createElement('div');
      const mine = acc.profiles.filter((p) => p.tool === tool);
      const marked = acc.defaults[tool];
      for (const p of mine) {
        const isDefault = marked?.id === p.id;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'vt-btn sm';
        btn.textContent = isDefault ? t('해제') : t('기본으로 지정');
        btn.setAttribute('aria-label', isDefault
          ? t('{tool} 기본 계정 해제', { tool: TOOL_LABEL[tool] })
          : t('{name}을(를) {tool} 기본 계정으로 지정', { name: p.name, tool: TOOL_LABEL[tool] }));
        btn.disabled = !isDefault && !p.selectable;
        btn.addEventListener('click', async () => {
          btn.disabled = true;
          await (isDefault ? setDefaultAccount(tool, null, '') : setDefaultAccount(tool, p.id, p.name));
          if (btn.isConnected) btn.disabled = false;
        });
        const help = p.selectable ? usageText(p) : `${authLabel(p.auth)}${p.reason ? ` · ${p.reason}` : ''}`;
        const el = row(p.name, btn, help, {
          scopeText: isDefault ? t('기본') : (p.auth === 'ok' ? '' : authLabel(p.auth)) || undefined,
        });
        el.dataset.profile = p.id;
        if (isDefault) el.dataset.default = 'true';
        const meta = el.querySelector('.vt-set-meta');
        const usageHelp = meta.querySelector('.vt-set-help');
        const tone = p.selectable ? usageTone(p) : '';
        if (usageHelp && tone) usageHelp.dataset.tone = tone;
        const origin = [sourceShort(p), updatedText(p)].filter(Boolean).join(' · ');
        if (origin && p.selectable) {
          const more = document.createElement('div');
          more.className = 'vt-set-help';
          more.textContent = origin;
          meta.appendChild(more);
        }
        body.appendChild(el);
      }
      host.appendChild(group(t('{tool} 기본 계정', { tool: TOOL_LABEL[tool] }), body, i === 0 ? refresh : undefined));
      const note = document.createElement('div');
      note.className = 'vt-set-help vt-set-accounts-note';
      note.textContent = marked
        ? t('새로 만드는 세션부터 적용됩니다 · 실행 중인 세션은 그대로입니다')
        : t('기본 계정이 없으면 새 세션은 평소 환경 그대로 만들어집니다');
      body.after(note);
    });
    const link = document.createElement('button');
    link.type = 'button'; link.className = 'vt-btn sm';
    link.textContent = t('실행 프로필 관리 ›');
    link.addEventListener('click', () => showMcp('profiles'));
    const foot = row(t('프로필 추가 · 로그인'), link, t('본인 계정만 등록하세요. 다른 사람의 계정이나 토큰을 넣지 마세요.'));
    host.appendChild(group(t('계정 추가'), foot));
  };

  const load = async (force) => {
    const acc = await loadAccounts({ force });
    if (host.isConnected || !host.parentNode) paint(acc);
  };
  host.appendChild(statusLine(t('계정 목록 확인 중…')));
  refresh.addEventListener('click', () => load(true));
  // 기본 계정이 상태 막대 칩·선택기에서 바뀌면 이 화면도 다시 읽는다. 화면을 떠나면(host가 DOM에서 빠지면) 스스로 해제한다.
  const onChanged = () => { if (!host.isConnected) { window.removeEventListener(ACCOUNTS_CHANGED, onChanged); return; } load(false); };
  window.addEventListener(ACCOUNTS_CHANGED, onChanged);
  load(true);
  return frag;
}
