"""등록 기기 화이트리스트 — `~/.vt/devices.json`.

로그인은 **항상 비밀번호**로 한다. 기기 등록은 "처음 보는 기기인가"를 가리는
별개의 관문이고, 한 번 등록된 기기는 장기 쿠키로 이후 비밀번호만으로 통과한다
(폰이 LTE↔wifi를 오가도 안 끊기도록 IP가 아니라 기기 단위로 신뢰한다).

저장하는 건 쿠키 원문이 아니라 **sha256 해시뿐**이다 — devices.json이 통째로
새도 쿠키를 만들어낼 수 없다.

수명(2026-10 추가):
- 레코드마다 `expires_at`(절대 시각)을 등록 시점에 확정한다. 기본은 `DEVICE_TTL`(90일),
  로그인 화면이나 `fsh mobile --temp`로 고른 임시 기기는 그보다 짧다. 그 전의
  레코드에는 이 칸이 없어서 `added_at + DEVICE_TTL`로 계산한다 — 기존 동작 그대로.
- 만료 판정은 `_alive` **한 곳**에서만 한다. 쿠키 검증(`verify_device`)과 세션 검증
  (`auth.session_device`)이 같은 함수를 봐야 "기기는 만료됐는데 세션은 24시간 더
  산다"는 틈이 안 생긴다 — 예전엔 세션 쪽이 존재 여부만 봤다.
- 만료된 레코드는 `DEVICE_EXPIRED_GRACE`(7일) 동안 목록에 "만료"로 남았다가 다음
  저장 때 지워진다. 그 기기의 설정 파일(device-settings/<id>.json)도 같이 지운다.

`ua`·`client`·`ip_*`·`via`는 목록에서 기기를 알아보기 위한 표시용이다. 인증에
쓰지 않는다(auth/client_info.py 머리말).

동시 수정(2026-11): devices.json을 **읽고-고치고-쓰는** 모든 경로는 락 하나(`_locked`, 락 전용 파일
`devices.json.lock`에 `store_lock.exclusive`)를 잡고 그 안에서 다시 읽는다. `verify_device`의 `last_seen`
갱신이 스레드에서(공유 링크 라우트의 `asyncio.to_thread`) 돌면서 루프의 `revoke_device`/`register_device`와
겹치면, 락 없이는 먼저 읽어 둔 목록을 그대로 저장해 **폐기한 기기를 되살리거나 막 등록한 기기를 지웠다.**
flock은 열린 파일 단위라 같은 프로세스의 스레드끼리도, `fsh device` CLI와도 서로 막는다. 재진입하지 않으므로
락은 공개 함수에서만 잡고 `_save_devices`는 잡지 않는다. 읽기만 하는 경로(검증·목록)는 락 없이 읽는다 —
쓰기는 원자적 교체(`fileio._write_json_secure`)라 반쯤 쓴 파일을 볼 일이 없다.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from contextlib import contextmanager
from typing import Optional

import store_lock

# ⚠ 경로·설정은 **호출 시점에** `auth.X`로 읽는다. 모듈 상단에서
# `from auth import DEVICES_PATH`로 당겨오면 그 순간 값이 굳어서, 테스트가
# `auth.DEVICES_PATH`를 monkeypatch해도 여기는 계속 진짜 ~/.vt를 본다 —
# **테스트는 통과하면서 사용자의 실제 파일을 건드린다.** 조용히 틀리는
# 종류라 가장 나쁘고, server/tests/test_auth_isolation.py가 이 성질을
# 실제로 확인한다.
import auth
from auth import fileio

# device_settings.py의 파일명 규칙과 같다. 그 모듈은 `import auth`를 하므로 여기서
# 거꾸로 import하지 않고 경로만 같은 규칙으로 만든다.
_SAFE_ID = re.compile(r"^[a-z0-9]{1,32}$")

# 메타는 하루 단위로만 갱신한다(last_seen과 같은 주기) — 매 요청 디스크 쓰기를 피한다.
_SEEN_INTERVAL = 86400


def _expires_at(d: dict) -> int:
    """레코드의 만료 시각. `expires_at`이 없는 예전 레코드는 등록일 + DEVICE_TTL."""
    try:
        exp = int(d.get("expires_at") or 0)
    except (TypeError, ValueError):
        exp = 0
    if exp > 0:
        return exp
    try:
        added = int(d.get("added_at") or 0)
    except (TypeError, ValueError):
        added = 0
    return added + auth.DEVICE_TTL


def _alive(d: dict, now: int) -> bool:
    return now < _expires_at(d)


def device_remaining(d: dict, now: int | None = None) -> int:
    """남은 수명(초). 만료됐으면 0."""
    now = int(time.time()) if now is None else now
    return max(0, _expires_at(d) - now)


def _public(d: dict, now: int) -> dict:
    """밖으로 내보낼 형태 — 해시를 빼고 계산값(expires_at, expired)을 채운다."""
    out = {k: v for k, v in d.items() if k != "hash"}
    out["expires_at"] = _expires_at(d)
    out["expired"] = not _alive(d, now)
    return out


def _drop_device_settings(device_ids: list) -> None:
    base = auth.STATE_DIR / "device-settings"
    for did in device_ids:
        if not _SAFE_ID.match(did or ""):
            continue
        try:
            (base / f"{did}.json").unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _split_stale(devices: list, now: int, grace: int) -> tuple[list, list]:
    """(남길 것, 지울 것). 만료 후 `grace`초가 지난 레코드가 지울 대상이다."""
    keep, stale = [], []
    for d in devices:
        (stale if now >= _expires_at(d) + grace else keep).append(d)
    return keep, stale


@contextmanager
def _locked():
    """devices.json 읽고-고치고-쓰기 구간. 락 파일은 데이터 파일 옆(`devices.json.lock`)에 두고 지우지 않는다
    (store_lock 머리말). 기한(`store_lock.DEFAULT_DEADLINE`)을 넘기면 `store_lock.LockTimeout`."""
    path = auth.DEVICES_PATH
    with store_lock.exclusive(path.with_name(path.name + ".lock")):
        yield


def _load_devices() -> list:
    data = fileio._read_json(auth.DEVICES_PATH, {})
    devices = data.get("devices") if isinstance(data, dict) else None
    return devices if isinstance(devices, list) else []


def _save_devices(devices: list) -> None:
    """저장 + 보관 기간이 지난 만료 레코드 정리.

    정리를 저장 경로에 두는 이유: 별도 주기 작업 없이도 등록·갱신·폐기 어느 쓰기든
    한 번 일어나면 오래된 기록이 빠진다. 서버가 쓰기 없이 오래 떠 있어도
    `list_devices`가 같은 기준으로 걸러 보여 주므로 화면은 어긋나지 않는다.
    """
    keep, stale = _split_stale(devices, int(time.time()), auth.DEVICE_EXPIRED_GRACE)
    fileio._write_json_secure(auth.DEVICES_PATH, {"version": 1, "devices": keep})
    if stale:
        _drop_device_settings([d.get("id", "") for d in stale])


def _find_device(device_id: str) -> Optional[dict]:
    for d in _load_devices():
        if d.get("id") == device_id:
            return d
    return None


def find_alive_device(device_id: str) -> Optional[dict]:
    """id로 기기를 찾되 만료됐으면 None. 세션 검증이 쓴다."""
    d = _find_device(device_id)
    if d is None or not _alive(d, int(time.time())):
        return None
    return d


def register_device(
    label: str = "",
    *,
    ttl: int | None = None,
    method: str = "",
    meta: dict | None = None,
) -> tuple[str, str]:
    """새 기기 등록 → (쿠키에 심을 secret 원문, device_id).

    저장하는 건 sha256 해시뿐이다. devices.json이 통째로 새도 쿠키를 만들어낼 수 없다
    — 비밀번호를 scrypt 해시로만 두는 것과 같은 원칙.

    `ttl`은 기본 `DEVICE_TTL`이고 그보다 길게는 만들 수 없다(호출부가 허용값을 먼저
    거르지만, 여기서도 한 번 더 자른다 — 수명을 늘리는 경로가 하나라도 생기면 안 된다).
    """
    life = auth.DEVICE_TTL if ttl is None else int(ttl)
    if life <= 0:
        raise ValueError("ttl must be positive")
    life = min(life, auth.DEVICE_TTL)
    secret = secrets.token_hex(32)
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    device_id = digest[:16]
    now = int(time.time())
    m = meta or {}
    rec = {
        "id": device_id,
        "hash": digest,
        "label": (label or "기기")[:60],
        "added_at": now,
        "last_seen": now,
        "expires_at": now + life,
        "kind": "temporary" if life < auth.DEVICE_TTL else "persistent",
        "method": method or "",
        "ua": str(m.get("ua") or ""),
        "client": m.get("client") if isinstance(m.get("client"), dict) else {},
        "ip_first": str(m.get("ip") or ""),
        "ip_last": str(m.get("ip") or ""),
        "ip_seen_at": now,
        "via": str(m.get("via") or ""),
    }
    with _locked():
        devices = [d for d in _load_devices() if d.get("id") != device_id]
        devices.append(rec)
        _save_devices(devices)
    return secret, device_id


def verify_device(secret: str, meta: dict | None = None) -> Optional[dict]:
    """`vt_device` 쿠키 값 → 등록된 기기 레코드. 미등록/만료면 None.

    `meta`(client_info.request_meta)를 주면 마지막 접속 IP·경로도 하루 단위로 갱신한다.
    IP를 알 수 있는 호출부(로그인·인증 상태 조회)만 넘긴다.
    """
    if not secret:
        return None
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    now = int(time.time())
    d = _match(_load_devices(), digest)   # 대부분의 요청은 여기서 끝난다 — 락 없이 읽기만
    if d is None or not _alive(d, now):
        return None
    updates = {}
    if now - int(d.get("last_seen", 0) or 0) > _SEEN_INTERVAL:
        updates["last_seen"] = now
    ip = str((meta or {}).get("ip") or "")
    if ip and now - int(d.get("ip_seen_at", 0) or 0) > _SEEN_INTERVAL:
        updates.update(ip_last=ip, via=str((meta or {}).get("via") or ""), ip_seen_at=now)
    if not updates:
        return d
    try:
        # 위에서 읽은 목록을 그대로 저장하면 그 사이의 폐기·등록을 덮어쓴다 — 락 안에서 다시 읽고 이 기기만 고친다.
        with _locked():
            devices = _load_devices()
            cur = _match(devices, digest)
            if cur is None:
                return None   # 그 사이에 폐기됐다 — 되살리지 않는다
            cur.update(updates)
            _save_devices(devices)
            return cur
    except (OSError, store_lock.LockTimeout):
        # 표시용 메타 갱신일 뿐이다 — 저장을 못 해도 인증 판정은 위에서 읽은 그대로다.
        d.update(updates)
        return d


def _match(devices: list, digest: str) -> Optional[dict]:
    for d in devices:
        stored = d.get("hash", "")
        if stored and hmac.compare_digest(stored, digest):
            return d
    return None


def list_devices() -> list:
    """등록 기기 목록(해시 제외). 보관 기간이 지난 만료 레코드는 빼고 보여 준다."""
    now = int(time.time())
    keep, _ = _split_stale(_load_devices(), now, auth.DEVICE_EXPIRED_GRACE)
    return [_public(d, now) for d in sorted(keep, key=lambda x: x.get("added_at", 0))]


def prune_devices() -> list:
    """만료된 기기를 보관 기간과 무관하게 지금 정리한다(`fsh device prune`). 지운 목록 반환."""
    now = int(time.time())
    with _locked():
        devices = _load_devices()
        keep = [d for d in devices if _alive(d, now)]
        removed = [d for d in devices if not _alive(d, now)]
        if removed:
            fileio._write_json_secure(auth.DEVICES_PATH, {"version": 1, "devices": keep})
    if removed:
        _drop_device_settings([d.get("id", "") for d in removed])
    return [_public(d, now) for d in removed]


def rename_device(prefix: str, label: str) -> Optional[dict]:
    """id 접두사로 기기 별명 변경. 못 찾거나 여럿이면 None.

    자동 라벨(`_device_label`이 UA에서 뽑는 "iPhone"/"Mac")만으로는 같은 기종이
    여럿이면 구분이 안 된다 — 목록에서 어느 행이 어느 기기인지 알 수 있어야
    `fsh device revoke`를 안심하고 쓸 수 있다.

    **CLI 전용이다.** 웹에서 바꾸게 하지 않는다 — routes/security.py가 "보안 탭은
    읽기 전용"을 불변식으로 두고 있고(프런트 테스트로도 고정), 별명이 인증 수단은
    아니지만 그 탭에 입력칸을 하나 여는 순간 그 불변식이 무너진다.
    """
    prefix = (prefix or "").strip().lower()
    if not prefix:
        return None
    with _locked():
        devices = _load_devices()
        hits = [d for d in devices if d.get("id", "").startswith(prefix)]
        if len(hits) != 1:
            return None
        hits[0]["label"] = (label or "").strip()[:60] or hits[0].get("label", "기기")
        _save_devices(devices)
    return _public(hits[0], int(time.time()))


def revoke_device(prefix: str) -> list:
    """id 접두사로 기기 폐기. 폐기된 기기 목록 반환(해당 기기의 세션도 함께 죽는다)."""
    prefix = (prefix or "").strip().lower()
    if not prefix:
        return []
    with _locked():
        devices = _load_devices()
        removed = [d for d in devices if d.get("id", "").startswith(prefix)]
        if removed:
            _save_devices([d for d in devices if d not in removed])
    now = int(time.time())
    return [_public(d, now) for d in removed]
