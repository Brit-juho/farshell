"""authz 저장소 — `~/.vt/authz/*.json` (0600, 디렉터리 0700, 단일 flock `authz.lock`).

문서 하나가 파일 하나다: grants · approvals · windows · shares · pairs · leases · sagas · meta,
그리고 peer 등록부 `principals/peers.json`. 쓰기는 전부 `txn()` 하나로 — 락 안에서 모두 읽고,
바뀐 문서만 tmp → os.replace 로 저장한다(`agent_share._txn` 패턴). 여러 문서를 한 락에 묶어야
"승인 = 요청 삭제 + grant 기록" 같은 변경이 원자적이다.

손상 파일(F1): 읽을 때 손상을 표시하고 빈 문서로 계속한다. 덮어쓰기 **전에** 원본 바이트를
`<name>.corrupt-<rand>`(0600)로 보존하고, 보존에 실패하면 저장을 중단한다(StoreError).
엔진은 손상 표시가 있는 snapshot 에서 agent·peer·pair·cli scope 를 fail closed 한다.

읽기 전용 `snapshot()`은 락을 잡지 않는다 — 쓰기가 os.replace 라 반쯤 쓴 파일을 볼 일이 없고,
shadow 판정이 다른 저장소 락(위임·hosts) 안에서 불려도 락 순서 문제가 생기지 않게.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import store_lock
import vt_paths

logger = logging.getLogger(__name__)

DOCS = ("grants", "approvals", "windows", "shares", "pairs", "leases", "sagas", "meta", "principals/peers")
# 문서별 빈 모양 — 목록 문서는 {"items": []}, 사전 문서는 {"items": {}}.
_DICT_DOCS = ("shares", "meta")
SCHEMA_VERSION = 1


class StoreError(Exception):
    def __init__(self, code: str, reason: str, status: int = 503):
        super().__init__(reason)
        self.code, self.reason, self.status = code, reason, status


def root() -> Path:
    return vt_paths.state_dir() / "authz"


def doc_path(name: str) -> Path:
    if name not in DOCS:
        raise ValueError(f"모르는 authz 문서: {name}")
    return root() / f"{name}.json"


def _empty(name: str) -> dict:
    return {"version": SCHEMA_VERSION, "items": {} if name in _DICT_DOCS else []}


def _ensure_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass


def _read(name: str) -> tuple[dict, bool]:
    """(문서, 손상 여부). 파일이 없으면 빈 문서·정상."""
    p = doc_path(name)
    if not p.is_file():
        return _empty(name), False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"authz {name}.json 읽기 실패({e}) — 빈 문서로 계속, 저장 전 원본 보존")
        return _empty(name), True
    want = dict if name in _DICT_DOCS else list
    if not isinstance(data, dict) or not isinstance(data.get("items"), want):
        return _empty(name), True
    if name not in _DICT_DOCS:
        data["items"] = [x for x in data["items"] if isinstance(x, dict)]
    return data, False


def _backup_corrupt(name: str) -> Path:
    p = doc_path(name)
    try:
        raw = p.read_bytes()
        b = p.with_name(f"{p.name}.corrupt-{uuid.uuid4().hex[:12]}")
        fd = os.open(str(b), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return b
    except OSError as e:
        raise StoreError("store_backup_failed", f"손상된 authz {name}.json 의 백업을 만들지 못해 저장을 중단했습니다") from e


def write_private(p: Path, data: bytes) -> None:
    """0600 tmp → fsync → os.replace. 부모 디렉터리는 0700."""
    _ensure_dir(p.parent)
    tmp = p.with_name(f"{p.name}.tmp-{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(p))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _save(name: str, data: dict) -> None:
    write_private(doc_path(name), (json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode())


@contextmanager
def locked():
    _ensure_dir(root())
    with store_lock.exclusive(root() / "authz.lock"):
        yield


@contextmanager
def txn():
    """`with txn() as docs:` — docs[name]["items"] 를 고치면 빠져나올 때 바뀐 문서만 저장된다.
    예외로 빠져나오면 아무것도 저장하지 않는다."""
    with locked():
        docs: dict[str, dict] = {}
        before: dict[str, str] = {}
        corrupt: set[str] = set()
        for n in DOCS:
            d, bad = _read(n)
            docs[n] = d
            before[n] = json.dumps(d, sort_keys=True)
            if bad:
                corrupt.add(n)
        yield docs
        for n in DOCS:
            if json.dumps(docs[n], sort_keys=True) == before[n]:
                continue
            if n in corrupt and doc_path(n).exists():
                _backup_corrupt(n)
            _save(n, docs[n])


# --- 읽기 전용 snapshot -----------------------------------------------------------------


@dataclass
class Snapshot:
    grants: list = field(default_factory=list)
    approvals: list = field(default_factory=list)
    windows: list = field(default_factory=list)
    shares: dict = field(default_factory=dict)
    corrupt: frozenset = frozenset()
    now: float = 0.0
    source: str = "store"            # store | live | empty — 어디서 만든 snapshot 인가(감사·진단용)

    def deny_rules(self) -> list:
        return [g for g in self.grants if g.get("effect") == "deny"]


_cache: dict[str, tuple[tuple, dict, bool]] = {}


def _read_cached(name: str) -> tuple[dict, bool]:
    """mtime·크기가 같으면 다시 파싱하지 않는다(shadow 가 요청마다 부른다)."""
    p = doc_path(name)
    try:
        st = p.stat()
        sig = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return _empty(name), False
    hit = _cache.get(name)
    if hit and hit[0] == sig:
        return json.loads(json.dumps(hit[1])), hit[2]
    d, bad = _read(name)
    _cache[name] = (sig, d, bad)
    return json.loads(json.dumps(d)), bad


def snapshot(now: Optional[float] = None) -> Snapshot:
    corrupt = set()
    out = {}
    for n in ("grants", "approvals", "windows", "shares"):
        d, bad = _read_cached(n)
        out[n] = d["items"]
        if bad:
            corrupt.add(n)
    return Snapshot(grants=out["grants"], approvals=out["approvals"], windows=out["windows"],
                    shares=out["shares"], corrupt=frozenset(corrupt),
                    now=time.time() if now is None else now, source="store")


def exists() -> bool:
    return any(doc_path(n).is_file() for n in DOCS)
