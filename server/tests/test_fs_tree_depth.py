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


# --- 페이지(계획 4.4) -----------------------------------------------------------------


def _many(tmp_path, n_files, n_dirs=0):
    for i in range(n_dirs):
        (tmp_path / f"Dir{i:03d}").mkdir()
    for i in range(n_files):
        (tmp_path / f"f{i:04d}.txt").write_text("x" * i)
    return tmp_path


def test_listing_sorts_whole_directory_before_paging(tmp_path):
    # 예전엔 scandir 앞 1000개를 자른 뒤 정렬했다 — 1000개 넘는 폴더에서 어느 것이 보일지가 디스크 순서였다.
    _many(tmp_path, 7, 2)
    (tmp_path / "B.txt").write_text("")
    (tmp_path / "a.txt").write_text("")
    d = F._list_dir(tmp_path)
    names = [e["name"] for e in d["entries"]]
    assert names[:2] == ["Dir000", "Dir001"]
    assert names[2:4] == ["a.txt", "B.txt"]     # casefold 순
    assert d["total"] == 11 and d["next"] is None and d["truncated"] is False and d["capped"] is False
    f = next(e for e in d["entries"] if e["name"] == "f0003.txt")
    assert f["size"] == 3 and isinstance(f["mtime"], int)
    assert "mtime" not in d["entries"][0]       # 폴더는 stat 하지 않는다


def test_pages_follow_the_cursor_without_gaps(tmp_path):
    _many(tmp_path, 25, 3)
    seen, after, pages = [], None, 0
    while True:
        d = F._list_dir(tmp_path, after, 10)
        pages += 1
        seen += [e["name"] for e in d["entries"]]
        assert d["total"] == 28
        if d["next"] is None:
            assert d["truncated"] is False
            break
        assert d["truncated"] is True
        after = F.decode_cursor(d["next"])
    assert pages == 3
    assert seen == [e["name"] for e in F._list_dir(tmp_path, None, 1000)["entries"]]
    assert len(set(seen)) == 28


def test_cursor_after_a_deleted_entry_still_continues(tmp_path):
    _many(tmp_path, 6)
    d = F._list_dir(tmp_path, None, 3)
    (tmp_path / "f0002.txt").unlink()    # 커서가 가리키던 항목이 사라져도 그 다음부터 이어진다
    rest = F._list_dir(tmp_path, F.decode_cursor(d["next"]), 3)
    assert [e["name"] for e in rest["entries"]] == ["f0003.txt", "f0004.txt", "f0005.txt"]


def test_scan_cap_sets_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "MAX_SCAN_ENTRIES", 5)
    _many(tmp_path, 8)
    d = F._list_dir(tmp_path)
    assert d["capped"] is True and d["truncated"] is True and d["total"] == 5


def test_cursor_round_trip_and_malformed():
    import base64
    import json

    import pytest

    key = F._sort_key(False, "한글 이름.md")
    assert F.decode_cursor(F.encode_cursor(key)) == key
    bad = ["", "!!!", "x" * 3000,
           base64.urlsafe_b64encode(json.dumps([2, "a", "a"]).encode()).decode(),
           base64.urlsafe_b64encode(json.dumps({"a": 1}).encode()).decode(),
           base64.urlsafe_b64encode(b"\xff\xfe").decode()]
    for c in bad:
        with pytest.raises(F.BadCursor):
            F.decode_cursor(c)


def test_prefetch_skips_children_over_the_entry_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "MAX_PREFETCH_CHILD_ENTRIES", 3)
    (tmp_path / "small").mkdir()
    (tmp_path / "small" / "a").write_text("")
    big = tmp_path / "big"
    big.mkdir()
    for i in range(5):
        (big / f"x{i}").write_text("")
    d = F._list_dir_deep(tmp_path, 1)
    by = {e["name"]: e for e in d["entries"]}
    assert "children" in by["small"] and "children" not in by["big"]
