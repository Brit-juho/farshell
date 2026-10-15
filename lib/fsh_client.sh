#!/bin/bash
# FarShell 서버 호출(셸) — unix socket 우선, 기계 토큰 fallback (plan-2.6 2단계).
# server/fsh_client.py 와 같은 규칙이다: 소켓 파일 이름은 포트를 따라간다(기본 7777 → fsh.sock,
# 그 외 fsh-<port>.sock). `source` 해서 쓴다. 이 파일은 설정을 읽지 않는다 — VT_PORT 등은 호출부가 먼저 로드한다.
#
#   fsh_api METHOD PATH [DATA]     응답 본문을 stdout 으로. 2xx 면 0, 아니면 1(연결 실패는 7).
#                                  HTTP 상태 코드는 FSH_API_CODE, 사용한 경로는 FSH_API_VIA(socket|tcp).
#   환경: FSH_SVC=<이름>           서버에 귀속용으로 알리는 서비스 이름(X-Fsh-Svc, 예: tmux-notify)
#         VT_AUTHZ_SOCKET=0        소켓을 쓰지 않는다
#         FSH_API_TIMEOUT=3        curl -m
#
# 토큰은 **argv 에 올리지 않는다**(`ps` 에 보인다). curl 의 설정 파일(`-K`)을 프로세스 치환(/dev/fd)으로 넘긴다 —
# 파일이 디스크에 생기지 않고 명령행에도 값이 없다. 값은 따옴표·역슬래시를 이스케이프한다.
#
# 소켓 요청에는 Authorization 을 싣지 않는다(서버가 400 ambiguous_principal 로 거부한다).

fsh_sock_path() {
  if [ -n "${VT_AUTHZ_SOCKET_PATH:-}" ]; then
    printf '%s' "$VT_AUTHZ_SOCKET_PATH"
    return
  fi
  local port="${VT_PORT:-${PORT:-7777}}" name
  if [ "$port" = "7777" ]; then name="fsh.sock"; else name="fsh-$port.sock"; fi
  printf '%s' "${VT_STATE_DIR:-$HOME/.vt}/run/$name"
}

# 소켓을 써 볼 만한가 — 켜져 있고, 소켓 파일이 있고, 내 것이며, 그룹/기타 권한이 없다.
fsh_sock_usable() {
  case "$(printf '%s' "${VT_AUTHZ_SOCKET:-1}" | tr 'A-Z' 'a-z')" in 0|off|false|no) return 1 ;; esac
  local p; p="$(fsh_sock_path)"
  [ -S "$p" ] && [ -O "$p" ] || return 1
  local mode
  mode="$(stat -f '%Lp' "$p" 2>/dev/null || stat -c '%a' "$p" 2>/dev/null)" || return 1
  case "$mode" in 600|400|700) return 0 ;; esac
  return 1
}

_fsh_curl_token_cfg() {
  local t="${VT_AUTH_TOKEN:-${VT_TOKEN:-}}"
  t="${t//\\/\\\\}"; t="${t//\"/\\\"}"
  printf 'header = "Authorization: Bearer %s"\n' "$t"
}

fsh_api() {
  local method="${1:-GET}" path="${2:-/}" data="${3-}"
  local tmo="${FSH_API_TIMEOUT:-3}" out rc code
  local -a common=(-s -m "$tmo" -o - -w $'\n%{http_code}' -X "$method")
  [ -n "${FSH_SVC:-}" ] && common+=(-H "X-Fsh-Svc: $FSH_SVC")
  if [ -n "${3+x}" ]; then common+=(-H 'Content-Type: application/json' --data-binary "$data"); fi
  FSH_API_CODE=0; FSH_API_VIA=""
  if fsh_sock_usable; then
    out="$(curl "${common[@]}" --unix-socket "$(fsh_sock_path)" "http://localhost$path" 2>/dev/null)"; rc=$?
    if [ $rc -eq 0 ]; then
      code="${out##*$'\n'}"; out="${out%$'\n'*}"
      # 소켓 계층의 거부(허용표 밖·미무장 등)만 토큰 경로로 내려간다. 라우트가 낸 4xx 는 그대로 돌려준다.
      case "$code:$out" in
        403:*socket_route_not_allowed*|503:*socket_not_armed*|403:*peer_unidentified*|403:*peer_gone*|503:*identity_unavailable*) ;;
        *) FSH_API_CODE="$code"; FSH_API_VIA=socket; printf '%s' "$out"
           case "$code" in 2??) return 0 ;; *) return 1 ;; esac ;;
      esac
    fi
  fi
  local base="http://127.0.0.1:${VT_PORT:-${PORT:-7777}}"
  if [ -n "${VT_AUTH_TOKEN:-${VT_TOKEN:-}}" ]; then
    out="$(curl "${common[@]}" -K <(_fsh_curl_token_cfg) "$base$path" 2>/dev/null)"; rc=$?
  else
    out="$(curl "${common[@]}" "$base$path" 2>/dev/null)"; rc=$?
  fi
  [ $rc -eq 0 ] || return $rc
  code="${out##*$'\n'}"; out="${out%$'\n'*}"
  FSH_API_CODE="$code"; FSH_API_VIA=tcp; printf '%s' "$out"
  case "$code" in 2??) return 0 ;; *) return 1 ;; esac
}
