"""pytest 부트스트랩 — server/ 를 import 경로에 추가하고 인증/네트워크 env를 초기화."""

import os
import sys

# server/ 디렉토리를 sys.path에 추가 (main, network_access 등 직접 import)
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

# auth 모듈은 import 시점에 인증 env를 읽는다. 테스트가 셸 env에 오염되지 않도록
# import 전에 인증 관련 값을 모두 비우고, 네트워크 모드도 명시적으로 all(기본)로 둔다.
for _k in ("VT_AUTH_TOKEN", "VT_TOKEN", "VT_AUTH_PASSWORD_HASH", "VT_PASSWORD_HASH",
           "VT_AUTH_SESSION_KEY", "VT_SECRET_KEY"):
    os.environ.pop(_k, None)
os.environ.pop("VT_ACCESS_SPEC", None)
os.environ.pop("VT_TRUST_PROXY", None)
os.environ.setdefault("VT_NETWORK_MODE", "all")
