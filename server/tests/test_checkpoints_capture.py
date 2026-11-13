"""에이전트 턴 체크포인트(`checkpoints.py`) — 캡처·목록·턴 파일·diff.

지키는 것: 캡처가 사용자 저장소의 HEAD·브랜치·인덱스 바이트·`git status`를 바꾸지 않는다, 추적 안 된 파일은 담고 ignore는 뺀다,
거부 이름(.env·*.pem·.ssh/·점 디렉터리)·큰 파일·중첩 저장소는 뺀다, 같은 상태면 ref를 안 만든다, 첫 커밋 전·linked worktree·
사용자 git 신원 없음·서명 강제·split index에서도 성공, 악성 저장소 설정(fsmonitor·hooksPath)이 프로그램을 못 띄운다,
기한을 넘기면 프로세스를 죽이고 `timeout`, 임시 인덱스가 남지 않는다.
"""
import hashlib
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

import checkpoints

SESS = "0123456789abcdef"


def g(repo, *args, check=True, env=None):
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid", **(env or {})}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=e)
    if check and r.returncode:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r.stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    g(r, "init", "-q", "-b", "main")
    g(r, "config", "user.name", "t")
    g(r, "config", "user.email", "t@x.invalid")
    (r / "a.txt").write_text("a1\n")
    (r / "b.txt").write_text("b1\n")
    (r / ".gitignore").write_text("ignored/\n*.log\n")
    g(r, "add", "-A")
    g(r, "commit", "-q", "-m", "init")
    return r


def state(repo):
    gd = Path(g(repo, "rev-parse", "--absolute-git-dir").strip())
    return {
        "head": g(repo, "rev-parse", "HEAD", check=False).strip(),
        "branch": g(repo, "branch", "--show-current").strip(),
        "index": hashlib.sha1((gd / "index").read_bytes()).hexdigest() if (gd / "index").exists() else None,
        "status": g(repo, "status", "--porcelain=v1", "-uall"),
        "heads": g(repo, "for-each-ref", "refs/heads"),
    }


def tree_files(repo, ref):
    return set(g(repo, "ls-tree", "-r", "--name-only", ref).split())


def cap(repo, **kw):
    kw.setdefault("kind", "turn-end")
    kw.setdefault("sess_key", SESS)
    return checkpoints.capture(repo, **kw)


def test_capture_creates_ref_and_leaves_user_repo_untouched(repo):
    (repo / "a.txt").write_text("a2\n")                  # 추적 중 수정
    (repo / "new.txt").write_text("n\n")                 # 추적 안 됨
    (repo / "b.txt").unlink()                            # 삭제
    g(repo, "add", "a.txt")                              # 일부는 스테이징돼 있다
    (repo / "a.txt").write_text("a3\n")                  # 스테이징 뒤 또 수정
    state(repo)                                          # `git status`가 인덱스의 stat 캐시를 갱신하므로 한 번 돌려 안정시킨 뒤 비교한다
    before = state(repo)
    r = cap(repo, agent="claude", turn=7)
    assert r["status"] == "ok", r
    assert r["ref"].startswith("refs/fsh/ckpt/v1/") and r["ref"].endswith("/000001")
    assert r["id"] == "v1/" + r["ref"][len("refs/fsh/ckpt/v1/"):]
    assert checkpoints.ID_RE.match(r["id"])
    assert state(repo) == before                         # HEAD·브랜치·인덱스 바이트·status·브랜치 ref 전부 그대로
    assert tree_files(repo, r["commit"]) == {".gitignore", "a.txt", "new.txt"}   # 삭제 반영, 추적 안 된 파일 포함
    assert g(repo, "show", f"{r['commit']}:a.txt") == "a3\n"                      # 작업 폴더 상태(스테이징본 아님)
    assert g(repo, "rev-parse", r["commit"] + "^").strip() == before["head"]     # HEAD가 부모
    assert (repo / "a.txt").read_text() == "a3\n"
    gd = Path(g(repo, "rev-parse", "--absolute-git-dir").strip())
    assert not [n for n in os.listdir(gd) if n.startswith("fsh-ckpt-index-")]    # 임시 인덱스가 남지 않음


