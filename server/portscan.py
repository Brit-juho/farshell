"""리스닝 포트 스캔 + 종료 — P3 포트 대시보드의 판정 로직.

Ports.app이 하는 일을 원격에서 한다. 폰에서 "지금 뭐가 떠 있지 / 3000번 죽여줘"가
안 되던 문제를 푼다.

주의점 3가지가 구현을 지배한다.

  1. lsof는 blocking이다(여기 맥에서 ~90ms). 반드시 호출부가 to_thread로 offload한다 —
     동기로 부르면 터미널 WS가 그만큼 멈춘다(preview.py:91-93과 같은 이유).
  2. COMMAND 컬럼은 기본 9자에서 잘린다("ControlCe", "redis-ser"). +c 0으로 전체를 받고,
     실행 경로/인자는 ps로 따로 보강한다.
  3. 같은 프로세스가 IPv4/IPv6로 두 줄 나온다. (port, pid)로 dedup하지 않으면
     목록이 두 배로 보인다.

종료(kill)에는 PID 재사용 경쟁이 있다. 조회 시점의 pid가 죽고 다른 프로세스가 같은 pid를
받으면 엉뚱한 것을 죽인다. 그래서 kill 직전에 port→pid를 다시 확인하고 불일치면 중단한다.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import time

logger = logging.getLogger(__name__)

LSOF_TIMEOUT = 5.0
PS_TIMEOUT = 5.0

# 조회 결과 캐시. 폰에서 패널을 열어두면 주기적으로 부르는데, 매번 lsof를 돌릴 이유가 없다.
_CACHE_TTL = 3.0
_cache: tuple[float, list[dict]] | None = None

# 죽이면 원격 접속 자체가 끊기는 것들. 이름은 ps의 comm 기준.
_CRITICAL_NAMES = ("cloudflared", "tailscaled", "sshd", "tailscale")

# 목록에서 감추지는 않되 kill을 막는 이유들.
PROTECT_SELF = "VT 서버 — 죽이면 이 화면이 끊깁니다"
PROTECT_CRITICAL = "원격 접속 인프라 — 죽이면 외부에서 못 들어옵니다"
PROTECT_FOREIGN = "다른 사용자의 프로세스입니다"

MAX_ROWS = 200


def _vt_port() -> int:
    try:
        return int(os.environ.get("VT_PORT", "7777"))
    except ValueError:
        return 7777


def _run(args: list[str], timeout: float) -> str:
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning(f"{args[0]} 시간 초과")
        return ""
    except (OSError, ValueError) as e:
        logger.warning(f"{args[0]} 실행 실패: {e}")
        return ""
    return p.stdout.decode("utf-8", errors="replace")


# lsof NAME 컬럼: "*:7777", "127.0.0.1:6379", "[::1]:5432"
_ADDR_RE = re.compile(r"^(?P<addr>.*?):(?P<port>\d+)$")


def _parse_lsof(text: str) -> dict[tuple[int, int], dict]:
    rows: dict[tuple[int, int], dict] = {}
    for line in text.splitlines()[1:]:          # 첫 줄은 헤더
        parts = line.split()
        if len(parts) < 9:
            continue
        # 컬럼: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
        #        0       1   2    3  4    5      6       7    8
        # TYPE(4)이 IPv4/IPv6, NODE(7)이 TCP. 둘을 헷갈리면 중복 병합이 깨진다.
        cmd, pid_s, user = parts[0], parts[1], parts[2]
        family = parts[4]
        name = parts[8]
        m = _ADDR_RE.match(name)
        if not m:
            continue
        try:
            pid, port = int(pid_s), int(m.group("port"))
        except ValueError:
            continue
        key = (port, pid)
        addr = m.group("addr")
        if key in rows:
            # IPv4/IPv6 중복 — 계열만 합치고 행은 늘리지 않는다.
            if family not in rows[key]["families"]:
                rows[key]["families"].append(family)
            if addr not in rows[key]["addrs"]:
                rows[key]["addrs"].append(addr)
            continue
        rows[key] = {
            "port": port, "pid": pid, "cmd": cmd, "user": user,
            "families": [family], "addrs": [addr],
        }
    return rows


def _ps_info(pids: list[int]) -> dict[int, dict]:
    """ps로 가동시간·CPU·RSS·실행 경로를 한 번에 받는다."""
    if not pids:
        return {}
    out = _run(
        ["ps", "-o", "pid=,etime=,%cpu=,rss=,command=", "-p", ",".join(str(p) for p in pids)],
        PS_TIMEOUT,
    )
    info: dict[int, dict] = {}
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            cpu = float(parts[2])
            rss = int(parts[3])
        except ValueError:
            continue
        info[pid] = {
            "uptime": parts[1],
            "cpu": cpu,
            "rss_kb": rss,
            "command": parts[4],
        }
    return info


def _classify(row: dict, me: str, vt_port: int) -> tuple[bool, str]:
    if row["port"] == vt_port:
        return True, PROTECT_SELF
    base = (row.get("command") or row["cmd"]).split()[0].rsplit("/", 1)[-1]
    if base in _CRITICAL_NAMES or row["cmd"] in _CRITICAL_NAMES:
        return True, PROTECT_CRITICAL
    if row["user"] != me:
        return True, PROTECT_FOREIGN
    return False, ""


def scan(use_cache: bool = True) -> dict:
    global _cache
    now = time.monotonic()
    if use_cache and _cache and (now - _cache[0]) < _CACHE_TTL:
        return {"ports": _cache[1], "cached": True, "truncated": len(_cache[1]) >= MAX_ROWS}

    text = _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "+c", "0"], LSOF_TIMEOUT)
    rows = _parse_lsof(text)
    ps = _ps_info(sorted({pid for (_, pid) in rows}))
    me = os.environ.get("USER") or ""
    vt_port = _vt_port()

    out: list[dict] = []
    truncated = False
    for (_port, _pid), row in sorted(rows.items()):
        if len(out) >= MAX_ROWS:
            truncated = True
            break
        row.update(ps.get(row["pid"], {}))
        protected, reason = _classify(row, me, vt_port)
        row["protected"] = protected
        row["protected_reason"] = reason
        # 루프백에만 열린 것과 전 인터페이스에 열린 것은 위험도가 다르다 — UI에서 구분한다.
        row["public"] = any(a in ("*", "0.0.0.0", "[::]") for a in row["addrs"])
        out.append(row)

    _cache = (now, out)
    return {"ports": out, "cached": False, "truncated": truncated}


def pid_for_port(port: int) -> int | None:
    """kill 직전 재확인용. 캐시를 쓰지 않는다 — 그게 이 함수의 존재 이유다."""
    text = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], LSOF_TIMEOUT)
    for line in text.split():
        try:
            return int(line)
        except ValueError:
            continue
    return None


def kill_port(port: int, expected_pid: int | None = None) -> dict:
    """SIGTERM → 3초 대기 → SIGKILL.

    expected_pid가 주어지면 현재 pid와 일치할 때만 진행한다(PID 재사용 방어).
    """
    vt_port = _vt_port()
    if port == vt_port:
        return {"ok": False, "error": "protected", "reason": PROTECT_SELF}

    pid = pid_for_port(port)
    if pid is None:
        return {"ok": False, "error": "not_found", "reason": f"포트 {port}에 리스닝 프로세스가 없습니다"}
    if expected_pid is not None and pid != expected_pid:
        return {"ok": False, "error": "pid_changed",
                "reason": f"프로세스가 바뀌었습니다 (기대 {expected_pid}, 현재 {pid})"}

    # 재분류 — 조회와 kill 사이에 상태가 바뀌었을 수 있다.
    rows = _parse_lsof(_run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "+c", "0"], LSOF_TIMEOUT))
    row = rows.get((port, pid))
    if row:
        row.update(_ps_info([pid]).get(pid, {}))
        protected, reason = _classify(row, os.environ.get("USER") or "", vt_port)
        if protected:
            return {"ok": False, "error": "protected", "reason": reason}

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": True, "pid": pid, "signal": "none", "note": "이미 종료됨"}
    except PermissionError:
        return {"ok": False, "error": "permission", "reason": "종료 권한이 없습니다"}

    for _ in range(30):                       # 3초
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            _invalidate()
            return {"ok": True, "pid": pid, "signal": "TERM"}
        except PermissionError:
            break

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        _invalidate()
        return {"ok": True, "pid": pid, "signal": "TERM"}
    except PermissionError:
        return {"ok": False, "error": "permission", "reason": "강제 종료 권한이 없습니다"}
    _invalidate()
    return {"ok": True, "pid": pid, "signal": "KILL"}


def _invalidate() -> None:
    global _cache
    _cache = None
