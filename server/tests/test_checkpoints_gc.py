"""체크포인트 정리(`checkpoints.gc`) — 세션당 최근 N개·기한·복원 안전판 기한·총량 상한, 다른 작업 폴더의 ref는 건드리지 않는다.

시계는 `now=`로 주입한다(실제 커밋 시각을 그대로 두고 "미래의 지금"에서 본다). git 객체는 건드리지 않는다(gc/prune 안 부름).
"""
import os
import subprocess
import time

import pytest

import checkpoints
from test_checkpoints_capture import g, repo, cap, SESS  # noqa: F401  (fixture·헬퍼 재사용)

DAY = 86400


def make(repo, n, sess=SESS, kind="turn-end", first_kind="baseline"):
    out = []
    for i in range(n):
        (repo / "f.txt").write_text(f"{sess}-{i}-{time.time_ns()}")
        r = checkpoints.capture(repo, kind=first_kind if i == 0 else kind, sess_key=sess, agent="claude", turn=i)
        assert r["status"] == "ok", r
        out.append(r)
    return out


def refs(repo):
    return [e["id"] for e in checkpoints.list_refs(repo)]


def test_keeps_baseline_plus_last_n_turns(repo, monkeypatch):
    monkeypatch.setenv("VT_CKPT_KEEP_TURNS", "3")
    made = make(repo, 8)                                  # baseline + 7 턴
    res = checkpoints.gc(repo)
    assert res == {"deleted": 4, "kept": 4}
    assert refs(repo) == [made[0]["id"]] + [m["id"] for m in made[-3:]]


def test_nothing_to_delete_is_a_noop(repo):
    make(repo, 3)
    assert checkpoints.gc(repo) == {"deleted": 0, "kept": 3}


def test_idle_session_removed_as_a_whole_after_keep_days(repo, monkeypatch):
    monkeypatch.setenv("VT_CKPT_KEEP_DAYS", "14")
    a = make(repo, 3, sess="a" * 16)
    time.sleep(2.1)                                        # committerdate는 초 단위 — b가 확실히 더 새롭게
    b = make(repo, 2, sess="b" * 16)
    es = checkpoints.list_refs(repo)
    a_last = max(e["created"] for e in es if e["sess"] == "a" * 16)
    assert checkpoints.gc(repo, now=a_last + 14 * DAY - 1)["deleted"] == 0      # 14일이 안 지났다
    res = checkpoints.gc(repo, now=a_last + 14 * DAY + 1)                      # a만 14일을 넘겼다(b는 2초 이상 새롭다)
    assert res == {"deleted": 3, "kept": 2}
    assert refs(repo) == [m["id"] for m in b]


def test_restore_safety_refs_expire_after_seven_days(repo):
    (repo / "n.txt").write_text("1")
    r = checkpoints.capture(repo, kind="pre-restore")
    keep = make(repo, 2)
    created = [e for e in checkpoints.list_refs(repo) if e["restore"]][0]["created"]
    assert checkpoints.gc(repo, now=created + 7 * DAY - 1)["deleted"] == 0
    assert checkpoints.gc(repo, now=created + 7 * DAY + 1) == {"deleted": 1, "kept": 2}
    assert r["id"] not in refs(repo) and all(m["id"] in refs(repo) for m in keep)


def test_total_cap_removes_oldest_first(repo, monkeypatch):
    monkeypatch.setenv("VT_CKPT_MAX_REFS", "10")
    monkeypatch.setenv("VT_CKPT_KEEP_TURNS", "1000")
    a = make(repo, 8, sess="a" * 16)
    time.sleep(1.1)                                        # committerdate는 초 단위 — b가 확실히 더 새롭게
    b = make(repo, 8, sess="b" * 16)
    res = checkpoints.gc(repo)
    assert res == {"deleted": 6, "kept": 10}
    left = set(refs(repo))
    assert all(m["id"] in left for m in b)                 # 새 세션은 그대로
    assert sum(1 for m in a if m["id"] in left) == 2       # 오래된 세션에서 6개가 빠졌다


def test_other_worktrees_refs_untouched(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("VT_CKPT_KEEP_TURNS", "1")
    wt = tmp_path / "wt"
    g(repo, "worktree", "add", "-q", "-b", "feat", str(wt))
    make(repo, 4)
    (wt / "w.txt").write_text("w")
    other = checkpoints.capture(wt, kind="turn-end", sess_key=SESS)
    assert other["status"] == "ok"
    checkpoints.gc(repo)
    assert [e["id"] for e in checkpoints.list_refs(wt)] == [other["id"]]      # 다른 작업 폴더의 ref는 그대로
    assert len(checkpoints.list_refs(repo)) == 2                              # baseline + 최근 1


def test_foreign_refs_and_objects_are_left_alone(repo, monkeypatch):
    monkeypatch.setenv("VT_CKPT_KEEP_TURNS", "1")
    made = make(repo, 3)
    wt12 = made[0]["ref"].split("/")[4]
    plain = g(repo, "commit-tree", g(repo, "write-tree").strip(), "-m", "not ours").strip()
    g(repo, "update-ref", f"refs/fsh/ckpt/v1/{wt12}/{'f' * 16}/000001", plain)
    g(repo, "update-ref", "refs/heads/keepme", plain)
    checkpoints.gc(repo)
    assert g(repo, "rev-parse", f"refs/fsh/ckpt/v1/{wt12}/{'f' * 16}/000001").strip() == plain
    assert g(repo, "rev-parse", "refs/heads/keepme").strip() == plain
    assert g(repo, "cat-file", "-t", made[1]["commit"]).strip() == "commit"     # 지운 ref의 객체는 남는다(gc/prune 안 함)


def test_not_a_repo_returns_zero(tmp_path):
    assert checkpoints.gc(tmp_path) == {"deleted": 0, "kept": 0}
