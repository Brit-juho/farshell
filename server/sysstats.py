"""시스템 사용률 — CPU · GPU · RAM · 네트워크.

새 의존성 없이 표준 라이브러리와 OS 기본 도구만 쓴다(psutil 없음 — 터미널만 쓰는 설치가
~50MB를 목표로 한다). 읽는 방법이 OS마다 다르다.

  macOS   CPU  `host_statistics`(ctypes, libSystem) — 실패하면 `top -l 2`
          RAM  `sysctl hw.memsize` + `vm_stat`
          NET  `netstat -ib`(en* 인터페이스의 링크 행)
          GPU  `ioreg -c IOAccelerator`의 `Device Utilization %`(sudo 불필요, 전용 VRAM이 없어 사용률만)
  Linux   CPU  /proc/stat 두 번의 차이
  WSL2    RAM  /proc/meminfo(MemTotal − MemAvailable)
          NET  /proc/net/dev(가상·루프백 제외)
          GPU  `nvidia-smi`(있을 때만) → AMD `gpu_busy_percent`. GPU가 여럿이면 가장 바쁜 하나 + 개수

CPU와 네트워크는 **누적 카운터의 차이 ÷ 시간**이라 값 하나가 아니라 두 번의 표본이 필요하다.
호출이 몇 개든 계산은 하나가 되도록 `MIN_INTERVAL` 동안은 같은 결과를 돌려준다.
읽지 못한 항목은 `None`이다 — 화면은 값이 없으면 그 칸을 숨긴다.

파서는 전부 순수 함수다(텍스트 → 값). 실제 명령 출력 샘플로 테스트한다(tests/test_sysstats.py).
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import threading
import time
from typing import Optional

import platform_utils

logger = logging.getLogger(__name__)

MIN_INTERVAL = 1.0       # 이보다 자주 불러도 같은 결과(초)
FIRST_SAMPLE_GAP = 0.25  # 첫 호출은 비교할 이전 표본이 없어 이 간격으로 두 번 읽는다(초)
CMD_TIMEOUT = 2.0

# 합산하지 않는 인터페이스: 루프백·가상·터널은 실제 트래픽의 이중 집계거나 무관하다.
# (tailscale0/utun은 물리 인터페이스를 한 번 더 지나는 같은 트래픽이다.)
_NET_SKIP_PREFIXES = ("lo", "veth", "docker", "br-", "virbr", "tailscale", "wg", "utun", "tun", "dummy")
_MAC_NET_RE = re.compile(r"^en\d+$")


# ── 파서(순수 함수) ──────────────────────────────────────────────────────────


def parse_proc_stat(text: str) -> Optional[tuple[int, int]]:
    """/proc/stat 첫 줄 → (busy, total) 누적 틱. idle·iowait는 놀고 있는 시간으로 본다."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            parts = line.split()[1:]
            try:
                v = [int(x) for x in parts[:8]]
            except ValueError:
                return None
            if len(v) < 4:
                return None
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            total = sum(v)
            return total - idle, total
    return None


def parse_meminfo(text: str) -> Optional[tuple[int, int]]:
    """/proc/meminfo → (used, total) 바이트. used = MemTotal − MemAvailable."""
    kb: dict[str, int] = {}
    for line in text.splitlines():
        m = re.match(r"^(\w+):\s+(\d+)\s*kB", line)
        if m:
            kb[m.group(1)] = int(m.group(2))
    total = kb.get("MemTotal")
    avail = kb.get("MemAvailable")
    if avail is None and total is not None and "MemFree" in kb:
        avail = kb["MemFree"] + kb.get("Buffers", 0) + kb.get("Cached", 0)   # 옛 커널
    if not total or avail is None:
        return None
    return max(0, total - avail) * 1024, total * 1024


def parse_proc_net_dev(text: str) -> dict[str, tuple[int, int]]:
    """/proc/net/dev → {인터페이스: (rx_bytes, tx_bytes)}."""
    out: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            out[name.strip()] = (int(fields[0]), int(fields[8]))
        except ValueError:
            continue
    return out


def parse_vm_stat(text: str, total: int) -> Optional[tuple[int, int]]:
    """macOS `vm_stat` → (used, total) 바이트.

    활성 모니터의 「메모리 사용」과 같은 뜻으로 active + wired + 압축기가 차지한 페이지를 쓴다
    (비활성·투기적·캐시 파일은 곧바로 되돌려 쓸 수 있어 뺀다). 정확히 같은 값은 아니고 근사다."""
    m = re.search(r"page size of (\d+) bytes", text)
    if not m or total <= 0:
        return None
    page = int(m.group(1))
    pages: dict[str, int] = {}
    for line in text.splitlines():
        m2 = re.match(r"^(.+?):\s+(\d+)\.?\s*$", line)
        if m2:
            pages[m2.group(1).strip()] = int(m2.group(2))
    need = ("Pages active", "Pages wired down")
    if any(k not in pages for k in need):
        return None
    used_pages = pages["Pages active"] + pages["Pages wired down"] + pages.get("Pages occupied by compressor", 0)
    return min(total, used_pages * page), total


