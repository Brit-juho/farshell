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

# 스크립트가 파이프(curl | bash)로 실행되면 레포 자동 클론
if [ -t 0 ] && [ -f "$(dirname "$0")/bin/vt" ]; then
  VT_DIR="$(cd "$(dirname "$0")" && pwd)"
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
VT_PORT=${VT_PORT:-7777}
VT_PYTHON=$VENV/bin/python
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
