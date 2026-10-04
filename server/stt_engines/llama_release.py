"""llama.cpp 공식 배포본(llama-server) — 고정 버전·sha256 검증 설치.

앱이 대신 받아 주는 유일한 실행 파일이다. 그래서:
- **버전을 코드에 고정**한다(최신 자동 추적 금지) — 갱신은 이 파일의 TAG/해시를 바꾸는 PR로 한다.
- 다운로드 후 **sha256을 대조**하고 다르면 지운다(해시는 GitHub 릴리스 API의 digest에서 가져와 여기 고정).
- 받기는 사용자가 확인 창에서 승인한 경로에서만 부른다.
- 압축 해제는 `filter="data"`로 경로 이탈·장치 파일·링크 탈출을 막는다.

사용자가 이미 설치한 llama-server가 있으면 그걸 쓴다: `VT_LLAMA_SERVER` → 앱이 받은 것 → PATH 순.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from .env import HostEnv

TAG = "b11347"
_URL = "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{name}"

# 환경 키 → (파일명, sha256, 바이트). 리눅스는 ubuntu 빌드(glibc 계열 배포판에서 동작, WSL2 포함).
ASSETS = {
    "macos-arm64": ("llama-b11347-bin-macos-arm64.tar.gz",
                    "0009fd608997a47f931afef26ceab1d3ba7e9afebf816c917b48ef43b123b59b", 11828974),
    "macos-x86_64": ("llama-b11347-bin-macos-x64.tar.gz",
                     "e3603f89685ad94a1870ab0cf414394c8a31dc8b7a53323f5eb4ce1dfbcd0d51", 11383816),
    "linux-arm64": ("llama-b11347-bin-ubuntu-arm64.tar.gz",
                    "c7e30d36b2d26d1469e823e5edffce42498616d500ab8a499748913937095aef", 13591763),
    "linux-x86_64": ("llama-b11347-bin-ubuntu-x64.tar.gz",
                     "a21ad95ccd452fc22b29c6badf126a9186612dccc00260373dea68d3932eb97d", 17551840),
}

BINARY_NAME = "llama-server"


def asset_for(env: HostEnv) -> Optional[tuple]:
    return ASSETS.get(env.key)


def install_root() -> Path:
    import vt_paths
    return vt_paths.state_dir() / "engines" / "llama.cpp"


def install_dir(tag: str = TAG) -> Path:
    return install_root() / tag


def _executable(p: Path) -> bool:
    return p.is_file() and os.access(p, os.X_OK)


def managed_binary() -> Optional[Path]:
    d = install_dir()
    if d.is_dir():
        for p in d.rglob(BINARY_NAME):
            if _executable(p):
                return p
    return None


def find_binary() -> Optional[Path]:
    """VT_LLAMA_SERVER → 앱이 받은 것 → PATH. 없으면 None."""
    override = os.environ.get("VT_LLAMA_SERVER", "").strip()
    if override:
        p = Path(override).expanduser()
        return p if _executable(p) else None
    m = managed_binary()
    if m:
        return m
    found = shutil.which(BINARY_NAME)
    return Path(found) if found else None


class ReleaseError(RuntimeError):
    """다운로드·검증·설치 실패 — 메시지는 사용자에게 그대로 보인다."""


def download_and_install(env: HostEnv, progress: Callable[[int, Optional[int]], None],
                         _opener=urllib.request.urlopen) -> Path:
    asset = asset_for(env)
    if asset is None:
        raise ReleaseError(f"이 환경({env.label})용 llama.cpp 공식 배포본이 없습니다 — "
                           "직접 설치한 뒤 VT_LLAMA_SERVER에 경로를 지정해 주세요")
    name, want_sha, size = asset
    dest = install_dir()
    root = install_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=".dl-", dir=str(root)))
    archive = tmp_dir / name
    try:
        req = urllib.request.Request(_URL.format(tag=TAG, name=name), headers={"User-Agent": "farshell"})
        done = 0
        h = hashlib.sha256()
        with _opener(req, timeout=60) as r, open(archive, "wb") as out:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                done += len(chunk)
                if done > size * 1.05:       # 예상보다 훨씬 크면 중단 — 엉뚱한 파일을 받는 중이다
                    raise ReleaseError("받은 파일이 예상 크기를 넘어 중단했습니다")
                h.update(chunk)
                out.write(chunk)
                progress(done, size)
        if h.hexdigest() != want_sha:
            raise ReleaseError("받은 파일의 sha256이 일치하지 않아 폐기했습니다(손상되었거나 변조되었을 수 있습니다)")
        staging = tmp_dir / "x"
        staging.mkdir()
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(staging, filter="data")
        except tarfile.TarError as e:      # 경로 이탈·링크 탈출·손상 모두 — 안전하지 않으면 설치하지 않는다
            raise ReleaseError(f"압축 파일이 안전하지 않거나 손상돼 설치를 중단했습니다 ({type(e).__name__})") from e
        found = next((p for p in staging.rglob(BINARY_NAME) if p.is_file()), None)
        if found is None:
            raise ReleaseError("압축 안에서 llama-server를 찾지 못했습니다")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(staging), str(dest))
        binary = managed_binary()
        if binary is None:
            for p in dest.rglob(BINARY_NAME):
                p.chmod(p.stat().st_mode | 0o755)
            binary = managed_binary()
        if binary is None:
            raise ReleaseError("설치 후 실행 파일을 찾지 못했습니다")
        return binary
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def remove_installed() -> int:
    """앱이 받은 llama.cpp를 지우고 회수한 바이트 수를 돌려준다(사용자가 따로 설치한 건 건드리지 않는다)."""
    root = install_root()
    if not root.is_dir():
        return 0
    from .hfcache import dir_size
    freed = dir_size(root)
    shutil.rmtree(root, ignore_errors=True)
    return freed
