"""N41(60-settings-palette.md §5) — 누적형(CounterProvider) 어댑터.

한도형(clauth)과 달리 이 소스는 **FarShell 자신이 쓰는** 로그다(`~/.vt/usage-
counter.jsonl`, `fsh usage add` 또는 `POST /api/usage/counter`). 그래서 이
테스트의 무게는 clauth 테스트와 다르게 **집계가 맞는가**(모델별 합산·7일
스파크라인·tok/s)와 **동시 기록이 안전한가**(flock)에 있다. 입력 검증(범위
밖 tokens/seconds, 빈 model)도 이 파일이 유일한 방어선이라 같이 본다.
"""

import json
import os
import time

import pytest
from starlette.testclient import TestClient

import main
import usage
from usage.counter_jsonl import CounterJsonlProvider


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.delenv("VT_USAGE_PROVIDER", raising=False)
    usage._reset_for_tests()
    yield tmp_path / "vt"
    usage._reset_for_tests()


def _provider(state):
    return CounterJsonlProvider(state / "usage-counter.jsonl")


# ── 기록 ─────────────────────────────────────────────────────────────────
def test_add_writes_0600_file(state):
    p = _provider(state)
    r = p.add("qwen2.5-coder", 1840, 41)
    assert r["ok"] is True
    path = state / "usage-counter.jsonl"
    assert path.is_file()
    assert oct(path.stat().st_mode)[-3:] == "600"
    row = json.loads(path.read_text().splitlines()[0])
    assert row["model"] == "qwen2.5-coder" and row["tokens"] == 1840 and row["seconds"] == 41


def test_add_rejects_empty_model(state):
    r = _provider(state).add("", 10, 1)
    assert r["ok"] is False and r["error"] == "invalid"


def test_add_rejects_out_of_range(state):
    p = _provider(state)
    assert p.add("m", -1, 1)["ok"] is False
    assert p.add("m", 10, -1)["ok"] is False
    assert p.add("m", 10 ** 9, 1)["ok"] is False, "tokens 상한을 넘으면 오기입으로 본다"
    assert p.add("m", 10, 999999999)["ok"] is False, "seconds 상한(하루)을 넘으면 거부"


def test_add_rejects_non_numeric(state):
    p = _provider(state)
    assert p.add("m", "not-a-number", 1)["ok"] is False
    assert p.add("m", 10, "not-a-number")["ok"] is False


# ── 집계 ─────────────────────────────────────────────────────────────────
def test_snapshot_aggregates_per_model(state):
    p = _provider(state)
    p.add("qwen2.5-coder", 1000, 20)
    p.add("qwen2.5-coder", 840, 21)
    p.add("llama3", 500, 10)
    snap = p.snapshot()
    by_label = {c["label"]: c for c in snap["counters"]}
    assert by_label["qwen2.5-coder"]["tokens"] == 1840
    assert by_label["qwen2.5-coder"]["seconds"] == 41
    assert by_label["qwen2.5-coder"]["tok_per_sec"] == round(1840 / 41, 1)
    assert by_label["llama3"]["tokens"] == 500


def test_snapshot_since_filters_totals_but_not_sparkline(state, monkeypatch):
    p = _provider(state)
    old_ts = time.time() - 3600
    p.add("m", 100, 10, ts=old_ts)
    p.add("m", 50, 5)
    cutoff = time.time() - 60
    snap = p.snapshot(since=cutoff)
    c = snap["counters"][0]
    assert c["tokens"] == 50, "since 이후 이벤트만 누적에 들어간다"
    # 스파크라인은 since와 무관하게 최근 7일 전부를 본다 — 오늘 버킷엔 두 이벤트가 다 들어간다
    today = c["samples"][-1]
    assert today["tokens"] == 150


def test_sparkline_has_seven_daily_buckets(state):
    p = _provider(state)
    p.add("m", 10, 1)
    snap = p.snapshot()
    assert len(snap["counters"][0]["samples"]) == 7
    for s in snap["counters"][0]["samples"]:
        assert "day" in s and "tokens" in s


