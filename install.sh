#!/bin/bash
# FarShell 원라인 설치 스크립트
# 사용법:
#   ./install.sh             # 터미널만 (경량, ~50MB)
#   ./install.sh voice       # 터미널 + 음성 모드 (수백MB; 음성 인식 모델은 설정에서 따로 받음)
#   ./install.sh --lang en   # 화면 언어 지정 (ko|en) — ~/.vt.env 의 VT_LANG 로 저장됨
#   curl -fsSL <URL>/install.sh | bash       # 원격 설치 (터미널만)
#   curl -fsSL <URL>/install.sh | bash -s voice
#   curl -fsSL <URL>/install.sh | bash -s -- --lang en
#
# 환경변수:
#   VT_DIR   — 설치 경로 (기본: 스크립트 위치 또는 ~/farshell)
#   VT_PORT  — 포트 (기본: 7777)

set -euo pipefail

# i18n — 레포의 lib/ 가 있으면 그걸 쓰고, 없으면(파이프 설치 직후 클론 전) 아래 대체 구현을 쓴다.
# 대체 구현은 번역 사전이 없으므로 원문(한국어)을 그대로 내보내고, vt_is_en 은 OS 로캘만 본다.
_t() {
  local out="${1-}" kv
  shift 2>/dev/null || true
  for kv in "$@"; do out="${out//\{${kv%%=*}\}/${kv#*=}}"; done
  printf '%s' "$out"
}
vt_is_en() {
  local k v
  for k in LC_ALL LC_MESSAGES LANG; do
    eval "v=\${$k-}"
    case "$v" in ''|C|POSIX) continue ;; esac
    case "$v" in ko*|KO*) return 1 ;; *) return 0 ;; esac
  done
  return 0
}

# lib/vt_env.sh + lib/i18n.sh 를 불러온다(있을 때만). 불러오면 위 대체 구현을 덮어쓴다.
_inst_load_libs() {
  local d="$1"
  if [ -f "$d/lib/vt_env.sh" ]; then
    # shellcheck disable=SC1091
    . "$d/lib/vt_env.sh"
  fi
  if [ -f "$d/lib/i18n.sh" ]; then
    # shellcheck disable=SC1091
    . "$d/lib/i18n.sh"
  fi
}

_inst_usage() {
  if vt_is_en; then cat <<'EOF_EN'
FarShell installer

Usage:
  ./install.sh [terminal|voice] [--lang ko|en]

  terminal        terminal only (default, ~50MB)
  voice           terminal + voice mode (hundreds of MB; speech models are downloaded later in Settings)
  --lang ko|en    display language; saved as VT_LANG in ~/.vt.env
                  (default: keep the saved value, else ask, else follow the OS locale)
  -h, --help      show this help

Environment:
  VT_DIR          install path (default: this script's folder, or ~/farshell)
  VT_PORT         port (default: 7777)
EOF_EN
  else cat <<'EOF'
FarShell 설치 스크립트

사용법:
  ./install.sh [terminal|voice] [--lang ko|en]

  terminal        터미널만 (기본, ~50MB)
  voice           터미널 + 음성 모드 (수백MB; 음성 인식 모델은 설정에서 따로 받음)
  --lang ko|en    화면 언어. ~/.vt.env 의 VT_LANG 로 저장됨
                  (기본: 저장된 값 유지, 없으면 질문, 비대화형이면 OS 로캘 기준)
  -h, --help      이 도움말

환경변수:
  VT_DIR          설치 경로 (기본: 스크립트 위치 또는 ~/farshell)
  VT_PORT         포트 (기본: 7777)
EOF
  fi
}

# 로컬 레포가 이미 있으면(=SCRIPT_DIR 에 lib/ 가 있으면) 인자 처리·가드 메시지부터 번역을 쓴다.
_SCRIPT_DIR_EARLY="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
if [ -n "$_SCRIPT_DIR_EARLY" ] && [ -f "$_SCRIPT_DIR_EARLY/bin/fsh" ]; then
  _inst_load_libs "$_SCRIPT_DIR_EARLY"
fi

# 인자 처리: [terminal|voice] [--lang ko|en] [-h|--help]
PROFILE=""
LANG_OPT=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) _inst_usage; exit 0 ;;
    --lang)
      if [ $# -lt 2 ]; then
        echo "✗ --lang requires a value: ko or en" >&2
        exit 2
      fi
      LANG_OPT="$2"; shift 2 ;;
    --lang=*) LANG_OPT="${1#--lang=}"; shift ;;
    -*)
      echo "✗ Unknown option: $1 (see ./install.sh --help)" >&2
      exit 2 ;;
    *)
      [ -n "$PROFILE" ] || PROFILE="$1"
      shift ;;
  esac
