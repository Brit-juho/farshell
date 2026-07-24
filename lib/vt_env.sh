# voice-terminal — ~/.vt.env 형식 정의 + 단일 reader/writer (bash 측)
#
# 이 파일이 형식을 정의한다. Python 대응 구현은 server/vt_env.py이며,
# server/tests/test_vt_env.py가 둘이 같은 값을 읽는지 매번 확인한다.
#
# ── 형식 ─────────────────────────────────────────────────────────────
#   KEY=VALUE            (선택적으로 'export ' 접두)
#   전체 줄 주석(#)과 빈 줄은 무시. 줄 끝 주석은 지원하지 않는다(값에 #이 흔하므로).
#
#   VALUE 표기:
#     'literal'   홑따옴표 — 확장 없음. 내부 ' 는 '\'' 로. **writer가 항상 쓰는 형태**
#     "expanded"  큰따옴표 — ${VAR} / $VAR 확장. \" \\ \$ \` 이스케이프
#     bare        따옴표 없음 — 공백 전까지, 확장 있음
#
#   확장 참조 순서: 이 파일에서 앞서 정의된 키 → 프로세스 환경변수 → 빈 문자열.
#   (install.sh가 만드는 VT_PYTHON=${VT_DIR}/.venv/bin/python 이 이걸 쓴다)
#
#   **미지원**: 명령 치환 $( ) / 백틱, 산술 $(( )), 조건문 등 모든 실행 구문.
#   설정 파일은 데이터이지 코드가 아니다. 해당 구문은 리터럴로 남고 vt_env_lint가 잡는다.
#   동적인 값이 필요하면 셸 rc에 'export VT_X=$(...)' 를 두세요 — 환경변수가 파일보다 우선입니다.
#
# ── 파일 권한 ────────────────────────────────────────────────────────
#   VT_AUTH_TOKEN / VT_AUTH_PASSWORD_HASH / VT_AUTH_SESSION_KEY 같은 시크릿이 들어가므로
#   쓰기 함수는 항상 0600을 보장한다. 임시 파일도 umask 077로 만든다.

# 대상 파일 — VT_CONFIG가 있으면 그것을 쓴다(프로필 분리 / 테스트 격리).
vt_env_file() {
  printf '%s' "${VT_CONFIG:-$HOME/.vt.env}"
}

_vt_env_valid_key() {
  case "${1-}" in
    ''|[0-9]*|*[!A-Za-z0-9_]*) return 1 ;;
    *) return 0 ;;
  esac
}

# ── 값 파싱 ──────────────────────────────────────────────────────────
# 호출자의 raw/i/n/out 지역변수를 공유한다(bash 동적 스코프).

# 이름 하나를 확장해 out에 붙인다. 정의돼 있지 않으면 _vt_undef에 이름을 남긴다
# (vt_env_lint가 "값이 조용히 사라지는" 줄을 찾는 데 쓴다).
_vt_env_append_var() {
  local nm="$1"
  if [ -z "${!nm+x}" ]; then
    _vt_undef="${_vt_undef}${nm} "
    return 0
  fi
  out="$out${!nm}"
}

# raw[i] == '$' 인 지점에서 확장. out/i를 갱신한다.
_vt_env_expand_at() {
  local two="${raw:i:2}" name="" j

  # 실행 구문은 형식에 없다 — '$'를 리터럴로 두고 넘어간다
  if [ "$two" = '$(' ] || [ "$two" = '$[' ]; then
    out="$out\$"; i=$((i + 1)); return
  fi

  if [ "$two" = '${' ]; then
    j=$((i + 2))
    while [ "$j" -lt "$n" ] && [ "${raw:j:1}" != "}" ]; do
      name="$name${raw:j:1}"; j=$((j + 1))
    done
    if [ "$j" -ge "$n" ] || ! _vt_env_valid_key "$name"; then
      out="$out\$"; i=$((i + 1)); return    # 안 닫혔거나 ${#x} 같은 미지원 형태
    fi
    _vt_env_append_var "$name"; i=$((j + 1)); return
  fi

  j=$((i + 1))
  while [ "$j" -lt "$n" ]; do
    case "${raw:j:1}" in
      [A-Za-z0-9_]) name="$name${raw:j:1}"; j=$((j + 1)) ;;
      *) break ;;
    esac
  done
  if ! _vt_env_valid_key "$name"; then
    out="$out\$"; i=$((i + 1)); return      # $1, $$ 등 — 리터럴
  fi
  _vt_env_append_var "$name"; i="$j"
}

