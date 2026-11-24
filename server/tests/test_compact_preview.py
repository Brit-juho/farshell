"""정리 미리보기(session_history/compact_preview.py) — 읽기 전용이고, 규칙별 절약량을 바르게 센다."""
import base64
import hashlib
import json
import os

import pytest

IMG = base64.b64encode(os.urandom(30000)).decode()


@pytest.fixture
def private_history(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VT_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(home))
    from session_catalog import db
    db.snapshot()
    assert str(db.directory().resolve()).startswith(str(tmp_path.resolve()))
    return home


def _seed(home):
    from session_history.archive import register_source, scan_source
    from session_catalog.db import database
    root = home / ".claude"
    folder = root / "projects" / "sample"
    folder.mkdir(parents=True)
    body = "alpha beta gamma delta " * 80
    rows = [{"sessionId": "s-1", "type": "assistant", "cwd": str(home), "uuid": "a1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "시작합니다"}]}}]
    for i in range(6):
        rows.append({"sessionId": "s-1", "type": "user", "cwd": str(home), "uuid": f"u{i}", "parentUuid": "a1",
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": f"t{i}", "content": body + str(i)},
                         {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": IMG}}]},
                     "toolUseResult": {"stdout": body + str(i), "stderr": ""}})
    (folder / "s-1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    registered = register_source("claude", str(root))
    with database() as db:
        source = dict(db.execute("SELECT * FROM sources WHERE id=?", (registered["id"],)).fetchone())
    scan_source(source)
    return len("".join(json.dumps(r) + "\n" for r in rows))


def _state(home):
    from session_catalog.db import directory
    chunks = directory() / "chunks"
    listing = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in chunks.iterdir())
    dbfile = directory() / "catalog.sqlite3"
    return listing, hashlib.sha256(dbfile.read_bytes()).hexdigest()


def test_the_preview_measures_each_rule_and_changes_nothing(private_history):
    from session_history import compact_preview as cp
    size = _seed(private_history)
    before = _state(private_history)
    result = cp.scan()
    after = _state(private_history)
    assert before == after, "미리보기는 조각도 DB도 바꾸지 않는다"
    assert len(result["files"]) == 1
    f = result["files"][0]
    assert f["provider"] == "claude" and f["raw"] == size and f["original_kept"] is True
    assert f["now"] > f["tur+img"], "이미지와 중복 사본을 빼면 작아진다"
    assert f["tur"] < f["now"] and f["img"] < f["now"]
    assert f["tur+img"] <= min(f["tur"], f["img"])
    assert f["stats"]["img_count"] == 6 and f["stats"]["tur_dropped_bytes"] > 5000


def test_the_summary_groups_by_idle_time_and_leaves_out_files_whose_original_is_gone(private_history):
    from session_history import compact_preview as cp
    from session_catalog.db import database
    _seed(private_history)
    summary = cp.summarize(cp.scan(), idle_days=(0, 30), top=5)
    block = summary["providers"]["claude"]
    assert block["files"] == 1 and block["by_idle"][0]["files"] == 1
    assert block["by_idle"][30]["files"] == 0, "방금 쓴 대화는 30일 이상 조건에서 빠진다"
    assert summary["top"][0]["saved"] > 0
    with database(write=True) as db:
        db.execute("UPDATE source_files SET state='source_missing'")
    gone = cp.summarize(cp.scan(), idle_days=(0,))
    assert gone["providers"]["claude"]["original_missing"] == 1 and gone["providers"]["claude"]["by_idle"][0]["files"] == 0


def test_the_report_is_readable_korean_and_main_returns_zero(private_history, capsys):
    from session_history import compact_preview as cp
    _seed(private_history)
    assert cp.main(["preview"]) == 0
    text = capsys.readouterr().out
    for needle in ("정리 미리보기", "읽기만 했습니다", "[Claude]", "이미지 제거", "없어지는 내용", "마지막 활동 후"):
        assert needle in text
    assert cp.main(["preview", "--json", "--idle-days", "0"]) == 0
    assert json.loads(capsys.readouterr().out)["providers"]["claude"]["files"] == 1
    assert cp.main(["preview", "--idle-days", "abc"]) == 2
