"""디바이스 워크스페이스 동기화 — 탭 순서·활성 세션·UI 설정 디스크 저장.

데스크톱·모바일이 같은 vt 서버에 접속 시 LocalStorage 대신 서버 상태를 공유.
"""

import json
import os
from pathlib import Path

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


def load() -> dict:
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


def save(data: dict) -> None:
    WS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 기존 데이터와 병합
    cur = load()
    merged = {**cur, **(data or {})}
    if "ui" in (data or {}):
        merged["ui"] = {**cur.get("ui", {}), **(data["ui"] or {})}
    if "settings" in (data or {}):
        # settings도 ui와 같은 얕은 병합 — 프런트가 바뀐 키만 보내도 되고,
        # 두 기기가 서로 다른 항목을 동시에 바꿔도 한쪽이 통째로 지워지지 않는다.
        merged["settings"] = {**cur.get("settings", {}), **(data["settings"] or {})}
    WS_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False))


def update(patch: dict) -> dict:
    """부분 업데이트 — 기존 데이터에 patch 적용 후 반환."""
    save(patch)
    return load()
