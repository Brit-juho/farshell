// N5/N40/N46(60-settings-palette.md §3) — 팔레트의 순수 로직(접두사 파싱·
// fuzzy 매치·기본 명령 목록)만 모은다. Palette.tsx(DOM/Solid)와 이 파일을
// 분리한 이유는 shell/hud-data.ts·shell/rail-data.ts와 같다 — 상태 모듈을
// 정적 import하지 않는 순수 함수라야 node:test에서 값만 검증할 수 있다
// (jsdom 마운트나 fetch 스텁 없이).

export type PaletteMode =
  | 'default'
  | 'file'
  | 'session'
  | 'keymap'
  | 'queue'
  | 'port'
  | 'scrollback'
  | 'settings';

export interface ParsedQuery {
  mode: PaletteMode;
  /** 접두사·앞뒤 공백을 뗀 필터링용 질의. */
  query: string;
  /** 트리밍만 된 원본(접두사 포함) — 비동기 응답이 도착했을 때 "그 사이 입력이
   * 바뀌었는지" 비교하는 스냅샷 용도(quickopen.js의 rawQ 관행과 동일). */
  raw: string;
}

// 접두사 ↔ 모드. `>`(설정)는 60-settings-palette.md §3 표에는 없지만, 2.0의
// ⋯ 메뉴 「설정」 그룹(테마·푸시 등)을 대체하던 기존 기능이라 제거하면 순수
// 퇴행이다 — 문서에 없는 결정이라 커밋 메시지에 근거를 남긴다(7모드에 얹은
// 8번째 접두사로 유지).
export const PREFIX_MODE: Record<string, PaletteMode> = {
  '/': 'file',
  '@': 'session',
  ':': 'keymap',
  '#': 'queue',
  '!': 'port',
  '~': 'scrollback',
  '>': 'settings',
};

export const MODE_PREFIX: Record<Exclude<PaletteMode, 'default'>, string> = {
  file: '/',
  session: '@',
  keymap: ':',
  queue: '#',
  port: '!',
  scrollback: '~',
  settings: '>',
};

export const PLACEHOLDER =
  '세션 · 최근 파일 · 명령 검색… ( / 파일 · @ 세션 · : 명령 · # 큐 · ! 포트 · ~ 스크롤백 )';

export function parseQuery(rawInput: string): ParsedQuery {
  const raw = (rawInput || '').trim();
  const prefix = raw.slice(0, 1);
  const mode = PREFIX_MODE[prefix];
  if (mode) {
    return { mode, query: raw.slice(1).trim(), raw };
  }
  return { mode: 'default', query: raw, raw };
}

// quickopen.js의 _fuzzyMatch와 동일한 규칙(subsequence 아님 — 단순 부분
// 문자열, server/routes/files.py의 진짜 fuzzy와는 의도적으로 다르다: 팔레트
// 로컬 목록은 작아서 단순 포함 검사로 충분하고, 서버 쪽만 대량 파일에
// subsequence 스코어링을 쓴다).
export function fuzzyMatch(hay: string, needle: string): boolean {
  if (!needle) return true;
  return hay.toLowerCase().includes(needle.toLowerCase());
}

export interface CommandDescriptor {
  label: string;
  /** registerAction 액션 id. run이 있으면 action은 참고용(패리티 검사만). */
  action?: string;
  gate?: string;
  /** true면 rail↔팔레트 패리티(ADR-8) 검사 대상 — 패널/모달을 여는 명령. */
  isPanel?: boolean;
}

// 기본(접두사 없음) 팔레트의 "명령" 섹션. ADR-8에 따라 ⋯ 메뉴가 하던 일을
// 전부 커버해야 한다 — rail-palette-parity.test.js가 이 배열과 rail의
// data-action 버튼을 교차 검사한다.
export const DEFAULT_COMMANDS: CommandDescriptor[] = [
  { label: '프롬프트 큐', action: 'queue.show', isPanel: true },
  { label: '프롬프트 스니펫', action: 'snippets.show', isPanel: true },
  { label: '파일 · 공유 링크', action: 'files.show', isPanel: true },
  { label: '포트 대시보드', action: 'ports.show', gate: 'ports', isPanel: true },
  { label: '사용량', action: 'usage.open', gate: 'usage', isPanel: true },
  { label: '터미널 내 검색', action: 'search.toggle' },
  { label: '음성 전용 모드', action: 'voice.only-toggle', gate: 'voice' },
  { label: '새 세션', action: 'session.add-menu' },
];

// `>` 접두사(설정 — 2.0에서 이어받은 부가 기능, 위 PREFIX_MODE 주석 참고).
// 테마 항목은 index.html의 .theme-chip을 그대로 읽으므로(라벨 중복 방지)
// 여기 데이터에는 없다 — Palette.tsx가 DOM에서 직접 구성한다.
export const SETTINGS_COMMANDS: CommandDescriptor[] = [
  { label: '푸시 알림', action: 'push.toggle', gate: 'push' },
  { label: '드래그 시 자동 복사', action: '__toggle:mouse.autocopyOnSelect' },
  { label: '새 세션을 맥에서도 열기', action: '__toggle:session.openOnMac' },
  { label: '이어폰 미디어키', action: 'voice.mediakey-toggle', gate: 'voice' },
  { label: '가이드 보기', action: 'guide.show' },
];

export function panelCommands(cmds: CommandDescriptor[]): CommandDescriptor[] {
  return cmds.filter((c) => c.isPanel);
}