def test_commit_message_has_trailers_and_no_paths(repo):
    (repo / "new.txt").write_text("n\n")
    r = cap(repo, agent="claude", turn=3, trusted=False)
    msg = g(repo, "log", "-1", "--format=%B", r["commit"])
    assert "FarShell-Checkpoint: 1" in msg and "FarShell-Kind: turn-end" in msg and "FarShell-Turn: 3" in msg
    assert "FarShell-Agent: claude" in msg and "FarShell-Trusted: no" in msg
    assert "new.txt" not in msg and str(repo) not in msg
    assert g(repo, "log", "-1", "--format=%an <%ae>", r["commit"]).strip() == "FarShell checkpoint <checkpoint@farshell.invalid>"


def test_ignored_files_excluded_untracked_included(repo):
    (repo / "ignored").mkdir()
    (repo / "ignored" / "x.txt").write_text("x")
    (repo / "debug.log").write_text("l")
    (repo / "src").mkdir()
    (repo / "src" / "m.py").write_text("m")
    r = cap(repo)
    assert tree_files(repo, r["commit"]) == {".gitignore", "a.txt", "b.txt", "src/m.py"}


@pytest.mark.parametrize("name", [".env", ".env.production", "a.pem", "id_rsa", ".ssh/x", ".sometool/state.json", "svc/.env"])
def test_denied_names_never_captured(repo, name):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("SECRET=1\n")
    (repo / "ok.txt").write_text("ok")
    r = cap(repo)
    assert r["status"] == "ok"
    files = tree_files(repo, r["commit"])
    assert "ok.txt" in files and name not in files
    assert r["skipped"] >= 1
    assert "SECRET" not in g(repo, "log", "-p", "--all", "--format=%B")


def test_tracked_denied_file_change_is_not_captured(repo):
    (repo / ".env").write_text("OLD=1\n")
    g(repo, "add", "-f", ".env")
    g(repo, "commit", "-q", "-m", "env")
    (repo / ".env").write_text("NEW=SECRET\n")
    r = cap(repo)
    # 거부 파일이라 담지 않았다 → 임시 인덱스의 옛 내용 그대로. 새 값이 객체에 들어가지 않는다.
    assert r["status"] in ("ok", "unchanged")
    if r["status"] == "ok":
        assert g(repo, "show", f"{r['commit']}:.env") == "OLD=1\n"


def test_large_untracked_file_skipped_but_small_kept(repo, monkeypatch):
    monkeypatch.setenv("VT_CKPT_MAX_FILE_MB", "1")
    (repo / "big.bin").write_bytes(b"\0" * (1024 * 1024 + 1))
    (repo / "small.txt").write_text("s")
    r = cap(repo)
    files = tree_files(repo, r["commit"])
    assert "small.txt" in files and "big.bin" not in files
    assert r["skipped"] == 1
    assert "FarShell-Skipped: 1" in g(repo, "log", "-1", "--format=%B", r["commit"])


def test_default_file_cap_is_2mb(repo):
    assert checkpoints.max_file_bytes() == 2 * 1024 * 1024


def test_too_many_paths_or_bytes_is_too_large_and_makes_no_ref(repo, monkeypatch):
    monkeypatch.setenv("VT_CKPT_MAX_PATHS", "3")
    for i in range(5):
        (repo / f"f{i}.txt").write_text(str(i))
    r = cap(repo)
    assert r["status"] == "too_large"
    assert g(repo, "for-each-ref", "refs/fsh") == ""
    monkeypatch.setenv("VT_CKPT_MAX_PATHS", "5000")
    monkeypatch.setenv("VT_CKPT_MAX_BYTES_MB", "1")
    (repo / "m.bin").write_bytes(b"x" * 1024 * 1024)          # 1MB(파일 상한 2MB 이하)
    (repo / "n.bin").write_bytes(b"x" * 1024 * 1024)
    assert cap(repo)["status"] == "too_large"


def test_unchanged_state_makes_no_second_ref(repo):
    (repo / "n.txt").write_text("n")
    assert cap(repo)["status"] == "ok"
    r2 = cap(repo)
    assert r2["status"] == "unchanged" and r2["ref"] is None
    assert len(g(repo, "for-each-ref", "refs/fsh").splitlines()) == 1
    (repo / "n.txt").write_text("n2")
    r3 = cap(repo)
    assert r3["status"] == "ok" and r3["ref"].endswith("/000002")


def test_head_move_with_same_tree_is_a_new_checkpoint(repo):
    assert cap(repo)["status"] == "ok"
    g(repo, "commit", "-q", "--allow-empty", "-m", "empty")     # 트리는 같고 HEAD만 이동
    assert cap(repo)["status"] == "ok"


