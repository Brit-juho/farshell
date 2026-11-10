'use strict';
// 회귀: 뷰어 막대 단추(.vt-vw-bar-btn, 마크다운 「주소 복사」 포함)가 옛 --crust(거의 검정) 바탕으로
// 나오던 문제. 「작업대」 토큰(--color-*)·투명 바탕이어야 9개 스킨 모두 대비 검사(bg-0~2 위 txt/sub) 안에 든다.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const css = fs.readFileSync(path.join(__dirname, '..', '..', 'styles', 'screens', '30-viewer.css'), 'utf8');

function rule(sel) {
  const esc = sel.replace(/[.[\]]/g, '\\$&');
  const m = css.match(new RegExp(`(?:^|\\n)${esc}\\{([^}]*)\\}`));
  assert.ok(m, `${sel} 규칙이 있어야 한다`);
  return m[1];
}

test('.vt-vw-bar-btn 은 --crust 를 쓰지 않고 투명 바탕 + --color-* 토큰', () => {
  const body = rule('.vt-vw-bar-btn');
  assert.ok(!/--crust/.test(body));
  assert.match(body, /background:transparent/);
  assert.match(body, /color:var\(--color-txt\)/);
  assert.match(body, /border:1px solid var\(--color-line-strong\)/);
});

test('.vt-md-copy 는 조용한 단추(테두리 없음, --color-sub)', () => {
  const body = rule('.vt-md-copy');
  assert.match(body, /border-color:transparent/);
  assert.match(body, /color:var\(--color-sub\)/);
});

test('막대 단추·인코딩 선택에 --crust 가 남지 않는다', () => {
  assert.ok(!/\.vt-vw-enc select\{[^}]*--crust/.test(css));
});
