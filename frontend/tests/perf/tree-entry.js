// 측정용 묶음의 진입점 — 새 파일 트리(frontend/js/files/, Solid + TS)는 브라우저가 원본 그대로 읽지 못하므로
// run.mjs가 이 파일을 vite(vite-plugin-solid)로 한 번 묶어 `/perf-build/tree-bench.js`로 내준다.
export { mountFileTree } from '../../js/files/mount.tsx';
