// 설정 항목의 **순수 데이터** — 화면(panels/settings.js)과 빠른 열기 팔레트(shell/Palette.tsx)가
// 같은 정의를 읽는다. 항목을 두 곳에 적으면 한쪽만 고쳐져 어긋난다.
//
// ⚠ 이 파일은 core/i18n.js 말고 아무것도 import하지 않는다. 팔레트는 지연 청크(shell.js)라
// core/store.js·core/settings.js 같은 상태 싱글톤을 정적 import하면 청크 안에 복제된다(ADR-26,
// build-output.test.js). 값 읽기·쓰기는 호출하는 쪽이 window.vtSettings* 브리지로 한다.
import { t } from '../../core/i18n.js';

// 터미널 섹션의 스키마 항목. 값·범위·기본값은 core/settings.js SCHEMA가 갖고, 여기는
// "어떤 키를 어떤 컨트롤로 보여줄지"만 정한다(settings.js 머리 주석의 렌더링 원칙).
export const TERMINAL_GROUPS = [
  {
    label: t('표시'),
    items: [
      { key: 'terminal.fontSize', label: t('글자 크기'), kind: 'range', unit: 'px' },
      { key: 'terminal.cursorStyle', label: t('커서 모양'), kind: 'select',
        labels: { block: t('블록'), underline: t('밑줄'), bar: t('막대') } },
      { key: 'terminal.cursorBlink', label: t('커서 깜빡임'), kind: 'bool' },
      // 2026-09-18 — ui/settings-toggles.js가 진작 스토어에 묶어뒀지만
      // (E2), 그 체크박스가 살던 자리(legacy rail 플라이아웃)가 오늘
      // 도달 불가능해지며 이 키를 끄고 켤 화면이 아예 없어졌다.
      { key: 'session.openOnMac', label: t('새 세션 생성 시 맥 터미널에서도 열기'), kind: 'bool' },
    ],
  },
  {
    label: t('입력 및 선택'),
    items: [
      { key: 'mouse.forwardToApp', label: t('앱에 마우스 이벤트 전달'),
        // S1 spike로 구현 가능함이 확인된 항목. 끄면 iTerm2 기본 동작이 된다.
        help: t('끄면 vim·tmux가 마우스를 잡아도 항상 드래그로 선택할 수 있습니다 (iTerm2 기본 동작).'),
        kind: 'bool' },
      { key: 'mouse.autocopyOnSelect', label: t('드래그 시 자동 복사'), kind: 'bool' },
      { key: 'terminal.osc52Untrusted', label: t('tmux가 아닌 터미널의 클립보드 쓰기 허용(OSC 52)'),
        help: t('켜면 tmux 없는 터미널의 출력이 이 기기 클립보드를 바꿀 수 있습니다. 신뢰할 수 없는 출력(cat 한 파일, 접속한 서버)이 클립보드를 바꿀 위험이 있습니다.'), kind: 'bool' },
      { key: 'mouse.touchTapToApp', label: t('터치 탭을 앱에 전달'),
        help: t('터치 기기에서 짧은 탭을 앱으로 넘겨 커서를 옮깁니다.'), kind: 'bool' },
    ],
  },
  {
    label: t('접근성'),
    items: [
      { key: 'a11y.screenReader', label: t('스크린 리더 모드'), kind: 'select',
        labels: { auto: t('자동 (터치 기기에서 켬)'), on: t('항상 켬'), off: t('항상 끔') },
        help: t('새로 여는 탭부터 적용됩니다 — 터미널 내부 구조가 달라져 실행 중에는 바꿀 수 없습니다.') },
    ],
  },
  {
    // ADR-43(2.3.0) — 서버 디스크 영속화 토글을 없앴다. 줄 수 설정(브라우저 쪽
    // xterm.js 버퍼)은 별개 기능이라 남는다.
    label: t('스크롤백'),
    items: [
      { key: 'terminal.scrollback', label: t('스크롤백 줄 수'), kind: 'range', step: 500, unit: '줄', presets: [2000, 5000, 10000] },
    ],
  },
];

