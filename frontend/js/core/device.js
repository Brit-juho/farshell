// iPad의 데스크톱 UA도 모바일로 판별한다. 화면 폭·입력 포인터와는 독립적이다.
export function isMobileDevice() {
  return /iPhone|iPad|iPod|Android/i.test(navigator.userAgent)
    || (/Macintosh/i.test(navigator.userAgent) && navigator.maxTouchPoints > 1);
}
