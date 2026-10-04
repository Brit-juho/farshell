"""디바이스 워크스페이스 동기화 — 탭 순서·활성 세션·UI 설정 디스크 저장.

데스크톱·모바일이 같은 vt 서버에 접속 시 LocalStorage 대신 서버 상태를 공유.

**이 파일은 동시 쓰기가 예외가 아니라 존재 목적이다.** 기기 둘이 같은 서버에
붙어 각자 설정을 바꾸는 것이 정상 경로다. 그래서 `save()`는 읽기-수정-쓰기
**전체**를 락으로 감싸고, 디스크 교체는 임시파일 + `os.replace`로만 한다.

둘 중 하나만 해서는 안 된다 —

  - 락 없이 원자쓰기만 하면 `load()`와 쓰기 사이에 끼어든 쪽이 통째로 사라진다
    (lost update). 아래 병합 로직이 그걸 막으려고 얕은 병합을 하는데, **로직은
    맞고 그 로직을 감싸는 보호가 없었다.**
  - 원자쓰기 없이 락만 걸면 쓰는 도중 읽은 독자가 잘린 JSON을 본다. 그러면
    `load()`의 `except`가 그걸 삼키고 **통째로 DEFAULT로 떨어지며**, 그 직후
    `save()`가 불리면 그 DEFAULT가 디스크에 굳는다. 탭 순서·활성 세션·모든 UI
    설정이 한 번에 리셋된다.

`queue_store`와 달리 0600/0700 권한은 맞추지 않는다 — 여기 담기는 것은 화면
상태이지 비밀이 아니고, 경로도 `~/.config/vt/`로 다르다.
"""

import contextlib
import json
import os
import tempfile
from pathlib import Path

import store_lock

WS_PATH = Path(os.environ.get(
    "VT_WORKSPACE_PATH",
    str(Path.home() / ".config" / "vt" / "workspace.json"),
))

DEFAULT = {
    "tabs": [],          # [{"id": "...", "name": "...", "tmux_name": "..."}]
    "active": None,      # 현재 활성 세션 id
    "ui": {              # UI 환경설정(레이아웃·레일 등 화면 상태)
        "theme": "dark",
        "font_size": 14,
    },
    # S2: 사용자 설정의 단일 진실(ADR-5). 프런트의 core/settings.js 스키마 키를
    # 그대로 담는다("terminal.fontSize" 등) — 서버는 값의 의미를 모르고 보관만
    # 한다. 스키마를 서버에도 두면 프런트와 두 벌이 되어 반드시 어긋난다.
    # ui와 분리한 이유: ui는 "이 기기에서 지금 어떻게 보이는가"(레일 폭·열린
    # 패널·레이아웃 트리)이고 settings는 "사용자가 무엇을 원하는가"다.
    "settings": {},
    "version": 1,
}


def _lock_path() -> Path:
    """락 파일은 `WS_PATH`에서 유도한다.

    하드코딩하면 `VT_WORKSPACE_PATH`로 격리한 테스트 서버가 **실사용자의 락**을
    잡는다. 격리 서버가 실사용자 `workspace.json`을 건드린 전례가 실제로 있다.

    데이터 파일 자신을 잠그지 않는 이유는 `os.replace`가 inode를 갈아치우기
    때문이다 — 그러면 먼저 잡은 쪽의 락이 허공에 걸린다.
    """
    return WS_PATH.with_name(WS_PATH.name + ".lock")


def load() -> dict:
    """**락을 잡지 않는다.**

    `os.replace`가 원자적이라 독자는 항상 옛 버전이나 새 버전 **중 하나를 온전히**
    본다. 읽기에 락을 걸면 매 폴링이 쓰기와 경쟁하게 되고, 얻는 것이 없다.
    """
    if WS_PATH.exists():
        try:
            data = json.loads(WS_PATH.read_text())
            # 기본값과 병합 (누락 키 보완)
            merged = {**DEFAULT, **data}
            merged["ui"] = {**DEFAULT["ui"], **(data.get("ui") or {})}
            merged["settings"] = {**DEFAULT["settings"], **(data.get("settings") or {})}
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT)


def _write_atomic(merged: dict) -> None:
    """같은 디렉토리에 임시파일로 쓰고 제자리에 옮긴다.

    같은 파일시스템이어야 `os.replace`가 원자적이므로 `dir=`을 목적지 부모로
    고정한다. 실패하면 조각을 치운다 — 남겨두면 다음 쓰기와 이름이 부딪히지는
    않지만(유니크 이름) 디렉토리에 쓰레기가 쌓인다.
    """
    fd, tmp = tempfile.mkstemp(
        dir=str(WS_PATH.parent), prefix="." + WS_PATH.name + "-", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        os.replace(tmp, str(WS_PATH))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# 2026-10-03(세션·뷰 규칙 2단계) — 레이아웃은 브라우저 탭(screen id)마다 따로
# 저장된다: `ui.layouts = {screen_id: snapshot}`. `ui`는 얕은 병합이라 그대로
# 두면 한 탭의 PUT이 다른 탭들의 레이아웃을 통째로 지운다 — 이 키만 탭 id 단위로
# 병합한다. 탭은 닫혀도 서버가 알 수 없으므로 최근 저장 순(savedAt)으로 상한을 둔다.
MAX_SCREEN_LAYOUTS = 20


def _merge_layouts(cur: dict, patch: dict) -> dict:
    merged = dict(cur) if isinstance(cur, dict) else {}
    for key, snap in patch.items():
        if not isinstance(key, str) or not key:
            continue
        if snap is None:
            merged.pop(key, None)
        elif isinstance(snap, dict):
            merged[key] = snap
    if len(merged) > MAX_SCREEN_LAYOUTS:
        def saved_at(item):
            v = item[1].get("savedAt") if isinstance(item[1], dict) else 0
            return v if isinstance(v, (int, float)) else 0
        keep = sorted(merged.items(), key=saved_at, reverse=True)[:MAX_SCREEN_LAYOUTS]
        merged = dict(keep)
    return merged


def save(data: dict) -> None:
    """읽기-수정-쓰기 **전체**가 임계구역이다.

    쓰기만 원자적으로 만들면 부분 기록은 사라져도 lost update는 그대로 남는다.
    락 범위가 이 함수의 요점이다.

    기한 안에 락을 못 얻으면 `store_lock.LockTimeout`이 그대로 올라간다.
    `async def` 라우트에서 불리는 함수라 무기한 대기는 서버 전체를 세운다 —
    요청 하나가 실패하는 쪽을 택한다(`store_lock` 모듈 docstring).
    """
    WS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with store_lock.exclusive(_lock_path()):
        # 기존 데이터와 병합
        cur = load()
        merged = {**cur, **(data or {})}
        if "ui" in (data or {}):
            cur_ui = cur.get("ui", {})
            patch_ui = data["ui"] or {}
            merged["ui"] = {**cur_ui, **patch_ui}
            if isinstance(patch_ui.get("layouts"), dict):
                merged["ui"]["layouts"] = _merge_layouts(cur_ui.get("layouts"), patch_ui["layouts"])
        if "settings" in (data or {}):
            # settings도 ui와 같은 얕은 병합 — 프런트가 바뀐 키만 보내도 되고,
            # 두 기기가 서로 다른 항목을 동시에 바꿔도 한쪽이 통째로 지워지지 않는다.
            merged["settings"] = {**cur.get("settings", {}), **(data["settings"] or {})}
        _write_atomic(merged)


def update(patch: dict) -> dict:
    """부분 업데이트 — 기존 데이터에 patch 적용 후 반환."""
    save(patch)
    return load()
