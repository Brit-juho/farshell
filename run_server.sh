#!/bin/bash
# farshell Server 실행 스크립트
# Python 환경은 ~/.vt.env의 VT_PYTHON 또는 프로젝트 .venv를 사용합니다.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server"

# 호출 시점의 VT_* 는 파일보다 우선 (bin/vt와 동일한 규칙)
_VT_ENV_PRESET_NAMES=" "
for _vt_v in ${!VT_@}; do _VT_ENV_PRESET_NAMES="$_VT_ENV_PRESET_NAMES$_vt_v "; done
unset _vt_v

# defaults → 사용자 override 순으로 설정 로드.
# source가 아니라 파서를 쓴다 — bin/vt와 같은 해석을 보장하고 설정 파일이 실행되지 않는다.
. "$SCRIPT_DIR/lib/vt_env.sh"
vt_env_load "$SCRIPT_DIR/config/vt.defaults.env"
vt_env_load "${VT_CONFIG:-$HOME/.vt.env}"

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

# bind 주소는 VT_NETWORK_MODE에서 도출한다(localhost 모드면 실제로 127.0.0.1에만 연다).
# HOST를 명시하면 그것이 우선.
PORT="${VT_PORT:-${PORT:-7777}}"
if [ -z "${HOST:-}" ]; then
  HOST="$(cd "$SERVER_DIR" && "$PYTHON" -c 'import network_access as n; print(n.resolve_bind_host(n.get_current_spec()))' 2>/dev/null || echo 0.0.0.0)"
fi

echo "farshell Server"
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
