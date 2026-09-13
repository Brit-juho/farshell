// N18 — 새 .tsx 코드(§3부터)가 쓰는 공용 타입. 기존 .js는 checkJs:false라
// 이 파일을 몰라도 그대로 동작한다. 여기 모양은 실제 런타임 객체(layout/
// tree.js·core/store.js·core/settings.js·agent/state.js)를 그대로 옮긴
// 선언일 뿐 — 저 파일들의 동작을 바꾸지 않는다.

// layout/tree.js의 leaf/split과 정확히 같은 모양. worktree/host는 ADR-10이
// 미리 뚫어둔 확장 자리 — 2.0/2.1.0에서는 항상 null/'local'이다.
export interface LayoutLeaf {
  t: 'leaf';
  id: string;
  session: string | null;
  worktree: string | null;
  host: string;
}

export interface LayoutSplit {
  t: 'split';
  id: string;
  dir: 'row' | 'col';
  a: LayoutNode;
  b: LayoutNode;
  ratio: number;
}

export type LayoutNode = LayoutLeaf | LayoutSplit;

// core/store.js registerSession()이 채우는 필드. term/wrapper 등은 vendor(xterm)
// 전역 타입이라 여기서는 실제 형태를 강제하지 않고 존재만 표시한다.
export interface Session {
  id: string;
  term: unknown;
  ws: WebSocket | null;
  /** 세션 탭 DOM. 10-shell-layout.md §4 3단계에서 사라진다 — 표시 이름의
   * 출처는 이미 아래 `displayName`으로 옮겼다(core/store.js). */
  tabEl: HTMLElement | null;
  /** 화면에 보이는 이름의 **단일 출처**. 읽을 때는 sessionDisplayName(id). */
  displayName?: string;
  wrapper: HTMLElement | null;
  tmuxName?: string;
  [key: string]: unknown; // fitAddon·searchAddon·onResize 등 이후 참조용 필드
}

// core/settings.js SCHEMA의 값 타입 — 키 자체는 스키마가 늘어날 때마다 바뀌므로
// 문자열 인덱스로 열어둔다.
export type Settings = Record<string, string | number | boolean>;

// agent/state.js가 서버로부터 받는 4+1상태. URGENCY 정렬과 같은 값이어야 한다.
export type AgentState = 'idle' | 'working' | 'waiting' | 'done' | 'error';

// 30-worktree.md(2.1.1)에서 확정될 자리 — 지금은 레일이 세션을 워크트리 자리에
// 임시로 그리는 단계(ADR-10 「기타」 규칙)라 최소 모양만 선언한다. 2.1.1에서
// 실제 API 응답 모양이 나오면 이 인터페이스를 갱신한다.
export interface Worktree {
  id: string;
  name: string;
  path: string;
}
