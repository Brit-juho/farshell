// 코드 뷰어 본문 — CodeMirror 6. 기본은 읽기 전용(파일 개편 단계 2), 「편집」이면 같은 상태로 편집 가능(단계 8).
//
// 옛 렌더러는 줄마다 DOM 행을 만들었다(512KB 파일이면 노드 약 7만 개, 감속 조건에서 메인 스레드가 2초 넘게
// 멈춘다). CM6는 보이는 줄만 DOM으로 그리고 구문 분석을 나눠서 한다. 수치는
// docs/ref/session-view-measurements.md 7절.
//
// 이 모듈과 @codemirror/*·@lezer/* 는 `editor` 지연 청크다(vite.config.mjs chunkOf). file.js가 파일을 열 때
// import()한다 — 초기 페이로드에 들어가지 않는다.
//
// 스크롤은 지금처럼 바깥 상자(뷰어 본문·페인 본문)가 한다. 편집기는 높이가 내용만큼 늘어나고, CM이 스크롤하는
// 조상을 찾아 보이는 범위만 그린다. 그래서 소비처(overlay.js, layout/panes.js)의 스크롤 보존·sticky 도구 줄이
// 그대로 동작한다.
//
// 색은 전부 tokens.css 변수다(AGENTS.md 계약 9). 하이라이트는 @lezer/highlight의 classHighlighter가 `tok-*`
// 클래스를 붙이고 styles/screens/30-viewer.css가 색을 준다. 편집기 틀(줄 번호 칸, 찾기 창, 일치 표시)은 아래
// _THEME이 정한다 — CM이 넣는 기본 테마는 @layer 밖이라, @layer legacy 안의 우리 CSS가 명시도와 무관하게 진다.
import { EditorState, Compartment, StateEffect, StateField, RangeSet } from '@codemirror/state';
import { EditorView, Decoration, lineNumbers, highlightSpecialChars, keymap, gutter, GutterMarker } from '@codemirror/view';
import { syntaxHighlighting, StreamLanguage } from '@codemirror/language';
import { classHighlighter, highlightCode } from '@lezer/highlight';
import { search, searchKeymap } from '@codemirror/search';
import { history, historyKeymap, defaultKeymap, indentWithTab } from '@codemirror/commands';
import { t } from '../../core/i18n.js';
import { vtEsc } from '../../core/api.js';
import { textChange, editorLangKey } from './editor-text.js';

// 언어 팩은 쓸 때만 평가한다. 청크는 같지만(번들 크기는 단계 2에서 실측) 파서 초기화 비용을 열지 않은 언어에
// 치르지 않는다. 키는 editorLangKey()가 돌려주는 이름이다.
const LANGS = {
  javascript: () => import('@codemirror/lang-javascript').then((m) => m.jsxLanguage),
  typescript: () => import('@codemirror/lang-javascript').then((m) => m.typescriptLanguage),
  tsx: () => import('@codemirror/lang-javascript').then((m) => m.tsxLanguage),
  python: () => import('@codemirror/lang-python').then((m) => m.pythonLanguage),
  css: () => import('@codemirror/lang-css').then((m) => m.cssLanguage),
  xml: () => import('@codemirror/lang-html').then((m) => m.htmlLanguage),
  json: () => import('@codemirror/lang-json').then((m) => m.jsonLanguage),
  markdown: () => import('@codemirror/lang-markdown').then((m) => m.markdownLanguage),
  yaml: () => import('@codemirror/lang-yaml').then((m) => m.yamlLanguage),
  sql: () => import('@codemirror/lang-sql').then((m) => m.StandardSQL.language),
  java: () => import('@codemirror/lang-java').then((m) => m.javaLanguage),
  go: () => import('@codemirror/lang-go').then((m) => m.goLanguage),
  bash: () => import('@codemirror/legacy-modes/mode/shell').then((m) => StreamLanguage.define(m.shell)),
  kotlin: () => import('@codemirror/legacy-modes/mode/clike').then((m) => StreamLanguage.define(m.kotlin)),
  swift: () => import('@codemirror/legacy-modes/mode/swift').then((m) => StreamLanguage.define(m.swift)),
};
const _langCache = new Map();

