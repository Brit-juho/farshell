# shellcheck shell=bash
# 인증 없는 기동 막기 — bin/fsh 의 _start_server·cmd_start 가 부른다(함수만 있다).
#
# 비밀번호도 기계 토큰도 없이 서버를 띄우면 그 서버에 닿는 누구나(LAN·tailnet·공개 터널, 또는 DNS
# rebinding 으로 이 맥의 브라우저) 터미널을 연다. 예전에는 공개 터널(--network all)만 거부했고 `fsh voice`·
# `fsh clip`·`--network lan/tailscale/localhost` 는 인증 없이 떴다. 이제 어느 경로든 Jupyter 처럼 무작위
# 비밀번호를 만들어 scrypt 해시만 ~/.vt.env 에 저장하고 원문은 이 터미널에 **한 번만** 보여 준다.
# 기계 토큰을 만들지 않는 이유: 로그인 폼은 토큰을 받지 않는다(2026-09-17, docs/ref/auth.md).
#
# 터미널이 아니면(업데이터·launchd·훅이 부른 경우) 원문이 로그 파일로 갈 수 있어 만들지 않고 실패한다.
#
# 필요한 것: VT_DIR · VT_PYTHON · lib/vt_env.sh(vt_env_set/get) · `_t`.

_vt_auth_present() {
  (cd "$VT_DIR/server" && "$VT_PYTHON" -m auth status >/dev/null 2>&1)
}

_vt_ensure_auth() {
  _vt_auth_present && return 0
  if [ ! -t 1 ]; then
    echo "  ✗ $(_t '인증(비밀번호)이 없어 서버를 띄우지 않습니다. 터미널에서 fsh password 로 설정하세요.')" >&2
    return 1
  fi
  local out pw hash
  out="$(cd "$VT_DIR/server" && "$VT_PYTHON" -m auth generate-password 2>/dev/null)" || out=""
  pw="$(printf '%s\n' "$out" | sed -n 1p)"
  hash="$(printf '%s\n' "$out" | sed -n 2p)"
  unset out
  if [ -z "$pw" ] || [ -z "$hash" ]; then
    echo "  ✗ $(_t '비밀번호를 만들지 못했습니다. fsh password 로 직접 설정하세요.')" >&2
    return 1
  fi
  vt_env_set "VT_AUTH_PASSWORD_HASH" "$hash" || { unset pw; return 1; }
  export VT_AUTH_PASSWORD_HASH="$hash"
  if [ -z "$(vt_env_get VT_AUTH_SESSION_KEY)" ] && [ -z "$(vt_env_get VT_SECRET_KEY)" ]; then
    local key
    key="$("$VT_PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
    vt_env_set "VT_AUTH_SESSION_KEY" "$key" && export VT_AUTH_SESSION_KEY="$key"
    unset key
  fi
  echo ""
  echo "  🔑 $(_t '비밀번호가 없어서 무작위 웹 로그인 비밀번호를 만들었습니다 (지금 한 번만 보여 줍니다):')"
  echo ""
  echo "       $pw"
  echo ""
  echo "     $(_t '저장된 것은 해시뿐이라 다시 볼 수 없습니다. 바꾸려면: fsh password')"
  echo ""
  unset pw hash
  return 0
}
