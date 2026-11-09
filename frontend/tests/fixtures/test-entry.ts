// N18 — 테스트가 `vite build --mode test`로 한 번에 뽑아 쓰는 배럴.
// tests/helpers/vm-esm.js는 .ts/.tsx를 직접 못 읽으므로(TS/JSX 변환 필요),
// 검사 대상 TS 모듈을 여기 모아 실제로 컴파일한 뒤 그 산출물을 jsdom에
// 로드한다. 새 .ts/.tsx를 테스트하려면 여기에 re-export만 추가하면 된다.
export { mountSmoke } from './smoke.js';
export { SCHEMA as SETTINGS_SCHEMA } from '../../js/core/settings.js';
export {
  buildHudChips, formatResetsIn, formatRate, formatClock, HUD_KINDS, PINNED_HUD_KINDS, DEFAULT_HUD_LAYOUT, parseHudLayout, serializeHudLayout,
  sameHudLayout, hudZoneOf, moveHudKind, stepHudKind,
} from '../../js/shell/hud-data.js';
export {
  formatRelativeTime, statusSentence, stateKeyword, defaultRailCollapsed,
  buildRailLists, compactRailRows, readRailPrefs, RAIL_PREF_DEFAULTS, ATTENTION_SHOW_KEY, ATTENTION_COLLAPSED_KEY, originShowKey,
  HIDEABLE_ORIGINS, normalizeOrigin, normalizeKind, normalizeBadge, rowMenuItems, rowSummary, runRowMenu,
  worktreeIconName, diffLabel, KIND_LABEL, BADGE_LABEL, normalizeShare, shareSentence, groupSessionRows,
  normalizeLineage, lineageLabel, buildSessionTree, kidsProgress, moveInOrder, stepInOrder, parseNameList,
  ORDER_KEY, LINEAGE_KEY, encodeOrder, encodeLineagePrefs,
  splitRunning, pickRecentEnded, parseRailView, encodeRailView,
} from '../../js/shell/rail-data.js';
export { collectSessionRows } from '../../js/shell/rail-fetch.js';
export { mountRow, mountMenu, mountNativeChildren } from './row-mount.js';
export {
  parseQuery, fuzzyMatch, PLACEHOLDER, PREFIX_MODE, MODE_PREFIX, MODE_HINT,
  DEFAULT_COMMANDS, SETTINGS_COMMANDS, panelCommands,
} from '../../js/shell/palette-data.js';
export {
  splitGlobs, globParam, grepUrl, groupByFile, segments, hitColumn, summary, skippedLine, DEFAULT_OPTIONS as GREP_DEFAULTS,
} from '../../js/shell/grep-data.js';
export {
  buildSettingEntries, matchSettingEntry, stepSettingValue, formatSettingValue, SECTION_META, RAIL_TOGGLES,
} from '../../js/panels/settings/index-data.js';
export { estimateCols, cursorClass, SAMPLE_LINES, resolveSkin, MONO_ADVANCE } from '../../js/panels/settings/preview-data.js';
export { mountPalette } from '../../js/shell/Palette.tsx';
export {
  buildHostMenu, resolveActiveHost, hostDetail, reasonLabel, LOCAL_HOST,
} from '../../js/shell/host-data.js';
export {
  deleteWorktreeRow, fetchDiffCount, diffCountStale, cachedDiffCount, createDiffWatcher, msUntilDiffRefresh,
} from '../../js/shell/rail-fetch.js';
export {
  ACTIVITIES, TOOL_ACTIVITIES, availableActivities, activityById, activityByPanelId, badgeTip, stepActivityIndex,
} from '../../js/shell/activity-data.js';
export { mountActivityBar } from './activity-bar-mount.js';
export { mountWorkbench } from '../../js/shell/Workbench.tsx';
export { mountTitleBar, accessUrl } from '../../js/shell/TitleBar.tsx';
export { mountHud } from '../../js/shell/Hud.tsx';
export {
  keyOf, joinPath, parentOf, isUnder, relToAbs, toEntries, dirFromListing, appendPage, flattenPrefetch, changedNames,
  removedDirs, compileFilter, flatten, FlattenCache, reuseRows, windowRange, scrollToReveal, rowHeight, treeKey,
  typeahead, focusable, fmtSize, emptyDir, OVERSCAN,
} from '../../js/files/tree-model.js';
export { TreeStore } from '../../js/files/tree-store.js';
export { mountFileTree } from '../../js/files/mount.tsx';
