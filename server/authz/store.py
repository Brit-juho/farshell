"""authz 저장소 — `~/.vt/authz/*.json` (0600, 디렉터리 0700, 단일 flock `authz.lock`).

문서 하나가 파일 하나다: grants · approvals · windows · shares · pairs · leases · sagas · meta,
그리고 peer 등록부 `principals/peers.json`. 쓰기는 전부 `txn()` 하나로 — 락 안에서 모두 읽고,
바뀐 문서만 tmp → os.replace 로 저장한다(`agent_share._txn` 패턴). 여러 문서를 한 락에 묶어야
"승인 = 요청 삭제 + grant 기록" 같은 변경이 원자적이다.

읽을 수 없거나(권한·I/O) 파싱할 수 없는 문서(손상 파일): 읽을 때 손상을 표시하고 빈 문서로 **읽기만** 계속한다.
그 문서를 **바꾸는** 쓰기는 거부한다(StoreError `store_unreadable`, 503) — 빈 문서로 덮으면 모든 grant 가 사라진다.
거부할 때 원본 바이트를 `<name>.corrupt-<rand>`(0600)로 한 번 복사해 두지만(같은 내용이면 새로 만들지 않는다)
손상 파일 자체는 지우지도 고치지도 않는다. 사람이 파일을 고치거나 `fsh migrate-auth --restore` 로 되돌린다.
저장 대상이 아닌 다른 문서의 손상은 쓰기를 막지 않는다. 한 트랜잭션이 거부되면 어떤 문서도 저장하지 않는다.
엔진은 손상 표시가 있는 snapshot 에서 agent·peer·local(cli) scope 를 fail closed 한다.

**스키마 2**: grant·approval 은 17개 scope + 조건(`sconds`) 형태로 저장한다(레코드의 `schema: 2`, 문서의 `version: 2`).
`schema` 가 2가 아닌 레코드(옛 67개 이름)는 **읽을 때** `scopes.upgrade_record` 로 변환하고, 바뀐 문서를 쓸 때 새 형태로 쓴다.
옛 형태 문서를 처음 덮어쓰기 직전에 `fsh migrate-auth` 백업(`pre-schema2`)을 한 번 만든다 — 커밋을 되돌린 뒤
`fsh migrate-auth --restore <ts>` 로 옛 형태를 되살릴 수 있다. 감사 체인의 과거 기록은 바꾸지 않는다.

읽기 전용 `snapshot()`은 락을 잡지 않는다 — 쓰기가 os.replace 라 반쯤 쓴 파일을 볼 일이 없고,
shadow 판정이 다른 저장소 락(위임·hosts) 안에서 불려도 락 순서 문제가 생기지 않게.
"""

from __future__ import annotations

import contextlib
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

from . import scopes as S
from .errors import ShareError

logger = logging.getLogger(__name__)

DOCS = ("grants", "approvals", "windows", "shares", "pairs", "leases", "sagas", "meta", "principals/peers")
# 문서별 빈 모양 — 목록 문서는 {"items": []}, 사전 문서는 {"items": {}}.
_DICT_DOCS = ("shares", "meta")
SCHEMA_VERSION = 2
_UPGRADED_DOCS = ('grants', 'approvals')     # 레코드에 scope 가 들어 있는 문서


class StoreError(ShareError):
    """저장소 오류 — `ShareError` 의 한 종류라 라우트가 코드·상태를 그대로 JSON 으로 돌려준다."""

    def __init__(self, code: str, reason: str, status: int = 503, **extra):
        super().__init__(code, reason, status, **extra)


def root() -> Path:
    return vt_paths.state_dir() / "authz"


def doc_path(name: str) -> Path:
    if name not in DOCS:
        raise ValueError(f"모르는 authz 문서: {name}")
    return root() / f"{name}.json"


def _empty(name: str) -> dict:
    return {"version": SCHEMA_VERSION, "items": {} if name in _DICT_DOCS else []}


def _ensure_dir(d: Path) -> None:
    """`d` 와 새로 만든 모든 상위 디렉터리를 정확히 0700 으로 둔다(umask·부모 모드와 무관).

    `mkdir(parents=True, mode=...)` 는 마지막 단계에만 mode 를 쓰고 그마저 umask 를 받는다 — 그래서 만든 디렉터리마다
    직접 chmod 한다. 이미 있던 `authz/` 아래 디렉터리도 0700 으로 되돌린다(authz 밖의 기존 상위는 건드리지 않는다)."""
    d = Path(d)
    made = []
    p = d
    while not p.exists() and p != p.parent:
        made.append(p)
        p = p.parent
    for q in reversed(made):
        try:
            q.mkdir(mode=0o700)
        except FileExistsError:
            pass
    todo = list(made)
    if d not in todo:
        todo.append(d)
    try:
        r = root()
        if r == d or r in d.parents:
            for q in (d, *d.parents):
                if q not in todo:
                    todo.append(q)
                if q == r:
                    break
    except Exception:  # noqa: BLE001
        pass
    for q in todo:
        try:
            os.chmod(q, 0o700)
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
    if name in _UPGRADED_DOCS:
        data["items"] = [S.upgrade_record(x) for x in data["items"]]
    return data, False


def diagnose(name: str) -> Optional[str]:
    """문서를 읽을 수 없는 이유(사람이 읽는 한 줄). 정상이거나 파일이 없으면 None."""
    p = doc_path(name)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        return f"읽기 실패: {e.strerror or e}"
    except ValueError as e:
        return f"JSON 파싱 실패: {e}"
    want = dict if name in _DICT_DOCS else list
    if not isinstance(data, dict) or not isinstance(data.get("items"), want):
        return "모양이 올바르지 않음(items 없음)"
    return None


