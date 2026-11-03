// 셸에 붙여넣을 경로 하나를 인용한다 — 순수 로직만(DOM 없음). 서버 server/tmux_target.py `shell_quote_path`와 규칙이 같다.
//
// 업로드 파일 이름은 보내는 쪽이 정한다(피어 Mac의 헤더, 받은 파일). `$(…)`·백틱·`;`·`'`가 든 이름이 경로에 그대로
// 섞여 명령줄에 붙으면 사용자가 Enter를 누르는 순간 실행된다. 안전한 문자(문자·숫자·결합 기호 + `_@%+=:,./-`)로만
// 된 경로는 그대로 두고(일반 업로드는 달라지지 않는다), 아니면 홑따옴표로 감싼다 — 홑따옴표 안에서는 아무것도
// 해석되지 않고, 이름의 `'`만 `'"'"'`로 바꾼다.

const SAFE = /^[\p{L}\p{N}\p{M}_@%+=:,./-]+$/u;

export function shellQuotePath(path) {
  const s = String(path);
  if (s === '') return "''";
  // 첫 글자: zsh는 `=cmd`(명령 경로 치환)·`%1`(작업)·`~`를, 모든 셸은 `-x`를 단어 맨 앞에서만 특별 취급한다.
  if (SAFE.test(s) && '=%-+~'.indexOf(s.charAt(0)) < 0) return s;
  return "'" + s.replace(/'/g, "'\"'\"'") + "'";
}
