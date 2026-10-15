"""에이전트 위임(`routes/delegation.py`) — 지금 판정 고정.

에이전트용 6개는 pane 확인 + 장부(부모·자식 key)로, 사람용 close-by-user 는 `require_human_session`으로
가른다. spawn 은 grant·승인이 없고 `cwd`는 아무 절대 경로나 받는다.
"""

from __future__ import annotations

import json

import pytest

from authz_cases import cases_for


@pytest.fixture(autouse=True)
def _deleg_flag_off(monkeypatch):
    """이 파일은 3b 이전의 옛 동작을 고정한다 — 환경에 `deleg` 가 켜져 있어도 끄고 돈다(켠 판은 `*_enforced.py`)."""
    from authz import flags
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", ",".join(sorted(flags.tokens() - {"deleg"})))


@pytest.mark.parametrize("case", cases_for("delegation"), ids=lambda c: c.id)
def test_matrix(case, world, golden):
    golden.check(case, world.run(case))


def test_spawn_accepts_root_cwd_without_any_grant(world):
    """H9 CURRENT_BEHAVIOR — 확인된 에이전트는 승인 없이 `/`에서 하위 세션을 띄운다."""
    from authz_harness import Case
    r = world.run(Case(id="adhoc", group="adhoc", caller="agent_verified", method="POST",
                       path="/api/delegations", body={"task": "x", "cwd": "/"}))
    assert r["status"] == 200
    import vt_paths
    items = json.loads((vt_paths.state_dir() / "delegations.json").read_text())["items"]
    assert any(x["cwd"] == "/" and x["parent"]["name"] == "agent-a" for x in items)
