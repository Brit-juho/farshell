// 트리 노드 id → DOM element id. 한 줄짜리 함수 둘을 위해 파일을 따로 두는
// 이유가 둘 있다.
//
// 1) 접두사가 두 번 붙어 있었다. `layout/store.js`의 `_genId('pane')`이 이미
//    `pane-<uuid>`를 만드는데 `panes.js`가 거기에 `vt-pane-`을 또 붙여
//    `id="vt-pane-pane-<uuid>"`가 나왔다(split도 `vt-split-split-<uuid>`).
//    동작에는 영향이 없었지만 DevTools에서 눈에 걸리는 종류의 흠이고, 규칙이
//    코드 어디에도 안 적혀 있어서 읽는 쪽이 매번 두 곳을 맞춰 봐야 했다.
//    노드 id가 이미 종류를 말하므로(`pane-` / `split-`) 여기서는 네임스페이스
//    `vt-`만 붙인다.
//
// 2) 이 규칙을 아는 곳이 둘이다 — `panes.js`(만드는 쪽)와
//    `resize-overlay.js`(찾는 쪽). 그런데 panes.js가 resize-overlay.js를
//    import하므로 반대 방향 import를 넣으면 순환이 된다. 그래서 둘 다 기댈 수
//    있는 **의존성 없는 잎 모듈**에 둔다.
//
// ⚠ 이 규칙을 바꾸면 만드는 쪽과 찾는 쪽이 같이 바뀌어야 한다. 그게 바로 이
//    파일이 존재하는 이유이므로, 문자열을 호출부에 다시 인라인하지 말 것.

/** pane leaf의 DOM id. `pane-<uuid>` → `vt-pane-<uuid>` */
export function paneElId(paneId) {
  return `vt-${paneId}`;
}

/** split 노드의 DOM id. `split-<uuid>` → `vt-split-<uuid>` */
export function splitElId(splitId) {
  return `vt-${splitId}`;
}
