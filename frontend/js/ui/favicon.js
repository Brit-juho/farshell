/* VT 동적 파비콘 — 탭 아이콘을 canvas로 그려 16px에서도 선명하게 + 작업 상태 뱃지.
   보라(FarShell/Claude 아이덴티티) 라운드 사각 배경 + 흰 터미널(">_") 글리프 → 라이트/다크 탭바 양쪽에서 보임.
   상태 뱃지는 **우상단 모서리를 접은 삼각형**: 유휴=없음, 작업중=그린,
   승인대기=앰버(가장 급함), 완료=블루. 색은 D3 상태 토큰(--color-st-*)과 같은 값이다.

   2026-09-18 — 그전까지는 우하단 **점**이었다. 그 점은 반지름 11(지름 22)로
   64 캔버스의 34%였고 중심이 (48,48)이라 `_` 커서(32~48, y=46) 위에 정확히
   얹혔다. 실제 탭 크기인 16px에서 재보니 글리프가 통째로 사라지고 색 덩어리만
   남았다 — 알리려던 신호가 브랜드 마크를 잡아먹고 있었다(사용자 보고:
   "우측 하단에 점이 먼지를 모르겠네").

   왜 점이 아니라 모서리인가: 16px에서 4px 점은 색으로만 말하는데, 그 크기의
   색은 탭바 배경·옆 탭과 섞여 "뭔가 붙어 있다"까지만 전달된다. 모서리를
   접으면 **실루엣이 바뀐다** — 색을 구별하지 못해도 "모양이 다른 아이콘"으로
   먼저 읽힌다. 그리고 삼각형이 차지하는 우상단은 글리프가 비어 있는 자리라
   `>`(x≤28)도 `_`(y=46)도 건드리지 않는다.
   네 안을 실제 탭바에 얹어 비교한 페이지: frontend/static/farshell-favicon.html

   theme.js/grid.js/voice.js보다 먼저 로드. window.VTFavicon.set('idle'|'working'|'done').
   - grid.js: agent_event(도구 시작) → 'working'
   - voice.js: task_complete(응답 완료) → 'done'
   - 탭 재포커스(visibilitychange) 시 'done' → 'idle' 자동 복귀. */
(function () {
  'use strict';

  var SIZE = 64;               // 렌더 해상도 (브라우저가 16px로 다운스케일 → 선명)
  var BG = '#8839ef';          // catppuccin mauve — Claude 에이전트 색과 동일 계열
  var FG = '#ffffff';          // 터미널 글리프
  // A5: waiting 추가 + **D3 토큰과 색 정렬**. 이 파일은 D3(디자인 시스템)보다
  // 먼저 있던 classic script라 그때 색(작업중=앰버, 완료=그린)을 그대로 들고
  // 있었는데, 토큰은 작업중=그린·대기=앰버(액센트)·완료=블루로 이미 재정의돼
  // 있었다 — 같은 화면에서 탭 dot과 파비콘이 서로 다른 색으로 같은 상태를
  // 가리키고 있었다는 뜻이다. canvas라 CSS 변수를 못 읽어 리터럴이지만,
  // 값은 styles/theme/skins.css의 --color-st-*와 같게 유지한다.
  var BADGE = { working: '#32d74b', waiting: '#ffd60a', done: '#0a84ff' };

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

    // 상태 뱃지 — 우상단 모서리를 접은 삼각형.
    // 라운드 사각으로 clip하는 게 핵심이다: 안 하면 삼각형의 직각 꼭짓점이
    // 판 밖으로 튀어나와 라운드가 한 군데만 깨진 것처럼 보인다. clip을 걸면
    // 모서리의 둥근 곡선을 따라 잘려서 "접힌 것"으로 읽힌다.
    var badge = BADGE[status];
    if (badge) {
      ctx.save();
      roundRect(ctx, 2, 2, 60, 60, 15);
      ctx.clip();
      ctx.fillStyle = badge;
      ctx.beginPath();
      ctx.moveTo(62, 2);
      ctx.lineTo(62, 30);
      ctx.lineTo(34, 2);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
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

  // draw·mark도 노출한다 — 같은 마크를 쓰는 다른 자리(앱 아이콘 생성기
  // scripts/gen-icons.html)가 색·좌표를 여기서 읽어갈 수 있게. 탭 아이콘과
  // 앱 아이콘이 서로 다른 코드로 그려지면 반드시 어긋난다.
  window.VTFavicon = { set: set, draw: draw, mark: { bg: BG, fg: FG, badge: BADGE } };

  // 초기 렌더 — muddy PNG 대체
  draw('idle');
})();
