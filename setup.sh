#!/bin/bash
# 랄프톤 Voice Terminal — 설치 스크립트
# macOS에서 실행. conda whisper 환경 + 의존성 설치 + ralph CLI 등록.
#
# 사용법:
#   git clone <repo> ~/ralphton && cd ~/ralphton && ./setup.sh

set -uo pipefail

echo ""
echo "  🎤 랄프톤 Voice Terminal 설치"
echo ""

RALPH_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- 1. Python 환경 확인/설치 ---
CONDA_BASE=""
if command -v conda >/dev/null 2>&1; then
  CONDA_BASE="$(conda info --base 2>/dev/null)"
fi

if [ -z "$CONDA_BASE" ]; then
  if [ "$(uname)" = "Darwin" ]; then
    echo "⚠  conda가 설치되지 않았습니다."
    echo "   miniforge 설치: brew install --cask miniforge"
  else
    echo "⚠  conda가 설치되지 않았습니다."
    echo "   miniconda 설치: wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && bash Miniconda3-latest-Linux-x86_64.sh"
  fi
  echo "   설치 후 다시 실행하세요."
  exit 1
fi

WHISPER_ENV="$CONDA_BASE/envs/whisper"
WHISPER_PY="$WHISPER_ENV/bin/python"

if [ ! -x "$WHISPER_PY" ]; then
  echo "▸ conda 'whisper' 환경 생성 중..."
  conda create -n whisper python=3.10 -y
fi

echo "▸ 의존성 설치 중..."
_PIP_PKGS="fastapi uvicorn faster-whisper edge-tts sounddevice numpy pynput python-multipart websockets"
if [ "$(uname)" = "Darwin" ]; then
  _PIP_PKGS="$_PIP_PKGS pyobjc-framework-Cocoa"
fi
"$WHISPER_PY" -m pip install -q $_PIP_PKGS 2>&1 | tail -3

echo "✓ Python 환경 준비 완료"

# --- 2. cloudflared 확인 ---
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "▸ cloudflared 설치..."
  if command -v brew >/dev/null 2>&1; then
    brew install cloudflared
  elif command -v apt >/dev/null 2>&1; then
    echo "  sudo apt install cloudflared 또는:"
    echo "  wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O ~/.local/bin/cloudflared && chmod +x ~/.local/bin/cloudflared"
  else
    echo "⚠  cloudflared를 수동 설치하세요: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/"
  fi
fi

# --- 3. ralph CLI 전역 등록 ---
mkdir -p ~/.local/bin
ln -sf "$RALPH_DIR/bin/ralph" ~/.local/bin/ralph
chmod +x "$RALPH_DIR/bin/ralph"

# PATH 확인
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
  SHELL_RC=""
  [ -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.zshrc"
  [ -f "$HOME/.bashrc" ] && SHELL_RC="$HOME/.bashrc"
  if [ -n "$SHELL_RC" ]; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
    echo "✓ PATH에 ~/.local/bin 추가 ($SHELL_RC)"
    echo "  → 새 터미널을 열거나 source $SHELL_RC 실행"
  fi
fi

echo "✓ ralph CLI 등록 완료"

# --- 4. 설정 파일 생성 ---
CONFIG="$HOME/.ralph.env"
if [ ! -f "$CONFIG" ]; then
  cat > "$CONFIG" << EOF
# 랄프톤 설정
# 포트 (기본 7777, 충돌 시 변경)
RALPH_PORT=7777

# Python 경로 (conda whisper 환경)
RALPH_PYTHON=$WHISPER_PY

# API 토큰 (원격 접속 시 보안, 비워두면 인증 없음)
# RALPH_TOKEN=my-secret-token
EOF
  echo "✓ 설정 파일 생성: $CONFIG"
else
  echo "✓ 설정 파일 이미 존재: $CONFIG"
fi

# --- 5. Claude Code 전역 스킬 등록 ---
if [ -d "$HOME/.claude" ]; then
  mkdir -p "$HOME/.claude/skills/ralph"
  cp "$RALPH_DIR/.claude/skills/ralph/SKILL.md" "$HOME/.claude/skills/ralph/SKILL.md" 2>/dev/null || \
  ln -sf "$RALPH_DIR/.claude/skills/ralph" "$HOME/.claude/skills/ralph" 2>/dev/null || true
  echo "✓ Claude Code /ralph 스킬 등록"
fi

# --- 6. 플랫폼별 안내 ---
echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  설치 완료!                                    │"
echo "  │                                               │"
echo "  │  사용법:                                       │"
echo "  │    ralph voice   — 음성 모드 시작              │"
echo "  │    ralph mobile  — 모바일 접속 URL             │"
echo "  │    ralph stop    — 종료                        │"
echo "  │                                               │"
echo "  │  설정: ~/.ralph.env (포트, 토큰 등)            │"
echo "  └─────────────────────────────────────────────┘"

if [ "$(uname)" = "Darwin" ]; then
  echo ""
  echo "  macOS: 음성 모드 시 시스템 설정 → 개인정보 →"
  echo "         접근성에서 터미널 앱 허용 필요"
elif grep -qi microsoft /proc/version 2>/dev/null; then
  echo ""
  echo "  WSL2: Windows에서 localhost:7777로 브라우저 접속"
  echo "        음성 핫키는 WSLg 필요 (Windows 11)"
  echo "        Windows PowerShell: .\\ralph.ps1 voice"
fi
echo ""