# _vt_env_parse_value RAW — 결과를 호출자의 _vt_val 에 담는다.
# 서브셸($(...))을 쓰지 않으므로 _vt_undef 같은 부수 정보를 호출자가 받을 수 있고,
# 줄마다 fork하지 않아 빠르다.
_vt_env_parse_value() {
  local raw="$1" out="" i=0 n c nx
  n=${#raw}
  while [ "$i" -lt "$n" ]; do
    c="${raw:i:1}"
    case "$c" in
      "'")
        i=$((i + 1))
        while [ "$i" -lt "$n" ] && [ "${raw:i:1}" != "'" ]; do
          out="$out${raw:i:1}"; i=$((i + 1))
        done
        i=$((i + 1))
        ;;
      '"')
        i=$((i + 1))
        while [ "$i" -lt "$n" ] && [ "${raw:i:1}" != '"' ]; do
          c="${raw:i:1}"
          if [ "$c" = '\' ] && [ $((i + 1)) -lt "$n" ]; then
            nx="${raw:i+1:1}"
            case "$nx" in
              '"'|'\'|'$'|'`') out="$out$nx"; i=$((i + 2)); continue ;;
            esac
            out="$out$c"; i=$((i + 1)); continue
          fi
          if [ "$c" = '$' ]; then _vt_env_expand_at; continue; fi
          out="$out$c"; i=$((i + 1))
        done
        i=$((i + 1))
        ;;
      '\')
        i=$((i + 1))
        if [ "$i" -lt "$n" ]; then out="$out${raw:i:1}"; i=$((i + 1)); fi
        ;;
      '$')
        _vt_env_expand_at
        ;;
      ' '|'	')
        break   # 따옴표 없는 값은 공백에서 끝난다
        ;;
      *)
        out="$out$c"; i=$((i + 1))
        ;;
    esac
  done
  _vt_val="$out"
}

# 한 줄에서 KEY와 RAW 값을 뽑는다. 유효하지 않으면 1.
# 결과는 호출자의 _vt_key / _vt_raw 에 담긴다.
_vt_env_split_line() {
  local line="$1"
  line="${line#"${line%%[![:space:]]*}"}"        # 앞 공백 제거
  case "$line" in ''|'#'*) return 1 ;; esac
  case "$line" in
    export\ *|export$'\t'*) line="${line#export}"; line="${line#"${line%%[![:space:]]*}"}" ;;
  esac
  case "$line" in *=*) ;; *) return 1 ;; esac
  _vt_key="${line%%=*}"
  _vt_key="${_vt_key%"${_vt_key##*[![:space:]]}"}"   # 뒤 공백 제거
  _vt_env_valid_key "$_vt_key" || return 1
  _vt_raw="${line#*=}"
  return 0
}

# ── 로드 ─────────────────────────────────────────────────────────────
# vt_env_load FILE — 파일의 키를 export한다. source가 아니므로 파일 안의 어떤 구문도
# 실행되지 않는다. _VT_ENV_PRESET_NAMES(호출 시점 환경변수)에 있는 키는 건너뛴다
# → 문서화된 우선순위 '환경변수 > ~/.vt.env > defaults'가 구조적으로 성립.
vt_env_load() {
  local file="${1:-$(vt_env_file)}" line _vt_key _vt_raw _vt_val _vt_undef
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    _vt_env_split_line "$line" || continue
    case " ${_VT_ENV_PRESET_NAMES-} " in
      *" $_vt_key "*) continue ;;
    esac
    _vt_undef=""
    _vt_env_parse_value "$_vt_raw"
    export "$_vt_key=$_vt_val"
  done < "$file"
}

# vt_env_get KEY — 파일에 기록된 값을 출력. 마지막 정의가 이긴다.
vt_env_get() {
  local key="${1-}" file line _vt_key _vt_raw _vt_val _vt_undef found=""
  _vt_env_valid_key "$key" || return 2
  file="$(vt_env_file)"
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    _vt_env_split_line "$line" || continue
    [ "$_vt_key" = "$key" ] || continue
    _vt_undef=""
    _vt_env_parse_value "$_vt_raw"
    found="$_vt_val"
  done < "$file"
  printf '%s' "$found"
}

# ── 쓰기 ─────────────────────────────────────────────────────────────

# 값을 홑따옴표 리터럴로 (확장 없음이 보장되는 유일한 형태).
vt_env_quote() {
  printf "'%s'" "$(printf '%s' "${1-}" | sed "s/'/'\\\\''/g")"
}

_vt_env_line_re() {
  printf '^[[:space:]]*(export[[:space:]]+)?(%s)=' "$1"
}

# 0600으로 빈 임시 파일 생성. stdout에 경로.
_vt_env_mktemp() {
  local file="$1" tmp="$1.tmp.$$" old_umask
  old_umask="$(umask)"
  umask 077
  if ! : > "$tmp" 2>/dev/null; then
    umask "$old_umask"
    echo "vt_env: 임시 파일 생성 실패: $tmp" >&2
    return 1
  fi
  umask "$old_umask"
  printf '%s' "$tmp"
}

# vt_env_set KEY VALUE — 기존 정의를 모두 지우고 새 값을 덧붙인다(멱등).
vt_env_set() {
  local key="${1-}" value="${2-}" file tmp rc=0
  if ! _vt_env_valid_key "$key"; then
    echo "vt_env_set: 잘못된 키 이름: '$key'" >&2
    return 2
  fi
  file="$(vt_env_file)"
  tmp="$(_vt_env_mktemp "$file")" || return 1

  if [ -f "$file" ]; then
    # grep -v: 0=출력 있음, 1=출력 없음(파일이 그 키뿐이었음), 2+=실제 오류.
    # 오류를 || true로 삼키면 tmp가 빈 채 mv되어 설정 전체가 날아간다.
    grep -vE "$(_vt_env_line_re "$key")" "$file" >> "$tmp" || rc=$?
    if [ "$rc" -gt 1 ]; then
      rm -f "$tmp"
      echo "vt_env_set: $file 읽기 실패 (원본 유지)" >&2
      return 1
    fi
  fi

  if ! printf '%s=%s\n' "$key" "$(vt_env_quote "$value")" >> "$tmp"; then
    rm -f "$tmp"; echo "vt_env_set: 쓰기 실패 (원본 유지)" >&2; return 1
  fi

  chmod 600 "$tmp" 2>/dev/null
  if ! mv -f "$tmp" "$file"; then
    rm -f "$tmp"; echo "vt_env_set: 교체 실패 (원본 유지)" >&2; return 1
  fi
}

# vt_env_unset KEY [KEY...]
vt_env_unset() {
  local file tmp rc=0 key pattern=""
  file="$(vt_env_file)"
  [ -f "$file" ] || return 0
  for key in "$@"; do
    if ! _vt_env_valid_key "$key"; then
      echo "vt_env_unset: 잘못된 키 이름: '$key'" >&2
      return 2
    fi
    pattern="${pattern:+$pattern|}$key"
  done
  [ -n "$pattern" ] || return 0

  tmp="$(_vt_env_mktemp "$file")" || return 1
  grep -vE "$(_vt_env_line_re "$pattern")" "$file" >> "$tmp" || rc=$?
  if [ "$rc" -gt 1 ]; then
    rm -f "$tmp"; echo "vt_env_unset: $file 읽기 실패 (원본 유지)" >&2; return 1
  fi
  chmod 600 "$tmp" 2>/dev/null
  if ! mv -f "$tmp" "$file"; then
    rm -f "$tmp"; echo "vt_env_unset: 교체 실패 (원본 유지)" >&2; return 1
  fi
}

# ── 검사 ─────────────────────────────────────────────────────────────
# vt_env_lint [FILE] — 형식에 없는 구문을 쓴 줄을 "행번호:내용"으로 출력. 있으면 1.
# 잡는 것 (전부 "사용자가 기대한 값이 안 나오는" 경우):
#   1) 파싱 불가한 줄
#   2) 실행 구문($( ) / 백틱 / $[ ]) — 형식에 없어 리터럴로 남는다
#   3) 정의되지 않은 변수 참조 — 그 부분이 조용히 사라진다
#      (옛 setter가 만들던 VT_H="scrypt$16384$8$1$abc" 의 $abc 가 여기 걸린다)
# 의도한 확장(install.sh의 VT_PYTHON=${VT_DIR}/... 처럼 앞줄에서 정의된 것)은 통과.
vt_env_lint() {
  local file="${1:-$(vt_env_file)}"
  [ -f "$file" ] || return 0
  # 서브셸 — 실제 셸 환경을 오염시키지 않고 "앞줄에서 정의된 키"를 반영해 검사한다
  (
    local n=0 bad=0 line _vt_key _vt_raw _vt_val _vt_undef
    while IFS= read -r line || [ -n "$line" ]; do
      n=$((n + 1))
      case "${line#"${line%%[![:space:]]*}"}" in ''|'#'*) continue ;; esac
      if ! _vt_env_split_line "$line"; then
        printf '%s: 형식을 알 수 없는 줄 — %s\n' "$n" "$line"; bad=1; continue
      fi
      case "$_vt_raw" in
        *'$('*|*'`'*|*'$['*)
          printf '%s: 실행 구문은 지원하지 않습니다(리터럴로 남음) — %s\n' "$n" "$line"
          bad=1 ;;
      esac
      _vt_undef=""
      _vt_env_parse_value "$_vt_raw"
      if [ -n "$_vt_undef" ]; then
        printf '%s: 정의되지 않은 변수 %s— 그 부분이 사라집니다. 홑따옴표로 감싸세요 — %s\n' \
          "$n" "$_vt_undef" "$line"
        bad=1
      fi
      export "$_vt_key=$_vt_val"
    done < "$file"
    exit $bad
  )
}
