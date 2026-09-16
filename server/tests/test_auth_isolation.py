"""auth 패키지의 **테스트 격리가 실제로 성립하는지** 확인한다.

## 왜 이 파일이 따로 있나

`auth`는 774줄짜리 한 파일이었다가 패키지로 갈라졌다. 그 분할에는 조용히
틀리는 함정이 하나 있다:

    # auth/devices.py — 이렇게 쓰면 안 된다
    from auth import DEVICES_PATH          # ← 값이 import 시점에 굳는다

이러면 테스트가 `monkeypatch.setattr(auth, "DEVICES_PATH", tmp)`를 해도
devices.py는 계속 진짜 `~/.vt/devices.json`을 본다. 그런데 **테스트는 그대로
통과한다** — 기기가 등록되고 조회되니까. 다만 그게 사용자의 실제 파일에서
일어난다. 기존 테스트 10여 개는 이 상태를 전혀 구분하지 못한다.

그래서 여기서는 기능이 아니라 **"쓴 것이 임시 디렉토리에 있고, 진짜 홈에는
없다"**를 직접 본다. 하위 모듈이 실수로 값을 굳히는 순간 이 파일이 깨진다.

`~/.vt`를 읽거나 쓰는 하위 도메인 셋(기기·TOTP·티켓)을 전부 덮는다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import auth


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """기존 테스트 픽스처 10여 곳이 쓰는 것과 **똑같은 방식**으로 격리한다.
    이 파일의 값어치는 그 방식이 진짜로 먹는지를 확인하는 데 있으므로,
    더 강한 방법(예: HOME 통째 교체)을 쓰면 안 된다."""
    state = tmp_path / "vt"
    monkeypatch.setattr(auth, "STATE_DIR", state)
    monkeypatch.setattr(auth, "DEVICES_PATH", state / "devices.json")
    monkeypatch.setattr(auth, "TOTP_PATH", state / "totp.json")
    monkeypatch.setattr(auth, "TICKETS_PATH", state / "tickets.json")
    return state


def _real_home_vt() -> Path:
    return Path(os.path.expanduser("~")) / ".vt"


def test_device_registration_lands_in_the_patched_path(isolated):
    auth.register_device("probe")
    assert (isolated / "devices.json").is_file(), (
        "기기가 monkeypatch한 경로에 안 쓰였다 — 하위 모듈이 DEVICES_PATH를 "
        "import 시점에 굳혔을 가능성이 높다")


def test_device_reads_come_from_the_patched_path(isolated):
    """쓰기만 격리되고 읽기가 진짜 파일을 보면 목록에 남의 기기가 섞인다."""
    assert auth.list_devices() == []
    auth.register_device("probe")
    labels = [d.get("label") for d in auth.list_devices()]
    assert labels == ["probe"]


def test_totp_state_lands_in_the_patched_path(isolated):
    auth.totp_new_secret()
    assert (isolated / "totp.json").is_file()
    assert auth.totp_enabled() is True


def test_ticket_lands_in_the_patched_path(isolated):
    auth.issue_ticket("probe")
    assert (isolated / "tickets.json").is_file()


def test_nothing_touched_the_real_home(isolated):
    """이 파일의 핵심. 위 넷을 전부 한 번에 하고 진짜 홈이 그대로인지 본다.

    진짜 `~/.vt`는 개발 머신에 실제로 존재하므로 "없어야 한다"고는 못 한다 —
    대신 **우리가 만지기 전후로 안 바뀌었는지**를 본다.
    """
    real = _real_home_vt()
    before = {}
    for name in ("devices.json", "totp.json", "tickets.json"):
        p = real / name
        before[name] = p.read_bytes() if p.is_file() else None

    auth.register_device("probe")
    auth.totp_new_secret()
    auth.issue_ticket("probe")
    auth.list_devices()
    auth.totp_enabled()

    for name, content in before.items():
        p = real / name
        now = p.read_bytes() if p.is_file() else None
        assert now == content, f"실제 ~/.vt/{name} 이(가) 바뀌었다 — 격리가 깨졌다"


def test_session_signing_key_patch_takes_effect(isolated, monkeypatch):
    """경로뿐 아니라 **서명 키**도 monkeypatch가 먹어야 한다 — 안 먹으면
    테스트가 사용자의 진짜 키로 서명한 쿠키를 만들게 된다."""
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", "key-one")
    a = auth.make_session()
    monkeypatch.setattr(auth, "VT_AUTH_SESSION_KEY", "key-two")
    assert not auth.verify_session(a), "서명 키 교체가 반영되지 않았다"


def test_password_hash_patch_takes_effect(monkeypatch):
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", auth.hash_password("pw-one"))
    assert auth.check_credential("pw-one")
    monkeypatch.setattr(auth, "VT_AUTH_PASSWORD_HASH", auth.hash_password("pw-two"))
    assert not auth.check_credential("pw-one")


def test_submodules_do_not_freeze_config_at_import_time():
    """위 검사들을 **뚫고 지나갈 수 없게** 소스 수준에서도 한 번 못박는다.

    행동 검사만 두면 새 하위 모듈을 추가하면서 같은 실수를 해도 그 모듈에
    대한 테스트를 안 쓰면 안 걸린다. `from auth import <가변 설정>` 형태
    자체를 금지한다.
    """
    import auth as pkg

    mutable = {"STATE_DIR", "DEVICES_PATH", "TOTP_PATH", "TICKETS_PATH",
               "VT_AUTH_PASSWORD_HASH", "VT_AUTH_SESSION_KEY", "VT_AUTH_TOKEN"}
    pkg_dir = Path(pkg.__file__).parent
    offenders = []
    for path in sorted(pkg_dir.glob("*.py")):
        if path.name in ("__init__.py", "__main__.py"):
            continue   # 설정을 소유한 쪽 / 진입점
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("from auth import"):
                continue
            imported = {t.strip().strip("(),") for t in stripped.split("import", 1)[1].split(",")}
            bad = imported & mutable
            if bad:
                offenders.append(f"{path.name}: {sorted(bad)}")
    assert offenders == [], (
        "하위 모듈이 가변 설정을 import 시점에 굳힌다 — 호출 시점에 `auth.X`로 "
        f"읽어야 한다: {offenders}")