def parse_netstat_ib(text: str) -> dict[str, tuple[int, int]]:
    """macOS `netstat -ib` → {en*: (rx_bytes, tx_bytes)}. 인터페이스마다 주소 행이 여럿이라
    `<Link#N>` 행(인터페이스 자체의 카운터) 하나만 쓴다."""
    out: dict[str, tuple[int, int]] = {}
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 8 or not cols[2].startswith("<Link#"):
            continue
        name = cols[0].rstrip("*")
        if not _MAC_NET_RE.match(name):
            continue
        try:
            # 끝에서 7개: Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll — 주소 칸이 비는 행이 있어 끝에서 센다.
            ibytes, obytes = int(cols[-5]), int(cols[-2])
        except ValueError:
            continue
        out[name] = (ibytes, obytes)
    return out


def parse_top_cpu(text: str) -> Optional[float]:
    """macOS `top -l 2` 마지막 `CPU usage:` 줄 → 사용률(%) = 100 − idle."""
    idle = None
    for m in re.finditer(r"CPU usage:.*?([\d.]+)%\s*idle", text):
        idle = float(m.group(1))
    return None if idle is None else max(0.0, min(100.0, 100.0 - idle))


def parse_ioreg_gpu(text: str) -> Optional[dict]:
    """macOS `ioreg -r -d 1 -w0 -c IOAccelerator` → 가장 바쁜 가속기 {pct, name, cores, count}."""
    best: Optional[dict] = None
    count = 0
    for block in re.split(r"(?m)^\s*\+-o ", text)[1:]:
        m = re.search(r'"Device Utilization %"\s*=\s*(\d+)', block)
        if not m:
            continue
        count += 1
        pct = float(max(0, min(100, int(m.group(1)))))
        if best is None or pct > best["pct"]:
            name = re.search(r'"model"\s*=\s*"([^"]+)"', block)
            cores = re.search(r'"gpu-core-count"\s*=\s*(\d+)', block)
            best = {"pct": pct, "name": name.group(1) if name else None,
                    "cores": int(cores.group(1)) if cores else None}
    if best is not None:
        best["count"] = count
    return best


def parse_nvidia_smi(text: str) -> Optional[dict]:
    """`nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits`
    → 가장 바쁜 GPU {pct, name, mem_used, mem_total(바이트), count}."""
    best: Optional[dict] = None
    count = 0
    for line in text.splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 4:
            continue
        try:
            pct = float(max(0, min(100, int(float(cols[1])))))
            used, total = int(float(cols[2])) * 1024 ** 2, int(float(cols[3])) * 1024 ** 2
        except ValueError:
            continue    # "[N/A]" 같은 값
        count += 1
        if best is None or pct > best["pct"]:
            best = {"pct": pct, "name": cols[0] or None, "mem_used": used, "mem_total": total}
    if best is not None:
        best["count"] = count
    return best


