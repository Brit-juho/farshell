"""업데이트 확인(1단계)과 자동 적용(2단계) — 가짜 원격(bare)과 시험용 SSH 서명 키만 쓴다.

실제 GitHub·실제 ~/.vt·실서비스는 쓰지 않는다. 점검기는 conftest 가 `VT_UPDATE_CHECK=0` 으로 꺼 두므로 여기서는 fixture 가 켠다.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import agent_status
import pane_input
import update_check as uc
import updater
from resilience import proc as res_proc
from update_testkit import isolate_env, make_world, release, sh, sign_tag, trust

pytestmark = pytest.mark.skipif(not (shutil.which("git") and shutil.which("ssh-keygen")), reason="git/ssh-keygen 필요")


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    isolate_env(tmp_path, monkeypatch)
    monkeypatch.setattr(updater, "post_checkout", lambda root, old, new: None)
    pane_input._reset_for_tests()
    yield


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


def run(coro):
    return asyncio.run(coro)


def stable(world):
    updater.write_channel("stable")
    trust(world)
    return world["inst"]


# ── 설정 ─────────────────────────────────────────────────────────────────
def test_settings_defaults_and_corrupt_file_fall_back():
    assert uc.load_settings() == {"check": True, "auto": "off", "auto_hour": 4}
    uc.settings_path().parent.mkdir(parents=True, exist_ok=True)
    uc.settings_path().write_text("{not json")
    assert uc.load_settings()["auto"] == "off"
    uc.settings_path().write_text(json.dumps({"check": "yes", "auto": "everything", "auto_hour": 99}))
    assert uc.load_settings() == {"check": True, "auto": "off", "auto_hour": 4}   # 항목별로 기본값


def test_save_settings_validates_and_writes_atomically_with_mode_0600():
    with pytest.raises(ValueError):
        uc.save_settings({"auto": "dev-too"})
    with pytest.raises(ValueError):
        uc.save_settings({"auto_hour": 24})
    with pytest.raises(ValueError):
        uc.save_settings({"auto_hour": True})
    with pytest.raises(ValueError):
        uc.save_settings({"channel": "dev"})           # 알 수 없는 키
    with pytest.raises(ValueError):
        uc.save_settings({})
    out = uc.save_settings({"auto": "stable-only", "auto_hour": 3})
    assert out == {"check": True, "auto": "stable-only", "auto_hour": 3}
    assert uc.load_settings() == out
    assert oct(uc.settings_path().stat().st_mode & 0o777) == "0o600"
    assert not [p for p in uc.settings_path().parent.iterdir() if p.name.endswith(".vt-tmp")]


def test_env_switch_turns_the_check_off(monkeypatch):
    assert uc.check_enabled()
    monkeypatch.setenv("VT_UPDATE_CHECK", "0")
    assert not uc.check_enabled()
    monkeypatch.delenv("VT_UPDATE_CHECK")
    uc.save_settings({"check": False})
    assert not uc.check_enabled()


# ── 확인 ─────────────────────────────────────────────────────────────────
def test_stable_check_reports_signed_newer_tag(world):
    inst = stable(world)
    st = uc.check_once(inst, now=1000.0)
    assert (st["channel"], st["current"], st["latest"], st["latest_tag"]) == ("stable", "1.0.0", "1.1.0", "v1.1.0")
    assert st["available"] is True and st["signature_ok"] is True and st["signer"].startswith("SHA256:")
    assert st["error"] is None and st["blocked"] is None and st["checked_at"] == 1000.0
    assert uc.read_status() == st
    assert oct(uc.status_path().stat().st_mode & 0o777) == "0o600"


def test_check_on_the_latest_release_says_not_available(world):
    inst = stable(world)
    sh(inst, "git", "reset", "-q", "--hard", "v1.1.0")
    st = uc.check_once(inst)
    assert st["available"] is False and st["latest"] == "1.1.0" and st["signature_ok"] is True


def test_check_refuses_a_tag_signed_by_another_key(world):
    inst = stable(world)
    release(world, "1.2.0", key=world["other"])
    st = uc.check_once(inst)
    assert st["available"] is False and st["signature_ok"] is False
    assert st["latest_tag"] == "v1.2.0" and "맞지 않습니다" in st["error"]


def test_check_without_a_pinned_key_still_shows_the_tag_but_marks_it_unverified(world):
    updater.write_channel("stable")
    st = uc.check_once(world["inst"])
    assert st["available"] is True and st["signature_ok"] is None and st["blocked"] == "no_trusted_key"


def test_dev_channel_compares_origin_dev_with_head(world):
    inst = world["inst"]
    updater.write_channel("dev")
    st = uc.check_once(inst)
    assert st["channel"] == "dev" and st["available"] is True and len(st["latest"]) == 8 and st["latest_tag"] is None
    sh(inst, "git", "reset", "-q", "--hard", "origin/dev")
    assert uc.check_once(inst)["available"] is False


def test_devmode_and_unset_channel_only_look(world):
    inst = stable(world)
    updater.write_channel("devmode")
    st = uc.check_once(inst)
    assert st["available"] is True and st["blocked"] == "devmode"
    (updater.state_dir() / "update-channel").unlink()
    sh(inst, "git", "checkout", "-q", "-b", "feature")
    st = uc.check_once(inst)
    assert st["channel"] is None and st["blocked"] == "channel_unset" and st["latest_tag"] == "v1.1.0"


def test_check_marks_a_dirty_checkout_as_not_applicable(world):
    inst = stable(world)
    (inst / "app.txt").write_text("local\n")
    st = uc.check_once(inst)
    assert st["available"] is True and st["blocked"] == "dirty"
    assert uc.public_status()["applicable"] is False


def test_a_network_failure_keeps_what_was_known_and_records_the_error(world):
    inst = stable(world)
    assert uc.check_once(inst, now=1.0)["available"] is True
    sh(inst, "git", "remote", "set-url", "origin", str(world["origin"]) + ".gone")
    st = uc.check_once(inst, now=2.0)
    assert st["error"] and st["available"] is True and st["latest_tag"] == "v1.1.0" and st["checked_at"] == 2.0


def test_disabled_check_makes_no_network_request_at_all(world, monkeypatch):
    inst = stable(world)
    calls = []
    monkeypatch.setattr(updater, "fetch", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(res_proc, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a))
    uc.save_settings({"check": False})
    uc.check_once(inst)
    state = uc.LoopState(0.0, random.Random(1))
    assert run(uc.tick(inst, 10**9, state)) == "disabled"
    monkeypatch.setenv("VT_UPDATE_CHECK", "0")
    uc.save_settings({"check": True})
    uc.check_once(inst)
    assert run(uc.tick(inst, 10**9, state)) == "disabled"
    assert run(uc.manual_check(inst))[1] in (True, False) and calls == []


def test_checks_run_through_proc_run_with_a_deadline(world, monkeypatch):
    """서버 안의 점검은 `proc.run`(새 세션·기한에 그룹째 kill)으로만 git 을 돌린다 — 멈춘 원격이 스레드를 붙잡지 않게."""
    inst = stable(world)
    seen = []
    real = res_proc.run

    def spy(argv, **kw):
        seen.append((argv[0], kw.get("timeout")))
        return real(argv, **kw)
    monkeypatch.setattr(res_proc, "run", spy)

    def no_plain(*a, **k):
        raise AssertionError("subprocess.run 직접 호출")
    monkeypatch.setattr(updater.subprocess, "run", no_plain)
    uc.check_once(inst)
    assert seen and all(cmd == "git" and (to or 0) > 0 for cmd, to in seen)


def test_a_hung_fetch_is_abandoned_at_the_deadline(world, monkeypatch, tmp_path):
    """fetch 가 영원히 도는 가짜 git — 기한에 죽이고 오류로 남기며 점검이 끝난다."""
    inst = stable(world)
    fake = tmp_path / "bin"
    fake.mkdir()
    g = fake / "git"
    g.write_text('#!/bin/sh\ncase " $* " in *" fetch "*) sleep 30;; *) exec /usr/bin/git "$@";; esac\n')
    g.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    monkeypatch.setattr(uc, "FETCH_TIMEOUT", 1)
    t0 = time.monotonic()
    st = uc.check_once(inst)
    assert time.monotonic() - t0 < 10 and st["error"] and "끝나지 않았습니다" in st["error"]


# ── 상태 파일 ────────────────────────────────────────────────────────────
def test_corrupt_status_file_is_set_aside_and_replaced():
    uc.status_path().parent.mkdir(parents=True, exist_ok=True)
    uc.status_path().write_text("{truncated")
    assert uc.read_status() == uc.empty_status()
    st = uc.mutate_status(lambda s: s.update(current="9.9.9"))
    assert st["current"] == "9.9.9" and json.loads(uc.status_path().read_text())["current"] == "9.9.9"
    assert uc.status_path().with_name("update-status.json.corrupt").read_text() == "{truncated"
    uc.status_path().write_text("[1,2]")
    assert uc.read_status() == uc.empty_status()


def test_status_with_wrong_types_is_normalized():
    uc.status_path().parent.mkdir(parents=True, exist_ok=True)
    uc.status_path().write_text(json.dumps({"auto": "x", "attempts": 3, "last_apply": "no", "current": "1.0.0"}))
    st = uc.read_status()
    assert st["auto"] == uc.empty_status()["auto"] and st["attempts"] == {"version": None, "count": 0}
    assert st["last_apply"] is None and st["current"] == "1.0.0"


def test_concurrent_status_writers_do_not_lose_updates():
    def bump(st):
        st["attempts"]["count"] += 1
    threads = [threading.Thread(target=lambda: [uc.mutate_status(bump) for _ in range(10)]) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert uc.read_status()["attempts"]["count"] == 60
    assert not [p for p in uc.status_path().parent.iterdir() if p.name.endswith(".vt-tmp")]


def test_refresh_current_after_an_update_clears_available(world):
    inst = stable(world)
    uc.check_once(inst)
    sh(inst, "git", "reset", "-q", "--hard", "v1.1.0")
    st = uc.refresh_current(inst)
    assert st["current"] == "1.1.0" and st["available"] is False


def test_refresh_current_marks_an_interrupted_apply_as_failed(world):
    inst = stable(world)
    uc.record_apply("running", frm="1.0.0", to="1.1.0")
    st = uc.refresh_current(inst)
    assert st["last_apply"]["result"] == "failed" and "끊겼" in st["last_apply"]["reason"]


# ── 자동 적용 판정 ───────────────────────────────────────────────────────
def test_auto_blockers_accept_a_signed_patch_release(world):
    inst = stable(world)
    release(world, "1.0.1")
    updater.fetch(inst)
    uc.save_settings({"auto": "stable-only"})
    assert uc.auto_blockers(inst, "v1.0.1") is None


@pytest.mark.parametrize("case", ["off", "channel", "major", "dirty", "no_key", "wrong_key", "branch", "not_master", "same", "bad"])
def test_auto_blockers_refuse(world, case):
    inst = world["inst"]
    updater.write_channel("stable")
    trust(world)
    uc.save_settings({"auto": "stable-only"})
    tag, expect = "v1.0.1", case
    if case == "off":
        uc.save_settings({"auto": "off"}); release(world, "1.0.1")
    elif case == "channel":
        updater.write_channel("dev"); release(world, "1.0.1")
    elif case == "major":
        tag = release(world, "2.0.0")
    elif case == "dirty":
        release(world, "1.0.1"); (inst / "app.txt").write_text("local\n")
    elif case == "no_key":
        release(world, "1.0.1"); updater.signers_file().unlink(); expect = "no_trusted_key"
    elif case == "wrong_key":
        release(world, "1.0.1", key=world["other"]); expect = "signature"
    elif case == "branch":
        release(world, "1.0.1"); sh(inst, "git", "checkout", "-q", "-b", "feature")
    elif case == "not_master":
        release(world, "1.0.1", on_master=False); expect = "signature"
    elif case == "same":
        tag = "v1.0.0"; expect = "not_newer"
    elif case == "bad":
        tag = "latest"; expect = "bad_tag"
    updater.fetch(inst)
    got = uc.auto_blockers(inst, tag)
    assert got is not None and got[0] == {"off": "auto_off"}.get(expect, expect), got
    assert got[1]


def test_auto_blockers_never_trust_automatically(world):
    """신뢰 키가 없으면 어떤 태그도 적용하지 않는다 — TOFU 는 사람이 `fsh update trust` 로 한다."""
    updater.write_channel("stable")
    uc.save_settings({"auto": "stable-only"})
    release(world, "1.0.1")
    updater.fetch(world["inst"])
    assert uc.auto_blockers(world["inst"], "v1.0.1")[0] == "no_trusted_key"
    assert not updater.signers_file().exists()


# ── 일정(가짜 시계) ──────────────────────────────────────────────────────
def fake_status(available=True, tag="v1.1.0", checked=0.0, first_seen=0.0, attempts=None, policy=None):
    def f(st):
        st.update(available=available, latest_tag=tag, latest=tag[1:], checked_at=checked, current="1.0.0", channel="stable",
                  signature_ok=True)
        st["auto"].update(first_seen=first_seen if available else None, policy=policy, policy_tag=tag if policy is not None else None)
        if attempts is not None:
            st["attempts"] = attempts
    uc.mutate_status(f)


class Spy:
    def __init__(self, blocked=None, idle=(True, None)):
        self.checks, self.applies, self.blocked, self._idle = [], [], blocked, idle
        self.events = []

    async def check(self, root, now):
        self.checks.append(now)
        return uc.mutate_status(lambda st: st.update(checked_at=now))

    async def blockers(self, root, tag, s):
        return self.blocked

    def idle(self):
        return self._idle

    async def apply(self, root, tag):
        self.applies.append(tag)

    async def notify(self, msg):
        self.events.append(msg)


def tick(spy, state, now, *, hour=4, root=Path("/nonexistent")):
    return run(uc.tick(root, now, state, check=spy.check, blockers=spy.blockers, idle=spy.idle, apply=spy.apply,
                       notify=spy.notify, local_hour=hour))


def test_first_check_waits_for_jitter_then_repeats_every_24_hours():
    state = uc.LoopState(1000.0, random.Random(7))
    assert 1000 + 60 <= state.first_due <= 1000 + 600
    spy = Spy()
    assert tick(spy, state, 1000.0 + 59) == "idle" and spy.checks == []
    assert tick(spy, state, state.first_due) == "checked" and spy.checks == [state.first_due]
    t = state.first_due
    assert tick(spy, state, t + 300) == "idle"
    assert tick(spy, state, t + 24 * 3600 - 1) == "idle"
    assert tick(spy, state, t + 24 * 3600) == "checked"
    assert len(spy.checks) == 2


def test_jitter_differs_between_starts():
    assert len({uc.LoopState(0.0, random.Random(i)).first_due for i in range(8)}) > 1


def test_auto_off_never_applies_even_when_everything_else_matches():
    fake_status()
    spy = Spy()
    state = uc.LoopState(0.0, random.Random(1)); state.did_first = True
    assert tick(spy, state, 100.0) == "idle" and spy.applies == []


def test_auto_waits_outside_the_window_and_applies_inside_it():
    uc.save_settings({"auto": "stable-only", "auto_hour": 4})
    fake_status()
    spy = Spy()
    state = uc.LoopState(0.0, random.Random(1)); state.did_first = True
    assert tick(spy, state, 100.0, hour=3) == "wait:waiting_window" and spy.applies == []
    assert uc.read_status()["auto"]["reason"] == "waiting_window"
    assert tick(spy, state, 200.0, hour=4) == "applied"
    assert spy.applies == ["v1.1.0"]
    assert uc.read_status()["attempts"] == {"version": "v1.1.0", "count": 1}


@pytest.mark.parametrize("why", ["terminal_input", "agent_busy"])
def test_auto_defers_while_busy(why):
    uc.save_settings({"auto": "stable-only", "auto_hour": 4})
    fake_status()
    spy = Spy(idle=(False, why))
    state = uc.LoopState(0.0, random.Random(1)); state.did_first = True
    assert tick(spy, state, 100.0) == f"wait:busy_{why}" and spy.applies == []
    spy._idle = (True, None)
    assert tick(spy, state, 400.0) == "applied"


def test_auto_gives_up_after_three_attempts_for_the_same_version():
    uc.save_settings({"auto": "stable-only"})
    fake_status(attempts={"version": "v1.1.0", "count": 3})
    spy = Spy()
    state = uc.LoopState(0.0, random.Random(1)); state.did_first = True
    assert tick(spy, state, 100.0) == "wait:gave_up" and spy.applies == []
    # 다른(더 새) 버전이면 시도 횟수는 새로 센다
    fake_status(tag="v1.1.1", attempts={"version": "v1.1.0", "count": 3})
    assert tick(spy, state, 200.0) == "applied" and spy.applies == ["v1.1.1"]


def test_auto_stops_after_seven_days_of_deferral():
    uc.save_settings({"auto": "stable-only"})
    day = 24 * 3600.0
    fake_status(first_seen=0.0, checked=0.0, policy="")
    spy = Spy(idle=(False, "terminal_input"))
    state = uc.LoopState(0.0, random.Random(1)); state.did_first = True
    assert tick(spy, state, 6.9 * day) == "wait:busy_terminal_input"
    assert tick(spy, state, 7.1 * day) == "wait:stalled" and spy.applies == []
    assert uc.read_status()["auto"]["reason"] == "stalled"


def test_policy_block_is_recorded_and_nothing_is_applied():
    uc.save_settings({"auto": "stable-only"})
    fake_status()
    spy = Spy(blocked=("dirty", "로컬 변경"))
    state = uc.LoopState(0.0, random.Random(1)); state.did_first = True
    assert tick(spy, state, 100.0, hour=4) == "wait:dirty" and spy.applies == []
    assert uc.read_status()["auto"]["reason"] == "dirty"


def test_a_stale_check_is_repeated_right_before_applying():
    uc.save_settings({"auto": "stable-only"})
    fake_status(checked=0.0)
    spy = Spy()
    state = uc.LoopState(0.0, random.Random(1)); state.did_first = True
    t = uc.FRESH_SEC + 10
    assert tick(spy, state, t) == "applied"
    assert spy.checks == [t]


def test_idle_reads_human_input_and_agent_states(monkeypatch):
    assert uc.is_idle(15) == (True, None)
    monkeypatch.setattr(pane_input, "_human_input_at", {"s": time.monotonic() - 60})
    assert uc.is_idle(15) == (False, "terminal_input")
    assert uc.is_idle(0.5) == (True, None)
    monkeypatch.setattr(pane_input, "_human_input_at", {})
    monkeypatch.setattr(agent_status, "_state", {"a": {"status": "idle"}, "b": {"status": "working"}})
    assert uc.is_idle(15) == (False, "agent_busy")
    monkeypatch.setattr(agent_status, "_state", {"a": {"status": "waiting"}})
    assert uc.is_idle(15) == (False, "agent_busy")
    monkeypatch.setattr(agent_status, "_state", {"a": {"status": "done"}})
    assert uc.is_idle(15) == (True, None)


# ── 적용(자식) ───────────────────────────────────────────────────────────
def test_apply_main_moves_to_the_new_tag_and_records_it(world):
    inst = stable(world)
    uc.save_settings({"auto": "stable-only"})
    release(world, "1.0.1")
    assert uc.apply_main(inst, "v1.0.1") == 0
    assert (inst / "VERSION").read_text().strip() == "1.0.1"
    la = uc.read_status()["last_apply"]
    assert (la["result"], la["from"], la["to"], la["rolled_back"]) == ("ok", "1.0.0", "1.0.1", False)
    prev = json.loads((updater.state_dir() / "update-prev.json").read_text())
    assert prev["version"] == "1.0.0"                         # 롤백 지점


def test_apply_main_rolls_back_and_records_when_the_build_fails(world, monkeypatch):
    inst = stable(world)
    uc.save_settings({"auto": "stable-only"})
    release(world, "1.0.1")
    before = sh(inst, "git", "rev-parse", "HEAD")

    def broken(root, old, new):
        raise updater.UpdateError("프런트엔드 빌드 실패: boom")
    monkeypatch.setattr(updater, "post_checkout", broken)
    assert uc.apply_main(inst, "v1.0.1") == 1
    assert sh(inst, "git", "rev-parse", "HEAD") == before and (inst / "VERSION").read_text().strip() == "1.0.0"
    la = uc.read_status()["last_apply"]
    assert la["result"] == "failed" and la["rolled_back"] is True and "boom" in la["reason"]


def test_apply_main_rechecks_policy_and_changes_nothing_when_refused(world):
    inst = stable(world)
    uc.save_settings({"auto": "stable-only"})
    tag = release(world, "2.0.0")
    before = sh(inst, "git", "rev-parse", "HEAD")
    assert uc.apply_main(inst, tag) == 2
    assert sh(inst, "git", "rev-parse", "HEAD") == before
    la = uc.read_status()["last_apply"]
    assert la["result"] == "failed" and "메이저" in la["reason"]


def test_apply_main_refuses_a_dirty_checkout_without_touching_it(world):
    inst = stable(world)
    uc.save_settings({"auto": "stable-only"})
    release(world, "1.0.1")
    (inst / "app.txt").write_text("local edit\n")
    assert uc.apply_main(inst, "v1.0.1") == 2
    assert (inst / "app.txt").read_text() == "local edit\n" and (inst / "VERSION").read_text().strip() == "1.0.0"


def test_only_one_update_runs_at_a_time(world):
    inst = stable(world)
    uc.save_settings({"auto": "stable-only"})
    release(world, "1.0.1")
    with updater.update_lock():
        assert uc.apply_main(inst, "v1.0.1") == 3                       # 자동 적용은 물러난다
        assert uc.read_status()["last_apply"] is None                   # 돌고 있는 쪽의 기록을 덮지 않는다
        with pytest.raises(updater.UpdateError, match="이미 진행 중"):
            with updater.update_lock():
                pass
        assert updater.main(["--rollback"]) == 1                         # 수동 롤백·업데이트도 같은 잠금
        assert updater.main([]) == 1
    assert (inst / "VERSION").read_text().strip() == "1.0.0"
    assert uc.apply_main(inst, "v1.0.1") == 0                           # 잠금이 풀리면 된다


def test_check_only_update_does_not_need_the_lock(world, monkeypatch):
    inst = stable(world)
    monkeypatch.setenv("VT_DIR", str(inst))
    with updater.update_lock():
        assert updater.main(["--check"]) == 0


# ── 적용(서버 쪽) ────────────────────────────────────────────────────────
def fake_root(tmp_path):
    (tmp_path / "root" / "server").mkdir(parents=True, exist_ok=True)
    return tmp_path / "root"


def test_server_notifies_then_restarts_after_a_successful_child(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(
        "import sys; sys.path.insert(0, %r)\nimport update_check as uc\nuc.record_apply('ok', frm='1.0.0', to='1.0.1')\n"
        % str(Path(uc.__file__).parent))
    events, restarts = [], []

    async def notify(msg):
        events.append(msg)
    ok = run(uc.apply_via_child(fake_root(tmp_path), "v1.0.1", notify=notify, restart=lambda r: restarts.append(r),
                                cmd=[sys.executable, str(script)], restart_delay=0))
    assert ok and restarts == [tmp_path / "root"]
    assert events == [{"type": "update_restarting", "version": "1.0.1"}]


def test_server_does_not_restart_after_a_failed_child(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(
        "import sys; sys.path.insert(0, %r)\nimport update_check as uc\nuc.record_apply('failed', frm='1.0.0', to='1.0.1', reason='x', rolled_back=True)\nsys.exit(1)\n"
        % str(Path(uc.__file__).parent))
    events, restarts = [], []

    async def notify(msg):
        events.append(msg)
    assert not run(uc.apply_via_child(fake_root(tmp_path), "v1.0.1", notify=notify, restart=lambda r: restarts.append(r),
                                      cmd=[sys.executable, str(script)], restart_delay=0))
    assert restarts == [] and events == [{"type": "update_status", "kind": "failed"}]


def test_server_survives_a_child_that_cannot_start(tmp_path):
    async def notify(msg):
        pass
    assert not run(uc.apply_via_child(fake_root(tmp_path), "v1.0.1", notify=notify, cmd=["/nonexistent/binary"], restart_delay=0))
    assert uc.read_status()["last_apply"]["result"] == "failed"


def test_the_server_does_not_overwrite_its_own_code_in_process():
    """서버는 체크아웃을 바꾸지 않는다 — 코드를 옮기는 함수(cmd_update)를 이 모듈의 서버 쪽 경로가 부르지 않는다."""
    src = Path(uc.__file__).read_text()
    server_side = src[src.index("# ── 적용(서버 쪽"):src.index("# ── 적용(자식 쪽)")]
    assert "cmd_update" not in server_side and "post_checkout" not in server_side


def test_cli_status_line_and_auto_toggle(world, capsys):
    inst = stable(world)
    assert uc.summary_line()[0] == "unknown"
    uc.check_once(inst)
    assert uc.summary_line() == ("available", "1.1.0")
    sh(inst, "git", "reset", "-q", "--hard", "v1.1.0")
    uc.check_once(inst)
    assert uc.summary_line() == ("latest", "1.1.0")
    uc.save_settings({"check": False})
    assert uc.summary_line() == ("off", "")
    uc.save_settings({"check": True})
    assert uc.cmd_auto(inst, "on", yes=True, hour=2) == 0
    assert uc.load_settings()["auto"] == "stable-only" and uc.load_settings()["auto_hour"] == 2
    assert uc.cmd_auto(inst, "off") == 0 and uc.load_settings()["auto"] == "off"
    with pytest.raises(updater.UpdateError, match="--yes"):          # 비대화형은 --yes 없이 못 켠다
        uc.cmd_auto(inst, "on")
    assert uc.load_settings()["auto"] == "off"
    capsys.readouterr()
    assert updater.main(["status", "--line"]) == 0
    assert capsys.readouterr().out.strip() == "latest 1.1.0"
