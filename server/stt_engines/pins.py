"""모델 고정(pin) — 카탈로그 모델을 「이름」이 아니라 커밋 해시(revision)와 파일별 SHA-256으로 받고 쓴다.

표는 `model_pins.json` 한 곳: `{repo: {"revision": 40자 커밋, "files": {파일: [바이트, sha256]}}}`.
HF API(`/api/models/<repo>`의 sha, `/tree/main`의 LFS oid)에서 가져왔고, LFS가 아닌 작은 파일은 그 커밋의 파일을
받아 직접 해시했다. 표를 갱신하는 것은 모델을 바꾸는 결정이므로 사람이 검토하는 코드 변경으로만 한다.

규칙:
- 받기: `snapshot_download(repo, revision=<커밋>, allow_patterns=<표의 파일>)` 후 모든 파일의 SHA-256을 대조한다.
  불일치하면 **그 커밋 스냅샷의 불일치 파일만** 지우고(다른 revision 스냅샷·사용자의 옛 캐시는 건드리지 않는다)
  `PinError`로 알린다.
- 쓰기(load): 표의 파일이 전부 있고 해시가 맞는 스냅샷만 쓴다. 옛 revision 캐시도 해시가 맞으면 재사용한다.
  해시 결과는 (크기, mtime)과 함께 상태 폴더의 표식 파일에 적어 두어 같은 파일을 다시 해시하지 않는다.
- 표에 없는 리포(직접 입력한 모델)는 고정하지 않는다 — 호출자가 `pin_for() is None`으로 가려 옛 동작을 쓴다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import hfcache
from .base import NotReady
from i18n import tr

logger = logging.getLogger(__name__)

_PINS_FILE = Path(__file__).with_name("model_pins.json")
_CHUNK = 4 * 1024 * 1024


class PinError(NotReady):
    """고정된 모델 파일이 표와 다르다(해시·크기 불일치, 빠진 파일) — 메시지는 사용자에게 그대로 보인다."""


@dataclass(frozen=True)
class Pin:
    repo: str
    revision: str
    files: dict          # 파일 → (바이트, sha256)


def _load() -> dict:
    raw = json.loads(_PINS_FILE.read_text(encoding="utf-8"))
    out = {}
    for repo, ent in raw.items():
        files = {n: (int(v[0]), str(v[1]).lower()) for n, v in ent["files"].items()}
        out[repo] = Pin(repo, ent["revision"], files)
    return out


PINS: dict = _load()


def pin_for(repo: str) -> Optional[Pin]:
    return PINS.get(repo)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 표식(이미 검증한 파일) ──────────────────────────────────────────

def _marker_path() -> Path:
    import vt_paths
    return vt_paths.state_dir() / "engines" / "pin_verified.json"


def _read_marker() -> dict:
    try:
        data = json.loads(_marker_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_marker(data: dict) -> None:
    p = _marker_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        logger.warning("모델 검증 표식을 쓰지 못했습니다(다음에 다시 해시합니다): %s", e)


def _stat_key(real: Path) -> list:
    st = real.stat()
    return [st.st_size, st.st_mtime_ns]


# ── 스냅샷 탐색 ─────────────────────────────────────────────────────

def _snapshots(pin: Pin, root: Optional[Path]) -> list:
    """후보 스냅샷 폴더: 고정 revision 먼저, 나머지는 최근 순."""
    snaps = hfcache.repo_dir(pin.repo, root) / "snapshots"
    if not snaps.is_dir():
        return []
    dirs = [p for p in snaps.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: (p.name != pin.revision, -p.stat().st_mtime))
    return dirs


def _complete(pin: Pin, snap: Path) -> Optional[dict]:
    """표의 파일이 전부 있고 크기가 맞으면 {이름: 실파일 경로}. 해시는 보지 않는다."""
    out = {}
    for name, (size, _sha) in pin.files.items():
        p = snap / name
        try:
            real = p.resolve()
            if not real.is_file() or real.stat().st_size != size:
                return None
        except OSError:
            return None
        out[name] = real
    return out


def complete_snapshot(repo: str, root: Optional[Path] = None) -> Optional[Path]:
    """받기가 끝난(파일·크기 일치) 스냅샷. 해시는 보지 않는다 — 상태 표시용으로 가볍다."""
    pin = pin_for(repo)
    if pin is None:
        return None
    return next((s for s in _snapshots(pin, root) if _complete(pin, s) is not None), None)


def _drop_files(pin: Pin, snap: Path, names, root: Optional[Path]) -> None:
    """고정 revision 스냅샷에서 **지정 파일만** 지운다. 다른 스냅샷이 가리키는 blob은 남긴다."""
    entry = hfcache.repo_dir(pin.repo, root)
    others = [s for s in (entry / "snapshots").iterdir() if s.is_dir() and s != snap]
    used = set()
    for s in others:
        used.update(hfcache.real_files(s).keys())
    for name in names:
        p = snap / name
        try:
            real = p.resolve() if (p.is_symlink() or p.exists()) else None
            if p.is_symlink() or p.exists():
                p.unlink()
            if real is not None and real.is_file() and real not in used:
                real.unlink()
        except OSError:
            pass


def _verify(pin: Pin, snap: Path, files: dict, marker: dict) -> list:
    """해시가 다른 파일 이름들. 맞은 파일은 표식에 적는다(호출자가 저장)."""
    bad = []
    ent = marker.setdefault(pin.repo, {}).setdefault(snap.name, {})
    for name, real in files.items():
        want = pin.files[name][1]
        key = _stat_key(real)
        rec = ent.get(name)
        if rec and rec[:2] == key and rec[2] == want:
            continue
        if sha256_file(real) == want:
            ent[name] = key + [want]
        else:
            ent.pop(name, None)
            bad.append(name)
    return bad


def verified_snapshot(repo: str, root: Optional[Path] = None) -> Optional[Path]:
    """해시까지 맞는 스냅샷 폴더. 없으면 None(받아야 함).

    고정 revision 스냅샷이 표와 다르면 그 불일치 파일을 지우고 `PinError`. 옛 revision 스냅샷이 다르면
    조용히 건너뛴다(사용자 캐시이므로 지우지 않는다)."""
    pin = pin_for(repo)
    if pin is None:
        return None
    marker = _read_marker()
    before = json.dumps(marker, sort_keys=True)
    try:
        for snap in _snapshots(pin, root):
            files = _complete(pin, snap)
            if files is None:
                continue
            bad = _verify(pin, snap, files, marker)
            if not bad:
                return snap
            if snap.name == pin.revision:
                _drop_files(pin, snap, bad, root)
                raise PinError(tr(
                    "받은 모델 파일이 고정된 해시와 다릅니다({files}) — 손상됐거나 바뀐 파일이라 지웠습니다. 다시 받아 주세요",
                    files=", ".join(sorted(bad))))
        return None
    finally:
        if json.dumps(marker, sort_keys=True) != before:
            _write_marker(marker)


def _drop_wrong_size(pin: Pin, root: Optional[Path]) -> None:
    """고정 revision 스냅샷에 크기가 다른(잘린·손상) 파일이 있으면 지운다 — HF는 blob 이름(etag)만 보고
    「이미 있다」고 넘어가므로 그대로 두면 다시 받지 않는다."""
    snap = hfcache.repo_dir(pin.repo, root) / "snapshots" / pin.revision
    if not snap.is_dir():
        return
    wrong = []
    for name, (size, _sha) in pin.files.items():
        p = snap / name
        try:
            if (p.is_symlink() or p.exists()) and (not p.resolve().is_file() or p.resolve().stat().st_size != size):
                wrong.append(name)
        except OSError:
            wrong.append(name)
    if wrong:
        _drop_files(pin, snap, wrong, root)


def download_pinned(repo: str, root: Optional[Path] = None) -> Path:
    """고정 revision을 받고 해시를 검증한 스냅샷 폴더를 돌려준다. 이미 검증된 캐시가 있으면 네트워크를 쓰지 않는다."""
    pin = pin_for(repo)
    if pin is None:
        raise PinError(tr("고정된 모델 표에 없는 리포입니다: {repo}", repo=repo))
    try:
        snap = verified_snapshot(repo, root)
    except PinError:
        snap = None                       # 손상 파일은 이미 지웠다 — 아래에서 다시 받는다
    if snap is not None:
        return snap
    _drop_wrong_size(pin, root)
    from huggingface_hub import snapshot_download
    snapshot_download(repo, revision=pin.revision, allow_patterns=sorted(pin.files),
                      cache_dir=str(root) if root else None)
    snap = verified_snapshot(repo, root)
    if snap is None:
        raise PinError(tr("모델을 받았지만 고정된 파일 목록과 맞지 않습니다({repo}) — 다시 받아 주세요", repo=repo))
    return snap