def usable_net(counters: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    return {k: v for k, v in counters.items() if not k.startswith(_NET_SKIP_PREFIXES)}


# ── 원시 값 읽기(OS별) ───────────────────────────────────────────────────────


def _run(args: list[str]) -> Optional[str]:
    try:
        p = subprocess.run(args, capture_output=True, timeout=CMD_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.decode("utf-8", errors="replace") if p.returncode == 0 else None


def _read_file(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _mac_cpu_ticks() -> Optional[tuple[int, int]]:
    """`host_statistics(HOST_CPU_LOAD_INFO)` — user·system·idle·nice 누적 틱. 프로세스를 띄우지 않는다."""
    try:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        lib.mach_host_self.restype = ctypes.c_uint
        ticks = (ctypes.c_uint * 4)()
        count = ctypes.c_uint(4)
        rc = lib.host_statistics(lib.mach_host_self(), 3, ticks, ctypes.byref(count))   # 3 = HOST_CPU_LOAD_INFO
        if rc != 0:
            return None
        user, system, idle, nice = (int(t) for t in ticks)
        total = user + system + idle + nice
        return total - idle, total
    except Exception as e:  # noqa: BLE001 — 아래의 top 폴백이 있다
        logger.debug(f"host_statistics 실패: {e}")
        return None


def _mac_cpu_pct_via_top() -> Optional[float]:
    out = _run(["top", "-l", "2", "-s", "1", "-n", "0"])
    return parse_top_cpu(out) if out else None


def read_cpu_ticks() -> Optional[tuple[int, int]]:
    if platform_utils.IS_MACOS:
        return _mac_cpu_ticks()
    text = _read_file("/proc/stat")
    return parse_proc_stat(text) if text else None


def read_ram() -> Optional[tuple[int, int]]:
    if platform_utils.IS_MACOS:
        total_s = (_run(["sysctl", "-n", "hw.memsize"]) or "").strip()
        vm = _run(["vm_stat"])
        if not total_s.isdigit() or not vm:
            return None
        return parse_vm_stat(vm, int(total_s))
    text = _read_file("/proc/meminfo")
    return parse_meminfo(text) if text else None


def read_gpu() -> Optional[dict]:
    if platform_utils.IS_MACOS:
        out = _run(["ioreg", "-r", "-d", "1", "-w0", "-c", "IOAccelerator"])
        return parse_ioreg_gpu(out) if out else None
    out = _run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits"])
    if out:
        found = parse_nvidia_smi(out)
        if found:
            return found
    busy: list[float] = []
    try:
        names = sorted(os.listdir("/sys/class/drm"))
    except OSError:
        names = []
    for n in names:
        if not re.fullmatch(r"card\d+", n):
            continue
        v = (_read_file(f"/sys/class/drm/{n}/device/gpu_busy_percent") or "").strip()
        if v.isdigit():
            busy.append(float(min(100, int(v))))
    return {"pct": max(busy), "name": None, "count": len(busy)} if busy else None


def read_net() -> dict[str, tuple[int, int]]:
    if platform_utils.IS_MACOS:
        out = _run(["netstat", "-ib"])
        return parse_netstat_ib(out) if out else {}
    text = _read_file("/proc/net/dev")
    return usable_net(parse_proc_net_dev(text)) if text else {}


# ── 표본 → 사용률 ────────────────────────────────────────────────────────────


def cpu_pct(prev: Optional[tuple[int, int]], cur: Optional[tuple[int, int]]) -> Optional[float]:
    if not prev or not cur:
        return None
    d_total = cur[1] - prev[1]
    d_busy = cur[0] - prev[0]
    if d_total <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * d_busy / d_total))


def net_rates(prev: dict, cur: dict, dt: float) -> Optional[tuple[float, float, list[str]]]:
    """공통 인터페이스의 (rx_bps, tx_bps, 이름들). 카운터가 줄었으면(재부팅·재설정) 그 값은 0으로 본다."""
    names = sorted(set(prev) & set(cur))
    if not names or dt <= 0:
        return None
    rx = sum(max(0, cur[n][0] - prev[n][0]) for n in names)
    tx = sum(max(0, cur[n][1] - prev[n][1]) for n in names)
    return rx / dt, tx / dt, names


class Sampler:
    """표본을 들고 있다가 사용률을 계산한다. 스레드 안전(라우트는 to_thread로 부른다)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prev: Optional[dict] = None
        self._cache: Optional[dict] = None
        self._cache_at = 0.0

    def _raw(self) -> dict:
        return {"t": time.monotonic(), "cpu": read_cpu_ticks(), "net": read_net()}

    def snapshot(self) -> dict:
        with self._lock:
            now = time.monotonic()
            if self._cache is not None and now - self._cache_at < MIN_INTERVAL:
                return self._cache
            cur = self._raw()
            prev = self._prev
            if prev is None:
                time.sleep(FIRST_SAMPLE_GAP)      # 비교할 표본이 없으면 짧게 두 번 읽는다
                prev, cur = cur, self._raw()
            self._prev = cur
            dt = cur["t"] - prev["t"]

            pct = cpu_pct(prev["cpu"], cur["cpu"])
            if pct is None and platform_utils.IS_MACOS and prev["cpu"] is None:
                pct = _mac_cpu_pct_via_top()      # ctypes를 못 쓴 환경의 폴백(1초 걸린다)
            ram = read_ram()
            gpu = read_gpu()
            rates = net_rates(prev["net"], cur["net"], dt)

            snap = {
                "ok": True,
                "platform": platform_utils.PLATFORM_NAME,
                "cpu": None if pct is None else {"pct": round(pct, 1), "cores": os.cpu_count() or 1},
                "gpu": None if gpu is None else {**gpu, "pct": round(gpu["pct"], 1)},
                "ram": None if ram is None else {
                    "used": ram[0], "total": ram[1],
                    "pct": round(100.0 * ram[0] / ram[1], 1) if ram[1] else 0.0,
                },
                "net": None if rates is None else {
                    "rx_bps": round(rates[0]), "tx_bps": round(rates[1]), "interfaces": rates[2],
                },
                "ts": time.time(),
            }
            self._cache, self._cache_at = snap, time.monotonic()
            return snap


_sampler = Sampler()


def snapshot() -> dict:
    return _sampler.snapshot()
