// unified diff 파싱 — 순수 로직만. keyseq.js와 같은 이유로 분리했다:
// DOM/세션 상태를 만지지 않아야 브라우저(window.VTDiffLex)와 Node 테스트
// (require('difflex.js')) 양쪽에서 재사용하고 단위 테스트할 수 있다.
(function (root) {
  'use strict';

  // 탭을 시각적으로 펼친다. 터미널과 달리 HTML은 탭 폭이 들쭉날쭉해서
  // diff의 열 정렬이 깨진다.
  function expandTabs(s, width) {
    width = width || 4;
    var out = '';
    for (var i = 0; i < s.length; i++) {
      if (s[i] === '\t') {
        var pad = width - (out.length % width);
        out += ' '.repeat(pad);
      } else {
        out += s[i];
      }
    }
    return out;
  }

  // CRLF/CR 혼재를 LF로 정규화. 안 하면 모든 줄 끝에 보이지 않는 ^M이 남아
  // 하이라이터와 열 계산이 어긋난다.
  function normalize(text) {
    return String(text == null ? '' : text).replace(/\r\n?/g, '\n');
  }

  // @@ -a,b +c,d @@ 에서 시작 줄번호를 뽑는다. b/d는 생략될 수 있다(=1).
  var HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/;

  function parseHunkHeader(line) {
    var m = HUNK_RE.exec(line);
    if (!m) return null;
    return {
      oldStart: parseInt(m[1], 10),
      oldCount: m[2] === undefined ? 1 : parseInt(m[2], 10),
      newStart: parseInt(m[3], 10),
      newCount: m[4] === undefined ? 1 : parseInt(m[4], 10),
      section: m[5] || ''
    };
  }

  // git diff 전체 텍스트 → 파일별 구조.
  // 반환: [{ oldPath, newPath, binary, hunks: [{header, lines:[{type, oldNo, newNo, text}]}] }]
  // type: 'ctx' | 'add' | 'del' | 'meta'
  function parse(diffText) {
    var lines = normalize(diffText).split('\n');
    var files = [];
    var cur = null;
    var hunk = null;
    var oldNo = 0, newNo = 0;

    function pushFile(f) { files.push(f); }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];

      if (line.indexOf('diff --git ') === 0) {
        cur = { oldPath: '', newPath: '', binary: false, hunks: [] };
        hunk = null;
        pushFile(cur);
        // "diff --git a/x b/y" — 공백이 든 경로는 이 형식으로 안전하게 못 쪼갠다.
        // ---/+++ 줄에서 다시 잡으므로 여기서는 best-effort로만 둔다.
        var mm = /^diff --git a\/(.+) b\/(.+)$/.exec(line);
        if (mm) { cur.oldPath = mm[1]; cur.newPath = mm[2]; }
        continue;
      }
      if (!cur) continue;

      if (line.indexOf('Binary files ') === 0 || line.indexOf('GIT binary patch') === 0) {
        cur.binary = true;
        continue;
      }
      if (line.indexOf('--- ') === 0) {
        cur.oldPath = line.slice(4).replace(/^a\//, '');
        continue;
      }
      if (line.indexOf('+++ ') === 0) {
        cur.newPath = line.slice(4).replace(/^b\//, '');
        continue;
      }

      var hh = parseHunkHeader(line);
      if (hh) {
        hunk = { header: line, section: hh.section, lines: [] };
        cur.hunks.push(hunk);
        oldNo = hh.oldStart;
        newNo = hh.newStart;
        continue;
      }
      if (!hunk) continue;   // index/mode 등 헤더 잡음은 버린다

      var c = line[0];
      if (c === '+') {
        hunk.lines.push({ type: 'add', oldNo: null, newNo: newNo++, text: expandTabs(line.slice(1)) });
      } else if (c === '-') {
        hunk.lines.push({ type: 'del', oldNo: oldNo++, newNo: null, text: expandTabs(line.slice(1)) });
      } else if (c === '\\') {
        // "\ No newline at end of file"
        hunk.lines.push({ type: 'meta', oldNo: null, newNo: null, text: line });
      } else if (c === ' ' || line === '') {
        hunk.lines.push({ type: 'ctx', oldNo: oldNo++, newNo: newNo++, text: expandTabs(line.slice(1)) });
      }
    }
    return files;
  }

  // 통계 — 헤더에 "+12 -3" 배지를 띄우기 위한 것.
  function stats(file) {
    var add = 0, del = 0;
    for (var i = 0; i < file.hunks.length; i++) {
      var ls = file.hunks[i].lines;
      for (var j = 0; j < ls.length; j++) {
        if (ls[j].type === 'add') add++;
        else if (ls[j].type === 'del') del++;
      }
    }
    return { add: add, del: del };
  }

  // 확장자 → highlight.js 언어명. 모르면 null(=plaintext 폴백).
  // 하이라이터가 예외를 던지면 뷰어 전체가 죽으므로, 호출부는 반드시 이 값이
  // null일 때 하이라이팅을 건너뛰어야 한다.
  var EXT_LANG = {
    py: 'python', pyi: 'python',
    js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
    ts: 'typescript', tsx: 'typescript',
    sh: 'bash', bash: 'bash', zsh: 'bash', env: null,
    json: 'json', jsonc: 'json',
    css: 'css', scss: 'css', less: 'css',
    html: 'xml', htm: 'xml', xml: 'xml', svg: 'xml', vue: 'xml',
    md: 'markdown', markdown: 'markdown',
    yml: 'yaml', yaml: 'yaml',
    sql: 'sql',
    java: 'java', kt: 'kotlin', kts: 'kotlin',
    swift: 'swift', go: 'go'
  };

  function langForPath(path) {
    if (!path) return null;
    var base = String(path).split('/').pop();
    var dot = base.lastIndexOf('.');
    if (dot < 0) return null;
    var ext = base.slice(dot + 1).toLowerCase();
    return Object.prototype.hasOwnProperty.call(EXT_LANG, ext) ? EXT_LANG[ext] : null;
  }

  var api = {
    parse: parse,
    parseHunkHeader: parseHunkHeader,
    stats: stats,
    langForPath: langForPath,
    expandTabs: expandTabs,
    normalize: normalize
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.VTDiffLex = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
