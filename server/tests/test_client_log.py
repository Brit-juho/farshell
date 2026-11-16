"""client_log.py — 화면이 보낸 오류·경고 기록 저장소.

지키는 것: 오류·경고만 받는다, 허용 키만 남는다, 비밀값·홈 경로는 저장 전에 가린다(브라우저와 같은 사례 파일),
상한(분당 줄 수·보관 일수·합계 용량), 파일 권한, 같은 id 는 가장 새 줄만 읽힌다."""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

import client_log

CASES = json.loads((Path(__file__).resolve().parents[2] / "frontend" / "tests" / "fixtures" / "redact-cases.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    client_log._reset_for_tests()
    return tmp_path / "vt" / "client-log"


def rec(i=1, **kw):
    now = int(time.time() * 1000)
    base = {"v": 1, "id": f"e_{i}", "ts": now, "last": now, "count": 1, "level": "error", "source": "toast", "msg": f"실패 {i}"}
    base.update(kw)
    return base


def lines(state):
    out = []
    for p in sorted(state.glob("*.jsonl")):
        out += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    return out


@pytest.mark.parametrize("case", CASES, ids=[c["in"][:30] for c in CASES])
def test_redact_matches_the_browser_cases(case):
    assert client_log.redact(case["in"]) == case["out"]


def test_info_and_unknown_levels_are_not_stored(state):
    r = client_log.append([rec(1, level="info"), rec(2, level="success"), rec(3, level="debug"), rec(4, level="bogus"), rec(5)])
    assert r == {"accepted": 1, "ignored": 4}
    assert [x["id"] for x in lines(state)] == ["e_5"]


def test_only_allowed_keys_survive_and_bodies_are_never_stored(state):
    client_log.append([rec(1, body="SECRETBODY", headers={"x": 1}, evil="x", req={"method": "PUT", "path": "/a", "status": 500, "body": "SECRETBODY", "ms": 4})])
    stored = lines(state)[0]
    assert "SECRETBODY" not in json.dumps(stored)
    assert set(stored) <= {"v", "id", "ts", "last", "count", "level", "source", "msg", "detail", "req", "rel", "relGuess", "ctx"}
    assert stored["req"] == {"method": "PUT", "path": "/a", "status": 500, "ms": 4}


def test_secrets_and_home_path_are_masked_even_if_the_browser_missed_them(state, tmp_path):
    home = str(tmp_path / "home")
    client_log.append([rec(1, msg="GET /x?token=SECRET1", detail=f"Cookie: vt_session=SECRET2 at {home}/proj/a.js:3",
                           req={"method": "GET", "path": "/y?ticket=SECRET3", "status": 500})])
    dump = json.dumps(lines(state)[0], ensure_ascii=False)
    for s in ("SECRET1", "SECRET2", "SECRET3", home):
        assert s not in dump
    assert "~/proj/a.js:3" in dump


def test_lengths_are_capped(state):
    client_log.append([rec(1, msg="가" * 900, detail="x" * 9000)])
    s = lines(state)[0]
    assert len(s["msg"]) == 500 and len(s["detail"]) == 4000


def test_invalid_ids_and_timestamps_are_ignored(state):
    now = int(time.time() * 1000)
    bad = [rec(1, id="../x"), rec(2, id=""), rec(3, ts=now - 8 * 86_400_000), rec(4, ts=now + 3 * 86_400_000), rec(5, ts="soon"),
           rec(6, ts=float("nan")), "not a dict", None]
    assert client_log.append(bad)["accepted"] == 0


def test_count_is_clamped(state):
    client_log.append([rec(1, count=10**9), rec(2, count=-5)])
    assert [x["count"] for x in lines(state)] == [10000, 1]


def test_permissions_are_private(state):
    client_log.append([rec(1)])
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
    f = next(state.glob("*.jsonl"))
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_rate_limit_drops_and_reports_the_count_on_the_next_write(state):
    n = client_log.RATE_PER_MIN
    now = time.time()
    client_log.append([rec(i) for i in range(n)], now=now)
    with pytest.raises(client_log.RateLimited) as e:
        client_log.append([rec(10_000 + i) for i in range(3)], now=now + 1)
    assert e.value.retry_after >= 1
    client_log.append([rec(20_000)], now=now + 61)
    marker = [x for x in lines(state) if x["source"] == "server"]
    assert marker and "dropped 3" in marker[0]["msg"]


def test_a_partial_batch_is_cut_at_the_remaining_room(state):
    now = time.time()
    client_log.append([rec(i) for i in range(client_log.RATE_PER_MIN - 2)], now=now)
    r = client_log.append([rec(90_000 + i) for i in range(5)], now=now + 1)
    assert r["accepted"] == 2


def test_prune_removes_files_older_than_retention(state):
    state.mkdir(parents=True)
    old = state / "2020-01-01.jsonl"
    old.write_text('{"id":"o","level":"error","last":1}\n')
    os.utime(old, (time.time() - 8 * 86400,) * 2)
    client_log.append([rec(1)])
    assert not old.exists()


def test_prune_enforces_the_total_size_cap_oldest_first(state, monkeypatch):
    monkeypatch.setattr(client_log, "MAX_TOTAL_BYTES", 1000)
    state.mkdir(parents=True)
    for day in ("2026-11-10", "2026-11-11", "2026-11-12"):
        (state / f"{day}.jsonl").write_text("x" * 600)
    client_log.append([rec(1)])
    names = sorted(p.name for p in state.glob("*.jsonl"))
    assert "2026-11-10.jsonl" not in names and "2026-11-12.jsonl" in names


def test_read_is_newest_first_and_dedupes_by_id_keeping_the_latest_line(state):
    now = int(time.time() * 1000)
    client_log.append([rec(1, ts=now - 3000, last=now - 3000), rec(2, ts=now - 2000, last=now - 2000)])
    client_log.append([rec(1, ts=now - 3000, last=now - 1000, count=4)])
    items = client_log.read()
    assert [i["id"] for i in items] == ["e_1", "e_2"]
    assert items[0]["count"] == 4


def test_read_filters(state):
    now = int(time.time() * 1000)
    client_log.append([rec(1, level="warn", msg="네트워크 끊김", last=now - 5000, ts=now - 5000), rec(2, msg="저장 실패", req={"method": "PUT", "path": "/api/fs/file", "status": 403}),
                       rec(3, msg="기타")])
    assert [i["id"] for i in client_log.read(level="warn")] == ["e_1"]
    assert [i["id"] for i in client_log.read(q="fs/file")] == ["e_2"]
    assert [i["id"] for i in client_log.read(q="없는말")] == []
    assert [i["id"] for i in client_log.read(before=now - 1000)] == ["e_1"]
    assert len(client_log.read(limit=1)) == 1


def test_read_skips_corrupt_lines_and_a_cut_first_line(state):
    state.mkdir(parents=True)
    f = state / (time.strftime("%Y-%m-%d") + ".jsonl")
    f.write_text("{broken\n" + json.dumps(rec(1)) + "\n")
    assert [i["id"] for i in client_log.read()] == ["e_1"]


def test_clear_removes_everything(state):
    client_log.append([rec(1)])
    assert client_log.clear() == 1
    assert client_log.read() == [] and client_log.clear() == 0
