// 코드 뷰어 본문 — CodeMirror 6, 읽기 전용(파일 개편 단계 2).
//
// 옛 렌더러는 줄마다 DOM 행을 만들었다(512KB 파일이면 노드 약 7만 개, 감속 조건에서 메인 스레드가 2초 넘게
// 멈춘다). CM6는 보이는 줄만 DOM으로 그리고 구문 분석을 나눠서 한다. 수치는
// docs/ref/session-view-measurements.md 7절.
//
// 이 모듈과 @codemirror/*·@lezer/* 는 `editor` 지연 청크다(vite.config.js chunkOf). file.js가 파일을 열 때
// import()한다 — 초기 페이로드에 들어가지 않는다.
//
// 스크롤은 지금처럼 바깥 상자(뷰어 본문·페인 본문)가 한다. 편집기는 높이가 내용만큼 늘어나고, CM이 스크롤하는
// 조상을 찾아 보이는 범위만 그린다. 그래서 소비처(overlay.js, layout/panes.js)의 스크롤 보존·sticky 도구 줄이
// 그대로 동작한다.
//
// 색은 전부 tokens.css 변수다(AGENTS.md 계약 9). 하이라이트는 @lezer/highlight의 classHighlighter가 `tok-*`
// 클래스를 붙이고 styles/screens/30-viewer.css가 색을 준다. 편집기 틀(줄 번호 칸, 찾기 창, 일치 표시)은 아래
// _THEME이 정한다 — CM이 넣는 기본 테마는 @layer 밖이라, @layer legacy 안의 우리 CSS가 명시도와 무관하게 진다.
import { EditorState, Compartment } from '@codemirror/state';
import { EditorView, lineNumbers, highlightSpecialChars, keymap } from '@codemirror/view';
import { syntaxHighlighting, StreamLanguage } from '@codemirror/language';
import { classHighlighter } from '@lezer/highlight';
import { search, searchKeymap } from '@codemirror/search';
import { t } from '../../core/i18n.js';
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
  '.cm-gutters': { backgroundColor: 'var(--crust)', color: 'var(--sub)', border: 'none' },
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
});

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

// 터치 기기에서는 편집 가능 상태(contenteditable)를 끈다. 켜 두면 읽기 전용이어도 누를 때마다 화상 키보드가
// 올라온다. 데스크톱에서는 켜 둔다 — 그래야 커서 이동, 전체 선택·복사, Ctrl/Cmd+F 찾기가 파일 전체에 걸린다
// (그리지 않은 줄은 DOM에 없어서 브라우저 찾기로는 안 잡힌다).
function _coarse() {
  try { return window.matchMedia('(pointer: coarse)').matches; } catch (_) { return false; }
}

/**
 * container 안에 읽기 전용 편집기를 붙인다. 돌려주는 핸들:
 * - `setText(text)` — 바뀐 부분만 교체한다(스크롤·선택 유지). 같으면 아무것도 하지 않는다.
 * - `destroy()`
 */
export async function mountEditor(container, text, path, hljsName) {
  const lang = await loadLanguage(editorLangKey(path, hljsName));
  const langSlot = new Compartment();
  const view = new EditorView({
    parent: container,
    state: EditorState.create({
      doc: text,
      extensions: [
        lineNumbers(),
        highlightSpecialChars(),
        EditorState.readOnly.of(true),
        EditorView.editable.of(!_coarse()),
        EditorState.tabSize.of(4),
        EditorState.phrases.of(_phrases()),
        syntaxHighlighting(classHighlighter),
        _THEME,
        search({ top: true }),
        keymap.of(searchKeymap),
        EditorView.contentAttributes.of({ 'aria-label': t('파일 내용'), 'aria-readonly': 'true' }),
        langSlot.of(lang || []),
      ],
    }),
  });
  container.classList.add('vt-vw-cm');
  return {
    view,
    setText(next) {
      const change = textChange(view.state.doc.toString(), next);
      if (change) view.dispatch({ changes: change });
      return !!change;
    },
    destroy() { view.destroy(); },
  };
}
