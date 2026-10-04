"""프로세스 메모리 조회 — 최소 리눅스 이미지에는 `ps`가 없을 수 있어 /proc을 먼저 읽는다."""
from __future__ import annotations

import subprocess


def rss_mb(pid: int) -> int:
    """pid의 상주 메모리(MB). 알 수 없으면 0 — 메모리 표시 때문에 인식이 실패하면 안 된다."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=3).stdout.strip()
        return int(out) // 1024 if out.isdigit() else 0
    except (OSError, subprocess.SubprocessError):
        return 0
