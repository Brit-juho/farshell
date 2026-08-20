"""fsh manage entry point — server/tui/ 패키지의 main()을 호출.

분할 이력: 이전엔 단일 460줄 파일이었으나 server/tui/{helpers,modals,app}.py로
분할됨. bin/fsh(구 bin/vt) 호출 경로 보존을 위해 shim 유지.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from tui.app import main

if __name__ == "__main__":
    sys.exit(main())
