"""fs_watch.py — 코드 뷰어 자동 갱신의 구독 검증·매칭·실제 감시.

경로 판정은 fsguard가 하고(계약 2) 여기서는 그 결과를 쓰는지만 본다. 마지막
테스트들은 mock 없이 실제 watchfiles로 파일을 바꿔 알림이 오는지 확인한다.
"""

import asyncio
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import fs_watch
import main
from fs_watch import Sub, match, resolve_subs, watch_targets


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    return tmp_path.resolve()


def _repo(root: Path) -> Path:
    r = root / "proj"
    (r / "src").mkdir(parents=True)
    subprocess.run(["git", "-C", str(r), "init", "-q"], check=True, capture_output=True)
    return r


# --- 구독 검증 ------------------------------------------------------------------


def test_outside_root_and_denied_names_are_rejected(root):
    (root / "a.md").write_text("x")
    subs, rejected = resolve_subs([
        {"kind": "file", "path": "/etc/hosts"},
        {"kind": "file", "path": str(root / ".env")},
        {"kind": "file", "path": str(root / "a.md")},
    ])
    assert [s.raw for s in subs] == [str(root / "a.md")]
    assert {r["path"] for r in rejected} == {"/etc/hosts", str(root / ".env")}


def test_file_may_be_missing_but_parent_must_exist(root):
    subs, rejected = resolve_subs([
        {"kind": "file", "path": str(root / "not-yet.md")},
        {"kind": "file", "path": str(root / "nodir" / "x.md")},
        {"kind": "dir", "path": str(root / "nodir")},
    ])
    assert [s.raw for s in subs] == [str(root / "not-yet.md")]
    assert len(rejected) == 2


def test_malformed_items_are_ignored_and_duplicates_collapse(root):
    p = str(root)
    subs, rejected = resolve_subs([
        "x", None, {"kind": "exec", "path": p}, {"kind": "dir", "path": 3},
        {"kind": "dir", "path": p}, {"kind": "dir", "path": p},
    ])
    assert len(subs) == 1 and rejected == []
    assert resolve_subs("not a list") == ([], [])


def test_subscription_count_is_capped(root):
    items = [{"kind": "file", "path": str(root / f"f{i}.md")} for i in range(fs_watch.MAX_SUBS_PER_CLIENT + 5)]
    subs, rejected = resolve_subs(items)
    assert len(subs) == fs_watch.MAX_SUBS_PER_CLIENT
    assert len(rejected) == 5 and all(r["reason"] == "too many" for r in rejected)


def test_repo_resolves_to_worktree_top_and_rejects_non_repo(root):
    r = _repo(root)
    subs, rejected = resolve_subs([
        {"kind": "repo", "path": str(r / "src")},
        {"kind": "repo", "path": str(root)},
    ])
    assert [(s.raw, s.path) for s in subs] == [(str(r / "src"), r)]
    assert rejected == [{"kind": "repo", "path": str(root), "reason": "not a git repository"}]


def test_repo_search_stops_at_browse_root(tmp_path, monkeypatch):
    # 경계 바깥(부모)에 .git이 있어도 그걸 저장소로 잡으면 경계 밖을 감시하게 된다.
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True, capture_output=True)
    inner = tmp_path / "inner"
    inner.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(inner))
    subs, rejected = resolve_subs([{"kind": "repo", "path": str(inner)}])
    assert subs == [] and rejected[0]["reason"] == "not a git repository"


def test_watch_targets_split_flat_and_recursive(root):
    subs = [
        Sub("file", "f", root / "d" / "a.md"),
        Sub("dir", "d", root / "e"),
        Sub("repo", "r", root / "proj"),
    ]
    flat, deep = watch_targets(subs)
    assert flat == {root / "d", root / "e"}
    assert deep == {root / "proj"}


# --- 매칭 -----------------------------------------------------------------------


def test_match_file_dir_repo(root):
    f = Sub("file", "F", root / "d" / "a.md")
    d = Sub("dir", "D", root / "d")
    r = Sub("repo", "R", root / "proj")
    subs = [f, d, r]
    assert match(subs, [str(root / "d" / "a.md")]) == [f, d]
    assert match(subs, [str(root / "d" / "b.md")]) == [d]
    # 디렉토리 구독은 바로 아래만 — 손주 변경은 트리의 그 행이 안 바뀐다.
    assert match(subs, [str(root / "d" / "sub" / "c.md")]) == []
    assert match(subs, [str(root / "proj" / "src" / "x.py")]) == [r]