done
PROFILE="${PROFILE:-terminal}"  # terminal | voice
if [ -n "$LANG_OPT" ]; then
  LANG_OPT="$(printf '%s' "$LANG_OPT" | tr 'A-Z' 'a-z')"
  case "$LANG_OPT" in
    ko|en) ;;
    *)
      echo "✗ Invalid --lang value: '$LANG_OPT' (use ko or en)" >&2
      exit 2 ;;
  esac
fi

# Windows 네이티브 가드 — Git Bash/MSYS/Cygwin에서 실행 시 거부
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    if vt_is_en; then
      echo ""
      echo "✗ Native Windows is not supported."
      echo ""
      echo "  FarShell uses tmux, so it needs Linux or macOS."
      echo "  On Windows, install it through WSL2:"
      echo ""
      echo "  1. In PowerShell (admin): wsl --install"
      echo "  2. Inside WSL2: git clone <repo> && cd farshell && ./install.sh"
      echo ""
      echo "  Details: the 'Windows (WSL2)' section of README.md"
    else
      echo ""
      echo "✗ Windows 네이티브 환경은 지원하지 않습니다."
      echo ""
      echo "  FarShell은 tmux를 사용하므로 Linux/macOS 환경이 필요합니다."
      echo "  Windows 사용자는 WSL2를 통해 설치하세요:"
      echo ""
      echo "  1. PowerShell(관리자)에서: wsl --install"
      echo "  2. WSL2 진입 후: git clone <repo> && cd farshell && ./install.sh"
      echo ""
      echo "  자세히: README.md 'Windows (WSL2)' 섹션"
    fi
    exit 1
    ;;
esac

PIPE_INSTALL=0
PIPE_REUSED=0

# 로컬 레포가 있으면 그걸 우선 사용. 진짜 파이프 설치(curl | bash)에서만 클론.
# 이전 버전은 `[ -t 0 ]`로 stdin TTY 여부를 체크했지만, 자동화/CI 환경도 false로
# 잡혀 로컬 레포를 무시하는 버그가 있었다 (TEST_REPORT.md Bug #2).
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/bin/fsh" ]; then
  VT_DIR="$SCRIPT_DIR"
else
  PIPE_INSTALL=1
  VT_DIR="${VT_DIR:-$HOME/farshell}"
  if [ ! -d "$VT_DIR" ]; then
    # 클론 전에는 사전이 없다 — 로캘만 보고 고른다
    if vt_is_en; then
      echo "▸ Cloning repo → $VT_DIR"
    else
      echo "▸ 레포 클론 중 → $VT_DIR"
    fi
    git clone --depth 1 https://github.com/Brit-juho/farshell.git "$VT_DIR"
  else
    PIPE_REUSED=1   # 언어가 정해진 뒤에 알린다
  fi
  cd "$VT_DIR"
  _inst_load_libs "$VT_DIR"
fi

# 화면 언어(VT_LANG) — --lang > ~/.vt.env 에 이미 있는 값 > 질문(터미널) > OS 로캘.
# ~/.vt.env 쓰기는 반드시 vt_env_set 으로 한다(AGENTS.md 계약 5번 — echo/sed 금지).
# 이 설치기는 $HOME/.vt.env 만 쓰므로 VT_CONFIG 환경변수는 호출 동안만 그 경로로 맞춘다.
_inst_os_lang() {
  local k v
  for k in LC_ALL LC_MESSAGES LANG; do
    eval "v=\${$k-}"
    case "$v" in ''|C|POSIX) continue ;; esac
    v="$(printf '%s' "$v" | tr 'A-Z' 'a-z')"
    case "$v" in ko*) echo ko ;; *) echo en ;; esac
    return 0
  done
  echo en
}

