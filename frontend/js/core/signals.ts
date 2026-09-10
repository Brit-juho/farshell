// N18 — 기존 store(layout/store.js · core/store.js · core/settings.js)를
// Solid signal로 감싸는 어댑터. 저 세 파일은 이 파일이 존재한다는 것 자체를
// 모른다 — onLayoutChange/subscribe라는 이미 있던 구독 API만 쓴다. 기존
// store를 손대지 않는 이유는 파일 상단 그대로: 283건짜리 테스트 계약이 그
// 위에 있다(10-shell-layout.md §2).
import { createSignal, type Accessor } from 'solid-js';
import { onLayoutChange, getTree, getActivePaneId } from '../layout/store.js';
import { subscribe as subscribeSessions, allSessions } from './store.js';
import { subscribe as subscribeSettings, get as getSetting } from './settings.js';
import type { LayoutNode, Session } from './types.js';

// 트리 전체 — kind('active'|'ratio'|'layout')는 여기선 구분하지 않는다.
// Solid는 구조가 실제로 바뀐 부분만 다시 그리므로, 매번 새 signal 값을 받아도
// 안 바뀐 서브트리는 갱신되지 않는다.
// getTree()/allSessions() 등은 checkJs:false인 .js가 주는 느슨한 추론 타입을
// 돌려주므로(mutable let 재할당 등으로 리터럴이 string으로 widen된다), 여기
// hand-authored types.ts 모양으로 명시 캐스트한다 — 이게 바로 이 파일의 역할.
export function useLayoutTree(): Accessor<LayoutNode> {
  const [tree, setTree] = createSignal<LayoutNode>(getTree() as LayoutNode);
  onLayoutChange((t: LayoutNode) => setTree(() => t));
  return tree;
}

export function useActivePaneId(): Accessor<string> {
  const [id, setId] = createSignal<string>(getActivePaneId() as string);
  onLayoutChange((_t: LayoutNode, activeId: string) => setId(activeId));
  return id;
}

// core/store.js의 subscribe(fn)는 "뭔가 바뀜"만 알리고 값을 안 주므로, 호출될
// 때마다 allSessions()를 다시 읽어 넣는다.
export function useSessions(): Accessor<Record<string, Session>> {
  const [sessions, setSessions] = createSignal<Record<string, Session>>(allSessions() as Record<string, Session>);
  subscribeSessions(() => setSessions(allSessions() as Record<string, Session>));
  return sessions;
}

// 설정 키 하나를 signal로. settings.js의 subscribe는 전체 변경을 알리므로
// 여기서 그 키만 다시 읽어 비교한다(같은 값이면 Solid가 알아서 재렌더를 접는다).
// createSignal<T>의 setter는 T가 함수 타입일 수 있어 "다음 값을 반환하는
// 함수"로 넘겨야 오버로드가 갈리지 않는다(Solid 제네릭 시그니처의 흔한 함정).
export function useSetting<T = unknown>(key: string): Accessor<T> {
  const [value, setValue] = createSignal<T>(getSetting(key) as T);
  subscribeSettings(() => setValue(() => getSetting(key) as T));
  return value;
}
