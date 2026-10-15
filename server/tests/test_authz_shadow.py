"""shadow 재생 — 0단계 특성 사례(authz_char/matrix.json)를 shadow 를 켠 채 돌려 엔진과 기존 판정을 비교한다.

- 차이 0 이어야 한다. 예외는 `ALLOWLIST`의 사례 id 뿐이고, 각 줄에 이유를 적는다(위험 3:
  예상된 구멍 차이가 진짜 차이를 가리지 않게 **사례 id 로** 허용한다). 허용 목록의 사례가 더는
  차이를 내지 않으면 그것도 실패다(낡은 허용이 남지 않게).
- shadow 예외도 0 이어야 한다(엔진이 터졌는데 조용히 삼켜진 것을 놓치지 않게).
- 두 가지 입력으로 돈다: `live`(1단계 기본 — 판정 지점의 옛 레코드를 그 자리에서 번역)와
  `store`(사례마다 `migrate.apply`로 이전한 저장소만 — `fsh migrate-auth --verify`가 이것을 돈다).

`VT_AUTHZ_VERIFY_REPORT=<경로>`이면 차이 목록을 그 파일(JSON)로도 쓴다(--verify 출력용).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_CHAR = Path(__file__).resolve().parent / "authz_char"
if str(_CHAR) not in sys.path:
    sys.path.insert(0, str(_CHAR))

from authz import flags as authz_flags  # noqa: E402
from authz_cases import all_cases  # noqa: E402
from authz_harness import World  # noqa: E402

# 사례 id → 이유. 엔진이 **의도적으로** 기존 판정과 다르게 답하는 사례만 적는다.
# 1단계는 미결정(D#)을 전부 지금 동작으로 두었으므로(defaults.py PROVISIONAL) 구멍 사례
# H1·H2·H3·H5·H6·H13·H14·H16·H17 도 지금 동작과 같게 판정한다 — 비어 있는 것이 정상이다.
ALLOWLIST: dict[str, str] = {}

SOURCES = [s for s in ("live", "store") if os.environ.get("VT_AUTHZ_VERIFY_ONLY", s) == s]
CASES = [c for c in all_cases() if c.method != "FUNC"]
_found: dict[str, dict[str, list]] = {s: {} for s in ("live", "store")}
_sites: dict[str, int] = {}

# 1단계 판정 지점 전부(hooks.py). `peer.ws_input`(peer.py peer_ws)은 matrix 사례가 없는 세션
# 이름으로만 닿아(실제 PTY 를 열지 않으려고) 판정 전에 4404 로 끝난다 — 아래 단위 테스트가 맡는다. 재생이 각 지점을 한 번 이상 지나야 "차이 0"이 공허하지 않다.
SITES = {"main.middleware", "agent_share.read", "agent_share.write", "delegation.spawn", "delegation.parent",
         "delegation.report", "agent_msg.local", "peer.level.view", "peer.level.control", "peer.agent_message",
         "relay.level", "relay.rule", "relay_ws.level", "relay_ws.rule"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(monkeypatch, tmp_path)


@pytest.fixture
def capture(monkeypatch):
    from authz import shadow
    rows: list[dict] = []
    monkeypatch.setattr(shadow, "STATS", shadow._Stats())
    monkeypatch.setattr(shadow, "_write", lambda rec: rows.append(rec))
    monkeypatch.setenv("VT_AUTHZ_SHADOW", "1")
    return rows


def _migrating_seed(world: World):
    orig = world.seed

    def seed(path, body):
        orig(path, body)
        from authz import migrate
        migrate.apply()

    return seed


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_shadow_matches_current_decision(case, source, world, capture, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_SHADOW_SOURCE", source)
    if source == "store":
        monkeypatch.setattr(world, "seed", _migrating_seed(world))
    from authz import shadow
    world.run(case)
    for k, v in shadow.STATS.as_dict()["bySite"].items():
        _sites[k] = _sites.get(k, 0) + v["checks"]
    errors = [r for r in capture if r.get("kind") == "error"]
    assert not errors, f"{case.id}: shadow 예외 {errors}"
    diffs = [r for r in capture if r.get("kind") == "mismatch"]
    if diffs:
        _found[source][case.id] = diffs
    if case.id in ALLOWLIST:
        assert diffs, f"{case.id}: 허용 목록에 있지만 차이가 없다 — ALLOWLIST 에서 지울 것"
    else:
        assert not diffs, f"{case.id} ({case.hole or '-'}): shadow 차이 {diffs}"


def pytest_sessionfinish_report() -> None:   # pragma: no cover — 모듈 끝 훅에서 부른다
    out = os.environ.get("VT_AUTHZ_VERIFY_REPORT")
    if out:
        Path(out).write_text(json.dumps(_found, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def _report():
    yield
    pytest_sessionfinish_report()


def test_holes_are_exercised_by_the_replay():
    """구멍 사례가 실제로 재생 대상에 들어 있다(허용 목록이 비어 있는 것이 '안 돌려서'가 아니게)."""
    holes = {c.hole for c in CASES if c.hole}
    assert {"H1", "H2", "H5", "H6"} <= holes


def test_every_shadow_site_was_exercised():
    if not CASES or len(SOURCES) == 0:
        pytest.skip("재생 안 함")
    missing = sorted(SITES - set(_sites))
    assert not missing, f"재생이 지나지 않은 shadow 지점: {missing} (지난 것: {_sites})"


# --- shadow 가 응답을 바꾸지 않는다 -------------------------------------------------------

def _one_per_site() -> list:
    """판정 지점마다 대표 사례 몇 개(빠르게) — 모든 그룹·호출자 종류가 섞이게."""
    seen, out = set(), []
    for c in CASES:
        k = (c.group, c.caller, c.method)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


@pytest.mark.parametrize("break_at", ["engine", "principal", "store", "write"])
def test_shadow_exceptions_are_swallowed_and_responses_unchanged(break_at, world, monkeypatch):
    """엔진·주체 만들기·저장소 읽기·기록 어디서 터져도 응답은 matrix.json 그대로다(F8)."""
    from authz import engine, principals, shadow, store

    def boom(*a, **k):
        raise RuntimeError(f"authz shadow 강제 예외({break_at})")

    monkeypatch.setattr(shadow, "STATS", shadow._Stats())
    errors: list = []
    target = {"engine": (engine, "decide"), "principal": (principals, "from_request"),
              "store": (store, "snapshot"), "write": (shadow, "_write")}[break_at]
    monkeypatch.setattr(*target, boom)
    if break_at != "write":
        monkeypatch.setattr(shadow, "_write", lambda rec: errors.append(rec))
    else:
        # 기록은 불일치에서만 일어난다 — 엔진 답을 뒤집어 매 판정이 기록을 시도하게 한다.
        real = engine.decide
        monkeypatch.setattr(engine, "decide", lambda *a, **k: engine.Decision(not real(*a, **k).allow, "flipped"))
    matrix = json.loads((_CHAR / "matrix.json").read_text(encoding="utf-8"))
    cases = _one_per_site()
    assert len(cases) > 40
    enforced = authz_flags.enforced("share")
    for case in cases:
        w = World(monkeypatch, world.tmp / case.id.replace("/", "_"))
        if enforced and break_at in ("engine", "write") and case.group == "agent_share" \
                and case.caller in ("agent_verified", "agent_child") and case.route.startswith("/api/agent/shared/"):
            # EXPECTED_CHANGE(3a): VT_AUTHZ_ENFORCE=share 이면 엔진이 이 판정의 **권한자**다 — shadow 라면 삼켰을 엔진
            # 예외·뒤집힌 답이 응답을 바꾸는 게 맞다(뒤집힌 답을 따르는 것도 권한자라서다). 터진 경우는 기존 판정으로
            # 되돌아가지 않고 **닫혀야** 한다: 2xx 가 되면 안 된다. 나머지 지점은 아직 shadow 라 아래 단언 그대로다.
            if break_at == "engine":
                res = w.run(case)
                assert res["status"] >= 400, (case.id, res)
            continue
        assert w.run(case) == matrix[case.id]["expected"], case.id
    st = shadow.STATS.as_dict()
    assert st["errors"] > 0
    assert st["mismatches"] == (0 if break_at != "write" else st["checks"])
    if break_at != "write":
        assert errors and all(r["kind"] == "error" for r in errors)
        assert all("text" not in r and "body" not in r for r in errors)


def test_peer_ws_input_hook_matches_level(monkeypatch):
    """peer_ws 의 can_control(peer.py) — view 는 입력 불가, control 은 가능. 엔진도 같아야 한다."""
    from authz import hooks, shadow
    rows: list = []
    monkeypatch.setattr(shadow, "STATS", shadow._Stats())
    monkeypatch.setattr(shadow, "_write", lambda rec: rows.append(rec))
    hooks.peer_ws_control({"id": "pv", "level": "view"}, "target", False)
    hooks.peer_ws_control({"id": "pc", "level": "control"}, "target", True)
    assert shadow.STATS.as_dict()["bySite"]["peer.ws_input"]["checks"] == 2
    assert rows == []
    hooks.peer_ws_control({"id": "pv", "level": "view"}, "target", True)    # 일부러 어긋난 옛 판정
    assert [r["site"] for r in rows] == ["peer.ws_input"] and rows[0]["new"] is False


def test_shadow_detects_a_policy_change(world, capture, monkeypatch):
    """shadow 가 실제 차이를 잡는지 — D7 을 (b)(쓰기 그룹은 새로 승인)로 바꾸면 H5 의 control→queue relay
    사례가 차이로 드러나야 한다. 미결정 상수 하나만 바꿔 shadow 가 무엇을 보게 될지 보여 주는 예."""
    from authz import defaults
    monkeypatch.setattr(defaults, "PEER_CONTROL_SCOPES",
                        tuple(s for s in defaults.PEER_CONTROL_SCOPES if s != "relay.*")
                        + ("relay.connect", "relay.sessions", "relay.status"))
    h5 = [c for c in CASES if c.hole == "H5" and c.group == "relay" and "/api/queue" in c.path
          and c.caller == "peer_control"]
    assert h5
    world.run(h5[0])
    diffs = [r for r in capture if r.get("kind") == "mismatch"]
    assert diffs and diffs[0]["scope"] == "relay.queue" and diffs[0]["old"] is True and diffs[0]["new"] is False


@pytest.mark.parametrize("caller,status", [("session", 200), ("machine", 403), ("agent_verified", 403),
                                           ("peer_control", 401), ("none", 401)])
def test_shadow_debug_endpoint_is_human_only(caller, status, world):
    """`GET /api/authz/shadow` — 사람 세션만(require_human_session), 읽기 전용."""
    from starlette.testclient import TestClient
    import main
    world.set_mode("password")
    client = TestClient(main.app, raise_server_exceptions=False)
    path = "/api/authz/shadow?limit=5"
    r = client.get(path, headers=world.headers_for(caller, "GET", path, b""))
    assert r.status_code == status, r.text
    if status == 200:
        j = r.json()
        assert set(j) == {"enabled", "source", "stats", "summary", "records"}
    assert client.post(path, headers=world.headers_for("session", "POST", path, b"")).status_code == 405