LANG_PENDING=""   # ~/.vt.env 가 아직 없을 때 5단계 뒤에 기록할 값
_inst_pick_lang() {
  local envf="$HOME/.vt.env" cur="" chosen="" def ans
  # 오래된 트리라 lib 가 없으면 언어 설정은 건너뛴다
  if ! type vt_env_set >/dev/null 2>&1 || ! type vt_lang_resolve >/dev/null 2>&1; then
    return 0
  fi
  if [ -f "$envf" ]; then
    cur="$(VT_CONFIG="$envf" vt_env_get VT_LANG 2>/dev/null || true)"
    case "$cur" in ko|en) ;; *) cur="" ;; esac
  fi
  if [ -z "$LANG_OPT" ] && [ -n "$cur" ]; then
    VT_CONFIG="$envf" vt_lang_resolve
    return 0
  fi
  chosen="$LANG_OPT"
  if [ -z "$chosen" ]; then
    def="$(_inst_os_lang)"
    if [ -t 0 ]; then
      # 두 언어를 함께 적는 고정 문구 — _t 로 감싸지 않는다
      printf 'Language / 언어 [ko/en] (기본 %s): ' "$def"
      IFS= read -r ans || ans=""
      ans="$(printf '%s' "$ans" | tr 'A-Z' 'a-z')"
      case "$ans" in ko|en) chosen="$ans" ;; *) chosen="$def" ;; esac
    else
      chosen="$def"
    fi
  fi
  if [ -f "$envf" ]; then
    if VT_CONFIG="$envf" vt_env_set VT_LANG "$chosen"; then
      VT_CONFIG="$envf" vt_lang_resolve
    else
      _VT_LANG="$chosen"
      echo "  ⚠ $(_t '언어 설정을 ~/.vt.env 에 저장하지 못했습니다 — 나중에 설정 화면에서 바꿀 수 있습니다')"
    fi
  else
    LANG_PENDING="$chosen"
    _VT_LANG="$chosen"
  fi
}
_inst_pick_lang

if [ "$PIPE_REUSED" = "1" ]; then
  echo "✓ $(_t '기존 레포 사용: {dir}' dir="$VT_DIR")"
fi

echo ""
echo "  🎤 $(_t 'FarShell 설치 — 프로필: {profile}' profile="$PROFILE")"
echo "  $(_t '설치 경로: {dir}' dir="$VT_DIR")"
echo ""

# 1. Python 확인
if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ $(_t "python3 미설치. macOS: 'brew install python@3.11', Linux: 'apt install python3'")"
  exit 1
fi
PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

# 최소 버전 게이트 (2026-09-08, 클린 환경 검증에서 발견).
# 서버 코드는 PEP 604(`str | None`, 3.10+)를 14개 파일에서 쓰고, CI 매트릭스의
# 하한은 3.11이다. 그런데 여기서는 버전을 **출력만 하고 막지 않아서**, macOS
# 기본 python3(3.9)로 설치하면 venv 생성·pip 설치까지 전부 성공한 뒤 서버가
# import 단계에서 TypeError로 죽었다 — 사용자 입장에서는 "설치는 됐다는데 안 켜진다"다.
# 조용히 깨진 설치를 만드느니 여기서 멈추는 게 낫다.
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  echo "✗ $(_t 'Python {version} — FarShell은 **3.11 이상**이 필요합니다.' version="$PY_VERSION")"
  echo "    $(_t '(서버 코드가 3.10+ 문법을 쓰고, CI가 검증하는 하한은 3.11입니다)')"
  if [ "$(uname)" = "Darwin" ]; then
    echo "    $(_t 'macOS 기본 python3는 3.9라 그대로는 동작하지 않습니다:')"
    echo "      brew install python@3.11"
    echo "      $(_t '그 뒤 PATH가 새 python3를 가리키는지 확인: python3 -V')"
  else
    echo "    $(_t '설치: {cmd} 등' cmd='apt install python3.11 / dnf install python3.11')"
  fi
  echo "    $(_t '이미 3.11+ 를 갖고 있다면 그 python으로 직접 venv를 만들고')"
  echo "    $(_t '~/.vt.env 의 VT_PYTHON 을 그 경로로 지정해도 됩니다.')"
  exit 1