// 리디자인 7단계 — 설정 › 모양 「집중 모드」(시안 O04-B). 화면(sections/appearance.js)과 팔레트 `>` 줄이 같이 읽는다.
// 집중 모드는 데스크톱(720px 이상)에서만 막대를 숨긴다 — 폰 화면은 이 값과 무관하다.
export const FOCUS_ITEMS = [
  { key: 'ui.focus.active', label: t('집중 모드'), kind: 'bool',
    help: t('활동 막대 · 사이드바 · 제목 막대 · 상태 막대를 숨기고 칸을 창 끝까지 넓힙니다. FarShell 메뉴와 팔레트에서도 켭니다. 새로고침하면 꺼집니다.') },
  { key: 'ui.focus.edgeReveal', label: t('가장자리에 막대 띄우기'), kind: 'bool',
    help: t('왼쪽 가장자리 → 활동 막대 · 사이드바, 위 → 제목 막대, 아래 → 상태 막대. 끄면 단축키나 개입 표시로만 돌아옵니다.') },
  { key: 'ui.focus.pill', label: t('개입 표시'), kind: 'bool',
    help: t('승인 대기가 생기면 오른쪽 위에 「개입 N」 알약을 띄웁니다. 누르면 그 세션으로 갑니다.') },
  { key: 'ui.focus.edgeDelay', label: t('가장자리 반응 시간'), kind: 'select',
    labels: { 0: t('바로'), 250: '250ms', 500: '500ms' } },
];

// 설정 › 모양 「메뉴 크기」 — 데스크톱의 활동 막대 · 사이드바 · 접힌 세션 띠 · 세션 행 크기(작게 44/36 · 보통 48/40 · 크게 52/44).
// 폰은 이 값과 무관하다. 값별 치수는 shell/side-prefs.js SIDE_SCALES, 스타일은 90-workbench.css · 70-worktree-rail.css.
export const SIDE_SCALE_ITEMS = [
  { key: 'ui.side.scale', label: t('메뉴 크기'), kind: 'select',
    labels: { small: t('작게'), normal: t('보통'), large: t('크게') },
    help: t('활동 막대, 사이드바, 접힌 세션 띠와 세션 목록의 크기입니다. 끌어서 정한 사이드바 폭은 그대로입니다.') },
];

// 섹션의 이름·설명. 렌더 함수(custom)는 settings.js가 id로 붙인다 — 렌더러를 여기서
// import하면 이 파일이 화면 전체를 끌고 들어와 팔레트 청크가 부풀기 때문이다.
export const SECTION_META = [
  { id: 'terminal', label: t('터미널'), description: t('표시, 입력, 접근성, 기록 방식을 설정합니다.') },
  { id: 'voice', label: t('음성 및 알림'), description: t('알림과 음성 입력 엔진을 관리합니다.') },
  { id: 'agents', label: t('에이전트'), description: t('에이전트 감지 상태를 확인합니다.') },
  { id: 'keymap', label: t('키맵'), description: t('단축키와 터미널 전달 여부를 바꿉니다.') },
  { id: 'appearance', label: t('모양'), description: t('스킨, 화면 언어, 메뉴 크기, 파일 뷰어 표시 방식, 집중 모드를 고릅니다.') },
  { id: 'hud', label: t('상태 막대 · 활동 막대'), description: t('화면 맨 아래 상태 막대의 칩과, 왼쪽 활동 막대의 순서를 고릅니다.'), desktopOnly: true },
  { id: 'permissions', label: t('권한'), description: t('에이전트의 승인 요청, 페어, 폴더 점유, 허용된 권한, 감사 기록을 봅니다.') },
  { id: 'security', label: t('계정 및 보안'), description: t('로그인, OTP, 등록 기기와 현재 세션을 확인합니다.') },
  { id: 'update', label: t('업데이트'), description: t('새 버전 확인과 자동 업데이트를 설정합니다.') },
  { id: 'about', label: t('정보 및 진단'), description: t('훅과 사용량 공급자의 연결 상태를 진단합니다.') },
];

export const SEARCH_ITEMS = [
  ['terminal', t('글자 크기')], ['terminal', t('커서 모양')], ['terminal', t('커서 깜빡임')],
  ['terminal', t('새 세션 생성 시 맥 터미널에서도 열기')], ['terminal', t('앱에 마우스 이벤트 전달')],
  ['terminal', t('드래그 시 자동 복사')], ['terminal', t('터치 탭을 앱에 전달')],
  ['terminal', t('스크린 리더 모드')], ['terminal', t('스크롤백 줄 수')],
  ['voice', t('웹 푸시 알림')], ['voice', t('완료 알림')], ['voice', t('알림 방식')], ['voice', t('음성 인식 모델')], ['voice', t('내려받은 파일 관리')],
  ['voice', t('현재 세션 음성')], ['voice', t('음성 전용 모드')], ['voice', t('확인 후 전송')], ['voice', t('입력 대상')],
  ['agents', t('에이전트 감지 상태')],
  ['keymap', t('단축키와 터미널 전달')],
  ['appearance', t('스킨')], ['appearance', t('화면 언어')], ['appearance', t('메뉴 크기')], ['appearance', t('파일 뷰어 표시 방식')],
  ['appearance', t('집중 모드')], ['appearance', t('가장자리에 막대 띄우기')], ['appearance', t('개입 표시')], ['appearance', t('가장자리 반응 시간')],
  ['hud', t('상태 막대 칩')], ['hud', t('활동 막대 순서')],
  ['permissions', t('승인 대기')], ['permissions', t('페어')], ['permissions', t('폴더 점유(lease)')], ['permissions', t('허용된 권한')], ['permissions', t('감사 기록')], ['permissions', t('비상 정지')],
  ['security', t('로그인과 OTP')], ['security', t('등록 기기')], ['security', t('로그아웃')],
  ['update', t('새 버전 확인')], ['update', t('자동 업데이트')], ['update', t('적용 시각')],
  ['about', t('에이전트 훅')], ['about', t('사용량 공급자')], ['about', t('버전과 진단')],
].map(([sectionId, label]) => ({ sectionId, label }));

