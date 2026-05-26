"""Voice Daemon 패키지 — 핫키/미디어 키 → 녹음 → STT → tmux 주입.

기존 server/voice_daemon.py는 이 패키지의 main()을 호출하는 shim입니다.
"""
from .daemon import main

__all__ = ["main"]
