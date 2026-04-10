#!/bin/bash
# 랄프톤 Voice Terminal Server 실행 스크립트
# whisper conda 환경에서 서버를 시작합니다.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server"
PYTHON="/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python"

if [ ! -f "$PYTHON" ]; then
    echo "whisper conda 환경이 없습니다."
    echo "conda create -n whisper python=3.10"
    echo "conda activate whisper"
    echo "pip install fastapi 'uvicorn[standard]' faster-whisper edge-tts sounddevice"
    exit 1
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

echo "랄프톤 Voice Terminal Server"
echo "  http://localhost:${PORT}"
echo "  http://$(ipconfig getifaddr en0 2>/dev/null || echo '0.0.0.0'):${PORT}"
echo ""

cd "$SERVER_DIR"
exec "$PYTHON" -m uvicorn main:app --host "$HOST" --port "$PORT" "$@"
