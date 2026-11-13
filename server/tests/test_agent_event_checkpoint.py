"""훅 이벤트 → 턴 체크포인트 예약(`checkpoint_jobs`). 실제 git 저장소와 `ckpt` 풀로 돌린다.

지키는 것: 켠 저장소에서만 동작하고 꺼져 있으면 git을 아예 안 부른다, 기준은 baseline → pre-turn → turn-end 순서와 턴 번호,
`pre-turn` 신뢰는 턴이 끝난 뒤(첫 `pre`의 시각과 write-tree 시각) 확정돼 `turn-end` 커밋에 적힌다, 서브에이전트·자식·반납·알 수 없는
이벤트·저장소 아님은 예약하지 않는다, 같은 종류가 연달아 오면 최신 하나만, 풀이 꽉 차면 건너뛰고 3번 연속 실패하면 쉰다,
훅 응답은 캡처를 기다리지 않는다, 서버 재시작 뒤 턴 번호가 ref에서 이어진다.
"""
import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest

import checkpoint_jobs as jobs
import checkpoint_store
import checkpoints
from resilience import pools as res_pools

SID = "sess-1"


def g(repo, *args):
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=e).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "state"))
    checkpoint_store._cache = (None, frozenset())
    jobs._reset_for_tests()
    r = tmp_path / "proj"
    r.mkdir()
    g(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a\n")
    g(r, "add", "-A")
    g(r, "commit", "-q", "-m", "init")
    return r


def enable(repo):
    checkpoint_store.set_enabled(checkpoints.repo_key(repo)["key"], True)


def ev(event, repo, *, sid=SID, state=None, ts=None, agent="claude", extra=None):
    payload = {"session_id": sid, "cwd": str(repo), **(extra or {})}
    jobs.on_agent_event(event, {"tmux_session": "dev"} if state is None else state, payload, ts, agent=agent)


async def settle(timeout=20):
    end = time.monotonic() + timeout
    while any(s.running or s.pending for s in list(jobs._sessions.values())):
        assert time.monotonic() < end, "예약된 작업이 끝나지 않았다"
        await asyncio.sleep(0.02)


def run(coro_fn):
    asyncio.run(coro_fn())


def kinds(repo):
    return [(e["kind"], e["turn"]) for e in checkpoints.list_refs(repo)]


def test_nothing_runs_for_a_repository_that_is_not_enabled(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(checkpoints, "repo_key", lambda *a, **k: calls.append(a) or None)

    async def main():
        for e in ("session_start", "prompt", "pre", "stop"):
            ev(e, repo)
        await settle()
    run(main)
    assert calls == [] and checkpoints.list_refs(repo) == []             # 켠 곳이 하나도 없으면 git조차 안 부른다


def test_enabled_for_another_repository_only_probes_then_stays_quiet(repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    g(other, "init", "-q")
    enable(other)

    async def main():
        ev("prompt", repo)
        await settle()
        assert checkpoints.list_refs(repo) == []
        assert repo.as_posix() in jobs._negative                           # 한동안 다시 조사하지 않는다
        ev("prompt", repo)
        await settle()
    run(main)
    assert checkpoints.list_refs(repo) == []


def test_full_turn_baseline_pre_turn_turn_end_with_trusted_pre_turn(repo):
    enable(repo)

    async def main():
        ev("session_start", repo, extra={"source": "startup"})
        await settle()
        (repo / "human.txt").write_text("사람이 턴 사이에 고침\n")
        ev("prompt", repo)
        await settle()
        ev("pre", repo, ts=time.time_ns() + 60_000_000_000)                # 첫 도구 호출이 write-tree보다 한참 뒤
        (repo / "agent.txt").write_text("에이전트가 고침\n")
        ev("stop", repo)
        await settle()
    run(main)
    es = checkpoints.list_refs(repo)
    assert [(e["kind"], e["turn"]) for e in es] == [("baseline", 0), ("pre-turn", 1), ("turn-end", 1)]
    assert es[2]["pre_trusted"] is True and all(e["agent"] == "claude" for e in es)
    t = checkpoints.turn_files(repo, es[2]["id"])
    assert t["from"] == es[1]["id"] and [f["path"] for f in t["files"]] == ["agent.txt"]       # 사람 수정은 이 턴에 안 섞인다


def test_untrusted_pre_turn_when_the_first_tool_ran_before_the_snapshot_finished(repo):
    enable(repo)

    async def main():
        ev("session_start", repo)
        await settle()
        (repo / "human.txt").write_text("h\n")
        ev("prompt", repo)
        ev("pre", repo, ts=1)                                              # 훅 시각이 write-tree보다 앞섬(경쟁에서 짐)
        await settle()
        (repo / "agent.txt").write_text("x\n")
        ev("stop", repo)
        await settle()
    run(main)
    es = checkpoints.list_refs(repo)
    end = es[-1]
    assert end["kind"] == "turn-end" and end["pre_trusted"] is False
    t = checkpoints.turn_files(repo, end["id"])
    assert t["from"] == es[0]["id"]                                         # 앞 체크포인트(baseline)를 기준으로 — 사람 수정이 섞여 보인다
    assert sorted(f["path"] for f in t["files"]) == ["agent.txt", "human.txt"]


def test_turn_without_tool_calls_trusts_the_pre_turn(repo):
    enable(repo)

    async def main():
        ev("session_start", repo)
        await settle()
        (repo / "h.txt").write_text("h")
        ev("prompt", repo)
        await settle()
        (repo / "late.txt").write_text("l")
        ev("stop", repo)                                                    # pre 훅이 한 번도 안 왔다 — 말만 한 턴
        await settle()
    run(main)
    assert checkpoints.list_refs(repo)[-1]["pre_trusted"] is True


def test_events_that_must_not_schedule(repo):
    enable(repo)

    async def main():
        ev("prompt", repo, extra={"agent_id": "sub-1"})                    # 서브에이전트
        jobs.on_agent_event("prompt", None, {"session_id": SID, "cwd": str(repo)}, None, agent="claude")                # 소유자 아님·은퇴
        jobs.on_agent_event("stop", {"released": True}, {"session_id": SID, "cwd": str(repo)}, None, agent="claude")    # 반납
        ev("post", repo)
        ev("notification", repo)
        ev("prompt", repo, sid="")
        ev("prompt", repo, extra={"cwd": "relative/path"})
        ev("prompt", repo, extra={"cwd": str(repo) + "\0x"})
        jobs.on_agent_event("prompt", {}, "not a dict", None, agent="claude")
        await settle()
    run(main)
    assert checkpoints.list_refs(repo) == [] and jobs._sessions == {}


def test_global_switch_off(repo, monkeypatch):
    enable(repo)
    monkeypatch.setenv("VT_CHECKPOINTS", "0")

    async def main():
        ev("session_start", repo)
        await settle()
    run(main)
    assert checkpoints.list_refs(repo) == [] and jobs._sessions == {}


def test_not_a_git_directory_makes_nothing(repo, tmp_path):
    enable(repo)
    plain = tmp_path / "plain"
    plain.mkdir()

    async def main():
        ev("session_start", plain)
        await settle()
    run(main)
    assert checkpoints.list_refs(repo) == [] and not (plain / ".git").exists()


def test_baseline_only_once_per_session(repo):
    enable(repo)

    async def main():
        ev("session_start", repo)
        await settle()
        (repo / "x.txt").write_text("x")
        ev("session_start", repo)                                           # clear/resume로 다시 와도
        await settle()
    run(main)
    assert kinds(repo) == [("baseline", 0)]


def test_repeated_events_coalesce_to_latest_per_kind(repo, monkeypatch):
    enable(repo)
    calls = []
    orig = jobs._capture_job

    def slow(s, kind, pre_trusted):
        calls.append(kind)
        time.sleep(0.4)
        return orig(s, kind, pre_trusted)

    monkeypatch.setattr(jobs, "_capture_job", slow)

    async def main():
        ev("prompt", repo)
        await asyncio.sleep(0.15)                                           # 첫 작업이 돌기 시작
        for _ in range(5):
            ev("stop", repo)
        await settle()
    run(main)
    assert calls == ["pre-turn", "turn-end"]                                # stop 5번 → 한 번(대기 칸 하나, 최신으로 덮음)


def test_pool_saturation_skips_instead_of_queueing(repo, monkeypatch):
    enable(repo)
    real = res_pools.run

    async def busy(pool, fn, *a, **k):
        if pool == "ckpt" and getattr(fn, "__name__", "") == "_capture_job":
            raise res_pools.Busy("ckpt")
        return await real(pool, fn, *a, **k)

    monkeypatch.setattr(res_pools, "run", busy)

    async def main():
        ev("session_start", repo)
        await settle()
    run(main)
    assert checkpoints.list_refs(repo) == []
    assert list(jobs._last.values())[0]["status"] == "pool_busy"


def test_three_failures_back_off_then_recover_when_cleared(repo, monkeypatch):
    enable(repo)
    seen = []

    def failing(s, kind, pre_trusted):
        seen.append(kind)
        return {"status": "timeout"}

    monkeypatch.setattr(jobs, "_capture_job", failing)

    async def main():
        for i in range(5):
            ev("stop", repo)
            await settle()
    run(main)
    assert len(seen) == jobs.FAILS_BEFORE_BACKOFF                           # 3번 실패 뒤 5분 쉰다
    assert list(jobs._last.values())[0]["status"] == "backoff"
    jobs._backoff.clear(list(jobs._last)[0])
    assert jobs._backoff.should_skip(list(jobs._last)[0]) is False


def test_turn_numbers_continue_after_a_server_restart(repo):
    enable(repo)

    async def first():
        ev("session_start", repo)
        await settle()
        ev("prompt", repo)
        await settle()
        (repo / "a.txt").write_text("changed")
        ev("stop", repo)
        await settle()
    run(first)
    assert kinds(repo) == [("baseline", 0), ("turn-end", 1)]          # 사람이 안 고쳤으니 pre-turn은 baseline과 같은 상태 — ref 없음
    jobs._sessions.clear()                                                  # 서버 재시작 — 장부를 잃는다

    async def second():
        (repo / "b.txt").write_text("b")
        ev("prompt", repo)
        await settle()
        (repo / "c.txt").write_text("c")
        ev("stop", repo)
        await settle()
    run(second)
    es = checkpoints.list_refs(repo)
    assert kinds(repo) == [("baseline", 0), ("turn-end", 1), ("pre-turn", 2), ("turn-end", 2)]
    assert es[-1]["pre_trusted"] is True


def test_stop_after_restart_mid_turn_keeps_the_turn_number_and_does_not_trust(repo):
    enable(repo)

    async def first():
        ev("session_start", repo)
        await settle()
        (repo / "h.txt").write_text("h")
        ev("prompt", repo)
        await settle()
    run(first)
    jobs._sessions.clear()                                                  # 턴 도중 재시작

    async def second():
        (repo / "a.txt").write_text("agent")
        ev("stop", repo)
        await settle()
    run(second)
    es = checkpoints.list_refs(repo)
    assert (es[-1]["kind"], es[-1]["turn"]) == ("turn-end", 1) and es[-1]["pre_trusted"] is False       # 장부가 없으므로 보수적으로


def test_turning_it_off_midway_stops_work(repo):
    enable(repo)

    async def main():
        ev("session_start", repo)
        await settle()
        checkpoint_store.set_enabled(checkpoints.repo_key(repo)["key"], False)
        (repo / "x.txt").write_text("x")
        ev("prompt", repo)
        await settle()
    run(main)
    assert kinds(repo) == [("baseline", 0)]


def test_two_sessions_in_one_worktree_do_not_mix(repo):
    enable(repo)

    async def main():
        ev("session_start", repo, sid="s-a")
        ev("session_start", repo, sid="s-b", agent="codex")
        await settle()
    run(main)
    es = checkpoints.list_refs(repo)
    assert sorted(e["agent"] for e in es) == ["claude", "codex"] and len({e["sess"] for e in es}) == 2


def test_session_key_is_agent_scoped_and_stable():
    a = jobs.session_key("claude", "x")
    assert a == jobs.session_key("claude", "x") and a != jobs.session_key("codex", "x") and len(a) == 16


def test_labels_stay_in_memory_only(repo):
    enable(repo)

    async def main():
        ev("session_start", repo, state={"tmux_session": "my-private-name"})
        await settle()
    run(main)
    e = checkpoints.list_refs(repo)[0]
    assert jobs.labels(e["sess"]) == {"tmux": "my-private-name"}
    assert "my-private-name" not in subprocess.run(["git", "-C", str(repo), "log", "--all", "--format=%B"], capture_output=True, text=True).stdout
