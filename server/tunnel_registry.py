"""추가 포트 터널 레지스트리 읽기 (L5) — "이 포트, 지금 공개 터널이 열려 있나?"를
포트 대시보드가 판정하는 데 쓴다.

쓰기는 여전히 bin/fsh(bash, `_registry_set`/`_registry_del`)만 한다 — 여기선 그
레지스트리를 읽기만 한다. 경로·포맷은 bin/fsh의 TUNNEL_REGISTRY와 동일해야
한다("port\turl\tlabel", `/tmp/vt-pids/tunnels.tsv`) — 두 곳이 갈라지면 조용히
어긋난다는 게 유일한 위험이라 경로를 상수 하나로 여기 박아둔다.
"""

from __future__ import annotations

import os
from pathlib import Path

PID_DIR = Path("/tmp/vt-pids")
REGISTRY_PATH = PID_DIR / "tunnels.tsv"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def exposed_ports() -> dict[int, dict]:
    """{port: {"url":..., "label":...}} — 레지스트리에 있어도 pid 파일로 살아있는지
    다시 확인한다. bin/fsh의 _registry_prune과 같은 목적이지만, fsh(bash)를 굳이
    거치지 않고 여기서 직접 검증 — 매 /api/ports 호출마다 subprocess를 새로 띄우지
    않기 위함(portscan.py의 lsof/ps와 같은 이유로 blocking을 to_thread offload하는
    호출부가 이미 있으니, 그 안에서 파일 읽기 정도는 추가 비용이 거의 없다).
    """
    if not REGISTRY_PATH.is_file():
        return {}
    try:
        lines = REGISTRY_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out: dict[int, dict] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        port_s, url = parts[0], parts[1]
        label = parts[2] if len(parts) > 2 else ""
        try:
            port = int(port_s)
        except ValueError:
            continue
        pidfile = PID_DIR / f"tunnel-{port}.pid"
        try:
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError):
            continue
        if not _pid_alive(pid):
            continue
        out[port] = {"url": url, "label": label or f"localhost:{port}"}
    return out