// 편집기 틀. 값은 tokens.css·skins.css 변수만 쓴다. 줄 번호 칸 폭은 CSS 변수로 열어 두어 30-viewer.css의 좁은 화면
// 규칙이 줄인다(--vt-cm-gutter-min·--vt-cm-gutter-pad).
const _THEME = EditorView.theme({
  '&': { color: 'var(--txt)', backgroundColor: 'transparent' },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { fontFamily: 'inherit', lineHeight: 'inherit' },
  '.cm-content': { padding: 'calc(var(--spacing) * 1.5) 0', caretColor: 'var(--txt)' },
  '.cm-line': { padding: '0 calc(var(--spacing) * 3) 0 0' },
  '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--txt)' },
  '.cm-gutters': { backgroundColor: 'var(--color-bg-0)', color: 'var(--sub)', border: 'none' },
  '.cm-lineNumbers .cm-gutterElement': {
    minWidth: 'var(--vt-cm-gutter-min, 44px)', padding: '0 var(--vt-cm-gutter-pad, calc(var(--spacing) * 2))',
    boxSizing: 'border-box', userSelect: 'none',
  },
  '.cm-specialChar': { color: 'var(--err)' },
  '.cm-searchMatch': {
    backgroundColor: 'color-mix(in srgb, var(--warn) 28%, transparent)',
    outline: '1px solid color-mix(in srgb, var(--warn) 55%, transparent)',
  },
  '.cm-searchMatch.cm-searchMatch-selected': { backgroundColor: 'color-mix(in srgb, var(--acc) 40%, transparent)' },
  '.cm-selectionMatch': { backgroundColor: 'color-mix(in srgb, var(--acc) 18%, transparent)' },
  '.cm-panels': { backgroundColor: 'var(--bar)', color: 'var(--txt)' },
  '.cm-panels.cm-panels-top': { borderBottom: '1px solid var(--line)' },
  '.cm-panel.cm-search': {
    font: '400 var(--fs-xs) var(--ui)',
    padding: 'calc(var(--spacing) * 1.5) calc(var(--spacing) * 8) calc(var(--spacing) * 1.5) calc(var(--spacing) * 2)',
  },
  '.cm-panel.cm-search label': { fontSize: 'inherit', color: 'var(--sub)' },
  '.cm-textfield': {
    backgroundColor: 'var(--crust)', color: 'var(--txt)', border: '1px solid var(--line)',
    borderRadius: 'var(--radius-sm)', font: 'inherit', padding: 'calc(var(--spacing) * 0.5) calc(var(--spacing) * 1.5)',
  },
  '.cm-button': {
    backgroundColor: 'var(--crust)', backgroundImage: 'none', color: 'var(--txt)', border: '1px solid var(--line)',
    borderRadius: 'var(--radius-sm)', font: 'inherit',
  },
  '.cm-button:hover': { backgroundColor: 'var(--menu-hover)' },
  '.cm-button:active': { backgroundImage: 'none', backgroundColor: 'var(--menu-hover)' },
  '.cm-panel button[name=close]': { color: 'var(--sub)' },
  '.cm-vt-target': { backgroundColor: 'color-mix(in srgb, var(--acc) 16%, transparent)' },
});

// 터미널 링크(`path:줄:열`)로 연 줄 표시. 터치 기기는 편집 불가라 커서가 안 보이므로 줄 배경으로 알린다.
const _setTarget = StateEffect.define();
const _targetLine = Decoration.line({ class: 'cm-vt-target' });
const _targetField = StateField.define({
  create: () => Decoration.none,
  update(deco, tr) {
    let d = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(_setTarget)) d = e.value == null ? Decoration.none : Decoration.set([_targetLine.range(e.value)]);
    }
    return d;
  },
  provide: (f) => EditorView.decorations.from(f),
});

