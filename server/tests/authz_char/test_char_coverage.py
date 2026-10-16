"""0단계 종료 조건 — 대상 라우트·relay 규칙마다 matrix 사례가 1건 이상인가(프로그램으로 센다).

새 라우트·규칙이 생겼는데 사례가 없으면 여기서 깨진다. 사례를 authz_cases.py 에 더하고
gen_matrix.py 로 matrix.json 을 다시 만든다.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute, APIWebSocketRoute

import relay_policy
from authz_cases import RELAY_DENY_SAMPLES, RELAY_SAMPLES, RELAY_WS_SAMPLES, all_cases
from conftest import REGEN, load_matrix

MODULES = {f"routes.{m}" for m in ("agent_share", "agent_msg", "delegation", "peer", "peer_relay",
                                   "host_relay", "hosts", "authz", "leases", "pairs")}

pytestmark = pytest.mark.skipif(REGEN, reason="matrix.json 을 다시 만드는 중")


def _target_routes() -> set[tuple[str, str]]:
    """대상 모듈의 APIRouter(에이전트용·사람용 둘 다)를 직접 훑는다. main 에 실제로 포함됐는지도 본다
    (포함 안 된 라우터를 세면 없는 라우트를 세는 셈이다)."""
    import importlib

    import main
    included = {id(getattr(r, "original_router", None)) for r in main.app.router.routes}
    out = set()
    for name in MODULES:
        mod = importlib.import_module(name)
        for router in {id(v): v for v in vars(mod).values() if isinstance(v, APIRouter)}.values():
            assert id(router) in included, f"{name} 의 라우터가 main 에 포함되지 않음"
            for r in router.routes:
                if isinstance(r, APIWebSocketRoute):
                    out.add((r.path, "WS"))
                elif isinstance(r, APIRoute):
                    out |= {(r.path, m) for m in r.methods - {"HEAD", "OPTIONS"}}
    return out


def test_every_target_route_and_method_has_a_case():
    rows = load_matrix().values()
    have = {(r["route"], r["method"]) for r in rows}
    routes = _target_routes()
    assert len(routes) >= 50, routes          # 라우트를 못 찾아 공허하게 통과하는 일을 막는다
    missing = sorted(routes - have)
    assert not missing, f"matrix 사례가 없는 라우트: {missing}"


def test_every_relay_policy_rule_has_a_case():
    rules = ([rx.pattern for rx, _ in relay_policy._HTTP_RULES] + [rx.pattern for rx in relay_policy._HTTP_DENY]
             + [rx.pattern for rx in relay_policy._WS_RULES])
    have = {r.get("rule") for r in load_matrix().values()}
    missing = [p for p in rules if p not in have]
    assert not missing, f"matrix 사례가 없는 relay 규칙: {missing}"


def test_relay_samples_match_their_rule_and_cover_the_policy_exactly():
    http = {rx.pattern: rx for rx, _ in relay_policy._HTTP_RULES}
    assert set(RELAY_SAMPLES) == set(http)
    for p, sample in RELAY_SAMPLES.items():
        assert http[p].fullmatch(sample.split("?")[0]), (p, sample)
    deny = {rx.pattern: rx for rx in relay_policy._HTTP_DENY}
    assert set(RELAY_DENY_SAMPLES) == set(deny)
    assert all(deny[p].fullmatch(s) for p, s in RELAY_DENY_SAMPLES.items())
    ws = {rx.pattern: rx for rx in relay_policy._WS_RULES}
    assert set(RELAY_WS_SAMPLES) == set(ws)
    assert all(ws[p].fullmatch(s) for p, s in RELAY_WS_SAMPLES.items())


def test_matrix_ids_match_the_case_list():
    ids = {c.id for c in all_cases()}
    m = set(load_matrix())
    assert not (ids - m), f"matrix.json 에 없는 사례: {sorted(ids - m)[:10]}"
    assert not (m - ids), f"사례 목록에 없는 matrix 줄: {sorted(m - ids)[:10]}"


def test_requested_holes_are_marked():
    holes = {r.get("hole") for r in load_matrix().values()}
    assert {"H1", "H2", "H3", "H5", "H6"} <= holes
