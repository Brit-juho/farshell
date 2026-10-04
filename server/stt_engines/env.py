"""서버가 도는 환경 감지 — 어떤 STT 엔진·모델을 쓸 수 있는지 판정하는 근거.

STT는 FarShell 서버가 도는 OS에서만 실행된다(접속하는 폰·PC는 녹음만 보낸다). 그래서 여기서 보는 건
**서버 호스트**다. WSL2는 리눅스로 취급하고 `wsl2` 플래그만 따로 남긴다.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class HostEnv:
    os: str                              # "macos" | "linux" | "other"
    arch: str                            # "arm64" | "x86_64" | 그 외 원문
    macos_version: Optional[tuple]       # (26, 0) 처럼. macOS가 아니면 None
    wsl2: bool = False
    ram_mb: Optional[int] = None

    @property
    def key(self) -> str:
        """`macos-arm64`, `linux-x86_64` 같은 짧은 키."""
        return f"{self.os}-{self.arch}"

    @property
    def label(self) -> str:
        if self.os == "macos":
            chip = "Apple Silicon" if self.arch == "arm64" else "Intel"
            v = ".".join(str(x) for x in (self.macos_version or ())[:2])
            return f"macOS {v} ({chip})".replace("  ", " ")
        if self.os == "linux":
            return ("WSL2 " if self.wsl2 else "Linux ") + self.arch
        return f"{self.os} {self.arch}"

    def to_dict(self) -> dict:
        return {"os": self.os, "arch": self.arch, "key": self.key, "label": self.label,
                "macos_version": list(self.macos_version) if self.macos_version else None,
                "wsl2": self.wsl2, "ram_mb": self.ram_mb}


def _norm_arch(machine: str) -> str:
    m = (machine or "").lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64"):
        return "x86_64"
    return m or "unknown"


def _macos_version() -> Optional[tuple]:
    ver = platform.mac_ver()[0]
    m = re.match(r"(\d+)\.(\d+)", ver or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _is_wsl2() -> bool:
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _ram_mb(system: str) -> Optional[int]:
    try:
        if system == "macos":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2).stdout
            return int(out.strip()) // (1024 * 1024)
        if system == "linux":
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


_cached: Optional[HostEnv] = None


def detect(refresh: bool = False) -> HostEnv:
    """현재 호스트. 프로세스 동안 바뀌지 않으므로 캐시한다. 테스트는 `override()`를 쓴다."""
    global _cached
    if _cached is not None and not refresh:
        return _cached
    system = {"Darwin": "macos", "Linux": "linux"}.get(platform.system(), "other")
    _cached = HostEnv(
        os=system,
        arch=_norm_arch(platform.machine()),
        macos_version=_macos_version() if system == "macos" else None,
        wsl2=_is_wsl2() if system == "linux" else False,
        ram_mb=_ram_mb(system),
    )
    return _cached


def override(env: Optional[HostEnv]) -> None:
    """테스트·진단용: 감지 결과를 강제로 바꾼다(None이면 다음 detect()가 다시 감지)."""
    global _cached
    _cached = env
