// T4: 출력 속 URL·파일 경로 자동 링크화. F4에서 terminal.js(구 :404-480)에서 분리.
// xterm.js 코어의 registerLinkProvider를 직접 쓴다 — 벤더에 addon-web-links가
// 없고, 파일 경로 인식은 표준 애드온이 아예 없어서 URL·경로 둘 다 우리가 직접
// 정규식으로 찾는 편이 애드온 하나 더 늘리는 것보다 낫다고 판단.
//
// 경로 정규식은 슬래시를 반드시 요구한다(`/`로 시작하거나 최소 한 디렉토리
// 세그먼트) — "README.md"처럼 슬래시 없는 평범한 단어까지 링크로 잡으면
// 오탐이 너무 많아진다(약어·문장 속 마침표 등). 대신 `src/foo.py:42:10`처럼
// 컴파일러/린터/git 출력에 흔한 `:줄:열` 접미사는 인식한다.
import { getSession } from '../core/store.js';
import { vtFetch } from '../core/api.js';

const _URL_RE = /https?:\/\/[^\s<>"'\)\]]+/g;
const _PATH_RE = /(?:\.{0,2}\/(?:[\w.\-]+\/)*[\w.\-]+\.\w{1,10}|(?:[\w.\-]+\/)+[\w.\-]+\.\w{1,10})(?::\d+(?::\d+)?)?/g;

function _findLinkMatches(text) {
  const urls = [];
  for (const m of text.matchAll(_URL_RE)) {
    urls.push({ start: m.index, end: m.index + m[0].length, text: m[0], kind: 'url' });
  }
  const out = urls.slice();
  // 경로 정규식이 URL 안의 "/example.com" 같은 부분 문자열을 별도 경로로도
  // 잡는다 — URL 매치 범위와 겹치는 경로 매치는 버린다(URL 하나로만 링크).
  for (const m of text.matchAll(_PATH_RE)) {
    const start = m.index, end = m.index + m[0].length;
    if (urls.some((u) => start < u.end && end > u.start)) continue;
    out.push({ start, end, text: m[0], kind: 'path' });
  }
  return out;
}

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

async function _openLinkAsPath(id, rawText) {
  const clean = rawText.replace(/:\d+(?::\d+)?$/, '');
  let full = clean;
  if (!full.startsWith('/')) {
    const cwd = await _getSessionCwd(id);
    if (cwd) full = cwd.replace(/\/$/, '') + '/' + full;
  }
  // viewer.js는 아직 classic script라 showViewer/openFile을 bare identifier로 읽는다.
  if (!document.getElementById('vt-viewer') && typeof showViewer === 'function') {
    await showViewer();
  }
  if (typeof openFile === 'function') openFile(full);
}

export function wireLinks(id, term) {
  if (typeof term.registerLinkProvider !== 'function') return; // 구버전 xterm 방어
  term.registerLinkProvider({
    provideLinks(bufferLineNumber, callback) {
      const line = term.buffer.active.getLine(bufferLineNumber - 1);
      if (!line) { callback(undefined); return; }
      const text = line.translateToString(false);
      const matches = _findLinkMatches(text);
      if (!matches.length) { callback(undefined); return; }
      callback(matches.map((m) => ({
        range: { start: { x: m.start + 1, y: bufferLineNumber }, end: { x: m.end, y: bufferLineNumber } },
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