def test_repo_ignores_git_internals_and_heavy_dirs(root):
    r = Sub("repo", "R", root / "proj")
    base = root / "proj"
    for noise in (".git/objects/ab/cd", ".git/index.lock", ".git/logs/HEAD",
                  "node_modules/x/index.js", "a/__pycache__/m.pyc", ".git"):
        assert match([r], [str(base / noise)]) == [], noise
    for signal in (".git/index", ".git/HEAD", ".git/refs/heads/main"):
        assert match([r], [str(base / signal)]) == [r], signal


def test_denied_names_never_trigger(root):
    d = Sub("dir", "D", root)
    assert match([d], [str(root / ".env")]) == []
    assert match([d], [str(root / "server.pem")]) == []


# --- 실제 감시 ------------------------------------------------------------------


@pytest.fixture
def polling_watch(monkeypatch):
    # 실제 파일 이벤트 검증은 OS별 native 감시 권한/시작 지연에 의존하지 않는다.
    # watchfiles의 공식 polling backend도 같은 구독·매칭·전송 경로를 사용한다.
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "true")
    monkeypatch.setenv("WATCHFILES_POLL_DELAY_MS", "50")


async def _collect(hub_client_msgs, until, timeout=5.0):
    async def wait():
        while not until(hub_client_msgs):
            await asyncio.sleep(0.05)
    await asyncio.wait_for(wait(), timeout)


def test_real_file_change_is_reported_once_per_subscription(root, polling_watch):
    (root / "d").mkdir()
    f = root / "d" / "a.md"
    f.write_text("v1")

    async def run():
        hub = fs_watch.Hub()
        got: list[dict] = []

        async def send(msg):
            got.append(msg)

        rejected = await hub.set_subs(1, send, [
            {"kind": "file", "path": str(f)},
            {"kind": "dir", "path": str(root / "d")},
        ])
        assert rejected == []
        await asyncio.sleep(0.5)   # 감시 스레드가 뜰 시간
        # 편집기식 저장: 임시 파일을 쓰고 rename으로 바꿔치기한다.
        tmp = root / "d" / ".a.md.tmp"
        tmp.write_text("v2")
        tmp.replace(f)
        try:
            await _collect(got, lambda m: any(x["type"] == "changed" for x in m))
        finally:
            await hub.remove(1)
        changed = [x for x in got if x["type"] == "changed"][0]["subs"]
        assert {"kind": "file", "path": str(f)} in changed
        assert {"kind": "dir", "path": str(root / "d")} in changed
        assert hub._tasks == []   # 마지막 연결이 빠지면 감시도 멈춘다

    asyncio.run(run())


def test_other_clients_do_not_see_unsubscribed_paths(root, polling_watch):
    (root / "a.md").write_text("1")
    (root / "b.md").write_text("1")

    async def run():
        hub = fs_watch.Hub()
        got_a: list[dict] = []
        got_b: list[dict] = []

        async def send_a(m):
            got_a.append(m)

        async def send_b(m):
            got_b.append(m)

        await hub.set_subs(1, send_a, [{"kind": "file", "path": str(root / "a.md")}])
        await hub.set_subs(2, send_b, [{"kind": "file", "path": str(root / "b.md")}])
        # macOS FSEvents는 감시 시작 직전의 쓰기(위 fixture의 파일 생성)도 넘겨준다 —
        # 그 잔여 이벤트가 가라앉은 뒤부터 센다.
        await asyncio.sleep(1.0)
        got_a.clear()
        got_b.clear()
        (root / "a.md").write_text("2")
        try:
            await _collect(got_a, lambda m: bool(m))
            await asyncio.sleep(0.5)
        finally:
            await hub.remove(1)
            await hub.remove(2)
        assert got_b == []

    asyncio.run(run())


def test_ws_route_rejects_outside_paths(root, monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    with TestClient(main.app) as c:
        with c.websocket_connect("/ws-fs-watch") as ws:
            ws.send_json({"type": "watch", "subs": [{"kind": "file", "path": "/etc/passwd"}]})
            msg = ws.receive_json()
            assert msg["type"] == "watching"
            assert msg["rejected"][0]["path"] == "/etc/passwd"
            ws.send_json({"type": "watch", "subs": []})
            assert ws.receive_json() == {"type": "watching", "rejected": []}
