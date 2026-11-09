// 편집 모드 거터의 바뀐 줄 표시 계산(파일 개편 후속) — 순수 함수만(jsdom 시험이 그대로 부른다).
// `panels` 청크에 둔다: edit.js가 정적으로 부르므로, `editor` 청크(editor-text.js)에 두면 panels가 CodeMirror 청크를
// 정적으로 끌어온다(build-output.test.js가 막는다). 그리기는 editor.js `setChangeMarks`.

/** 줄 단위 LCS를 쓸 가운데 크기 상한(줄 수 곱). 넘으면 가운데를 통째로 「수정」으로 본다(입력마다 도는 계산이다). */
export const MARK_LCS_MAX_CELLS = 1_000_000;

/**
 * 편집 모드 거터의 바뀐 줄 표시. `base`(불러온 판)와 `cur`(지금 편집기)를 줄 단위로 비교해
 * `[{line, kind}]`(1부터, 줄 순서)를 돌려준다. `kind`:
 * - `add` — 새로 생긴 줄(지운 줄 없이 들어간 덩어리)
 * - `mod` — 바뀐 줄(지운 줄과 들어간 줄이 함께 있는 덩어리)
 * - `del` — 이 줄 **위**에서 줄이 지워졌다, `del-below` — 마지막 줄 **아래**(파일 끝)에서 지워졌다
 * 앞뒤 공통 줄을 잘라 내고 가운데만 LCS로 맞춘다. 가운데가 `MARK_LCS_MAX_CELLS`보다 크면 가운데 전체를 `mod`로.
 */
export function lineChangeMarks(base, cur) {
  if (base === cur) return [];
  const A = String(base).split('\n');
  const B = String(cur).split('\n');
  let pre = 0;
  while (pre < A.length && pre < B.length && A[pre] === B[pre]) pre += 1;
  let suf = 0;
  while (suf < A.length - pre && suf < B.length - pre && A[A.length - 1 - suf] === B[B.length - 1 - suf]) suf += 1;
  const a = A.slice(pre, A.length - suf);
  const b = B.slice(pre, B.length - suf);
  const out = [];
  // 덩어리 하나: 지운 줄 수와, 지금 편집기에서 들어간 줄 번호들(1부터). `at`은 덩어리 바로 뒤의 지금 줄 번호.
  const hunk = (dels, adds, at) => {
    if (adds.length) {
      const kind = dels ? 'mod' : 'add';
      for (const line of adds) out.push({ line, kind });
    } else if (dels) {
      if (at <= B.length) out.push({ line: at, kind: 'del' });
      else out.push({ line: B.length, kind: 'del-below' });
    }
  };
  if (a.length * b.length > MARK_LCS_MAX_CELLS) {
    hunk(a.length, b.map((_, k) => pre + k + 1), pre + b.length + 1);
    return out;
  }
  const n = a.length;
  const m = b.length;
  const W = m + 1;
  const L = new Uint32Array((n + 1) * W);
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      L[i * W + j] = a[i] === b[j] ? L[(i + 1) * W + j + 1] + 1 : Math.max(L[(i + 1) * W + j], L[i * W + j + 1]);
    }
  }
  let i = 0;
  let j = 0;
  let dels = 0;
  let adds = [];
  const flush = () => {
    if (dels || adds.length) hunk(dels, adds, pre + j + 1);
    dels = 0;
    adds = [];
  };
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      flush();
      i += 1;
      j += 1;
    } else if (i < n && (j >= m || L[(i + 1) * W + j] >= L[i * W + j + 1])) {
      dels += 1;
      i += 1;
    } else {
      adds.push(pre + j + 1);
      j += 1;
    }
  }
  flush();
  return out;
}
