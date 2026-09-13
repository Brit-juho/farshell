"""`fsh pane report`(자기보고)를 **쓴 적이 있는 CLI**의 기록.

N9(2.1.4) — 감지 커버리지 표(`/api/agents/coverage`)가 claude 외 CLI에는
영원히 `trust: mid`를 주고 있었다. 이유가 패턴 깊이가 아니라 등급 함수에
있었다: `path_kind == "hook"`일 때만 high를 주는데 그 값은 claude일 때만
세워진다. codex/aider/gemini는 toml에 패턴을 100줄 넣어도 mid였다.

자기보고는 훅과 같은 **1차 정보**다 — 에이전트 자신이 "지금 승인 대기다"라고
말하는 것이라 출력 grep보다 정확하다. 그래서 report도 high로 인정한다.

왜 파일로 남기는가: 훅이 "hook" 경로로 읽히는 근거는 이벤트가 방금 왔다는
사실이 아니라 **설정에 설치돼 있다**는 지속적인 사실이다. 자기보고에서 그에
대응하는 지속적 사실은 "이 CLI가 이 서버에 보고한 적이 있다"뿐이다 —
agent_status의 엔트리는 TTL로 만료되므로, 그걸 근거로 삼으면 몇 분만 쉬어도
커버리지 등급이 high↔mid로 깜빡인다.

저장 형식은 `{cli: 마지막 보고 시각(epoch)}` 하나뿐이다. 시각은 사용자가
"언제부터 이 경로가 살아 있었나"를 볼 때만 쓰이고 등급 판정에는 존재 여부만
쓴다. 파일이 깨졌거나 못 읽으면 조용히 빈 값으로 취급한다 — 커버리지 표는
어떤 경우에도 떠야 하고, 등급이 한 칸 낮게 보이는 것이 표가 통째로 500나는
것보다 낫다.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

FILENAME = "agent-report-seen.json"


def _state_dir() -> Path:
    return Path(os.environ.get("VT_STATE_DIR", "~/.vt")).expanduser()


def _path() -> Path:
    return _state_dir() / FILENAME


def load() -> dict[str, float]:
    """{cli: 마지막 보고 epoch}. 읽기 실패·형식 오류는 빈 dict."""
    try:
        raw = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for cli, ts in raw.items():
        if isinstance(cli, str) and isinstance(ts, (int, float)):
            out[cli] = float(ts)
    return out


def mark(cli: Optional[str], now: Optional[float] = None) -> None:
    """이 CLI가 자기보고를 썼다고 기록한다. 실패해도 조용히 넘어간다 —
    보고 자체(상태 갱신)는 이미 성공했고, 그것이 이 호출의 본업이 아니다."""
    if not cli or not isinstance(cli, str):
        return
    cli = cli.strip().lower()
    if not cli:
        return
    data = load()
    data[cli] = float(now if now is not None else time.time())
    try:
        d = _state_dir()
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        tmp = _path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, _path())
    except OSError:
        pass
