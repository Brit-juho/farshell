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
    "ui": {              # UI 환경설정
        "theme": "dark",
        "font_size": 14,
    },
    "version": 1,
}


def load() -> dict:
    if WS_PATH.exists():
        try:
            data = json.loads(WS_PATH.read_text())
            # 기본값과 병합 (누락 키 보완)
            merged = {**DEFAULT, **data}
            merged["ui"] = {**DEFAULT["ui"], **(data.get("ui") or {})}
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
    WS_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False))


def update(patch: dict) -> dict:
    """부분 업데이트 — 기존 데이터에 patch 적용 후 반환."""
    save(patch)
    return load()