// 편집 모드 거터의 바뀐 줄 표시(추가 · 수정 · 삭제). 계산은 edit.js가 line-marks.js `lineChangeMarks`로 하고
// `setChangeMarks`로 넘긴다. 색은 30-viewer.css의 `.cm-vt-chg.*`(tokens.css `--color-vw-chg-*`) — 여기에는 색이 없다.
// 다음 계산 전까지는 편집에 맞춰 위치만 옮긴다(map). 거터 칸은 편집 확장(editSlot) 안에만 있어 읽기 전용에는 없다.
class _ChgMarker extends GutterMarker {
  constructor(kind) { super(); this.kind = kind; }
  eq(other) { return other.kind === this.kind; }
  toDOM() {
    const s = document.createElement('span');
    s.className = `cm-vt-chg ${this.kind}`;
    return s;
  }
}
const _CHG = { add: new _ChgMarker('add'), mod: new _ChgMarker('mod'), del: new _ChgMarker('del'), 'del-below': new _ChgMarker('del-below') };
const _setChg = StateEffect.define();
const _chgField = StateField.define({
  create: () => RangeSet.empty,
  update(set, tr) {
    let next = set.map(tr.changes);
    for (const e of tr.effects) if (e.is(_setChg)) next = e.value;
    return next;
  },
});
const _chgGutter = () => gutter({
  class: 'cm-vt-chg-gutter',
  markers: (view) => view.state.field(_chgField),
  initialSpacer: () => _CHG.add,
});

/** `[{line, kind}]` → 거터 표식 묶음. 문서 밖 줄 번호는 버린다. */
function _chgSet(doc, marks) {
  if (!marks || !marks.length) return RangeSet.empty;
  const ranges = [];
  for (const m of marks) {
    const mk = _CHG[m.kind];
    if (!mk || !(m.line >= 1 && m.line <= doc.lines)) continue;
    ranges.push(mk.range(doc.line(m.line).from));
  }
  return RangeSet.of(ranges, true);
}

// 끝부분 보기(tail)는 앞쪽 줄 수를 모른다. 줄 번호 대신 「끝에서 n번째 줄」을 -n으로 적는다(마지막 빈 줄은 세지 않는다).
function _fromEnd(lineNo, state) {
  const doc = state.doc;
  const total = doc.lines - (doc.lines > 1 && doc.line(doc.lines).length === 0 ? 1 : 0);
  const k = total - lineNo + 1;
  return k > 0 ? `-${k}` : '';
}

/** 언어를 불러온다. 모르는 언어이거나 불러오지 못하면 null(일반 텍스트로 보인다). */
export function loadLanguage(key) {
  const load = key && LANGS[key];
  if (!load) return Promise.resolve(null);
  if (!_langCache.has(key)) {
    _langCache.set(key, load().catch((e) => {
      _langCache.delete(key);
      console.warn('[FarShell viewer] language load failed', key, e);
      return null;
    }));
  }
  return _langCache.get(key);
}

/**
 * diff 문맥 줄 하이라이트(파일 개편 단계 9 — hljs를 대신한다). 파일 보기와 같은 언어 팩·같은 `tok-*` 클래스라
 * 두 화면의 색이 같다. 돌려주는 함수 `(text) => 줄마다 HTML[]`는 글자를 이스케이프하고 `<span class="tok-…">`만
 * 붙인다. 모르는 언어이거나 언어 팩을 못 받으면 null. 분석이 실패하면 그 덩어리만 null(글자만 그린다).
 */
