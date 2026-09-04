// L3 3단계 — 비율(0~1) 기반 리사이저 핸들. 분할 pane 구분선이 첫 소비처지만
// rail 패널 폭 조정도 나중에 이 파일을 같이 쓰도록 일반화해뒀다(계획서
// "viewer.js:265 _wireResizer를 공용화" 항목의 대체 구현 — 그 함수는 절대
// 픽셀 폭(--vt-dock-w) 하나만 다루는 전용 코드라 그대로 재사용하면 오히려
// API가 더 꼬인다. 대신 새로 작성해 여기서부터 공유를 시작한다).
export function wireRatioResizer(handle, { dir, getContainerSize, getStartRatio, onRatio, onEnd }) {
  const axis = dir === 'row' ? 'clientX' : 'clientY';
  let startPos = 0;
  let startRatio = 0.5;

  const onMove = (ev) => {
    const size = getContainerSize();
    if (!size) return;
    onRatio(startRatio + (ev[axis] - startPos) / size);
  };
  const onUp = () => {
    document.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerup', onUp);
    handle.classList.remove('dragging');
    document.body.classList.remove('vt-resizing');
    if (onEnd) onEnd();
  };
  handle.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    startPos = ev[axis];
    startRatio = getStartRatio();
    handle.classList.add('dragging');
    document.body.classList.add('vt-resizing');
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
  });
}