// 값은 화면에 나오지 않는 검색어 묶음이다. 영어 화면에서는 영어 검색어로 바뀐다.
export const SEARCH_ALIASES = {
  [t('글자 크기')]: t('폰트 글꼴 확대 축소'),
  [t('커서 모양')]: t('캐럿'),
  [t('스킨')]: t('테마 색상 컬러'),
  [t('파일 뷰어 표시 방식')]: t('코드 뷰어 시트 팝업 창 페인'),
  [t('웹 푸시 알림')]: t('브라우저 알림 구독'),
  [t('음성 인식 모델')]: t('음성 입력 받아쓰기 stt 모델 선택 받기 다운로드 qwen3 mlx llama 엔진 정확도 속도 메모리'),
  [t('알림 방식')]: t('완료음 낭독 tts 소리 알림음 최소 작업 시간 오류 제외 웹 접속 중'),
  [t('내려받은 파일 관리')]: t('모델 삭제 지우기 용량 저장 공간 엔진 파일'),
  [t('확인 후 전송')]: t('음성 입력 확인 위험 명령 rm sudo 셸 바로 입력 인식 결과 고치기'),
  [t('입력 대상')]: t('음성 입력 세션 고정 따라가기 마이크 대상 pin'),
  [t('맥에서 음성만 쓰기')]: t('로컬 음성 입력 마이크'),
  [t('등록 기기')]: t('디바이스 장치'),
  [t('승인 대기')]: t('승인함 인박스 에이전트 요청 허용 거부'),
  [t('페어')]: t('pair 짝 코드 합류 역할 교체 handoff driver navigator 운전자 길잡이 fsh pair'),
  [t('폴더 점유(lease)')]: t('lease 잠금 폴더 점유 배타 경로 glob'),
  [t('허용된 권한')]: t('grant 회수 철회 프리셋 보기 협업 운전 제어'),
  [t('감사 기록')]: t('audit 로그 체인 거부 기록'),
  [t('비상 정지')]: t('panic 모든 에이전트 권한 회수'),
  [t('화면 언어')]: t('언어 language 한국어 영어 english'),
  [t('집중 모드')]: t('전체 화면 막대 숨기기 젠 zen focus'),
  [t('메뉴 크기')]: t('사이드바 활동 막대 세션 목록 글자 크기 작게 보통 크게 확대 축소 scale'),
  [t('상태 막대 칩')]: t('메뉴바 하단바 hud 칩 시계 cpu'),
  [t('활동 막대 순서')]: t('활동 숨기기 아이콘 순서 왼쪽 막대'),
  [t('에이전트 훅')]: 'hook codex claude',
  [t('자동 업데이트')]: t('업데이트 버전 최신 릴리스 새 버전 update upgrade 서명 stable'),
  [t('새 버전 확인')]: t('업데이트 버전 확인 네트워크 fetch'),
};

// 레일 설정 중 스키마 키 하나로 끝나는 켜기/끄기. 「저장소별로 묶기」·「최근 종료 표시 개수」는
// rail.view JSON 한 덩어리, 「자동 보관」은 서버 API라 여기서 바로 못 바꾼다(레일 설정 시트에서).
// 라벨은 shell/RailSettings.tsx·rail-data.ts와 같은 문장이다(영어 사전 키가 한국어 원문).
export const RAIL_TOGGLES = [
  { key: 'rail.attention.show', label: t('개입 필요 구역 표시'), group: t('구역'),
    help: t('입력을 기다리는 세션을 맨 위에 모아 보여줍니다') },
  { key: 'rail.origin.peer.show', label: t('다른 맥에서 만듦'), group: t('만든 곳별 표시') },
  { key: 'rail.origin.external.show', label: t('직접 만듦 (CLI·tmux)'), group: t('만든 곳별 표시') },
];