fi
echo "✓ Python $PY_VERSION"

# 2. venv 생성 (이미 있으면 재사용)
VENV="$VT_DIR/.venv"
if [ ! -d "$VENV" ]; then
  echo "▸ $(_t '가상환경 생성') → $VENV"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --quiet --upgrade pip

# 3. 프로필별 패키지 설치 — 해시로 잠근 파일(locks/)로만 설치한다.
# 버전·해시가 저장소에 고정돼 있어, PyPI 에서 파일이 바뀌거나 중간에서 변조되면 설치가 멈춘다(`pip --require-hashes`).
# 잠금 파일은 requirements-*.txt 에서 scripts/lock_python.sh 로 만든다(설치 때는 순수 pip 만 쓴다).
_inst_pip_locked() {
  local f log="" rc=0 args=()
  for f in "$@"; do
    if [ ! -f "$VT_DIR/locks/$f.txt" ]; then
      echo "✗ $(_t '잠금 파일이 없습니다: {file}' file="$VT_DIR/locks/$f.txt")"
      echo "    $(_t '저장소가 불완전합니다. 다시 내려받거나 git pull 로 최신 상태로 맞춘 뒤 재실행하세요.')"
      exit 1
    fi
    args+=(-r "$VT_DIR/locks/$f.txt")
  done
  log="$(mktemp "${TMPDIR:-/tmp}/fsh-pip.XXXXXX")"
  pip install --quiet --require-hashes "${args[@]}" >"$log" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "✗ $(_t '파이썬 패키지 설치에 실패했습니다.')"
    if grep -qi 'do not match the hashes' "$log"; then
      echo "    $(_t '내려받은 파일의 해시가 잠금 파일(locks/)과 다릅니다. 네트워크 중간 장치(프록시·미러)의 변조, 또는 PyPI 의 파일 교체일 수 있어 설치를 멈췄습니다.')"
      echo "    $(_t '다른 네트워크에서 다시 시도하고, 같은 오류가 반복되면 이슈로 알려 주세요. 해시 검사를 끄고 설치하지는 마세요.')"
    else
      echo "    $(_t '아래는 pip 출력의 마지막 부분입니다(전체: {log}).' log="$log")"
      tail -n 12 "$log" | sed 's/^/      /'
      echo "    $(_t '네트워크 연결, Python 버전(3.11 이상), 컴파일러(Linux 음성 모드의 evdev)를 확인하세요.')"
    fi
    exit 1
  fi
  rm -f "$log"
}

echo "▸ $(_t '패키지 설치 중...')"
if [ "$PROFILE" = "voice" ]; then
  _inst_pip_locked core voice
  echo "✓ $(_t '터미널 + 음성 모드 의존성 설치 완료')"
else
  _inst_pip_locked core
  echo "✓ $(_t "터미널 의존성 설치 완료 (음성 모드는 './install.sh voice'로 추가)")"
fi

# 3-1. 프런트엔드 자산 준비 — **두 경로**(I4, 2026-09-08)
#
#   (a) 릴리스 tarball로 설치  → frontend/dist/가 이미 들어 있다. **node 불필요.**
#   (b) 소스(git clone)로 설치 → 직접 빌드해야 한다. node 필요.
#
# F1이 Vite/Tailwind를 들이면서 "2.0부터 node가 필요해졌다"가 생겼는데, 그걸
# 영구화하지 않으려고 릴리스 경로를 만든 것이다(80-ci-release.md §4). 산출물을
# git에 커밋하는 절충(ADR-2 위반) 없이 **개발자에게만 node를 요구하고 사용자에게는
# 요구하지 않는 상태**에 이 분기로 도달한다.
#
# 판정 기준은 "dist가 이미 있고 소스보다 최신인가"가 아니라 **"이 트리가 tarball인가"**다
# — .git이 없고 dist가 있으면 tarball이다. mtime 비교는 tar 전개 순서에 따라
# 뒤집힐 수 있어 신뢰할 수 없다.
if [ ! -d "$VT_DIR/.git" ] && [ -f "$VT_DIR/frontend/dist/app.js" ]; then
  echo "✓ $(_t '릴리스 패키지 — 빌드된 프런트엔드가 포함돼 있습니다 (node 불필요)')"
