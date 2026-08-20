#!/bin/bash
# FarShell 원라인 설치 스크립트
# 사용법:
#   ./install.sh             # 터미널만 (경량, ~50MB)
#   ./install.sh voice       # 터미널 + 음성 모드 (~1.5GB)
#   curl -fsSL <URL>/install.sh | bash       # 원격 설치 (터미널만)
#   curl -fsSL <URL>/install.sh | bash -s voice
#
# 환경변수:
#   VT_DIR   — 설치 경로 (기본: 스크립트 위치 또는 ~/farshell)
#   VT_PORT  — 포트 (기본: 7777)

set -euo pipefail

# Windows 네이티브 가드 — Git Bash/MSYS/Cygwin에서 실행 시 거부
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
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
    exit 1
    ;;
esac

PROFILE="${1:-terminal}"  # terminal | voice
PIPE_INSTALL=0

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
    echo "▸ 레포 클론 중 → $VT_DIR"
    git clone --depth 1 https://github.com/Brit-juho/farshell.git "$VT_DIR"
  else
    echo "✓ 기존 레포 사용: $VT_DIR"
  fi
  cd "$VT_DIR"
fi

echo ""
echo "  🎤 FarShell 설치 — 프로필: $PROFILE"
echo "  설치 경로: $VT_DIR"
echo ""

# 1. Python 확인
if ! command -v python3 >/dev/null 2>&1; then
  echo "✗ python3 미설치. macOS: 'brew install python@3.11', Linux: 'apt install python3'"
  exit 1
fi
PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "✓ Python $PY_VERSION"

# 2. venv 생성 (이미 있으면 재사용)
VENV="$VT_DIR/.venv"
if [ ! -d "$VENV" ]; then
  echo "▸ 가상환경 생성 → $VENV"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --quiet --upgrade pip

# 3. 프로필별 패키지 설치
echo "▸ 패키지 설치 중..."
pip install --quiet -r "$VT_DIR/requirements-core.txt"
if [ "$PROFILE" = "voice" ]; then
  pip install --quiet -r "$VT_DIR/requirements-voice.txt"
  if [ "$(uname)" = "Darwin" ]; then
    pip install --quiet pyobjc-framework-Cocoa
  fi
  echo "✓ 터미널 + 음성 모드 의존성 설치 완료"
else
  echo "✓ 터미널 의존성 설치 완료 (음성 모드는 './install.sh voice'로 추가)"
fi

# 4. fsh CLI 등록 (bin/vt는 bin/fsh를 가리키는 심링크 — 예전 명령어 vt도 그대로 동작)
mkdir -p "$HOME/.local/bin"
chmod +x "$VT_DIR/bin/fsh"
ln -sf "$VT_DIR/bin/fsh" "$HOME/.local/bin/fsh"
ln -sf "$VT_DIR/bin/vt" "$HOME/.local/bin/vt"
echo "✓ fsh CLI 등록 → ~/.local/bin/fsh (하위 호환: vt도 계속 동작)"

# 4-1. tmux 격리 config 복사 (Phase 8 G3)
mkdir -p "$HOME/.config/vt"
if [ -f "$VT_DIR/config/vt-tmux.conf" ] && [ ! -f "$HOME/.config/vt/tmux.conf" ]; then
  cp "$VT_DIR/config/vt-tmux.conf" "$HOME/.config/vt/tmux.conf"
  echo "✓ tmux 격리 config → ~/.config/vt/tmux.conf"
fi

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
# 'fsh mobile'(공개 터널)은 인증이 없으면 실행을 거부합니다 — 아래 둘 중 하나로
# 인증을 먼저 설정하세요(권장: fsh password). localhost/lan/tailscale 모드는 필요 없습니다.
#   fsh password              대화형으로 비밀번호 설정 (scrypt 해시만 여기 저장됨)
# VT_AUTH_TOKEN=your-secret  기계용 토큰 (데몬/QR 등, 원한다면 직접 값 채우기)
# VT_NOTIFY_URL=https://ntfy.sh/your-topic  # 푸시 알림 (선택)
EOF
  chmod 600 "$HOME/.vt.env"
  echo "✓ 설정 파일 생성 → ~/.vt.env (권한 600)"
else
  # 예전 install.sh가 644로 만들어 둔 파일을 여기서 바로잡는다
  chmod 600 "$HOME/.vt.env" 2>/dev/null || true
  echo "✓ 기존 설정 유지 → ~/.vt.env"
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
    echo "✓ PATH 등록 → $SHELL_RC (새 터미널에서 적용)"
  fi
fi

# 7. cloudflared 안내 (선택)
if ! command -v cloudflared >/dev/null 2>&1; then
  echo ""
  echo "  ⓘ 원격 접속(fsh mobile)용 cloudflared 미설치."
  if [ "$(uname)" = "Darwin" ]; then
    echo "    설치: brew install cloudflared"
  else
    echo "    설치: curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o ~/.local/bin/cloudflared && chmod +x ~/.local/bin/cloudflared"
  fi
fi

# 8. (W3-5) Linux voice 프로필 — espeak-ng + libnotify 안내
if [ "$PROFILE" = "voice" ] && [ "$(uname)" = "Linux" ]; then
  if ! command -v espeak-ng >/dev/null 2>&1 && ! command -v espeak >/dev/null 2>&1; then
    echo ""
    echo "  ⓘ Linux 음성 출력(TTS)을 위해 espeak-ng 권장:"
    echo "    Debian/Ubuntu: sudo apt-get install espeak-ng libnotify-bin"
    echo "    Fedora:        sudo dnf install espeak-ng libnotify"
    echo "    Arch:          sudo pacman -S espeak-ng libnotify"
  fi
fi

# 9. (W5-1) 터미널 profile 자동 등록 권유
# 비대화형(curl|bash)에서는 스킵. TTY가 있으면 사용자에게 확인 후 fsh install-profiles 실행
if [ -t 0 ] && [ -t 1 ]; then
  echo ""
  printf "  새 터미널 창이 자동으로 FarShell tmux로 진입하도록 설정할까요? [y/N] "
  IFS= read -r REPLY_PROFILE || REPLY_PROFILE=""
  if [ "${REPLY_PROFILE:-}" = "y" ] || [ "${REPLY_PROFILE:-}" = "Y" ]; then
    "$VT_DIR/bin/fsh" install-profiles 2>&1 || echo "  ⚠ install-profiles 실패 — 'fsh install-profiles' 수동 실행 가능"
  else
    echo "  ⓘ 나중에 'fsh install-profiles' 또는 'fsh shell-init zsh >> ~/.zshrc'로 활성화 가능"
  fi
fi

echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │  ✓ 설치 완료                              │"
echo "  │                                         │"
echo "  │  다음 명령으로 시작:                        │"
echo "  │    fsh status   — 상태 확인               │"
echo "  │    fsh mobile   — 폰 접속 URL             │"
if [ "$PROFILE" = "voice" ]; then
echo "  │    fsh voice    — 음성 모드               │"
fi
echo "  │                                         │"
echo "  │  새 터미널을 열거나 'source ~/.zshrc' 실행  │"
echo "  └─────────────────────────────────────────┘"
echo ""
echo "  참고: 'fsh mobile'로 공개 터널(원격 접속)을 열려면 먼저 인증을 설정해야"
echo "  합니다 — 'fsh password' 실행 (localhost/lan/tailscale 모드는 필요 없음)."
echo ""
