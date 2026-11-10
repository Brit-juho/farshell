#!/bin/bash
# 파이썬 의존성 해시 잠금 파일(locks/*.txt)을 requirements-*.txt 에서 다시 만든다.
#
#   scripts/lock_python.sh              # 지금 고정된 버전은 유지하고, 요구 사항이 바뀐 것만 반영
#   scripts/lock_python.sh --upgrade    # 모두 최신으로 올린다
#   scripts/lock_python.sh --upgrade-package fastapi   # 한 패키지만 올린다
#
# 사용자 설치(install.sh)·CI·fsh update 는 이 도구를 쓰지 않는다 — 그쪽은 순수 pip(`--require-hashes`)만 쓴다.
# 이 스크립트는 **의존성을 바꾸는 개발자**만 돌리고, 결과(locks/)를 커밋한다. 필요한 것: uv (https://docs.astral.sh/uv/).
#
# 잠금은 "범용(universal)"이다: 파일 하나에 모든 지원 환경(macOS arm64/x86_64, Linux x86_64/aarch64, Python 3.11 이상)의
# 버전이 환경 마커와 함께 들어 있고, 해시는 그 버전의 모든 휠·소스 배포를 담는다. 설치하는 쪽 pip 이 마커로 자기 환경 것만 고른다.
# core → voice·dev 순으로 만들고, 뒤의 둘은 core 의 버전을 제약으로 받아 같은 패키지가 같은 버전이 되게 한다.
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_FLOOR="3.11"   # install.sh 의 최소 버전·CI 매트릭스 하한과 같아야 한다(server/tests/test_python_lock.py 가 확인)

if ! command -v uv >/dev/null 2>&1; then
  echo "✗ uv 가 필요합니다(잠금 파일을 만드는 개발자용 도구, 설치에는 필요 없음): https://docs.astral.sh/uv/" >&2
  exit 1
fi

# 인자는 그대로 uv 로 넘긴다(--upgrade 등). 다만 출력 경로·대상은 바꿀 수 없게 막는다.
EXTRA=("$@")

compile() {
  local name="$1"; shift
  uv pip compile "requirements-$name.txt" "$@" \
    --universal --python-version "$PYTHON_FLOOR" --generate-hashes \
    --custom-compile-command "scripts/lock_python.sh" \
    ${EXTRA[@]+"${EXTRA[@]}"} \
    -o "locks/$name.txt" --quiet
  echo "✓ locks/$name.txt"
}

mkdir -p locks
compile core
compile voice -c locks/core.txt
compile dev -c locks/core.txt

# 만든 결과를 바로 점검한다. `packaging` 이 없는 파이썬이면 uv 가 임시로 붙여 돌린다.
if python3 -c 'import packaging' 2>/dev/null; then
  python3 scripts/check_python_lock.py
else
  uv run --quiet --no-project --with packaging python scripts/check_python_lock.py
fi
