"""`python -m host_store <cmd>` — bin/fsh의 `fsh host`가 서버 없이 부른다.

예전에는 `python host_store.py <cmd>`였다. 패키지로 바꾸면서 `-m`로 옮긴
이유는 `auth` 패키지와 같다: `python host_store.py`로 실행하면 그 파일이
`__main__`이 되어, 하위 모듈이 `import host_store`를 하는 순간 **같은 코드가
한 번 더 로드된다**(nonce 캐시가 두 벌이 되는 종류의 사고).
"""

import sys

from host_store import _cli

sys.exit(_cli(sys.argv[1:]))
