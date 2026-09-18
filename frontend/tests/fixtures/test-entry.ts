// N18 — 테스트가 `vite build --mode test`로 한 번에 뽑아 쓰는 배럴.
// tests/helpers/vm-esm.js는 .ts/.tsx를 직접 못 읽으므로(TS/JSX 변환 필요),
// 검사 대상 TS 모듈을 여기 모아 실제로 컴파일한 뒤 그 산출물을 jsdom에
// 로드한다. 새 .ts/.tsx를 테스트하려면 여기에 re-export만 추가하면 된다.
export { mountSmoke } from './smoke.js';
export { SCHEMA as SETTINGS_SCHEMA } from '../../js/core/settings.js';
export { buildHudChips, formatResetsIn } from '../../js/shell/hud-data.js';
export { buildRailSections, formatRelativeTime, statusSentence, GROUP_LABEL, mostUrgentStatus, COLLAPSIBLE_GROUPS, groupCollapseKey, GROUP_COLLAPSED_DEFAULT, defaultRailCollapsed, buildSessionSections, buildSleepingEntries } from '../../js/shell/rail-data.js';
export {
  parseQuery, fuzzyMatch, PLACEHOLDER, PREFIX_MODE, MODE_PREFIX,
  DEFAULT_COMMANDS, SETTINGS_COMMANDS, panelCommands,
} from '../../js/shell/palette-data.js';
export { mountPalette } from '../../js/shell/Palette.tsx';
export {
  buildHostMenu, remoteSessionRows, resolveActiveHost, hostDetail, reasonLabel, LOCAL_HOST,
} from '../../js/shell/host-data.js';
export { deleteWorktreeRow } from '../../js/shell/rail-fetch.js';
