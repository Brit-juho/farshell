"""fs_watch.py `tree` 구독 — 트리 루트 하나를 재귀로 보고, 알림에 바뀐 디렉토리 목록을 붙인다(계획 4.5).

예전 트리는 펼친 폴더마다 `dir`을 걸고, 알림이 오면 어느 폴더인지 몰라 펼친 폴더 전부를 다시 받았다
(루트 변경 1번에 목록 101회). 여기서는 목록 계산(`tree_dirs`)·거부 경로 숨김·넘침·실제 감시를 본다.
"""

import asyncio

import pytest

import fs_watch
from fs_watch import Sub, match, resolve_subs, tree_dirs, watch_targets


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    return tmp_path.resolve()


def test_tree_is_a_recursive_target_and_needs_a_directory(root):
    (root / "a").mkdir()
    (root / "f.txt").write_text("x")
    subs, rejected = resolve_subs([
        {"kind": "tree", "path": str(root / "a")},
        {"kind": "tree", "path": str(root / "f.txt")},
        {"kind": "tree", "path": "/etc"},
    ])
    assert [s.raw for s in subs] == [str(root / "a")]
    assert {r["path"] for r in rejected} == {str(root / "f.txt"), "/etc"}
    flat, deep = watch_targets(subs)
    assert flat == frozenset() and deep == {root / "a"}


def test_tree_dirs_are_parent_dirs_relative_to_root(root):
    s = Sub("tree", "T", root)
    dirs, overflow = tree_dirs(s, [root / "a.txt", root / "src" / "x.py", root / "src" / "deep" / "y.py",
                                   root / "src" / "x.py"])
    assert overflow is False
    assert dirs == ["", "src", "src/deep"]
    # 루트 밖(부모의 형제)은 무시한다.
    assert tree_dirs(s, [root.parent / "other" / "z"]) == ([], False)


def test_tree_dirs_hide_excluded_hidden_and_denied(root):
    s = Sub("tree", "T", root)
    noise = [
        root / "node_modules" / "pkg" / "index.js",   # 트리가 감추는 폴더 안
        root / ".git" / "index",                       # 저장소 내부
        root / ".cache" / "x",                          # 숨김 상태 폴더
        root / "node_modules",                          # 감추는 폴더 자체의 생성
        root / ".env",                                  # 거부 이름 — 이름조차 내보내지 않는다
        root / "keys.pem" / "x",                        # 거부 이름이 경로 중간에
    ]
    assert match([s], [str(p) for p in noise]) == []
    # _dispatch 와 같은 순서: 거부 이름을 먼저 뺀 뒤 tree_dirs.
    paths = [p for p in noise if not fs_watch.fsguard._is_denied_name(p.name)]
    assert tree_dirs(s, paths) == ([], False)
    # 허용된 dot 폴더(.github)는 트리에 나오므로 알린다.
    assert tree_dirs(s, [root / ".github" / "workflows" / "ci.yml"]) == ([".github/workflows"], False)


def test_tree_dirs_overflow(root, monkeypatch):
    monkeypatch.setattr(fs_watch, "MAX_TREE_DIRS", 3)
    s = Sub("tree", "T", root)
    dirs, overflow = tree_dirs(s, [root / f"d{i}" / "f" for i in range(5)])
    assert overflow is True and dirs == []
    assert tree_dirs(s, [root / f"d{i}" / "f" for i in range(3)]) == (["d0", "d1", "d2"], False)


def test_match_tree_alongside_other_kinds(root):
    t = Sub("tree", "T", root)
    d = Sub("dir", "D", root / "src")
    assert match([t, d], [str(root / "src" / "a.py")]) == [t, d]
    assert match([t, d], [str(root / "src" / "deep" / "a.py")]) == [t]


@pytest.fixture
def polling_watch(monkeypatch):
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", "true")
    monkeypatch.setenv("WATCHFILES_POLL_DELAY_MS", "50")


async def _until(got, pred):
    while not pred(got):
        await asyncio.sleep(0.05)


def test_real_change_reports_only_the_changed_folder(root, polling_watch):
    for i in range(5):
        (root / f"pkg-{i}" / "mod").mkdir(parents=True)
        (root / f"pkg-{i}" / "mod" / "a.js").write_text("1")
    (root / ".env").write_text("SECRET=1")

    async def run():
        hub = fs_watch.Hub()
        got: list[dict] = []

        async def send(msg):
            got.append(msg)

        rejected = await hub.set_subs(1, send, [
            {"kind": "tree", "path": str(root)},
            {"kind": "file", "path": str(root / "README.md")},
        ])
        assert rejected == []
        await asyncio.sleep(1.1)
        got.clear()
        (root / "pkg-3" / "mod" / "b.js").write_text("new")
        (root / ".env").write_text("SECRET=2")
        try:
            await asyncio.wait_for(_until(got, lambda m: any(x.get("dirs") for x in m)), 5.0)
            await asyncio.sleep(0.4)
        finally:
            await hub.remove(1)
        tree_msgs = [x for x in got if x["type"] == "changed" and x["subs"][0]["kind"] == "tree"]
        assert tree_msgs and all(x["subs"] == [{"kind": "tree", "path": str(root)}] for x in tree_msgs)
        dirs = set().union(*(set(x["dirs"]) for x in tree_msgs))
        assert "pkg-3/mod" in dirs
        assert not any(".env" in d for d in dirs)
        # README.md 는 바뀌지 않았다 — file 구독 알림은 없다.
        assert not [x for x in got if x["subs"][0]["kind"] == "file"]

    asyncio.run(run())