export async function highlighterFor(path, hljsName) {
  const lang = await loadLanguage(editorLangKey(path, hljsName));
  if (!lang) return null;
  return (text) => {
    const out = [];
    let cur = '';
    try {
      highlightCode(text, lang.parser.parse(text), classHighlighter,
        (s, cls) => { cur += cls ? `<span class="${cls}">${vtEsc(s)}</span>` : vtEsc(s); },
        () => { out.push(cur); cur = ''; });
    } catch (e) {
      console.warn('[FarShell diff] highlight failed', e);
      return null;
    }
    out.push(cur);
    return out;
  };
}

// CM 화면 문구(찾기 창, 줄 이동, 제어 문자 설명). 읽기 전용이라 바꾸기 관련 문구는 나타나지 않는다.
function _phrases() {
  return {
    Find: t('찾기'),
    next: t('다음'),
    previous: t('이전'),
    all: t('모두'),
    'match case': t('대소문자 구분'),
    regexp: t('정규식'),
    'by word': t('단어 단위'),
    close: t('닫기'),
    'current match': t('현재 일치 항목'),
    'on line': t('줄 번호'),
    'Go to line': t('줄로 이동'),
    go: t('가기'),
    'Control character': t('제어 문자'),
  };
}

function _scrollParent(el) {
  for (let cur = el.parentElement; cur; cur = cur.parentElement) {
    const o = getComputedStyle(cur).overflowY;
    if ((o === 'auto' || o === 'scroll') && cur.scrollHeight > cur.clientHeight) return cur;
  }
  return null;
}

// 터치 기기에서는 편집 가능 상태(contenteditable)를 끈다. 켜 두면 읽기 전용이어도 누를 때마다 화상 키보드가
// 올라온다. 데스크톱에서는 켜 둔다 — 그래야 커서 이동, 전체 선택·복사, Ctrl/Cmd+F 찾기가 파일 전체에 걸린다
// (그리지 않은 줄은 DOM에 없어서 브라우저 찾기로는 안 잡힌다).
function _coarse() {
  try { return window.matchMedia('(pointer: coarse)').matches; } catch (_) { return false; }
}

/**
 * container 안에 읽기 전용 편집기를 붙인다. 돌려주는 핸들:
 * - `setText(text)` — 바뀐 부분만 교체한다(스크롤·선택 유지). 같으면 아무것도 하지 않는다.
 * - `append(text)` — 끝에 붙인다(구간을 이어 받을 때, 「더 보기」).
 * - `replaceEnd(n, text)` — 끝의 n글자를 text로 바꾼다(커진 파일의 끝 이어 받기). 바꿨으면 true.
 * - `jumpTo(line, col)` — 그 줄로 옮기고 표시한다. 받은 범위를 넘으면 false.
 * - `atBottom()` — 스크롤이 끝에 있는가(끝부분 보기에서 새 내용을 따라갈지 정한다).
 * - `setEditable(on, {onSave})` — 편집 모드(단계 8). 같은 EditorState를 그대로 두고 읽기 전용만 푼다(스크롤·선택 유지).
 *   켜면 되돌리기 기록·기본 편집 키·⌘S/Ctrl+S(`onSave`)·Tab 들여쓰기가 붙는다. 끄면 읽기 전용으로 돌아간다.
 * - `getText()`, `onChange(fn)` — 문서가 바뀔 때마다 `fn()`(편집·다시 읽기 모두). `focus()`.
 * - `setChangeMarks(marks)` — 편집 모드 거터의 바뀐 줄 표시(`[{line, kind: 'add'|'mod'|'del'|'del-below'}]`, null = 지움).
 * - `destroy()`
 * `opts.lineFrom === 'end'`면 줄 번호를 끝에서 센다(끝부분 보기).
 */
