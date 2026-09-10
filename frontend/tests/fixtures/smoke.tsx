// N18 스모크 — tsx 컴파일 + solid-js 런타임 + vite lib 빌드(`--mode test`)가
// 실제로 동작하는지만 확인한다. 다른 아무것도 참조하지 않는다 — §3부터
// shell/App.tsx가 이 자리(그리고 이 파일)를 대신한다.
import { createSignal } from 'solid-js';
import { render } from 'solid-js/web';

function Counter() {
  const [count, setCount] = createSignal(0);
  return (
    <button type="button" data-testid="smoke-btn" onClick={() => setCount(count() + 1)}>
      {count()}
    </button>
  );
}

export function mountSmoke(root: HTMLElement) {
  return render(() => <Counter />, root);
}
