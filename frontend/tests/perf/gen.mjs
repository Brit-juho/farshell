// 성능 측정용 입력 생성기 — 실제 코드나 개인 경로를 쓰지 않는다(AGENTS.md 계약 7).
// 같은 seed 면 같은 바이트가 나온다(측정끼리 비교할 수 있게). 브라우저와 Node 양쪽에서 쓴다.

/** mulberry32 — 작은 결정적 난수. */
export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const WORDS = ['alpha', 'beta', 'gamma', 'delta', 'item', 'value', 'count', 'node', 'list', 'entry',
  'state', 'index', 'result', 'buffer', 'offset', 'target', 'source', 'handler', 'config', 'option'];

function word(r) { return WORDS[Math.floor(r() * WORDS.length)]; }
function ident(r) { return word(r) + (r() < 0.5 ? word(r)[0].toUpperCase() + word(r).slice(1) : ''); }

/** 한 줄짜리 JS 문장 — 들여쓰기, 주석, 문자열, 숫자가 섞인다(하이라이터가 일할 거리). */
function jsLine(r, depth) {
  const pad = '  '.repeat(depth);
  const k = r();
  if (k < 0.08) return `${pad}// ${word(r)} ${word(r)} ${word(r)} — ${word(r)} ${word(r)}`;
  if (k < 0.16) return `${pad}function ${ident(r)}(${ident(r)}, ${ident(r)}) {`;
  if (k < 0.24) return `${pad}}`;
  if (k < 0.40) return `${pad}const ${ident(r)} = ${ident(r)}.${word(r)}(${Math.floor(r() * 1000)}, '${word(r)}-${word(r)}');`;
  if (k < 0.52) return `${pad}if (${ident(r)} && ${ident(r)}.length > ${Math.floor(r() * 64)}) return ${ident(r)};`;
  if (k < 0.62) return `${pad}for (let i = 0; i < ${ident(r)}.length; i += 1) ${ident(r)}[i] = "${word(r)}";`;
  if (k < 0.72) return `${pad}${ident(r)}.${word(r)} = { ${word(r)}: ${r().toFixed(3)}, ${word(r)}: null };`;
  if (k < 0.80) return '';
  return `${pad}let ${ident(r)} = await ${ident(r)}(\`${word(r)}/\${${ident(r)}}\`);`;
}

/** 대략 `bytes` 바이트의 여러 줄 JS 텍스트(UTF-8 기준, 줄 끝 `\n`). */
export function genCode(bytes, seed = 1) {
  const r = rng(seed);
  const out = [];
  let size = 0;
  let depth = 0;
  while (size < bytes) {
    const ln = jsLine(r, depth);
    if (ln.endsWith('{')) depth = Math.min(depth + 1, 6);
    else if (ln.trim() === '}') depth = Math.max(depth - 1, 0);
    out.push(ln);
    size += ln.length + 1;
  }
  return out.join('\n') + '\n';
}

/** 줄바꿈 없는 한 줄(minified 번들 모양), 대략 `bytes` 바이트. */
export function genLongLine(bytes, seed = 2) {
  const r = rng(seed);
  const parts = [];
  let size = 0;
  let n = 0;
  while (size < bytes) {
    const s = `var ${word(r)[0]}${n}=function(${word(r)[0]}){return ${word(r)[0]}+${Math.floor(r() * 99)}};`;
    parts.push(s);
    size += s.length;
    n += 1;
  }
  return parts.join('');
}

/** 대략 `lines` 줄의 통합 diff(한 파일, 50줄 hunk 여러 개). */
export function genDiff(lines, seed = 3) {
  const r = rng(seed);
  const out = [
    'diff --git a/src/sample.js b/src/sample.js',
    'index 1111111..2222222 100644',
    '--- a/src/sample.js',
    '+++ b/src/sample.js',
  ];
  let oldNo = 1;
  let newNo = 1;
  while (out.length < lines) {
    const body = [];
    let o = 0;
    let n = 0;
    for (let i = 0; i < 49; i += 1) {
      const k = r();
      const text = jsLine(r, 1 + Math.floor(r() * 2));
      if (k < 0.2) { body.push('-' + text); o += 1; }
      else if (k < 0.4) { body.push('+' + text); n += 1; }
      else { body.push(' ' + text); o += 1; n += 1; }
    }
    out.push(`@@ -${oldNo},${o} +${newNo},${n} @@ function ${ident(r)}() {`);
    out.push(...body);
    oldNo += o + 40;
    newNo += n + 40;
  }
  return out.slice(0, lines).join('\n') + '\n';
}

/**
 * 가짜 디렉터리 트리. 루트 아래 `top`개 폴더, 각 폴더에 `sub`개 하위 폴더, 하위 폴더마다
 * 파일 `files`개. 기본값 50 × 100 = 5,000개 하위 폴더(+ 50개).
 * 반환: path → [{name, dir, size}] (서버 `_list_dir` 와 같은 모양·정렬: 폴더 먼저, 이름순).
 */
export function genTree(root, { top = 50, sub = 100, files = 3 } = {}) {
  const dirs = new Map();
  const pad = (n, w) => String(n).padStart(w, '0');
  const topEntries = [];
  for (let i = 0; i < top; i += 1) {
    const tName = `pkg-${pad(i, 2)}`;
    topEntries.push({ name: tName, dir: true, size: 0 });
    const subEntries = [];
    for (let j = 0; j < sub; j += 1) {
      const sName = `mod-${pad(j, 3)}`;
      subEntries.push({ name: sName, dir: true, size: 0 });
      const fileEntries = [];
      for (let f = 0; f < files; f += 1) fileEntries.push({ name: `file-${f}.js`, dir: false, size: 1000 + f });
      dirs.set(`${root}/${tName}/${sName}`, fileEntries);
    }
    subEntries.push({ name: 'index.js', dir: false, size: 512 });
    dirs.set(`${root}/${tName}`, subEntries);
  }
  topEntries.push({ name: 'README.md', dir: false, size: 2048 });
  dirs.set(root, topEntries);
  return dirs;
}
