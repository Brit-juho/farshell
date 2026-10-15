"""/api/fs/tree depth: 하위 디렉토리 목록을 children 으로 미리 담는다."""

from routes import files as F


def _mk(tmp_path):
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "f.txt").write_text("x")
    (tmp_path / "a" / "b" / "g.txt").write_text("y")
    return tmp_path


def test_depth_zero_has_no_children(tmp_path):
    d = F._list_dir(_mk(tmp_path))
    assert all("children" not in e for e in d["entries"])


def test_depth_two_prefetches_two_levels(tmp_path):
    d = F._list_dir_deep(_mk(tmp_path), 2)
    a = d["entries"][0]
    assert a["name"] == "a"
    names = [e["name"] for e in a["children"]["entries"]]
    assert names == ["b", "f.txt"]
    b = a["children"]["entries"][0]
    assert [e["name"] for e in b["children"]["entries"]] == ["c", "g.txt"]


def test_depth_one_stops_after_one_level(tmp_path):
    d = F._list_dir_deep(_mk(tmp_path), 1)
    a = d["entries"][0]
    b = a["children"]["entries"][0]
    assert "children" not in b


def test_prefetch_dir_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "MAX_PREFETCH_DIRS", 2)
    for i in range(5):
        (tmp_path / f"d{i}").mkdir()
    d = F._list_dir_deep(tmp_path, 1)
    assert sum(1 for e in d["entries"] if "children" in e) == 2
