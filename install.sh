#!/bin/bash
# voice-terminal 원라인 설치 스크립트
# 사용법:
#   ./install.sh             # 터미널만 (경량, ~50MB)
#   ./install.sh voice       # 터미널 + 음성 모드 (~1.5GB)
#   curl -fsSL <URL>/install.sh | bash       # 원격 설치 (터미널만)
#   curl -fsSL <URL>/install.sh | bash -s voice
#
# 환경변수:
#   VT_DIR   — 설치 경로 (기본: 스크립트 위치 또는 ~/voice-terminal)
#   VT_PORT  — 포트 (기본: 7777)

set -euo pipefail

PROFILE="${1:-terminal}"  # terminal | voice
PIPE_INSTALL=0

# 로컬 레포가 있으면 그걸 우선 사용. 진짜 파이프 설치(curl | bash)에서만 클론.
# 이전 버전은 `[ -t 0 ]`로 stdin TTY 여부를 체크했지만, 자동화/CI 환경도 false로
# 잡혀 로컬 레포를 무시하는 버그가 있었다 (TEST_REPORT.md Bug #2).
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/bin/vt" ]; then
  VT_DIR="$SCRIPT_DIR"
else
  PIPE_INSTALL=1
  VT_DIR="${VT_DIR:-$HOME/voice-terminal}"
  if [ ! -d "$VT_DIR" ]; then
    echo "▸ 레포 클론 중 → $VT_DIR"
    git clone --depth 1 https://github.com/NeTrioGit/voice-terminal.git "$VT_DIR"
  else
    echo "✓ 기존 레포 사용: $VT_DIR"
  fi
  cd "$VT_DIR"
fi

echo ""
echo "  🎤 voice-terminal 설치 — 프로필: $PROFILE"
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

# 4. vt CLI 등록
mkdir -p "$HOME/.local/bin"
ln -sf "$VT_DIR/bin/vt" "$HOME/.local/bin/vt"
chmod +x "$VT_DIR/bin/vt"
echo "✓ vt CLI 등록 → ~/.local/bin/vt"

# 4-0. vendor 자산 다운로드 (Phase 9 #3 — CDN 의존 제거)
VENDOR="$VT_DIR/frontend/static/vendor"
if [ ! -f "$VENDOR/xterm.min.js" ]; then
  echo "▸ vendor 자산 다운로드 (~1.5MB)..."
  mkdir -p "$VENDOR"
  CDN="https://cdn.jsdelivr.net/npm"
  for f in \
    "@xterm/xterm@5.5.0/lib/xterm.min.js" \
    "@xterm/xterm@5.5.0/css/xterm.min.css" \
    "@xterm/addon-fit@0.10.0/lib/addon-fit.min.js" \
    "@xterm/addon-search@0.15.0/lib/addon-search.min.js" \
    "lucide-static@0.469.0/font/lucide.min.css" \
    "lucide-static@0.469.0/font/lucide.woff2" \
    "lucide-static@0.469.0/font/lucide.woff" \
    "lucide-static@0.469.0/font/lucide.ttf" \
    "tweetnacl@1.0.3/nacl.min.js" \
    "tweetnacl-util@0.15.1/nacl-util.min.js"; do
    out="$VENDOR/$(basename "$f")"
    [ -f "$out" ] || curl -fsSL "$CDN/$f" -o "$out" || echo "  ⚠ $f 다운로드 실패"
  done
  # CSS의 ?t=... 캐시버스터 제거
  [ -f "$VENDOR/lucide.min.css" ] && sed -i.bak 's/?t=[0-9]*//g' "$VENDOR/lucide.min.css" && rm -f "$VENDOR/lucide.min.css.bak"
  echo "✓ vendor 자산 → $VENDOR"
fi

# 4-1. tmux 격리 config 복사 (Phase 8 G3)
mkdir -p "$HOME/.config/vt"
if [ -f "$VT_DIR/config/vt-tmux.conf" ] && [ ! -f "$HOME/.config/vt/tmux.conf" ]; then
  cp "$VT_DIR/config/vt-tmux.conf" "$HOME/.config/vt/tmux.conf"
  echo "✓ tmux 격리 config → ~/.config/vt/tmux.conf"
fi

# 5. 설정 파일 생성 (없을 때만)
if [ ! -f "$HOME/.vt.env" ]; then
  cat > "$HOME/.vt.env" <<EOF
# voice-terminal 설정 (수정 가능)
VT_DIR=$VT_DIR
VT_PORT=${VT_PORT:-7777}
VT_PYTHON=\${VT_DIR}/.venv/bin/python
# VT_TOKEN=your-secret    # 원격 접속 시 인증 (선택)
# VT_NOTIFY_URL=https://ntfy.sh/your-topic  # 푸시 알림 (D2, 선택)
EOF
  echo "✓ 설정 파일 생성 → ~/.vt.env"
else
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
  echo "  ⓘ 원격 접속(vt mobile)용 cloudflared 미설치."
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
# 비대화형(curl|bash)에서는 스킵. TTY가 있으면 사용자에게 확인 후 vt install-profiles 실행
if [ -t 0 ] && [ -t 1 ]; then
  echo ""
  printf "  새 터미널 창이 자동으로 voice-terminal tmux로 진입하도록 설정할까요? [y/N] "
  IFS= read -r REPLY_PROFILE || REPLY_PROFILE=""
  if [ "${REPLY_PROFILE:-}" = "y" ] || [ "${REPLY_PROFILE:-}" = "Y" ]; then
    "$VT_DIR/bin/vt" install-profiles 2>&1 || echo "  ⚠ install-profiles 실패 — 'vt install-profiles' 수동 실행 가능"
  else
    echo "  ⓘ 나중에 'vt install-profiles' 또는 'vt shell-init zsh >> ~/.zshrc'로 활성화 가능"
  fi
fi

echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │  ✓ 설치 완료                              │"
echo "  │                                         │"
echo "  │  다음 명령으로 시작:                        │"
echo "  │    vt status   — 상태 확인                │"
echo "  │    vt mobile   — 폰 접속 URL              │"
if [ "$PROFILE" = "voice" ]; then
echo "  │    vt voice    — 음성 모드                │"
fi
echo "  │                                         │"
echo "  │  새 터미널을 열거나 'source ~/.zshrc' 실행  │"
echo "  └─────────────────────────────────────────┘"
echo ""
