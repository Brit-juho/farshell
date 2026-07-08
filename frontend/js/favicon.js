/* VT 동적 파비콘 — 탭 아이콘을 canvas로 그려 16px에서도 선명하게 + 작업 상태 뱃지.
   보라(랄프톤/Claude 아이덴티티) 라운드 사각 배경 + 흰 마이크 → 라이트/다크 탭바 양쪽에서 보임.
   우하단 상태 점: 대기중=없음, 작업중=앰버, 완료=그린.

   theme.js/grid.js/voice.js보다 먼저 로드. window.VTFavicon.set('idle'|'working'|'done').
   - grid.js: agent_event(도구 시작) → 'working'
   - voice.js: task_complete(응답 완료) → 'done'
   - 탭 재포커스(visibilitychange) 시 'done' → 'idle' 자동 복귀. */
(function () {
  'use strict';

  var SIZE = 64;               // 렌더 해상도 (브라우저가 16px로 다운스케일 → 선명)
  var BG = '#8839ef';          // catppuccin mauve — Claude 에이전트 색과 동일 계열
  var FG = '#ffffff';          // 마이크 글리프
  var DOT = { working: '#f9b304', done: '#40c057' };  // 앰버 / 그린

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

    // 마이크 캡슐 (본체)
    ctx.fillStyle = FG;
    roundRect(ctx, 23, 12, 18, 27, 9);
    ctx.fill();

    // 마이크 거치대 (하단 U자 호) + 스탠드 + 받침
    ctx.strokeStyle = FG;
    ctx.lineWidth = 4;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.arc(32, 33, 15, 0.15 * Math.PI, 0.85 * Math.PI);  // 아래쪽 반원
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(32, 48);
    ctx.lineTo(32, 54);          // 스탠드
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(24, 55);
    ctx.lineTo(40, 55);          // 받침
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
    if (status !== 'idle' && status !== 'working' && status !== 'done') return;
    if (status === _status) return;
    _status = status;
    draw(status);
  }

  // 완료 뱃지를 사용자가 확인(탭 포커스)하면 대기중으로 되돌림
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && _status === 'done') set('idle');
  });

  window.VTFavicon = { set: set };

  // 초기 렌더 — muddy PNG 대체
  draw('idle');
})();
