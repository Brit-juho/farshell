// D3(20-design-system.md §3·§4-1) — 상태(idle/working/waiting/done/error)를
// 다루는 모든 곳이 지켜야 할 "동적 클래스 규약": Tailwind는 소스를 문자열
// 스캔해 클래스를 생성하므로 `el.className = 'bg-st-' + state` 같은 조립은
// 빌드에 클래스가 안 생겨 조용히 실패한다. 상태→표현은 항상 이 파일 같은
// 정적 객체 리터럴로 선언한다 — 오타는 `undefined`로 즉시 드러나고, 상태
// 목록이 한 파일에 모인다. safelist는 쓰지 않는다(어딘가 동적으로 만들고
// 있다는 사실을 숨겨 추적을 불가능하게 만든다).
//
// .status-dot(styles/layers/components.css) 자체는 이 맵이 없어도 동작한다
// — `data-state` 속성 하나만 있으면 배경색·waiting breathing 애니메이션이
// CSS attribute selector로 전부 따라온다(`<span class="status-dot"
// data-state="waiting">`). 이 맵은 dot이 아닌 다른 요소(배지·행 강조 등)가
// 상태색을 Tailwind 유틸리티 클래스로 직접 써야 할 때를 위한 것이다.
//
// 소비처 배선(탭·사이드바·pane 헤더·파비콘·푸시)은 A1(40-agent-state.md)의
// 몫 — 이 파일은 아직 아무도 import하지 않는다.
export const STATE_DOT = {
  idle:    'bg-st-idle',
  working: 'bg-st-working',
  waiting: 'bg-st-waiting',
  done:    'bg-st-done',
  error:   'bg-st-error',
};

export const STATE_LABEL = {
  idle:    '유휴',
  working: '작업 중',
  waiting: '입력 대기',
  done:    '완료',
  error:   '오류',
};
