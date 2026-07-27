#!/usr/bin/env python3
"""cloudflared 좀비 재연결 감지 → 자동 재시작.

Cloudflare Quick Tunnel(cloudflared)은 프로세스가 살아있어도(kill -0 성공)
엣지와의 QUIC 컨트롤 스트림이 끊긴 채 재연결만 무한 반복하는 좀비 상태에
빠질 수 있다. 이 상태에선 정적 파일 요청은 어쩌다 성공하고 API 요청은
503으로 실패하는 식으로 겉보기엔 "떠 있는데 안 되는" 증상만 남는다.
`vt status`/`_is_running`은 PID 생존만 보므로 이 좀비 상태를 못 잡는다.

cloudflared 로그(/tmp/cloudflared.log)에 재연결 실패 메시지
("control stream encountered a failure" 등)가 짧은 시간 안에 몰리면
좀비로 간주하고 `vt tunnel restart`를 호출해 프로세스를 강제로 교체한다.

실행: "$VT_PYTHON" server/tunnel_watchdog.py (vt start/voice/mobile이 자동 기동)
"""
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import vt_env

logger = logging.getLogger("tunnel-watchdog")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="[tunnel-watchdog] %(message)s")

_VT_ENV = vt_env.load()


def _vt_getenv(key: str, default: str = "") -> str:
    return vt_env.getenv(key, default, file_env=_VT_ENV)


# 좀비 판정: WINDOW_SEC 안에 재연결 실패가 THRESHOLD회 이상이면 재시작.
# 재시작 직후에도 한동안 정상 재연결 로그가 섞여 나올 수 있어 COOLDOWN_SEC 동안은 재판정 안 함.
POLL_SEC = float(_vt_getenv("VT_TUNNEL_WATCHDOG_POLL_SEC", "20"))
WINDOW_SEC = float(_vt_getenv("VT_TUNNEL_WATCHDOG_WINDOW_SEC", "90"))
FAILURE_THRESHOLD = int(_vt_getenv("VT_TUNNEL_WATCHDOG_THRESHOLD", "4"))
COOLDOWN_SEC = float(_vt_getenv("VT_TUNNEL_WATCHDOG_COOLDOWN_SEC", "120"))

TUNNEL_LOG = Path("/tmp/cloudflared.log")
VT_BIN = _HERE.parent / "bin" / "vt"

FAILURE_PATTERN = re.compile(
    r"control stream encountered a failure|failed to serve tunnel connection"
)


def _restart_tunnel() -> bool:
    logger.warning(
        f"최근 {WINDOW_SEC:.0f}초 안에 재연결 실패 {FAILURE_THRESHOLD}회 이상 — "
        "좀비로 판단, 터널 재시작"
    )
    try:
        result = subprocess.run(
            [str(VT_BIN), "tunnel", "restart"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            logger.info("재시작 성공")
            return True
        logger.error(f"재시작 실패 (rc={result.returncode}): {result.stderr.strip()}")
        return False
    except Exception as e:
        logger.error(f"재시작 중 오류: {e}")
        return False


def main():
    logger.info(
        f"감시 시작 — {POLL_SEC:.0f}s 간격, {WINDOW_SEC:.0f}s 안에 실패 "
        f"{FAILURE_THRESHOLD}회 이상이면 자동 재시작 (쿨다운 {COOLDOWN_SEC:.0f}s)"
    )

    offset = TUNNEL_LOG.stat().st_size if TUNNEL_LOG.exists() else 0
    failure_times: list[float] = []
    last_restart = 0.0

    while True:
        try:
            time.sleep(POLL_SEC)
            if not TUNNEL_LOG.exists():
                continue

            size = TUNNEL_LOG.stat().st_size
            if size < offset:
                # 재시작 등으로 로그가 truncate됨 — 처음부터 다시 추적
                offset = 0
                failure_times.clear()

            if size > offset:
                with TUNNEL_LOG.open("r", errors="ignore") as f:
                    f.seek(offset)
                    new_text = f.read()
                offset = size
                count = len(FAILURE_PATTERN.findall(new_text))
                if count:
                    now = time.time()
                    failure_times.extend([now] * count)

            now = time.time()
            failure_times[:] = [t for t in failure_times if now - t <= WINDOW_SEC]

            if (
                len(failure_times) >= FAILURE_THRESHOLD
                and (now - last_restart) > COOLDOWN_SEC
            ):
                if _restart_tunnel():
                    last_restart = now
                    failure_times.clear()
                    # _tunnel_restart가 로그를 truncate하므로 오프셋도 새로 맞춘다
                    offset = TUNNEL_LOG.stat().st_size if TUNNEL_LOG.exists() else 0
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.warning(f"감시 루프 오류(무시): {e}")


if __name__ == "__main__":
    main()
