#!/bin/bash
# voice-terminal Server 실행 스크립트
# whisper conda 환경에서 서버를 시작합니다.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server"
if [ "$(uname)" = "Darwin" ]; then
  PYTHON="/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python"
else
  PYTHON="$(which python3 2>/dev/null || echo python3)"
fi

if [ ! -f "$PYTHON" ] && ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "whisper conda 환경이 없습니다."
    echo "conda create -n whisper python=3.10"
    echo "conda activate whisper"
    echo "pip install fastapi 'uvicorn[standard]' faster-whisper edge-tts sounddevice"
    exit 1
fi

# ~/.vt.env 설정 로드
[ -f "$HOME/.vt.env" ] && source "$HOME/.vt.env"

HOST="${HOST:-0.0.0.0}"
PORT="${VT_PORT:-${PORT:-7777}}"
PYTHON="${VT_PYTHON:-$PYTHON}"

echo "voice-terminal Server"
echo "  http://localhost:${PORT}"
if [ "$(uname)" = "Darwin" ]; then
  _IP=$(ipconfig getifaddr en0 2>/dev/null || echo '0.0.0.0')
else
  _IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo '0.0.0.0')
fi
echo "  http://${_IP}:${PORT}"
echo ""

cd "$SERVER_DIR"
exec "$PYTHON" -m uvicorn main:app --host "$HOST" --port "$PORT" "$@"
