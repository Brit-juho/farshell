"""N41 — ollama 실행 중 모델 탐지(선택 기능).

`GET localhost:11434/api/ps`가 응답하면 지금 메모리에 올라와 있는 모델
이름을 뽑아 counter 스냅샷의 해당 label에 "지금 실행 중"을 붙이는 데 쓴다.
ollama가 없는 환경이 정상이므로 **연결 실패는 에러가 아니다** — 짧은
타임아웃으로 한 번 찔러보고 안 되면 조용히 빈 결과를 돌려준다(clauth 미설치
환경과 같은 원칙).

usage-counter.jsonl에 기록이 없어도(아직 `fsh usage add`를 한 번도 안 불렀어도)
"지금 뭐가 돌고 있는지"는 알 수 있어야 하므로, 이 모듈은 독립적으로
호출 가능하다 — `__init__.py`가 snapshot에 병합할지는 호출부가 정한다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

TIMEOUT_SEC = 0.3
URL = "http://localhost:11434/api/ps"


def running_models() -> list[str]:
    """실행 중인 모델 이름 목록. ollama가 없거나 안 돌면 빈 리스트."""
    try:
        with urllib.request.urlopen(URL, timeout=TIMEOUT_SEC) as r:
            data = json.load(r)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []
    if not isinstance(data, dict):
        return []
    models = data.get("models")
    if not isinstance(models, list):
        return []
    out = []
    for m in models:
        if isinstance(m, dict):
            name = m.get("name") or m.get("model")
            if isinstance(name, str) and name.strip():
                out.append(name.strip())
    return out