elif [ -f "$VT_DIR/frontend/dist/app.js" ] && [ ! -f "$VT_DIR/package.json" ]; then
  # package.json이 없는데 dist만 있는 이상한 트리 — 그래도 돌 수는 있다.
  echo "✓ $(_t '빌드된 프런트엔드 발견 (빌드 생략)')"
else
  if ! command -v npm >/dev/null 2>&1; then
    echo ""
    echo "✗ $(_t 'Node.js(npm)가 필요합니다 — 소스에서 설치할 때 프런트엔드를 빌드합니다.')"
    echo "    $(_t '(릴리스 tarball로 설치하면 node 없이 됩니다:')"
    echo "     https://github.com/Brit-juho/farshell/releases 에서 farshell-*.tar.gz)"
    if [ "$(uname)" = "Darwin" ]; then
      echo "    $(_t '설치: {cmd}' cmd='brew install node')"
    else
      echo "    $(_t '설치: {url} (또는 배포판 패키지 매니저: {cmds})' url='https://nodejs.org' cmds='apt/dnf/pacman install nodejs npm')"
    fi
    echo "    $(_t "설치 후 './install.sh {profile}'을 다시 실행하세요." profile="$PROFILE")"
    exit 1
  fi
  echo "▸ $(_t '프런트엔드 빌드 중... (Vite + Tailwind, node {version})' version="$(node --version)")"
  ( cd "$VT_DIR" && npm ci --silent && npm run build --silent )
  echo "✓ $(_t '프런트엔드 빌드 완료 → frontend/dist/')"
fi

# 4. fsh CLI 등록. 구 명령어 `vt`는 2.1에서 제거됐다 — 예전 설치본이 남긴
#    끊어진 심링크를 여기서 정리한다(남겨두면 `vt`가 "No such file"로 죽는다).
mkdir -p "$HOME/.local/bin"
chmod +x "$VT_DIR/bin/fsh"
ln -sf "$VT_DIR/bin/fsh" "$HOME/.local/bin/fsh"
# `set -e` 아래이므로 `[ -L … ] && rm` 형태는 안 된다 — 심링크가 없는 신규
# 설치에서 test가 1을 반환해 설치 전체가 조용히 중단된다.
if [ -L "$HOME/.local/bin/vt" ]; then rm -f "$HOME/.local/bin/vt"; fi
echo "✓ $(_t 'fsh CLI 등록 → ~/.local/bin/fsh')"

# 4-1. tmux 격리 config — 복사하지 않는다. fsh 는 레포의 config/vt-tmux.conf 를 그대로 쓰고,
#      개인 설정은 그 파일이 끝에서 읽는 ~/.config/vt/tmux.local.conf 에 둔다.
#      예전 설치가 복사해 둔 ~/.config/vt/tmux.conf 는 레포 변경을 영영 가렸으므로 치우고
#      (지우지 않고 이름만 바꾼다), tmux 서버가 떠 있으면 새 설정을 다시 읽힌다.
#      실패해도 설치는 계속한다 — 'fsh tmux reload' 로 다시 할 수 있다.
mkdir -p "$HOME/.config/vt"
"$VENV/bin/python" "$VT_DIR/server/tmux_conf.py" reload \
  || echo "  ⚠ $(_t "tmux 설정 정리 실패 — 'fsh tmux reload'로 다시 시도할 수 있습니다")"

# 5. 설정 파일 생성 (없을 때만)
if [ ! -f "$HOME/.vt.env" ]; then
  # 이 파일에는 VT_AUTH_TOKEN / VT_AUTH_PASSWORD_HASH / VT_AUTH_SESSION_KEY 같은
  # 시크릿이 들어간다. umask 기본(644)으로 만들면 같은 머신의 다른 사용자가 읽는다.
  # 세션 서명키가 유출되면 쿠키를 위조해 인증을 우회할 수 있으므로 처음부터 0600.
  ( umask 077; : > "$HOME/.vt.env" )
  cat > "$HOME/.vt.env" <<EOF
