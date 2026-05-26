#!/bin/bash
# voice-terminal Server 실행 스크립트
# Python 환경은 ~/.vt.env의 VT_PYTHON 또는 프로젝트 .venv를 사용합니다.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server"

# defaults → 사용자 override 순으로 설정 로드
[ -f "$SCRIPT_DIR/config/vt.defaults.env" ] && source "$SCRIPT_DIR/config/vt.defaults.env"
[ -f "$HOME/.vt.env" ] && source "$HOME/.vt.env"

# Python 결정: VT_PYTHON > .venv > 시스템 python3
if [ -n "${VT_PYTHON:-}" ] && [ -x "$VT_PYTHON" ]; then
  PYTHON="$VT_PYTHON"
elif [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
  PYTHON="$SCRIPT_DIR/.venv/bin/python"
else
  PYTHON="$(command -v python3 || echo python3)"
fi

if ! "$PYTHON" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "✗ Python 환경에 의존성이 없습니다: $PYTHON"
  echo "  설치: ./install.sh         (터미널만)"
  echo "  설치: ./install.sh voice   (음성 모드 포함)"
  echo "  또는 ~/.vt.env에 VT_PYTHON=/path/to/python 지정"
  exit 1
fi

HOST="${HOST:-0.0.0.0}"
PORT="${VT_PORT:-${PORT:-7777}}"

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
