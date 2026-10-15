"""기능별 강제(enforce) 플래그 — `VT_AUTHZ_ENFORCE` (30-phases.md 3단계).

쉼표로 구분한 토큰 목록이다: `share`(3a), `deleg`(3b), `msg`(3c), `peer`(3d), `legacy`(3e). **기본은 비어 있음(전부 꺼짐)** —
출하해도 아무것도 바뀌지 않고, 사용자가 켜야 그 기능의 판정을 엔진이 쥔다. 켠 기능도 옛 저장소에 같이 써서
(dual-write) 플래그를 끄면 곧바로 이전 동작으로 돌아간다.

환경변수는 호출할 때마다 읽는다(모듈 상태 없음 — 테스트가 monkeypatch 로 바꾸는 관례, 위험 16).
이 빌드에서 의미가 있는 토큰은 `share`(3a)·`deleg`(3b)·`msg`(3c) 이다. 모르는 토큰은 무시하고 `unknown()`으로만 보인다(오타가 조용히
"꺼짐"이 되는 것을 doctor 가 짚을 수 있게).
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

ENV = "VT_AUTHZ_ENFORCE"
KNOWN = ("share", "deleg", "msg", "peer", "legacy")
IMPLEMENTED = ("share", "deleg", "msg")  # 이 빌드에서 실제로 동작하는 토큰


def tokens(env: Optional[Mapping[str, str]] = None) -> frozenset[str]:
    raw = (env if env is not None else os.environ).get(ENV, "") or ""
    return frozenset(t for t in (x.strip().lower() for x in raw.split(",")) if t)


def enforced(name: str, env: Optional[Mapping[str, str]] = None) -> bool:
    return name in tokens(env)


def unknown(env: Optional[Mapping[str, str]] = None) -> list[str]:
    return sorted(t for t in tokens(env) if t not in KNOWN)
