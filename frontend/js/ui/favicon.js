/* VT 동적 파비콘 — 탭 아이콘을 canvas로 그려 16px에서도 선명하게 + 작업 상태 뱃지.
   보라(FarShell/Claude 아이덴티티) 라운드 사각 배경 + 흰 터미널(">_") 글리프 → 라이트/다크 탭바 양쪽에서 보임.
   우하단 상태 점: 유휴=없음, 작업중=앰버, 승인대기=레드(가장 급함), 완료=그린.

   theme.js/grid.js/voice.js보다 먼저 로드. window.VTFavicon.set('idle'|'working'|'done').
   - grid.js: agent_event(도구 시작) → 'working'
   - voice.js: task_complete(응답 완료) → 'done'
   - 탭 재포커스(visibilitychange) 시 'done' → 'idle' 자동 복귀. */
(function () {
  'use strict';

  var SIZE = 64;               // 렌더 해상도 (브라우저가 16px로 다운스케일 → 선명)
  var BG = '#8839ef';          // catppuccin mauve — Claude 에이전트 색과 동일 계열
  var FG = '#ffffff';          // 터미널 글리프
  // A5: waiting 추가. 값은 --color-st-* 토큰과 같은 계열이다 — canvas라
  // CSS 변수를 못 읽어서 리터럴로 둘 수밖에 없다(토큰을 바꾸면 여기도 함께).
  var DOT = { working: '#f9b304', waiting: '#e64553', done: '#40c057' };

  var _status = 'idle';
  var _canvas = null;
  var _link = null;

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function ensureCanvas() {
    if (_canvas) return _canvas;
    _canvas = document.createElement('canvas');
    _canvas.width = SIZE;
    _canvas.height = SIZE;
    return _canvas;
  }

  function ensureLink() {
    // 기존 <link rel="icon"> 재사용, 없으면 생성. type/href를 canvas PNG로 교체.
    var link = document.querySelector('link[rel~="icon"]');
    if (!link) {
      link = document.createElement('link');
      link.rel = 'icon';
      document.head.appendChild(link);
    }
    link.type = 'image/png';
    _link = link;
    return link;
  }

  function draw(status) {
    var c = ensureCanvas();
    var ctx = c.getContext('2d');
    ctx.clearRect(0, 0, SIZE, SIZE);

    // 배경 라운드 사각
    ctx.fillStyle = BG;
    roundRect(ctx, 2, 2, 60, 60, 15);
    ctx.fill();

    // 터미널 프롬프트 글리프 (">_")
    ctx.strokeStyle = FG;
    ctx.lineWidth = 6;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(16, 18);
    ctx.lineTo(28, 32);          // "> " 위쪽 사선
    ctx.lineTo(16, 46);          // "> " 아래쪽 사선
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(32, 46);
    ctx.lineTo(48, 46);          // "_" 커서
    ctx.stroke();

    // 상태 점 (우하단) — 배경색 링으로 마이크와 분리 후 컬러 점
    var dotColor = DOT[status];
    if (dotColor) {
      ctx.fillStyle = BG;
      ctx.beginPath();
      ctx.arc(48, 48, 15, 0, 2 * Math.PI);
      ctx.fill();
      ctx.fillStyle = dotColor;
      ctx.beginPath();
      ctx.arc(48, 48, 11, 0, 2 * Math.PI);
      ctx.fill();
    }

    var link = ensureLink();
    try {
      link.href = c.toDataURL('image/png');
    } catch (e) { /* toDataURL 실패(드묾) 시 무시 */ }
  }

  function set(status) {
    // A5: 'waiting' 추가 — 이 화이트리스트가 A1의 상태 집합과 어긋나면 새
    // 상태가 조용히 무시된다(그게 이 함수가 화이트리스트를 갖는 유일한 위험).
    if (status !== 'idle' && status !== 'working' && status !== 'waiting' && status !== 'done') return;
    if (status === _status) return;
    _status = status;
    draw(status);
  }

  // 완료 뱃지를 사용자가 확인(탭 포커스)하면 대기중으로 되돌림
  document.addEventListener('visibilitychange', function () {
    // waiting은 사용자가 탭을 봐도 자동으로 내리지 않는다 — 승인은 실제로
    // 답해야 끝나는 일이고, 그 해제는 서버(A3)가 판정한다.
    if (!document.hidden && _status === 'done') set('idle');
  });

  window.VTFavicon = { set: set };

  // 초기 렌더 — muddy PNG 대체
  draw('idle');
})();
