// 기록에 남기기 전에 비밀값을 가린다. 서버(server/client_log.py)가 같은 규칙을 한 번 더 적용한다.
// 두 구현은 frontend/tests/fixtures/redact-cases.json 을 함께 읽는 시험으로 어긋나지 않게 묶여 있다 —
// 규칙을 고치면 JS·Python·사례 파일 세 곳을 같이 고친다.
//
// 의도적으로 과하게 가린다(이름에 secret 단어가 들어간 값은 전부). 진단 가치가 있는 해시(커밋 sha 등)는
// 이름이 secret 단어가 아니므로 건드리지 않는다. 요청·응답 본문은 애초에 기록하지 않는다(event-log.js).
const SECRET = '(?:token|ticket|otp|secret|passw(?:or)?d|api_?key|auth|signature|session|cookie)';
const NAME = '[A-Za-z0-9_.-]*' + SECRET + '[A-Za-z0-9_.-]*';

// "password": "x" — JSON 모양. 값 문자열만 바꾸고 따옴표·공백은 그대로 둔다.
const JSON_KV = new RegExp('("' + NAME + '"\\s*:\\s*)"(?:[^"\\\\]|\\\\.)*"', 'gi');
// ?token=x · &ticket=x · Cookie: vt_session=x; — 이름=값. 값은 구분자(& 공백 따옴표 ; ,)에서 끝난다.
const QUERY_KV = new RegExp('((?:^|[?&;,\\s])' + NAME + '=)[^&\\s"\';,]+', 'gi');
const BEARER = /\b(Bearer\s+)[A-Za-z0-9._~+/=-]+/gi;

export function redact(text) {
  if (typeof text !== 'string' || !text) return text;
  return text
    .replace(JSON_KV, '$1"***"')
    .replace(QUERY_KV, '$1***')
    .replace(BEARER, '$1***');
}