def test_other_session_numbers_independently(repo):
    (repo / "n.txt").write_text("n")
    a = cap(repo, sess_key="a" * 16)
    b = cap(repo, sess_key="b" * 16)
    assert a["ref"].endswith("/000001") and b["ref"].endswith("/000001") and a["ref"] != b["ref"]


def test_repo_before_first_commit(tmp_path):
    r = tmp_path / "fresh"
    r.mkdir()
    g(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a")
    state(r)
    before = state(r)
    res = cap(r)
    assert res["status"] == "ok"
    assert g(r, "cat-file", "-p", res["commit"]).count("parent ") == 0
    assert tree_files(r, res["commit"]) == {"a.txt"}
    assert state(r) == before
    assert cap(r)["status"] == "unchanged"


def test_works_without_git_identity_with_forced_signing_and_split_index(repo):
    g(repo, "config", "--unset", "user.name")
    g(repo, "config", "--unset", "user.email")
    g(repo, "config", "commit.gpgSign", "true")
    g(repo, "config", "gpg.program", "/usr/bin/false")
    g(repo, "config", "core.splitIndex", "true")
    g(repo, "update-index", "--split-index")
    (repo / "a.txt").write_text("changed")
    (repo / "new.txt").write_text("n")
    state(repo)
    before = state(repo)
    r = cap(repo, timeout=20)
    assert r["status"] == "ok", r
    assert tree_files(repo, r["commit"]) == {".gitignore", "a.txt", "b.txt", "new.txt"}
    assert state(repo) == before


def test_linked_worktrees_get_different_keys_and_are_independent(repo, tmp_path):
    wt = tmp_path / "wt"
    g(repo, "worktree", "add", "-q", "-b", "feat", str(wt))
    (repo / "a.txt").write_text("main change")
    (wt / "a.txt").write_text("wt change")
    r1, r2 = cap(repo), cap(wt)
    assert r1["status"] == r2["status"] == "ok"
    w1, w2 = r1["ref"].split("/")[4], r2["ref"].split("/")[4]
    assert w1 != w2
    assert g(repo, "show", f"{r1['commit']}:a.txt") == "main change"
    assert g(repo, "show", f"{r2['commit']}:a.txt") == "wt change"
    assert [e["ref"] for e in checkpoints.list_refs(repo)] == [r1["ref"]]     # 목록도 작업 폴더별
    assert [e["ref"] for e in checkpoints.list_refs(wt)] == [r2["ref"]]


def test_sparse_checkout_unsupported_and_busy_states_skipped(repo):
    g(repo, "config", "core.sparseCheckout", "true")
    assert cap(repo)["status"] == "unsupported"
    g(repo, "config", "--unset", "core.sparseCheckout")
    gd = Path(g(repo, "rev-parse", "--absolute-git-dir").strip())
    (gd / "MERGE_HEAD").write_text(g(repo, "rev-parse", "HEAD"))
    assert cap(repo)["status"] == "skipped_busy"
    (gd / "MERGE_HEAD").unlink()
    (gd / "rebase-merge").mkdir()
    assert cap(repo)["status"] == "skipped_busy"
    assert g(repo, "for-each-ref", "refs/fsh") == ""


def test_untracked_nested_repo_is_left_out(repo):
    nested = repo / "vendor" / "lib"
    nested.mkdir(parents=True)
    g(nested, "init", "-q")
    (nested / "x.txt").write_text("x")
    (repo / "keep.txt").write_text("k")
    r = cap(repo)
    assert r["status"] == "ok"
    assert tree_files(repo, r["commit"]) == {".gitignore", "a.txt", "b.txt", "keep.txt"}
    assert r["skipped"] == 1


def test_not_a_repo_and_bad_arguments(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert cap(d)["status"] == "norepo"
    assert cap(tmp_path / "missing")["status"] == "norepo"
    assert checkpoints.capture(d, kind="nope", sess_key=SESS)["status"] == "error"
    assert checkpoints.capture(d, kind="turn-end", sess_key="XYZ")["status"] == "error"
    assert checkpoints.list_refs(d) == []


def test_malicious_repo_config_runs_no_program(repo, tmp_path):
    marker = tmp_path / "pwned"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    g(repo, "config", "core.fsmonitor", str(hook))
    g(repo, "config", "core.hooksPath", str(tmp_path / "hooks"))
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "pre-commit").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (tmp_path / "hooks" / "pre-commit").chmod(0o755)
    (repo / "n.txt").write_text("n")
    assert cap(repo)["status"] == "ok"
    time.sleep(0.2)
    assert not marker.exists()


def test_timeout_kills_the_slow_git_and_reports_timeout(repo, tmp_path, monkeypatch):
    fake = tmp_path / "bin"
    fake.mkdir()
    pidfile = tmp_path / "pid"
    (fake / "git").write_text(f"#!/bin/sh\necho $$ > {pidfile}\nsleep 30\n")
    (fake / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    t0 = time.monotonic()
    r = checkpoints.capture(repo, kind="turn-end", sess_key=SESS, timeout=2)   # 부하가 걸려도 sh가 pid를 쓸 시간은 준다
    assert r["status"] == "timeout"
    assert time.monotonic() - t0 < 8
    pid = int(pidfile.read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                                      # 프로세스 그룹째 죽었다


def test_concurrent_capture_on_same_worktree_is_serialized(repo):
    import threading
    (repo / "n.txt").write_text("n")
    out = []

    def run(i):
        out.append(cap(repo, sess_key=f"{i}" * 16, timeout=20)["status"])

    ts = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(out) == ["ok"] * 4                         # 잠금이 순서대로 처리(대기 3초 안)
    assert len(g(repo, "for-each-ref", "refs/fsh").splitlines()) == 4


def test_pre_restore_goes_to_restore_namespace_and_is_never_deduplicated(repo):
    (repo / "n.txt").write_text("n")
    a = checkpoints.capture(repo, kind="pre-restore")
    b = checkpoints.capture(repo, kind="pre-restore")
    assert a["status"] == b["status"] == "ok"
    assert "/restore/" in a["ref"] and a["ref"] != b["ref"]
    assert checkpoints.ID_RE.match(a["id"])


# --- 읽기 --------------------------------------------------------------------


def test_list_refs_and_metadata(repo):
    (repo / "n.txt").write_text("1")
    cap(repo, kind="baseline", agent="claude", turn=0)
    (repo / "n.txt").write_text("2")
    cap(repo, kind="pre-turn", agent="claude", turn=1, trusted=True)
    (repo / "n.txt").write_text("3")
    cap(repo, kind="turn-end", agent="claude", turn=1)
    es = checkpoints.list_refs(repo)
    assert [(e["kind"], e["turn"], e["seq"], e["trusted"]) for e in es] == [
        ("baseline", 0, 1, True), ("pre-turn", 1, 2, True), ("turn-end", 1, 3, True)]
    assert all(e["agent"] == "claude" and e["sess"] == SESS and not e["restore"] for e in es)
    assert all(e["created"] > 0 and len(e["commit"]) >= 40 for e in es)


def test_foreign_refs_in_namespace_are_ignored(repo):
    (repo / "n.txt").write_text("1")
    r = cap(repo)
    wt12 = r["ref"].split("/")[4]
    plain = g(repo, "commit-tree", g(repo, "write-tree").strip(), "-m", "not ours").strip()
    g(repo, "update-ref", f"refs/fsh/ckpt/v1/{wt12}/{SESS[::-1]}/000001", plain)
    assert [e["id"] for e in checkpoints.list_refs(repo)] == [r["id"]]


def test_turn_base_prefers_trusted_pre_turn_then_previous_turn_end():
    def e(i, kind, turn, trusted=True, sess="s" * 16):
        return {"id": f"v1/aaaaaaaaaaaa/{sess}/{i:06d}", "sess": sess, "seq": i, "kind": kind, "turn": turn,
                "trusted": trusted, "restore": False, "commit": f"c{i}", "parent": "p"}
    es = [e(1, "baseline", 0), e(2, "turn-end", 1), e(3, "pre-turn", 2), e(4, "turn-end", 2)]
    assert checkpoints.turn_base(es, es[3]["id"])["seq"] == 3                # 신뢰된 pre-turn
    es[2]["trusted"] = False
    assert checkpoints.turn_base(es, es[3]["id"])["seq"] == 2                # 못 믿으면 앞 턴의 turn-end
    assert checkpoints.turn_base(es, es[0]["id"]) is None
    assert checkpoints.turn_base(es, "v1/aaaaaaaaaaaa/nope") is None


def test_turn_files_numstat_and_head_moved(repo):
    (repo / "a.txt").write_text("a1\nmore\nlines\n")
    base = cap(repo, kind="baseline", turn=0)
    (repo / "a.txt").write_text("a1\nmore\nlines\nx\n")
    (repo / "new.txt").write_text("1\n2\n")
    (repo / "b.txt").unlink()
    (repo / "bin.dat").write_bytes(b"\0\1\2\3")
    end = cap(repo, kind="turn-end", turn=1)
    t = checkpoints.turn_files(repo, end["id"])
    assert t["from"] == base["id"] and t["to"] == end["id"] and t["head_moved"] is False
    by = {f["path"]: f for f in t["files"]}
    assert by["a.txt"] == {"path": "a.txt", "added": 1, "deleted": 0, "binary": False}
    assert by["new.txt"]["added"] == 2 and by["b.txt"]["deleted"] == 1
    assert by["bin.dat"]["binary"] is True and by["bin.dat"]["added"] == 0
    g(repo, "commit", "-q", "-a", "--allow-empty", "-m", "moved")
    (repo / "z.txt").write_text("z")
    later = cap(repo, kind="turn-end", turn=2)
    assert checkpoints.turn_files(repo, later["id"])["head_moved"] is True
    assert checkpoints.turn_files(repo, "v1/aaaaaaaaaaaa/bbbbbbbbbbbbbbbb/000009") is None
    assert checkpoints.turn_files(repo, "../etc") is None


def test_diff_redacts_denied_paths_and_rejects_bad_files(repo):
    base = cap(repo, kind="baseline", turn=0)
    (repo / "a.txt").write_text("a1\nvisible change\n")
    (repo / ".env").write_text("SECRET=1\n")            # 캡처에서 빠진다
    end = cap(repo, kind="turn-end", turn=1)
    d = checkpoints.diff(repo, end["id"], base["id"])
    assert "visible change" in d["diff"] and "SECRET" not in d["diff"] and d["truncated"] is False
    one = checkpoints.diff(repo, end["id"], base["id"], "a.txt")
    assert "visible change" in one["diff"]
    for bad in ("/etc/passwd", "../x", "-p", "a\0b"):
        assert checkpoints.diff(repo, end["id"], base["id"], bad) is None
    assert checkpoints.diff(repo, "v1/aaaaaaaaaaaa/bbbbbbbbbbbbbbbb/000009", base["id"]) is None


def test_diff_redaction_uses_the_shared_fsguard_rule(repo, monkeypatch):
    """캡처가 못 거른 이름(이중 안전)이 diff에 있어도 `_redact_diff`가 가린다 — 규칙은 fsguard 한 곳."""
    (repo / "x.pem").write_text("-----BEGIN-----\n")
    g(repo, "add", "-f", "x.pem")
    g(repo, "commit", "-q", "-m", "pem")
    base = cap(repo, kind="baseline", turn=0)
    (repo / "x.pem").write_text("-----BEGIN-----\nKEY-MATERIAL\n")
    monkeypatch.setattr(checkpoints.fsguard, "repo_relpath_denied", lambda p: False)   # 캡처가 .pem을 담게 일부러 푼다
    end = cap(repo, kind="turn-end", turn=1)
    monkeypatch.undo()
    assert "KEY-MATERIAL" in g(repo, "show", f"{end['commit']}:x.pem")                 # 객체에는 있다
    d = checkpoints.diff(repo, end["id"], base["id"])
    assert "KEY-MATERIAL" not in d["diff"] and "x.pem" in d["diff"]


def test_diff_is_truncated_at_the_shared_limit(repo, monkeypatch):
    base = cap(repo, kind="baseline", turn=0)
    (repo / "t.txt").write_text("line\n" * 300_000)                                     # 약 1.5MB, 2MB 파일 상한 이하
    end = cap(repo, kind="turn-end", turn=1)
    d = checkpoints.diff(repo, end["id"], base["id"], "t.txt")
    assert d["truncated"] is True and len(d["diff"].encode()) <= 1024 * 1024


def test_fsguard_repo_relpath_denied_is_the_single_rule():
    import fsguard
    from routes import git as git_routes
    assert git_routes._diff_path_denied is fsguard.repo_relpath_denied
    for p in (".env", "svc/.env.local", "a.pem", "id_rsa", "x/.ssh/config", ".sometool/state.json"):
        assert fsguard.repo_relpath_denied(p) is True, p
    for p in ("main.py", ".gitignore", ".github/workflows/ci.yml", "src/.eslintrc"):
        assert fsguard.repo_relpath_denied(p) is False, p


def test_ckpt_pool_is_separate_and_small():
    from resilience import pools
    assert pools.DEFAULT_SIZES["ckpt"] == 2 and pools.DEFAULT_SIZES["git"] == 6      # 레일 배지의 git 풀을 굶기지 않는다
    assert pools.get("ckpt").name == "ckpt"
