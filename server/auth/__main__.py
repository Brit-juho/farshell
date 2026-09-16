"""`python -m auth <cmd>` — bin/fsh가 서버 없이 직접 부르는 진입점.

예전에는 `python auth.py <cmd>`였다. 패키지로 바꾸면서 `-m`로 옮긴 이유는
편의가 아니라 **정확성**이다: `python auth.py`로 실행하면 그 파일이
`__main__` 모듈이 되는데, 하위 모듈이 설정을 읽으려고 `import auth`를 하면
**같은 코드가 `auth`라는 이름으로 한 번 더 로드된다.** 설정·잠금 카운터가
두 벌이 되어 CLI에서 고친 값이 반영되지 않는 종류의 버그가 생긴다.
`-m`는 처음부터 패키지로 로드하므로 그 사본이 생기지 않는다.
"""

import sys

from auth import _cli

sys.exit(_cli(sys.argv[1:]))
