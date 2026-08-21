"""Voice Daemon 패키지 — 핫키/미디어 키 → 녹음 → STT → tmux 주입.

기존 server/voice_daemon.py는 이 패키지의 main()을 호출하는 shim입니다.

이 패키지를 import하는 것만으로는 선택 의존성(pynput)을 읽지 않는다. 서버의
음성 기능 여부를 확인하거나 코어 프로필에서 테스트를 수집할 때에도, 실제 음성
데몬을 실행할 때만 해당 의존성이 필요해야 한다.
"""


def main():
    """음성 데몬을 실행할 때만 무거운/선택 의존성을 불러온다."""
    from .daemon import main as daemon_main

    return daemon_main()

__all__ = ["main"]
