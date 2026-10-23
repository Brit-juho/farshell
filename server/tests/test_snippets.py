"""L3 회귀: 프롬프트 스니펫이 지시를 잃지 않아야 한다. queue_store 테스트와 같은 톤 —
CRUD만 있으니 큐보다 훨씬 단순하다(순서/드레인/safe_mode 상태 기계가 없음)."""

import os
import subprocess
from pathlib import Path

import pytest

import snippet_store


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    yield


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """N6(60 §4) 프로젝트 스코프용 — 실제 git 저장소 하나 + fsguard 경계를
    tmp_path로 좁혀서 resolve_project_key가 실경로로 동작하게 한다."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    return repo


def test_add_and_list():
    snippet_store.add("git commit -m 'wip'", "wip 커밋")
    snippet_store.add("cd ~/proj\npython run.py")
    items = snippet_store.list_items()
    assert [x["text"] for x in items] == ["git commit -m 'wip'", "cd ~/proj\npython run.py"]
    assert items[0]["label"] == "wip 커밋"
    assert items[1]["label"] == ""


def test_empty_text_rejected():
    r = snippet_store.add("   ")
    assert not r["ok"] and r["error"] == "empty"


def test_too_long_rejected():
    r = snippet_store.add("x" * (snippet_store.MAX_TEXT_LEN + 1))
    assert not r["ok"] and r["error"] == "too_long"


def test_cap_rejects_instead_of_silently_dropping():
    for i in range(snippet_store.MAX_ITEMS):
        assert snippet_store.add(f"item-{i}")["ok"]
    r = snippet_store.add("넘침")
    assert not r["ok"] and r["error"] == "full"
    assert len(snippet_store.list_items()) == snippet_store.MAX_ITEMS
    assert snippet_store.list_items()[0]["text"] == "item-0"


def test_remove():
    a = snippet_store.add("a")["item"]
    snippet_store.add("b")
    assert snippet_store.remove(a["id"])["ok"]
    assert [x["text"] for x in snippet_store.list_items()] == ["b"]
    assert snippet_store.remove("nope")["error"] == "not_found"


def test_label_truncated_to_max_len():
    r = snippet_store.add("x", "l" * (snippet_store.MAX_LABEL_LEN + 10))
    assert len(r["item"]["label"]) == snippet_store.MAX_LABEL_LEN


def test_file_permissions_are_0600():
    snippet_store.add("a")
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "snippets.json"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert oct(p.parent.stat().st_mode & 0o777) == "0o700"


def test_survives_corrupt_file():
    snippet_store.add("a")
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "snippets.json"
    p.write_text("{ this is not json")
    assert snippet_store.list_items() == []
    assert snippet_store.add("b")["ok"]


def test_default_scope_is_global():
    r = snippet_store.add("a")
    assert r["item"]["scope"] == snippet_store.SCOPE_GLOBAL
    assert r["item"]["project"] is None


def test_project_scope_resolves_repo_top(git_repo):
    r = snippet_store.add("a", scope=snippet_store.SCOPE_PROJECT, cwd=str(git_repo))
    assert r["ok"]
    assert r["item"]["scope"] == snippet_store.SCOPE_PROJECT
    assert r["item"]["project"] == str(git_repo.resolve())


def test_project_scope_without_git_repo_rejected(tmp_path, monkeypatch):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    r = snippet_store.add("a", scope=snippet_store.SCOPE_PROJECT, cwd=str(plain))
    assert not r["ok"] and r["error"] == "no_project"


def test_project_scope_outside_fsguard_roots_rejected(tmp_path, git_repo, monkeypatch):
    outside = tmp_path.parent / "outside-repo"
    outside.mkdir(exist_ok=True)
    r = snippet_store.add("a", scope=snippet_store.SCOPE_PROJECT, cwd=str(outside))
    assert not r["ok"] and r["error"] == "no_project"


def test_resolve_project_key_matches_between_add_and_lookup(git_repo):
    """저장(add)과 조회(프로젝트 탭 필터)가 같은 project 키를 내야 한다."""
    r = snippet_store.add("a", scope=snippet_store.SCOPE_PROJECT, cwd=str(git_repo))
    assert r["item"]["project"] == snippet_store.resolve_project_key(str(git_repo))


def test_legacy_items_migrate_to_global_scope():
    """구 snippets.json(scope 없음)도 계속 동작해야 한다."""
    snippet_store.add("a")
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "snippets.json"
    import json
    raw = json.loads(p.read_text())
    del raw[0]["scope"]
    del raw[0]["project"]
    p.write_text(json.dumps(raw))
    item = snippet_store.list_items()[0]
    assert item["scope"] == snippet_store.SCOPE_GLOBAL
    assert item["project"] is None


# ── 본문 끝 줄바꿈 = 마지막 Enter (2026-10-23) ─────────────────────────────

def test_trailing_newline_is_kept_as_one():
    assert snippet_store.add("ls\n\n\n")["item"]["text"] == "ls\n"
    assert snippet_store.add("ls")["item"]["text"] == "ls"


def test_leading_blank_lines_dropped_and_crlf_normalized():
    assert snippet_store.add("\n\na\r\nb\r\n")["item"]["text"] == "a\nb\n"


def test_whitespace_only_is_empty():
    r = snippet_store.add("\n  \n")
    assert not r["ok"] and r["error"] == "empty"


def test_legacy_new_section_item_gets_trailing_newline():
    """옛 「새 섹션 자동 실행」은 항상 Enter까지 갔다 — 같은 동작이 되도록 본문 끝에 옮긴다."""
    snippet_store.add("make test")
    snippet_store.add("echo hi")
    p = Path(os.environ["VT_STATE_DIR"]).expanduser() / "snippets.json"
    import json
    raw = json.loads(p.read_text())
    raw[0]["mode"] = "new_section"
    raw[1]["mode"] = "paste"
    p.write_text(json.dumps(raw))
    items = snippet_store.list_items()
    assert items[0]["text"] == "make test\n" and "mode" not in items[0]
    assert items[1]["text"] == "echo hi" and "mode" not in items[1]


def test_add_ignores_mode_argument():
    r = snippet_store.add("echo hi", mode="new_section")
    assert r["item"]["text"] == "echo hi" and "mode" not in r["item"]


# ── 수정 ──────────────────────────────────────────────────────────────────

def test_update_changes_text_and_label_only():
    item = snippet_store.add("a", "old")["item"]
    r = snippet_store.update(item["id"], text="b\n", label="new")
    assert r["ok"] and r["item"]["text"] == "b\n" and r["item"]["label"] == "new"
    assert r["item"]["scope"] == snippet_store.SCOPE_GLOBAL
    assert snippet_store.list_items()[0]["text"] == "b\n"


def test_update_label_alone_keeps_text():
    item = snippet_store.add("keep")["item"]
    r = snippet_store.update(item["id"], label="x")
    assert r["item"]["text"] == "keep" and r["item"]["label"] == "x"


def test_update_rejects_empty_and_too_long():
    item = snippet_store.add("a")["item"]
    assert snippet_store.update(item["id"], text=" \n")["error"] == "empty"
    assert snippet_store.update(item["id"], text="x" * (snippet_store.MAX_TEXT_LEN + 1))["error"] == "too_long"
    assert snippet_store.list_items()[0]["text"] == "a"


def test_update_rejects_unknown_scope_and_missing_item():
    item = snippet_store.add("a")["item"]
    assert snippet_store.update(item["id"], scope="galaxy")["error"] == "bad_scope"
    assert snippet_store.update("no-such-id", text="b")["error"] == "not_found"


def test_update_to_project_scope_resolves_repo(git_repo):
    item = snippet_store.add("a")["item"]
    r = snippet_store.update(item["id"], scope="project", cwd=str(git_repo))
    assert r["ok"] and r["item"]["project"] == snippet_store.resolve_project_key(str(git_repo))
    back = snippet_store.update(item["id"], scope="global")
    assert back["item"]["scope"] == "global" and back["item"]["project"] is None


def test_update_to_project_scope_without_repo_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    item = snippet_store.add("a")["item"]
    r = snippet_store.update(item["id"], scope="project", cwd=str(tmp_path))
    assert not r["ok"] and r["error"] == "no_project"
    assert snippet_store.list_items()[0]["scope"] == "global"
