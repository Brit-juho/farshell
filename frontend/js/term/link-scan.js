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
const TAIL = /(?:https?:\/\/[\w\-.~:\/?#@!$&*+,;=%]*|(?:^|\s)[~\w.\-:]*\/[\w.\-\/:]*)$/;
const HEAD = /^ *([\w\-.~:\/?#@!$&*+,;=%]+)/;

// 한 행 → [[글자, 끝 칸]] (전각 뒷칸 제외)
function cellsOf(l) {
  const a = [];
  for (let x = 0, c; x < l.length && (c = l.getCell(x)); x++) if (c.getWidth()) a.push([c.getChars() || ' ', x + c.getWidth()]);
  return a;
}

/** u 행이 앱의 줄바꿈으로 l 행에 이어지는지(위 조건, 여백 2칸 이하). */
function hardWrap(u, l) {
  if (!u || !l || l.isWrapped) return false;
  const a = cellsOf(u), h = HEAD.exec(cellsOf(l).map((c) => c[0]).join(''));
  while (a.length && a[a.length - 1][0] === ' ') a.pop();
  return !!a.length && u.length - a[a.length - 1][1] <= 2 && !!h && !/^https?:\/\//i.test(h[1])
    && TAIL.test(a.map((c) => c[0]).join(''));
}

/** 논리 줄(줄바꿈으로 이어진 행들)을 읽는다.
 *  → { text, rows[], cols[], widths[] } — 문자열 한 글자(UTF-16 단위)마다 행(1부터)·칸(0부터)·칸 수. */
export function readLogicalLine(buf, row) {
  let first = row;
  for (let l; first > 1 && (l = buf.getLine(first - 1)) && (l.isWrapped || hardWrap(buf.getLine(first - 2), l)); first--);
  let text = '';
  const rows = [], cols = [], widths = [];
  for (let r = first, line; (line = buf.getLine(r - 1)); r++) {
    const hard = r > first && !line.isWrapped;
    if (hard && !hardWrap(buf.getLine(r - 2), line)) break;
    // 앱이 꺾은 자리: 윗줄 끝 패딩·아랫줄 들여쓰기는 주소가 아니다.
    while (hard && text.endsWith(' ')) { text = text.slice(0, -1); rows.pop(); cols.pop(); widths.pop(); }
    let indent = hard;
    for (let x = 0, cell; x < line.length && (cell = line.getCell(x)); x++) {
      const w = cell.getWidth();
      if (w === 0) continue;               // 전각 문자의 뒷칸
      const ch = cell.getChars() || ' ';
      if (indent && ch === ' ') continue;
      indent = false;
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
