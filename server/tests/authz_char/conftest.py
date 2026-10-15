"""authz 특성 테스트(plan-2.6 0단계) 픽스처 — 호출자 행렬 × 서버 모드 × tmux 상태.

상위 `server/tests/conftest.py`의 HOME·VT_CONFIG·tmux 소켓 격리를 그대로 물려받고, 여기서 더 좁힌다:
사례마다 tmp 상태 폴더·가짜 tmux·가짜 네트워크를 새로 깐다(`authz_harness.World`).

## golden(matrix.json)
- 평소: 각 사례의 실제 판정을 matrix.json 의 `expected`와 비교한다(메타 정보도 같아야 한다).
- `VT_AUTHZ_REGEN=1`: 비교 대신 기록하고, 세션이 끝나면 matrix.json 을 다시 쓴다(`gen_matrix.py`).
  일부만 돌려도 나머지 줄은 그대로 두고, 사례 목록에서 사라진 id 만 지운다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from authz_harness import World  # noqa: E402

MATRIX_PATH = _HERE / "matrix.json"
REGEN = os.environ.get("VT_AUTHZ_REGEN") == "1"
_recorded: dict[str, dict] = {}


def load_matrix() -> dict:
    try:
        return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(monkeypatch, tmp_path)


class Golden:
    def __init__(self):
        self.matrix = load_matrix()

    def check(self, case, actual: dict) -> None:
        if REGEN:
            _recorded[case.id] = {**case.meta(), "expected": actual}
            return
        row = self.matrix.get(case.id)
        assert row is not None, f"matrix.json 에 {case.id} 가 없습니다 — gen_matrix.py 로 다시 만드세요"
        assert {k: v for k, v in row.items() if k != "expected"} == case.meta(), f"{case.id} 메타가 바뀜"
        assert actual == row["expected"], f"{case.id}: 지금 {actual} ≠ 기록 {row['expected']}"


@pytest.fixture(scope="session")
def golden():
    return Golden()


def pytest_sessionfinish(session, exitstatus):
    if not REGEN or not _recorded:
        return
    from authz_cases import all_cases

    live = {c.id for c in all_cases()}
    merged = {k: v for k, v in load_matrix().items() if k in live}
    merged.update(_recorded)
    MATRIX_PATH.write_text(json.dumps(dict(sorted(merged.items())), ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
