"""HuggingFace 캐시 도우미 — 모델 용량 집계·부분 삭제·캐시 조회.

HF 캐시는 snapshots/ 의 파일이 blobs/ 실파일의 심볼릭 링크이고, 새 캐시(xet)는 가중치 실파일이 폴더 밖
공유 `hub/blobs/` 에 있다. 그래서:
- 용량은 링크를 끝까지 따라간 **실제 파일을 한 번씩만** 센다(링크를 그대로 따라가며 더하면 두 배로 보이고,
  링크를 안 세면 폴더 밖 가중치가 빠진다).
- 삭제는 폴더만 지우면 폴더 밖 가중치가 고아로 남으므로 같이 지우되, **다른 리포가 가리키는 파일은 남긴다**.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Optional


def cache_dir() -> Path:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        return Path(HF_HUB_CACHE)
    except Exception:
        return Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))) / "hub"


def repo_dir(repo: str, root: Optional[Path] = None) -> Path:
    return (root or cache_dir()) / ("models--" + repo.replace("/", "--"))


def real_files(path: Path) -> dict:
    """폴더 안 파일이 가리키는 실제 파일(링크를 끝까지 따라간 경로) → 크기."""
    out: dict = {}
    if path.is_dir():
        for p in path.rglob("*"):
            try:
                if p.is_symlink() or p.is_file():
                    real = p.resolve()
                    if real.is_file() and real not in out:
                        out[real] = real.stat().st_size
            except OSError:
                pass
    return out


def dir_size(path: Path) -> int:
    return sum(real_files(path).values())


def cached_file(repo: str, filename: str, root: Optional[Path] = None) -> Optional[Path]:
    """리포의 파일 하나가 캐시에 완전히 있으면 그 경로, 없으면 None. 네트워크를 쓰지 않는다."""
    snaps = repo_dir(repo, root) / "snapshots"
    if not snaps.is_dir():
        return None
    for snap in sorted(snaps.iterdir()):
        p = snap / filename
        try:
            if p.exists() and p.resolve().is_file():
                return p
        except OSError:
            continue
    return None


def cached_snapshot(repo: str, root: Optional[Path] = None) -> Optional[Path]:
    """리포 스냅샷 폴더(가장 최근). 없으면 None."""
    snaps = repo_dir(repo, root) / "snapshots"
    if not snaps.is_dir():
        return None
    entries = [p for p in snaps.iterdir() if p.is_dir()]
    return max(entries, key=lambda p: p.stat().st_mtime) if entries else None


def _referenced_elsewhere(cache_root: Path, exclude_dirs: Iterable[Path]) -> set:
    skip = {d.resolve() for d in exclude_dirs}
    used: set = set()
    for other in cache_root.iterdir():
        if other.is_dir() and other.name.startswith("models--") and other.resolve() not in skip:
            used.update(real_files(other).keys())
    return used


def delete_repo(repo: str, root: Optional[Path] = None) -> int:
    """리포 캐시 폴더를 지우고 폴더 밖 가중치도 함께 지운다(다른 리포가 쓰는 파일은 남긴다). 지운 바이트 수."""
    cache_root = (root or cache_dir()).resolve()
    entry = repo_dir(repo, cache_root)
    if not entry.is_dir():
        return 0
    entry = entry.resolve()
    inside_size = dir_size(entry)
    outside = {r: n for r, n in real_files(entry).items() if entry not in r.parents and cache_root in r.parents}
    shutil.rmtree(entry)
    freed = inside_size - sum(outside.values())
    if outside:
        still_used = _referenced_elsewhere(cache_root, [entry])
        for real, size in outside.items():
            if real not in still_used:
                try:
                    real.unlink()
                    freed += size
                    parent = real.parent
                    if parent != cache_root / "blobs" and not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass
    return max(freed, 0)


def delete_repo_files(repo: str, filenames: Iterable[str], root: Optional[Path] = None) -> int:
    """리포에서 **지정한 파일만** 지운다 — 같은 리포를 다른 도구가 다른 파일로 쓰고 있을 수 있다.
    남은 파일이 없으면 리포 폴더도 치운다. 지운 바이트 수를 돌려준다."""
    cache_root = (root or cache_dir()).resolve()
    entry = repo_dir(repo, cache_root)
    if not entry.is_dir():
        return 0
    entry = entry.resolve()
    names = set(filenames)
    freed = 0
    doomed_reals: dict = {}
    snaps = entry / "snapshots"
    if snaps.is_dir():
        for snap in snaps.iterdir():
            for name in names:
                p = snap / name
                if p.is_symlink() or p.exists():
                    try:
                        real = p.resolve()
                        if real.is_file():
                            doomed_reals[real] = real.stat().st_size
                        p.unlink()
                    except OSError:
                        pass
    # 아직 쓰이는 실파일 = 다른 리포가 가리키는 것 + **이 리포의 snapshots/ 에 남은 파일이 가리키는 것**.
    # (blobs/ 안의 파일 자체를 「쓰이는 중」으로 세면 일반 HF 캐시에서 아무것도 못 지운다 — 실제로 그랬다.)
    still_used = _referenced_elsewhere(cache_root, [entry])     # 이 리포 자신은 제외 — 자기 blobs를 「쓰이는 중」으로 세지 않는다
    if snaps.is_dir():
        for p in snaps.rglob("*"):
            try:
                if p.is_symlink() or p.is_file():
                    still_used.add(p.resolve())
            except OSError:
                pass
    for real, size in doomed_reals.items():
        if real not in still_used and cache_root in real.parents:
            try:
                real.unlink()
                freed += size
            except OSError:
                pass
    # 이 리포의 snapshots/ 에 파일이 하나도 안 남았으면 껍데기 폴더(고아 blobs 포함)를 정리한다.
    remaining = [p for p in snaps.rglob("*") if p.is_symlink() or p.is_file()] if snaps.is_dir() else []
    if not remaining:
        left = dir_size(entry)
        shutil.rmtree(entry, ignore_errors=True)
        freed += left
    return freed