const sectionLabel = (id) => SECTION_META.find((s) => s.id === id)?.label || id;

/**
 * 검색 색인 — 항목 하나가 한 줄이다.
 *   kind 'bool' | 'select' | 'range' : 그 자리에서 바꿀 수 있다(key + 컨트롤 정보).
 *   kind 'link'                      : 바꾸지 못하고 해당 섹션으로 보낸다(section).
 * 위험하거나 설명이 필요한 섹션(권한·보안·음성 모델 등)은 일부러 link로만 둔다 —
 * 확인 단계가 있는 화면을 건너뛰지 않게 하려는 것이다.
 */
export function buildSettingEntries(shownSectionIds) {
  const visible = (id) => !shownSectionIds || shownSectionIds.includes(id);
  const entries = [];
  const inlineLabels = new Set();
  for (const g of TERMINAL_GROUPS) {
    for (const it of g.items) {
      inlineLabels.add(it.label);
      entries.push({
        id: `s:${it.key}`, key: it.key, label: it.label, kind: it.kind,
        labels: it.labels, unit: it.unit, step: it.step, presets: it.presets, help: it.help,
        section: 'terminal', path: `${sectionLabel('terminal')} › ${g.label}`,
        aliases: SEARCH_ALIASES[it.label] || '',
      });
    }
  }
  if (visible('appearance')) {
    for (const [items, groupLabel] of [[SIDE_SCALE_ITEMS, t('메뉴')], [FOCUS_ITEMS, t('집중 모드')]]) {
      for (const it of items) {
        inlineLabels.add(it.label);
        entries.push({
          id: `s:${it.key}`, key: it.key, label: it.label, kind: it.kind, labels: it.labels, help: it.help,
          section: 'appearance', path: `${sectionLabel('appearance')} › ${groupLabel}`,
          aliases: SEARCH_ALIASES[it.label] || '',
        });
      }
    }
  }
  for (const r of RAIL_TOGGLES) {
    entries.push({
      id: `r:${r.key}`, key: r.key, label: r.label, kind: 'bool', help: r.help,
      section: null, path: `${t('세션 목록 설정')} › ${r.group}`, aliases: '',
    });
  }
  for (const s of SEARCH_ITEMS) {
    if (inlineLabels.has(s.label) || !visible(s.sectionId)) continue;
    entries.push({
      id: `l:${s.sectionId}:${s.label}`, key: null, label: s.label, kind: 'link',
      section: s.sectionId, path: sectionLabel(s.sectionId), aliases: SEARCH_ALIASES[s.label] || '',
    });
  }
  return entries;
}

/** 공백으로 나눈 낱말이 전부 라벨·경로·별칭 안에 들어 있으면 일치. 한 낱말이라도 빠지면 탈락. */
export function matchSettingEntry(entry, query) {
  const q = (query || '').trim().toLocaleLowerCase();
  if (!q) return true;
  const hay = `${entry.label} ${entry.path} ${entry.aliases || ''}`.toLocaleLowerCase();
  return q.split(/\s+/).every((word) => hay.includes(word));
}

/**
 * 컨트롤 한 칸 이동. dir은 +1/-1. 바꿀 값을 돌려주고, 바꿀 게 없으면 undefined.
 *  bool   : 방향과 무관하게 뒤집는다(Enter와 같은 동작).
 *  select : 값 목록을 돌며 이동(양 끝에서 감싼다).
 *  range  : step만큼. 범위 밖은 core/settings.js set()이 min/max로 자른다.
 */
export function stepSettingValue(entry, current, dir) {
  if (entry.kind === 'bool') return !current;
  if (entry.kind === 'select') {
    const values = Object.keys(entry.labels || {});
    if (!values.length) return undefined;
    const i = values.indexOf(current);
    return values[(i + dir + values.length) % values.length];
  }
  if (entry.kind === 'range') {
    const n = Number(current);
    if (Number.isNaN(n)) return undefined;
    return n + dir * (entry.step || 1);
  }
  return undefined;
}

/** 현재 값을 사람이 읽는 글자로. bool은 켜짐/꺼짐(컨트롤은 별도 스위치로 그린다). */
export function formatSettingValue(entry, value) {
  if (entry.kind === 'bool') return value ? t('켜짐') : t('꺼짐');
  if (entry.kind === 'select') return entry.labels?.[value] ?? String(value);
  if (entry.kind === 'range') return `${Number(value).toLocaleString()}${entry.unit ? (entry.unit === 'px' ? entry.unit : ` ${entry.unit}`) : ''}`;
  return String(value ?? '');
}
