# shellcheck shell=bash
# 런타임 폴더(VT_RUN_DIR) 판정과 2.7 업그레이드 이전 — bin/fsh 가 불러 쓴다. 함수만 있고 부르면 아무 일도
# 하지 않는다(server/tests/test_runtime_dir.py 가 이 파일만 불러 시험한다 — bin/fsh 를 통째로 돌리면
# `fsh stop` 의 데몬 정리가 이 트리에서 떠 있는 진짜 데몬까지 건드린다).
#
# 필요한 것: `_t`(lib/i18n.sh), 그리고 _vt_migrate_legacy_run 에는 VT_RUN_DIR · PID_DIR · _VT_LEGACY_RUN.

_vt_owner() { stat -c %u "$1" 2>/dev/null || stat -f %u "$1" 2>/dev/null; }

_vt_private_dir() {
  local d="$1"
  if [ -L "$d" ]; then
    echo "fsh: $(_t '{dir} 가 심볼릭 링크라 런타임 폴더로 쓰지 않습니다' dir="$d")" >&2; return 1
  fi
  if [ ! -d "$d" ]; then
    (umask 077 && mkdir -p "$d") || { echo "fsh: $(_t '{dir} 를 만들 수 없습니다' dir="$d")" >&2; return 1; }
  fi
  if [ "$(_vt_owner "$d")" != "$(id -u)" ]; then
    echo "fsh: $(_t '{dir} 의 소유자가 내가 아니라 쓰지 않습니다 — 다른 계정이 먼저 만든 폴더일 수 있습니다. VT_RUN_DIR 를 내 폴더로 지정하세요' dir="$d")" >&2
    return 1
  fi
  chmod 700 "$d" 2>/dev/null || { echo "fsh: $(_t '{dir} 의 권한을 0700 으로 바꿀 수 없습니다' dir="$d")" >&2; return 1; }
}

_vt_run_default() {
  local s="${VT_STATE_DIR:-$HOME/.vt}"
  case "$s" in "~") s="$HOME" ;; "~/"*) s="$HOME/${s#\~/}" ;; esac
  printf '%s/run' "$s"
}

# 2.7 업그레이드 — 예전 기본 위치(/tmp)에 PID·터널 기록이 남아 있으면 한 번 옮긴다. 안 옮기면 업그레이드 직후
# `fsh stop`/`restart` 가 돌고 있는 서버·터널을 못 찾아 포트가 겹친다. 서버를 다시 띄우는 명령에서만 부른다
# (cmd_start·cmd_stop 맨 앞) — 옛 서버는 /tmp 를 보고 있어서, 옮긴 뒤 재시작 전까지는 터널 주소를 못 읽는다. 그래서
# 서버를 내리고 다시 띄우는 그 명령 안에서만 옮기고, `fsh update trust`·`fsh tunnel`·`fsh mobile` 처럼 돌고 있는
# 옛 서버 곁에서 도는 명령은 옮기지 않는다(2026-11-03 실제 서버에서 확인한 사고).
# **내 소유이고 심볼릭 링크가 아닌 것만** 옮긴다: 남이 만들어 둔 /tmp 파일은 믿지 않는다(bin/fsh 의 VT_RUN_DIR 주석).
# 기본 설치(VT_RUN_DIR·VT_STATE_DIR 둘 다 미지정)만 옮긴다 — VT_STATE_DIR 만 바꾼 격리 인스턴스가 옮기면
# 같은 맥에서 돌고 있는 기본 인스턴스의 PID·터널 기록을 가져가 버린다.
_vt_legacy_pid_dir_ok() {
  [ -z "${_VT_RUN_DIR_EXPLICIT:-}" ] && [ -z "${VT_STATE_DIR:-}" ] \
    && [ -d "$_VT_LEGACY_RUN/vt-pids" ] && [ ! -L "$_VT_LEGACY_RUN/vt-pids" ] \
    && [ "$(_vt_owner "$_VT_LEGACY_RUN/vt-pids")" = "$(id -u)" ]
}

_vt_migrate_legacy_run() {
  _vt_legacy_pid_dir_ok || return 0
  [ -z "$(ls -A "$PID_DIR" 2>/dev/null)" ] || return 0   # 이미 새 위치를 쓰고 있다
  local f moved=0
  for f in "$_VT_LEGACY_RUN"/vt-pids/* "$_VT_LEGACY_RUN"/cloudflared.log "$_VT_LEGACY_RUN"/cloudflared-*.log \
           "$_VT_LEGACY_RUN"/ngrok.log "$_VT_LEGACY_RUN"/ngrok-*.log "$_VT_LEGACY_RUN"/vt-server.log \
           "$_VT_LEGACY_RUN"/vt-voice-daemon.log "$_VT_LEGACY_RUN"/vt-clipboard-daemon.log \
           "$_VT_LEGACY_RUN"/vt-tunnel-watchdog.log "$_VT_LEGACY_RUN"/vt-tunnel-hook-retry.log; do
    [ -f "$f" ] && [ ! -L "$f" ] && [ "$(_vt_owner "$f")" = "$(id -u)" ] || continue
    case "$f" in */vt-pids/*) mv -f "$f" "$PID_DIR/" ;; *) mv -f "$f" "$VT_RUN_DIR/" ;; esac && moved=1
  done
  rmdir "$_VT_LEGACY_RUN/vt-pids" 2>/dev/null
  # 옛 워치독은 환경에 VT_RUN_DIR=/tmp 를 들고 있어 옮겨진 터널 로그를 못 본다 — 좀비 재연결 감시가 조용히 멎는다.
  # 끝내 두면 이어지는 start 가 새 환경으로 다시 띄운다(_start_tunnel → _start_tunnel_watchdog). pid 파일은 낡았을 수
  # 있으니(번호 재사용) 명령줄이 워치독일 때만 끝낸다.
  local wpid; wpid="$(cat "$PID_DIR/tunnel-watchdog.pid" 2>/dev/null)"
  if [ -n "$wpid" ] && ps -o command= -p "$wpid" 2>/dev/null | grep -q "tunnel_watchdog.py"; then
    kill "$wpid" 2>/dev/null
    rm -f "$PID_DIR/tunnel-watchdog.pid"
  fi
  [ "$moved" = 1 ] && echo "  ⓘ $(_t '런타임 파일을 {old} 에서 {new} 로 옮겼습니다' old="$_VT_LEGACY_RUN" new="$VT_RUN_DIR")" >&2
  return 0
}
