#!/usr/bin/env python3
"""Voice Daemon entry point — server/voice/ 패키지의 main()을 호출.

분할 이력: 이전엔 단일 500줄 파일이었으나 server/voice/{config,stt,tmux_target,
recorder,media_keys,daemon}.py로 분할됨. bin/vt 호출 경로 보존을 위해 shim 유지.

실행: "$VT_PYTHON" server/voice_daemon.py
"""
import sys
from pathlib import Path

# server/ 디렉터리를 sys.path에 추가 (platform_utils 등 sibling import 지원)
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from voice.daemon import main

if __name__ == "__main__":
    main()
