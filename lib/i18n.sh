# FarShell — 화면 언어(ko/en) + 메시지 번역 (bash 측)
#
# Python 대응은 server/i18n.py다. 두 쪽이 같은 순서로 언어를 정한다:
#   1. ~/.vt.env 의 VT_LANG   (파일이 먼저다 — 셸·tmux에 남은 낡은 export가 설정 화면에서
#                               바꾼 값을 이기면 "바꿨는데 안 바뀐다"가 된다)
#   2. 환경변수 VT_LANG
#   3. OS 로캘 LC_ALL → LC_MESSAGES → LANG  (C/POSIX는 건너뜀, ko* 면 한국어, 그 밖은 영어)
#   4. 영어
#
# 사용:
#   echo "$(_t '서버를 시작합니다')"
#   echo "$(_t '{n}개 세션 닫음' n="$count")"
#
# 키는 **한국어 원문**이다. 한국어면 원문이 그대로 나오고, 영어면 lib/locales/en.sh의
# 사전을 본다(frontend/js/locales/en.js에서 scripts/gen_cli_i18n.py가 만든다).
# 사전에 없으면 원문(한국어)이 나온다.
#
# 규칙:
#   - 키는 **리터럴**이어야 한다(작은따옴표 권장). "$변수"를 키 안에 넣으면 사전과 안 맞는다.
#     값은 {name} 자리표시자로 넘긴다.
#   - bash 3.2(macOS 기본)에서 돌아야 해서 연관 배열을 쓰지 않는다 — 사전은 case 문이다.
#   - 여러 줄 안내문(heredoc)은 vt_is_en 으로 갈라 영어 본문을 따로 둔다.

# bash 5.2는 ${var//pat/rep} 의 rep 안 `&` 를 매치 문자열로 바꾼다(patsub_replacement). URL의
# `?a=1&b=2` 같은 값이 {url} 자리에서 깨지므로 끈다. 5.2 미만에는 없는 옵션이라 오류는 버린다.
shopt -u patsub_replacement 2>/dev/null || true

_vt_lang_norm() {
  local h="${1-}"
  h="$(printf '%s' "$h" | tr 'A-Z' 'a-z' | tr '-' '_')"
  h="${h%%.*}"; h="${h%%_*}"
  case "$h" in ko|en) printf '%s' "$h" ;; esac
}

# 언어 코드를 정해 _VT_LANG 에 담는다. vt_env.sh가 먼저 source 돼 있어야 한다.
vt_lang_resolve() {
  local v k
  v=""
  if command -v vt_env_get >/dev/null 2>&1; then
    v="$(_vt_lang_norm "$(vt_env_get VT_LANG 2>/dev/null)")"
  fi
  if [ -z "$v" ]; then v="$(_vt_lang_norm "${VT_LANG-}")"; fi
  if [ -z "$v" ]; then
    for k in LC_ALL LC_MESSAGES LANG; do
      eval "v=\${$k-}"
      case "$v" in ''|C|POSIX) v=""; continue ;; esac
      [ "$(_vt_lang_norm "$v")" = "ko" ] && v=ko || v=en
      break
    done
  fi
  _VT_LANG="${v:-en}"
}

vt_lang() { [ -n "${_VT_LANG-}" ] || vt_lang_resolve; printf '%s' "$_VT_LANG"; }
vt_is_en() { [ -n "${_VT_LANG-}" ] || vt_lang_resolve; [ "$_VT_LANG" != "ko" ]; }

_vt_i18n_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$_vt_i18n_dir/locales/en.sh" ]; then
  # shellcheck source=locales/en.sh
  . "$_vt_i18n_dir/locales/en.sh"
else
  _t_en_lookup() { return 1; }
fi

_t() {
  local out="${1-}" kv k v
  shift 2>/dev/null || true
  if vt_is_en; then
    _T_OUT=""
    if _t_en_lookup "$out" && [ -n "$_T_OUT" ]; then out="$_T_OUT"; fi
  fi
  for kv in "$@"; do
    k="${kv%%=*}"; v="${kv#*=}"
    out="${out//\{$k\}/$v}"
  done
  printf '%s' "$out"
}
