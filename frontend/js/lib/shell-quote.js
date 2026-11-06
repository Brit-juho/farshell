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

// 경로에 C0 제어문자(0x00-0x1F)나 DEL(0x7F)이 있는가. 인용은 셸이 읽을 때만 효과가 있다 — 터미널 입력은 셸에 닿기 전에
// tty 줄 규칙·readline·ZLE를 지나고, 거기서 `^U`(줄 지우기)·`\r`(Enter)은 따옴표와 상관없이 바로 처리된다:
// `x\x15touch /tmp/pwn\r` 이라는 이름은 `^U`가 여는 따옴표를 지우고 `\r`이 Enter 전에 `touch`를 실행한다. 이런 경로는
// 터미널에 아예 넣지 않는다(서버 server/shell_quote.py `has_control_chars`와 같은 규칙).
const CONTROL = /[\x00-\x1f\x7f]/;

export function hasControlChars(path) {
  return CONTROL.test(String(path));
}
