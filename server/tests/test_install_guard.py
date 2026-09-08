"""install.sh의 Python 최소 버전 게이트 (2026-09-08, 클린 환경 검증에서 발견).

서버 코드는 PEP 604(`str | None`)를 여러 파일에서 쓰는데 macOS 기본 python3는
3.9다. 게이트가 없으면 **설치는 전부 성공하고 서버만 import 단계에서 죽는다**
— 사용자에게는 "설치는 됐다는데 안 켜진다"로 보인다. 실제로 릴리스 tarball을
클린 환경에서 돌려 그 상태를 재현했고, 그래서 게이트를 넣었다.

이 테스트는 install.sh를 실행하지 않는다(venv/pip 설치가 일어난다). 대신
**게이트 자체가 사라지지 않았는지**와 **코드가 정말 3.11+를 요구하는지**를 본다.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_install_sh_has_min_python_gate():
    s = (ROOT / "install.sh").read_text()
    assert "PY_MINOR" in s, "최소 버전 게이트가 사라졌다"
    assert re.search(r'PY_MINOR"?\s*-lt\s*11', s), "하한이 3.11이 아니다 (CI 매트릭스와 어긋난다)"
    # 안내가 실행 가능해야 한다 — "안 된다"만 말하고 끝나면 사용자가 막힌다
    assert "brew install python@3.11" in s


def test_server_actually_requires_310_plus():
    """게이트의 근거 — PEP 604 문법이 실제로 쓰이고 있는가.

    언젠가 코드가 3.9 호환으로 바뀌면 이 테스트가 먼저 깨져서, 게이트를 낮출지
    다시 판단하게 된다(근거 없이 하한만 남는 것을 막는다).
    """
    hits = []
    for p in (ROOT / "server").glob("*.py"):
        text = p.read_text()
        if re.search(r"(?::|->)\s*[A-Za-z_\[\]\. ]+\|\s*None", text):
            hits.append(p.name)
    assert hits, "PEP 604 문법이 하나도 없다 — 3.11 하한의 근거를 다시 확인할 것"


def test_ci_matrix_lower_bound_matches_gate():
    """CI가 3.11을 보는 한 install.sh도 3.11을 요구해야 한다(두 곳이 어긋나면 안 된다)."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    m = re.search(r"python:\s*\[([^\]]+)\]", ci)
    assert m, "CI 매트릭스를 못 찾았다"
    versions = [v.strip().strip("'\"") for v in m.group(1).split(",")]
    assert min(versions, key=lambda v: tuple(map(int, v.split(".")))) == "3.11"