def test_tok_per_sec_none_when_no_duration(state):
    p = _provider(state)
    p.add("m", 100, 0)
    snap = p.snapshot()
    assert snap["counters"][0]["tok_per_sec"] is None


def test_no_feed_snapshot_and_capability(state):
    p = _provider(state)
    assert p.capability() == {"available": False, "provider": "counter_jsonl", "models": 0, "reason": "no-feed"}
    assert p.snapshot() is None


def test_broken_lines_are_skipped_not_fatal(state):
    path = state / "usage-counter.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"ts": %f, "model": "m", "tokens": 10, "seconds": 1}\nnot json\n' % time.time())
    snap = _provider(state).snapshot()
    assert snap["counters"][0]["tokens"] == 10


def test_truncation_keeps_most_recent_lines(state, monkeypatch):
    monkeypatch.setattr("usage.counter_jsonl.MAX_BYTES", 200)
    monkeypatch.setattr("usage.counter_jsonl.MAX_LINES_KEPT", 3)
    p = _provider(state)
    for i in range(10):
        p.add(f"m{i}", 1, 1)
    lines = (state / "usage-counter.jsonl").read_text().splitlines()
    assert len(lines) <= 3
    # 가장 최근 것들이 남아야 한다
    assert json.loads(lines[-1])["model"] == "m9"


# ── 팩토리(VT_USAGE_PROVIDER) ──────────────────────────────────────────────
def test_counter_capability_no_feed(state):
    assert usage.counter_capability()["available"] is False


def test_counter_none_mode_disables(state, monkeypatch):
    usage.counter_add("m", 1, 1)
    monkeypatch.setenv("VT_USAGE_PROVIDER", "none")
    cap = usage.counter_capability()
    assert cap["available"] is False and cap["reason"] == "disabled"
    assert usage.counter_snapshot() is None
    assert usage.counter_add("m", 1, 1)["ok"] is False


def test_counter_survives_clauth_mode(state, monkeypatch):
    """clauth 강제 모드는 한도형 전용 스위치다 — 누적형과는 무관해야 한다."""
    monkeypatch.setenv("VT_USAGE_PROVIDER", "clauth")
    r = usage.counter_add("m", 10, 1)
    assert r["ok"] is True
    assert usage.counter_capability()["available"] is True


# ── 엔드포인트 ──────────────────────────────────────────────────────────
@pytest.fixture
def client(state):
    with TestClient(main.app) as c:
        yield c


def test_api_post_then_get(client):
    r = client.post("/api/usage/counter", json={"model": "qwen2.5-coder", "tokens": 1840, "seconds": 41})
    assert r.status_code == 200 and r.json()["ok"] is True

    body = client.get("/api/usage/counter").json()
    assert body["available"] is True
    assert body["counters"][0]["label"] == "qwen2.5-coder"
    assert body["counters"][0]["tokens"] == 1840


def test_api_post_rejects_invalid(client):
    r = client.post("/api/usage/counter", json={"model": "", "tokens": 1, "seconds": 1})
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_api_get_without_feed_is_200(client):
    r = client.get("/api/usage/counter")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_capabilities_includes_usage_counter(client):
    client.post("/api/usage/counter", json={"model": "m", "tokens": 1, "seconds": 1})
    caps = client.get("/api/capabilities").json()
    assert caps["usage_counter"]["available"] is True
    assert caps["usage_counter"]["models"] == 1


def test_capabilities_usage_counter_absent_by_default(client):
    caps = client.get("/api/capabilities").json()
    assert caps["usage_counter"]["available"] is False


def test_running_flag_defaults_false_without_ollama(client, monkeypatch):
    """ollama가 안 떠 있으면(테스트 환경 기본) running은 항상 false — 에러가 아니다."""
    monkeypatch.setattr("usage.ollama.running_models", lambda: [])
    client.post("/api/usage/counter", json={"model": "m", "tokens": 1, "seconds": 1})
    body = client.get("/api/usage/counter").json()
    assert body["counters"][0]["running"] is False
