// 터미널 줄 → 링크 후보. links.js에서 분리한 순수 로직(DOM·네트워크 없음)이라 테스트가 쉽다.
//
// 정규식은 문자열 위치를 주지만 xterm 범위는 **칸 번호**다. 한글·이모지는 문자열에서
// 1글자여도 2칸이라, 문자열 위치를 그대로 칸으로 쓰면 앞에 전각 문자가 있는 줄에서
// 밑줄이 왼쪽으로 밀린다. 그래서 줄을 칸 단위로 읽어 글자마다 (행, 칸)을 기록한다.
// 줄바꿈으로 이어진 줄(isWrapped)은 하나의 논리 줄로 합쳐 긴 URL도 한 링크로 잡는다.

const URL_RE = /https?:\/\/[^\s<>"'\)\]]+/g;
// 슬래시 없는 단어(README.md)는 오탐이 많아 잡지 않는다. `~/`는 홈 기준 경로.
const PATH_RE = /(?:~\/(?:[\w.\-]+\/)*[\w.\-]+\.\w{1,10}|\.{0,2}\/(?:[\w.\-]+\/)*[\w.\-]+\.\w{1,10}|(?:[\w.\-]+\/)+[\w.\-]+\.\w{1,10})(?::\d+(?::\d+)?)?/g;

/** `src/a.py:42:10` → `{path:'src/a.py', line:42, col:10}`. 줄·열이 없으면 null. 0이나 이상한 값은 버린다. */
export function splitPathLine(text) {
  const m = /^(.*?):(\d+)(?::(\d+))?$/.exec(String(text || ''));
  if (!m || !m[1]) return { path: String(text || ''), line: null, col: null };
  const line = parseInt(m[2], 10);
  const col = m[3] ? parseInt(m[3], 10) : null;
  return { path: m[1], line: line > 0 ? line : null, col: col && col > 0 ? col : null };
}

export function findLinkMatches(text) {
  const urls = [];
  for (const m of text.matchAll(URL_RE)) {
    urls.push({ start: m.index, end: m.index + m[0].length, text: m[0], kind: 'url' });
  }
  const out = urls.slice();
  // URL 안의 "/example.com" 같은 부분이 경로로 또 잡히지 않게 겹치는 경로는 버린다.
  for (const m of text.matchAll(PATH_RE)) {
    const start = m.index, end = m.index + m[0].length;
    if (urls.some((u) => start < u.end && end > u.start)) continue;
    out.push({ start, end, text: m[0], kind: 'path' });
  }
  return out;
}

// 앱이 직접 줄바꿈을 넣어 꺾은 URL·경로(Claude Code 등: 폭 끝에서 자르고 다음 줄을 공백 몇 칸 들여쓴다)는
// 터미널 입장에서 그냥 새 줄이라 isWrapped 가 아니다. 아래 조건을 모두 만족할 때만 한 줄로 이어 본다(오탐을 줄이려고 좁게).
//   - 윗줄 오른쪽에 남는 칸이 HARD_WRAP_MARGIN 이하(폭 끝까지 찼다 — 입력창 꺾임은 1~2칸 남는다)
//   - 윗줄이 URL 또는 `/` 가 든 경로 글자로 끝난다(마지막 공백 이후 덩어리가 통째로 URL·경로 글자)
//   - 아랫줄은 들여쓰기 공백 뒤가 곧바로 URL·경로 글자 덩어리로 시작하고, 그 덩어리가 새 `http(s)://` 가 아니다
const HARD_WRAP_MARGIN = 2;
const URL_TAIL_RE = /https?:\/\/[A-Za-z0-9\-._~:\/?#@!$&*+,;=%]*$/;
const PATH_TAIL_RE = /(?:^|\s)([~\w.\-:]*\/[\w.\-\/:]*)$/;
const CONT_HEAD_RE = /^[A-Za-z0-9\-._~:\/?#@!$&*+,;=%]+/;

/** 한 행을 [{ch, w, x}] 로 읽는다(전각 뒷칸 제외). 읽을 수 없으면 null. */
function readCells(line) {
  if (!line) return null;
  const cells = [];
  for (let x = 0; x < line.length; x++) {
    const cell = line.getCell(x);
    if (!cell) break;
    const w = cell.getWidth();
    if (w === 0) continue;
    cells.push({ ch: cell.getChars() || ' ', w, x });
  }
  return cells;
}

/** upper 행이 앱의 줄바꿈으로 lower 행에 이어지는지(위 조건). */
function isHardWrap(upper, lower) {
  if (!upper || !lower || lower.isWrapped) return false;
  const up = readCells(upper), low = readCells(lower);
  if (!up || !low) return false;
  let last = up.length - 1;
  while (last >= 0 && up[last].ch === ' ') last--;
  if (last < 0) return false;
  if (upper.length - (up[last].x + up[last].w) > HARD_WRAP_MARGIN) return false;
  const upText = up.slice(0, last + 1).map((c) => c.ch).join('');
  const lowText = low.map((c) => c.ch).join('');
  const head = CONT_HEAD_RE.exec(lowText.replace(/^ +/, ''));
  if (!head || /^https?:\/\//i.test(head[0])) return false;
  if (URL_TAIL_RE.test(upText)) return true;
  const m = PATH_TAIL_RE.exec(upText);
  return !!m && !URL_TAIL_RE.test(upText) && !/^https?:/i.test(m[1]);
}

/** 논리 줄(줄바꿈으로 이어진 행들)을 읽는다.
 *  → { text, rows[], cols[], widths[] } — 문자열 한 글자(UTF-16 단위)마다 행(1부터)·칸(0부터)·칸 수. */
export function readLogicalLine(buf, row) {
  let first = row;
  while (first > 1) {
    const l = buf.getLine(first - 1);
    if (!l) break;
    if (!l.isWrapped && !isHardWrap(buf.getLine(first - 2), l)) break;
    first--;
  }
  let text = '';
  const rows = [], cols = [], widths = [];
  for (let r = first; ; r++) {
    const line = buf.getLine(r - 1);
    if (!line) break;
    let hard = false;
    if (r > first && !line.isWrapped) {
      hard = isHardWrap(buf.getLine(r - 2), line);
      if (!hard) break;
    }
    if (hard) {
      // 앱이 꺾은 자리: 윗줄 끝의 공백 패딩과 아랫줄 앞의 들여쓰기는 주소의 일부가 아니다.
      while (text.endsWith(' ')) { text = text.slice(0, -1); rows.pop(); cols.pop(); widths.pop(); }
    }
    let skipIndent = hard;
    // 마지막 칸 뒤 공백 패딩은 줄 끝이 아니면 의미가 없지만, 이어지는 줄이 있으면 그대로 둔다.
    for (let x = 0; x < line.length; x++) {
      const cell = line.getCell(x);
      if (!cell) break;
      const w = cell.getWidth();
      if (w === 0) continue;               // 전각 문자의 뒷칸
      const ch = cell.getChars() || ' ';
      if (skipIndent) { if (ch === ' ') continue; skipIndent = false; }
      for (let k = 0; k < ch.length; k++) {
        text += ch[k]; rows.push(r); cols.push(x); widths.push(w);
      }
    }
  }
  return { text, rows, cols, widths };
}

/** 요청한 행에 걸치는 링크만 xterm 범위(1-based, 끝 포함)로 돌려준다. */
export function linksForRow(buf, row) {
  const L = readLogicalLine(buf, row);
  const out = [];
  for (const m of findLinkMatches(L.text)) {
    const s = m.start, e = m.end - 1;
    if (L.rows[s] > row || L.rows[e] < row) continue;
    out.push({
      ...m,
      range: {
        start: { x: L.cols[s] + 1, y: L.rows[s] },
        end: { x: L.cols[e] + L.widths[e], y: L.rows[e] },
      },
    });
  }
  return out;
}
