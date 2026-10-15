// T4: 출력 속 URL·파일 경로 자동 링크화. F4에서 terminal.js(구 :404-480)에서 분리.
// xterm.js 코어의 registerLinkProvider를 직접 쓴다 — 벤더에 addon-web-links가
// 없고, 파일 경로 인식은 표준 애드온이 아예 없어서 URL·경로 둘 다 우리가 직접
// 정규식으로 찾는 편이 애드온 하나 더 늘리는 것보다 낫다고 판단.
//
// 정규식·칸 번호 계산은 link-scan.js. 경로 정규식은 슬래시를 반드시 요구한다(`/`로 시작하거나 최소 한 디렉토리
// 세그먼트) — "README.md"처럼 슬래시 없는 평범한 단어까지 링크로 잡으면
// 오탐이 너무 많아진다(약어·문장 속 마침표 등). 대신 `src/foo.py:42:10`처럼
// 컴파일러/린터/git 출력에 흔한 `:줄:열` 접미사는 인식한다.
import { getSession } from '../core/store.js';
import { vtFetch } from '../core/api.js';
import { openFile } from '../panels/viewer-lazy.js';
import { linksForRow } from './link-scan.js';

// 세션의 tmux cwd를 조회 — 상대경로를 그 위치 기준으로 풀기 위해서다.
// _openAtTerminalCwd(viewer.js)와 같은 방식.
async function _getSessionCwd(id) {
  const s = getSession(id);
  const tmuxName = s && (s.tmuxName || s.tmux_name);
  if (!tmuxName) return null;
  try {
    const list = await vtFetch('/api/tmux/sessions');
    const info = (list || []).find((x) => x.name === tmuxName);
    return (info && info.cwd) || null;
  } catch (_) { return null; }
}

let _home = null;
async function _getHome() {
  if (_home) return _home;
  try { _home = (await vtFetch('/api/fs/roots')).home || null; } catch (_) { /* 홈을 모르면 열지 않는다 */ }
  return _home;
}

async function _openLinkAsPath(id, rawText) {
  const clean = rawText.replace(/:\d+(?::\d+)?$/, '');
  let full = clean;
  if (full.startsWith('~/')) {
    const home = await _getHome();
    if (!home) return;
    full = home.replace(/\/$/, '') + full.slice(1);
  } else if (!full.startsWith('/')) {
    const cwd = await _getSessionCwd(id);
    if (cwd) full = cwd.replace(/\/$/, '') + '/' + full;
  }
  // ADR-44 — 표면 설정(sheet/float/pane)에 따라 연다.
  openFile(full);
}

export function wireLinks(id, term) {
  if (typeof term.registerLinkProvider !== 'function') return; // 구버전 xterm 방어
  term.registerLinkProvider({
    provideLinks(bufferLineNumber, callback) {
      const matches = linksForRow(term.buffer.active, bufferLineNumber);
      if (!matches.length) { callback(undefined); return; }
      callback(matches.map((m) => ({
        range: m.range,
        text: m.text,
        activate: () => {
          if (m.kind === 'url') {
            window.open(m.text, '_blank', 'noopener,noreferrer');
          } else {
            _openLinkAsPath(id, m.text);
          }
        },
      })));
    },
  });
}