export async function mountEditor(container, text, path, hljsName, opts = {}) {
  const lang = await loadLanguage(editorLangKey(path, hljsName));
  const langSlot = new Compartment();
  const editSlot = new Compartment();
  let changed = null;
  let saveFn = null;
  const readOnlyExt = () => [
    EditorState.readOnly.of(true),
    EditorView.editable.of(!_coarse()),
    EditorView.contentAttributes.of({ 'aria-label': t('파일 내용'), 'aria-readonly': 'true' }),
  ];
  // 편집 확장(쓰기) — 이 청크(`editor`) 안에만 있다. 저장 키는 기본 키보다 먼저 둔다(브라우저 「페이지 저장」을 막는다).
  const editExt = () => [
    EditorState.readOnly.of(false),
    EditorView.editable.of(true),
    _chgGutter(),
    history(),
    keymap.of([
      { key: 'Mod-s', preventDefault: true, run: () => { if (saveFn) saveFn(); return true; } },
      indentWithTab, ...historyKeymap, ...defaultKeymap,
    ]),
    EditorView.contentAttributes.of({ 'aria-label': t('파일 내용 — 편집 중'), 'aria-readonly': 'false', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false' }),
  ];
  const view = new EditorView({
    parent: container,
    state: EditorState.create({
      doc: text,
      extensions: [
        opts.lineFrom === 'end' ? lineNumbers({ formatNumber: _fromEnd }) : lineNumbers(),
        _targetField,
        _chgField,
        highlightSpecialChars(),
        editSlot.of(readOnlyExt()),
        EditorState.tabSize.of(4),
        EditorState.phrases.of(_phrases()),
        syntaxHighlighting(classHighlighter),
        _THEME,
        search({ top: true }),
        keymap.of(searchKeymap),
        EditorView.updateListener.of((u) => { if (u.docChanged && changed) changed(); }),
        langSlot.of(lang || []),
      ],
    }),
  });
  container.classList.add('vt-vw-cm');
  return {
    view,
    setEditable(on, o = {}) {
      saveFn = on ? (o.onSave || null) : null;
      view.dispatch({ effects: editSlot.reconfigure(on ? editExt() : readOnlyExt()) });
      container.classList.toggle('vt-vw-cm-edit', !!on);
    },
    getText() { return view.state.doc.toString(); },
    /** 거터의 바뀐 줄 표시(`[{line, kind}]`, line-marks.js `lineChangeMarks`). null이면 지운다. */
    setChangeMarks(marks) {
      view.dispatch({ effects: _setChg.of(_chgSet(view.state.doc, marks)) });
    },
    onChange(fn) { changed = fn || null; },
    focus() { view.focus(); },
    setText(next) {
      const change = textChange(view.state.doc.toString(), next);
      if (change) view.dispatch({ changes: change });
      return !!change;
    },
    append(more) {
      if (!more) return;
      const end = view.state.doc.length;
      view.dispatch({ changes: { from: end, to: end, insert: more } });
    },
    replaceEnd(n, more) {
      const end = view.state.doc.length;
      const from = Math.max(0, end - Math.max(0, n | 0));
      if (from === end && !more) return false;
      view.dispatch({ changes: { from, to: end, insert: more || '' } });
      return true;
    },
    lines() { return view.state.doc.lines; },
    jumpTo(line, col) {
      const doc = view.state.doc;
      const n = Math.floor(Number(line));
      if (!Number.isFinite(n) || n < 1 || n > doc.lines) return false;
      const ln = doc.line(n);
      const c = Math.max(1, Math.floor(Number(col)) || 1);
      const pos = Math.min(ln.to, ln.from + c - 1);
      view.dispatch({
        selection: { anchor: pos },
        effects: [_setTarget.of(ln.from), EditorView.scrollIntoView(pos, { y: 'center' })],
      });
      return true;
    },
    jumpToEnd() {
      view.dispatch({ effects: EditorView.scrollIntoView(view.state.doc.length, { y: 'end' }) });
    },
    atBottom() {
      const el = _scrollParent(view.dom);
      return !el || el.scrollTop + el.clientHeight >= el.scrollHeight - 4;
    },
    destroy() { view.destroy(); },
  };
}