def corrupt_copies(name: str) -> list[Path]:
    p = doc_path(name)
    try:
        return sorted(p.parent.glob(f"{p.name}.corrupt-*"))
    except OSError:
        return []


def preserve_copy(name: str) -> Optional[Path]:
    """손상 원본의 바이트를 `<name>.corrupt-<rand>`(0600)로 복사해 둔다. 같은 내용의 복사본이 이미 있으면 그것을 돌려준다.
    원본은 건드리지 않는다. 복사에 실패하면 None(쓰기 거부는 복사와 무관하게 유지된다)."""
    p = doc_path(name)
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    for old in corrupt_copies(name):
        try:
            if old.read_bytes() == raw:
                return old
        except OSError:
            continue
    try:
        b = p.with_name(f"{p.name}.corrupt-{uuid.uuid4().hex[:12]}")
        fd = os.open(str(b), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return b
    except OSError as e:
        logger.warning(f"손상된 authz {name}.json 의 복사본을 만들지 못했다: {e}")
        return None


def unreadable_error(names) -> StoreError:
    """쓰기를 거부하는 오류 — 어느 문서가 왜 막혔는지와 복구 방법을 사람 말로."""
    from i18n import tr
    names = sorted(names)
    detail = "; ".join(f"{n}.json ({diagnose(n) or '읽을 수 없음'})" for n in names)
    return StoreError("store_unreadable",
                      tr("권한 저장소를 읽을 수 없어 변경을 거부했습니다 — 파일은 그대로 두었습니다: {detail}. "
                         "복구: 파일을 고치거나 'fsh migrate-auth --status' 로 확인하세요", detail=detail),
                      503, docs=names)


def unreadable_report() -> list[dict]:
    """읽을 수 없는 문서 목록 `[{name, path, reason, copies}]` — `fsh migrate-auth --status`·`fsh doctor` 가 쓴다."""
    out = []
    for n in DOCS:
        why = diagnose(n)
        if why:
            out.append({"name": n, "path": str(doc_path(n)), "reason": why,
                        "copies": [str(c) for c in corrupt_copies(n)]})
    return out


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
def txn(held: bool = False):
    """`with txn() as docs:` — docs[name]["items"] 를 고치면 빠져나올 때 바뀐 문서만 저장된다.
    예외로 빠져나오면 아무것도 저장하지 않는다.

    `held=True` 는 호출자가 이미 `locked()` 를 쥐고 있다는 뜻이다(flock 은 같은 프로세스의 새 fd 와도 충돌해
    다시 잡으면 스스로 데드락한다). 위임 장부가 쓴다 — authz 락 → 장부 락 순서로 둘을 쥔 채 장부를 쓴다."""
    with (contextlib.nullcontext() if held else locked()):
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
        for n in _UPGRADED_DOCS:                  # 옛 이름으로 덧붙인 레코드도 새 형태로 쓴다
            docs[n]["items"] = [S.upgrade_record(x) for x in docs[n]["items"]]
        changed = [n for n in DOCS if json.dumps(docs[n], sort_keys=True) != before[n]]
        refused = [n for n in changed if n in corrupt and doc_path(n).exists()]
        if refused:                               # 읽지 못한 문서를 빈 문서로 덮지 않는다 — 아무것도 저장하지 않는다
            for n in refused:
                preserve_copy(n)
            raise unreadable_error(refused)
        for n in changed:
            if n in _UPGRADED_DOCS and _on_disk_schema(n) not in (None, SCHEMA_VERSION):
                _pre_upgrade_backup()
            docs[n]["version"] = SCHEMA_VERSION
            _save(n, docs[n])


# --- 스키마 --------------------------------------------------------------------------------

_prebackup_done: set[str] = set()


def _on_disk_schema(name: str) -> Optional[int]:
    """파일에 적힌 문서 버전. 파일이 없으면 None, 읽을 수 없으면 0."""
    p = doc_path(name)
    if not p.is_file():
        return None
    try:
        v = json.loads(p.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return 0
    return v if isinstance(v, int) else 0


def _pre_upgrade_backup() -> None:
    """옛 형태 문서를 새 형태로 처음 덮어쓰기 전에 되돌림용 백업을 한 번 만든다(프로세스·저장소 경로당 한 번). 실패해도 쓰기를 막지 않는다."""
    key = str(root())
    if key in _prebackup_done:
        return
    _prebackup_done.add(key)
    try:
        from . import migrate
        migrate.backup(tmux=None, tag="pre-schema2")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"스키마 2 전환 전 백업 실패({e}) — 계속 진행")


def schema_report() -> dict:
    """문서별 스키마 현황 — `{문서: {version, items, legacy_items}}`. 레코드가 `schema: 2` 가 아니면 옛 이름(변환 대상)이다."""
    out = {}
    for n in _UPGRADED_DOCS:
        p = doc_path(n)
        if not p.is_file():
            out[n] = {"version": None, "items": 0, "legacy_items": 0}
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            items = [x for x in d.get("items", []) if isinstance(x, dict)]
            out[n] = {"version": d.get("version"), "items": len(items),
                      "legacy_items": sum(1 for x in items if x.get("schema") != S.SCHEMA)}
        except (OSError, ValueError, AttributeError):
            out[n] = {"version": None, "items": 0, "legacy_items": 0, "corrupt": True}
    return out


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