# FarShell 설정 (수정 가능)
VT_DIR=$VT_DIR
VT_PORT=${VT_PORT:-7777}
VT_PYTHON=\${VT_DIR}/.venv/bin/python
# 'fsh start'(공개 터널)은 인증이 없으면 실행을 거부합니다 — 아래 둘 중 하나로
# 인증을 먼저 설정하세요(권장: fsh password). localhost/lan/tailscale 모드는 필요 없습니다.
#   fsh password              대화형으로 비밀번호 설정 (scrypt 해시만 여기 저장됨)
# VT_AUTH_TOKEN=your-secret  기계용 토큰 (데몬/QR 등, 원한다면 직접 값 채우기)
# VT_NOTIFY_URL=https://ntfy.sh/your-topic  # 푸시 알림 (선택)
EOF
  chmod 600 "$HOME/.vt.env"
  echo "✓ $(_t '설정 파일 생성 → ~/.vt.env (권한 600)')"
else
  # 예전 install.sh가 644로 만들어 둔 파일을 여기서 바로잡는다
  chmod 600 "$HOME/.vt.env" 2>/dev/null || true
  echo "✓ $(_t '기존 설정 유지 → ~/.vt.env')"
fi

# 5-1. 화면 언어 — ~/.vt.env 가 방금 새로 만들어졌다면 여기서 기록한다.
if [ -n "$LANG_PENDING" ]; then
  if VT_CONFIG="$HOME/.vt.env" vt_env_set VT_LANG "$LANG_PENDING"; then
    VT_CONFIG="$HOME/.vt.env" vt_lang_resolve
  else
    echo "  ⚠ $(_t '언어 설정을 ~/.vt.env 에 저장하지 못했습니다 — 나중에 설정 화면에서 바꿀 수 있습니다')"
  fi
fi

# 6. PATH 확인
SHELL_RC=""
case "$(basename "${SHELL:-}")" in
  zsh)  SHELL_RC="$HOME/.zshrc"  ;;
  bash) SHELL_RC="$HOME/.bashrc" ;;
esac
if [ -n "$SHELL_RC" ] && ! echo ":$PATH:" | grep -q ":$HOME/.local/bin:"; then
  if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$SHELL_RC" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
    echo "✓ $(_t 'PATH 등록 → {rc} (새 터미널에서 적용)' rc="$SHELL_RC")"
  fi
fi

# 7. cloudflared 안내 (선택)
if ! command -v cloudflared >/dev/null 2>&1; then
  echo ""
  echo "  ⓘ $(_t '원격 접속(fsh start)용 cloudflared 미설치.')"
  if [ "$(uname)" = "Darwin" ]; then
    echo "    $(_t '설치: {cmd}' cmd='brew install cloudflared')"
  else
    echo "    $(_t '설치: {cmd}' cmd='curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o ~/.local/bin/cloudflared && chmod +x ~/.local/bin/cloudflared')"
  fi
fi

# 8. (W3-5) Linux voice 프로필 — espeak-ng + libnotify 안내
if [ "$PROFILE" = "voice" ] && [ "$(uname)" = "Linux" ]; then
  if ! command -v espeak-ng >/dev/null 2>&1 && ! command -v espeak >/dev/null 2>&1; then
    echo ""
    echo "  ⓘ $(_t 'Linux 음성 출력(TTS)을 위해 espeak-ng 권장:')"
    echo "    Debian/Ubuntu: sudo apt-get install espeak-ng libnotify-bin"
    echo "    Fedora:        sudo dnf install espeak-ng libnotify"
    echo "    Arch:          sudo pacman -S espeak-ng libnotify"
  fi
fi

# 8-a. 개인 값 가드 훅 — git 클론일 때만 켠다(scripts/guard_personal.py). 켜지 않으면 개인 값이나
# 기록 문서가 커밋에 섞여도 막는 곳이 없다. 다른 값이 이미 있으면 사용자 설정이므로 건드리지 않는다.
if [ -e "$VT_DIR/.git" ] && command -v git >/dev/null 2>&1; then
  if [ -z "$(git -C "$VT_DIR" config --get core.hooksPath 2>/dev/null)" ]; then
    git -C "$VT_DIR" config core.hooksPath scripts/githooks \
      && echo "  ✓ $(_t '개인 값 가드 훅을 켰습니다 (scripts/githooks)')"
  fi
