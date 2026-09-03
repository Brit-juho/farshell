// ANSI escape → HTML 변환 + 프리뷰 정리, 순수 로직만. keyseq.js/difflex.js와 같은
// 이유로 분리했다: DOM/네트워크를 만지지 않아야 브라우저(window.VTAnsiLex)와 Node
// 테스트(require('ansilex.js')) 양쪽에서 재사용하고 단위 테스트할 수 있다.
// ansiToHtml은 그리드 카드 라이브 프리뷰의 유일한 XSS 방어선이라(server가 보낸
// tmux capture-pane 원문을 innerHTML로 꽂는다) 특히 회귀 감지가 중요하다.
(function (root) {
  'use strict';

  var ANSI_COLOR_MAP = {
    30: '#45475a', 31: '#f38ba8', 32: '#a6e3a1', 33: '#f9e2af', 34: '#89b4fa', 35: '#f5c2e7', 36: '#94e2d5', 37: '#cdd6f4',
    90: '#6c7086', 91: '#eba0ac', 92: '#a6e3a1', 93: '#f9e2af', 94: '#74c7ec', 95: '#cba6f7', 96: '#89dceb', 97: '#cdd6f4',
  };

  // ANSI escape를 HTML span으로 단순 변환 (XSS 방지: HTML escape 먼저 → escape 시퀀스만 처리)
  function ansiToHtml(text) {
    // HTML escape 먼저
    var html = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // ANSI CSI 시퀀스: ESC[<n>;<n>m
    html = html.replace(/\x1b\[([\d;]*)m/g, function (m, codes) {
      if (!codes || codes === '0') return '</span>';
      var parts = codes.split(';').map(Number);
      var styles = [];
      for (var i = 0; i < parts.length; i++) {
        var code = parts[i];
        if (code === 1) styles.push('font-weight:bold');
        else if (code === 3) styles.push('font-style:italic');
        else if (code === 4) styles.push('text-decoration:underline');
        else if (ANSI_COLOR_MAP[code]) styles.push('color:' + ANSI_COLOR_MAP[code]);
        else if (ANSI_COLOR_MAP[code - 10]) styles.push('background:' + ANSI_COLOR_MAP[code - 10]);
      }
      return styles.length ? '<span style="' + styles.join(';') + '">' : '<span>';
    });
    // 미닫힌 span 자동 종료
    var openCount = (html.match(/<span/g) || []).length;
    var closeCount = (html.match(/<\/span>/g) || []).length;
    for (var i = 0; i < openCount - closeCount; i++) html += '</span>';
    // 기타 ANSI escape 제거
    html = html.replace(/\x1b\[[?\d;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '');
    return html;
  }

  // Claude Code 같은 TUI는 화면 대부분(위쪽 스크롤 영역)을 비워두고 맨 아래
  // 상태줄만 채우는데, capture-pane은 마지막 N줄을 "있는 그대로" 준다. 그러면
  // 정작 보고 싶은 상태줄(브랜치·토큰 사용량 등)이 빈 줄 더미에 밀려 카드
  // max-height(240px) 밖으로 잘려나간다 — 처음/끝뿐 아니라 "중간에" 낀 빈 줄
  // 뭉치도 전부 압축해야 실제로 해결된다. ANSI escape만 있고 글자가 없는
  // 줄도 빈 줄로 친다.
  var ANSI_STRIP_RE = /\x1b\[[\d;]*m|\x1b\[[?\d;]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g;

  // 줄 앞의 아주 긴 공백은 들여쓰기가 아니라 원래 넓은 터미널에서의 정렬(우측/중앙
  // 정렬)용 패딩이다 — 예: "0 tokens"가 172컬럼 터미널에서 165칸 띄고 우측 정렬된
  // 채로 캡처된다. 좁은 카드에서 pre-wrap으로 줄바꿈되면 그 공백들이 몇 줄짜리
  // "빈 공간"처럼 보인다. 코드 들여쓰기(보통 <20칸)는 건드리지 않도록 24칸 이상만
  // 접는다 — 앞에 ANSI 이스케이프가 끼어 있어도(예: 색만 리셋하고 시작) 통과시킨다.
  var LEADING_PAD_RE = /^((?:\x1b\[[\d;]*m)*[ \t]{24,})/;
  function collapseLeadingPad(line) {
    return line.replace(LEADING_PAD_RE, '');
  }

  function trimBlankLines(text) {
    var isBlank = function (l) { return l.replace(ANSI_STRIP_RE, '').trim() === ''; };
    var out = [];
    var blankRun = false;
    var lines = text.split('\n');
    for (var i = 0; i < lines.length; i++) {
      var line = collapseLeadingPad(lines[i]);
      var blank = isBlank(line);
      if (blank && blankRun) continue;   // 연속된 빈 줄은 1개로 압축
      out.push(line);
      blankRun = blank;
    }
    while (out.length && isBlank(out[0])) out.shift();
    while (out.length && isBlank(out[out.length - 1])) out.pop();
    return out.join('\n');
  }

  var api = {
    ANSI_COLOR_MAP: ANSI_COLOR_MAP,
    ansiToHtml: ansiToHtml,
    trimBlankLines: trimBlankLines,
    collapseLeadingPad: collapseLeadingPad,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.VTAnsiLex = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
