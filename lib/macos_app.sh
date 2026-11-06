# shellcheck shell=bash
# FarShell.app — macOS 권한(TCC) 책임자를 FarShell 로 고정하는 실행 껍데기(macos/farshell-launcher.c).
#
# 서버와 tmux 를 이 껍데기를 거쳐 띄우면, 서버·터미널 안 프로그램이 보호 위치에 닿을 때 권한 창이 "python3.14" 가
# 아니라 "FarShell" 로 뜨고, 한 번 허용하면 릴리스·재시작·Python 업그레이드 뒤에도 유지된다(같은 앱으로 보이는 한).
# 그래서 앱은 저장소 밖(`$VT_STATE_DIR/app`)에 두고, 껍데기 소스나 Info.plist 가 바뀔 때만 다시 빌드한다 —
# 다시 빌드하면 서명(ad-hoc cdhash)이 바뀌어 macOS 가 새 앱으로 보고 다시 묻기 때문이다.
# 끄기: VT_MACOS_APP=0. macOS 가 아니거나 cc·codesign 이 없으면 쓰지 않는다(지금처럼 Python 이 직접 뜬다).

_vt_app_dir()  { printf '%s/app\n' "${VT_STATE_DIR:-$HOME/.vt}"; }
_vt_app_path() { printf '%s/FarShell.app\n' "$(_vt_app_dir)"; }
_vt_app_exe()  { printf '%s/Contents/MacOS/FarShell\n' "$(_vt_app_path)"; }

_vt_app_wanted() {
  [ "$(uname -s 2>/dev/null)" = "Darwin" ] || return 1
  case "${VT_MACOS_APP:-1}" in 0|false|no|off) return 1 ;; esac
  [ -f "$VT_DIR/macos/farshell-launcher.c" ] && [ -f "$VT_DIR/macos/Info.plist" ]
}

_vt_app_source_hash() {
  cat "$VT_DIR/macos/farshell-launcher.c" "$VT_DIR/macos/Info.plist" | shasum -a 256 | cut -d' ' -f1
}

# 쓸 수 있으면 0. 없거나 낡았으면 빌드한다(실패하면 1 — 호출부는 껍데기 없이 띄운다).
_vt_app_ensure() {
  _vt_app_wanted || return 1
  command -v cc >/dev/null 2>&1 || return 1
  command -v codesign >/dev/null 2>&1 || return 1
  local app want stamp tmp
  app="$(_vt_app_path)"
  stamp="$app.stamp"
  want="$(_vt_app_source_hash)" || return 1
  if [ -x "$(_vt_app_exe)" ] && [ "$(cat "$stamp" 2>/dev/null)" = "$want" ]; then
    return 0
  fi
  (umask 077; mkdir -p "$(_vt_app_dir)") || return 1
  tmp="$(mktemp -d "$(_vt_app_dir)/.build.XXXXXX")" || return 1
  mkdir -p "$tmp/FarShell.app/Contents/MacOS" \
    && cp "$VT_DIR/macos/Info.plist" "$tmp/FarShell.app/Contents/Info.plist" \
    && cc -O2 -Wall -o "$tmp/FarShell.app/Contents/MacOS/FarShell" "$VT_DIR/macos/farshell-launcher.c" 2>"$tmp/build.log" \
    && codesign --force -s - --identifier com.farshell.server "$tmp/FarShell.app" >/dev/null 2>&1
  local rc=$?
  if [ $rc -ne 0 ]; then
    rm -rf "$tmp"
    return 1
  fi
  rm -rf "$app" && mv "$tmp/FarShell.app" "$app" && printf '%s\n' "$want" > "$stamp"
  rc=$?
  rm -rf "$tmp"
  return $rc
}

# 명령 앞에 붙일 껍데기(쓸 수 없으면 아무것도 출력하지 않는다).
_vt_app_prefix() {
  if _vt_app_ensure; then _vt_app_exe; fi
}

# 빌드하지 않고 상태만 본다(fsh doctor 용): ready | stale | missing | off | unavailable
_vt_app_state() {
  if [ "$(uname -s 2>/dev/null)" != "Darwin" ]; then printf 'unavailable\n'; return; fi
  case "${VT_MACOS_APP:-1}" in 0|false|no|off) printf 'off\n'; return ;; esac
  if ! command -v cc >/dev/null 2>&1 || ! command -v codesign >/dev/null 2>&1; then printf 'unavailable\n'; return; fi
  if [ ! -x "$(_vt_app_exe)" ]; then printf 'missing\n'; return; fi
  if [ "$(cat "$(_vt_app_path).stamp" 2>/dev/null)" = "$(_vt_app_source_hash)" ]; then printf 'ready\n'; else printf 'stale\n'; fi
}