fi

# 8-b. (A0) Claude Code 훅 등록 — 에이전트 상태 배지·큐 자동 투입·TTS 요약이
# 전부 이 훅에 달려 있다. 지금까지는 README의 JSON 예시로만 안내해서 실제로
# 등록한 사용자가 사실상 없었고(그래서 서버가 이벤트를 한 건도 못 받았다),
# 그 사실을 알 방법조차 없었다. 등록기는 멱등이고 남의 훅을 보존한다.
# ~/.claude가 없으면(=Claude Code 미사용) 조용히 건너뛴다.
if [ -d "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" ]; then
  echo ""
  if "$VENV/bin/python" "$VT_DIR/server/claude_hooks.py" status >/dev/null 2>&1; then
    echo "  ✓ $(_t 'Claude Code 훅 이미 등록됨')"
  else
    echo "  $(_t 'Claude Code 훅 등록 (에이전트 상태·큐 자동 투입·TTS 요약의 전제)')"
    "$VENV/bin/python" "$VT_DIR/server/claude_hooks.py" install \
      || echo "  ⚠ $(_t "훅 등록 실패 — 'fsh hooks install'로 다시 시도할 수 있습니다")"
  fi
fi

# 9. (W5-1) 터미널 profile 자동 등록 권유
# 비대화형(curl|bash)에서는 스킵. TTY가 있으면 사용자에게 확인 후 fsh install-profiles 실행
if [ -t 0 ] && [ -t 1 ]; then
  echo ""
  printf "  %s " "$(_t '새 터미널 창이 자동으로 FarShell tmux로 진입하도록 설정할까요? [y/N]')"
  IFS= read -r REPLY_PROFILE || REPLY_PROFILE=""
  if [ "${REPLY_PROFILE:-}" = "y" ] || [ "${REPLY_PROFILE:-}" = "Y" ]; then
    "$VT_DIR/bin/fsh" install-profiles 2>&1 || echo "  ⚠ $(_t "install-profiles 실패 — 'fsh install-profiles' 수동 실행 가능")"
  else
    echo "  ⓘ $(_t "나중에 'fsh install-profiles' 또는 'fsh shell-init zsh >> ~/.zshrc'로 활성화 가능")"
  fi
fi

if vt_is_en; then
echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │  ✓ Installation complete                │"
echo "  │                                         │"
echo "  │  Get started:                           │"
echo "  │    fsh status   - check status          │"
echo "  │    fsh start    - server + entrance     │"
echo "  │    fsh mobile   - phone URL + QR        │"
if [ "$PROFILE" = "voice" ]; then
echo "  │    fsh voice    - voice mode            │"
fi
echo "  │                                         │"
echo "  │  Open a new terminal or run             │"
echo "  │  'source ~/.zshrc'                      │"
echo "  └─────────────────────────────────────────┘"
echo ""
echo "  Note: to open the public tunnel (remote access) with 'fsh start', set up"
echo "  authentication first - run 'fsh password' (not needed for localhost/lan/tailscale modes)."
echo ""
else
echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │  ✓ 설치 완료                              │"
echo "  │                                         │"
echo "  │  다음 명령으로 시작:                        │"
echo "  │    fsh status   — 상태 확인               │"
echo "  │    fsh start    — 서버 + 접속 경로 시작     │"
echo "  │    fsh mobile   — 폰 접속 URL + QR        │"
if [ "$PROFILE" = "voice" ]; then
echo "  │    fsh voice    — 음성 모드               │"
fi
echo "  │                                         │"
echo "  │  새 터미널을 열거나 'source ~/.zshrc' 실행  │"
echo "  └─────────────────────────────────────────┘"
echo ""
echo "  참고: 'fsh start'로 공개 터널(원격 접속)을 열려면 먼저 인증을 설정해야"
echo "  합니다 — 'fsh password' 실행 (localhost/lan/tailscale 모드는 필요 없음)."
echo ""
fi
