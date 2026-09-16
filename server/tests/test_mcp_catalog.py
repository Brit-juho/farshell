"""97번 2단계 — 그룹 태그 저장소(mcp_catalog).

여기서 못박는 것:
1. **태그는 우리 저장소에만 있다** — CLI 설정 파일은 전혀 안 건드린다.
2. **대소문자·공백이 그룹을 가르지 않는다.** 「검증용」과 「검증용 」이 다른
   그룹이 되면 "그룹 켜기"가 왜 일부를 빼먹는지 사용자가 알 수 없다.
3. 파일 권한 0600 — 다른 저장 파일과 같은 규율.
4. 깨진 파일로 서버를 세우지 않는다.
"""

from __future__ import annotations

import json
import os
import stat

import pytest


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    import importlib

    import mcp_catalog as m
    importlib.reload(m)
    return m


def test_starts_empty(catalog):
    assert catalog.get_tags() == {}
    assert catalog.all_tags() == []


def test_set_and_read_back(catalog):
    assert catalog.set_tags("github", ["앱개발용", "검증용"])["ok"]
    assert catalog.get_tags() == {"github": ["앱개발용", "검증용"]}
    assert catalog.all_tags() == ["검증용", "앱개발용"]


def test_file_is_0600(catalog):
    catalog.set_tags("github", ["x"])
    mode = stat.S_IMODE(os.stat(catalog._path()).st_mode)
    assert mode == 0o600, f"MCP 카탈로그 권한이 {oct(mode)}"


def test_empty_list_removes_the_key(catalog):
    """빈 목록은 "태그 없음"이다 — 빈 배열을 남기면 all_tags·members가
    매번 빈 값을 걸러야 하고, 파일도 안 쓰는 키로 자란다."""
    catalog.set_tags("github", ["x"])
    catalog.set_tags("github", [])
    assert catalog.get_tags() == {}


def test_duplicates_and_case_are_folded(catalog):
    catalog.set_tags("github", ["검증용", "검증용", "  검증용  "])
    assert catalog.get_tags()["github"] == ["검증용"]


def test_rejects_control_chars_and_overlong(catalog):
    catalog.set_tags("github", ["ok", "bad\nname", "a/b", "x" * 100])
    assert catalog.get_tags()["github"] == ["ok"]


def test_members_is_case_insensitive(catalog):
    catalog.set_tags("github", ["검증용"])
    catalog.set_tags("slack", ["검증용"])
    catalog.set_tags("other", ["앱개발용"])
    assert catalog.members("검증용") == ["github", "slack"]
    assert catalog.members("  검증용 ") == ["github", "slack"]


def test_rename_tag_touches_every_server(catalog):
    catalog.set_tags("github", ["검증용"])
    catalog.set_tags("slack", ["검증용", "앱개발용"])
    out = catalog.rename_tag("검증용", "테스트용")
    assert out["ok"] and out["renamed"] == 2
    assert catalog.members("테스트용") == ["github", "slack"]
    assert catalog.members("검증용") == []


def test_rename_into_existing_tag_merges(catalog):
    """합쳐질 때 중복이 남으면 칩이 두 개로 보인다."""
    catalog.set_tags("slack", ["검증용", "앱개발용"])
    catalog.rename_tag("검증용", "앱개발용")
    assert catalog.get_tags()["slack"] == ["앱개발용"]


def test_delete_tag_leaves_servers_alone(catalog):
    catalog.set_tags("github", ["검증용", "앱개발용"])
    catalog.set_tags("slack", ["검증용"])
    out = catalog.delete_tag("검증용")
    assert out["ok"] and out["removed"] == 2
    assert catalog.get_tags() == {"github": ["앱개발용"]}


def test_total_tag_kinds_are_capped(catalog):
    for i in range(catalog.MAX_TAGS_TOTAL):
        assert catalog.set_tags(f"s{i}", [f"t{i}"])["ok"]
    out = catalog.set_tags("overflow", ["one-too-many"])
    assert not out["ok"] and out["error"] == "too_many_tags"


def test_reusing_an_existing_tag_does_not_count_against_the_cap(catalog):
    for i in range(catalog.MAX_TAGS_TOTAL):
        catalog.set_tags(f"s{i}", [f"t{i}"])
    assert catalog.set_tags("another", ["t0"])["ok"]


def test_broken_file_does_not_raise(catalog):
    catalog.set_tags("github", ["x"])
    catalog._path().write_text("{broken")
    assert catalog.get_tags() == {}
    # 그 위에 계속 쓸 수 있어야 한다 — 깨진 파일이 영구 고장이 되면 안 된다.
    assert catalog.set_tags("github", ["y"])["ok"]
    assert catalog.get_tags() == {"github": ["y"]}


def test_unknown_shape_is_ignored_not_crashed(catalog):
    catalog._path().parent.mkdir(parents=True, exist_ok=True)
    catalog._path().write_text(json.dumps({"version": 1, "tags": {"a": "not-a-list"}}))
    assert catalog.get_tags() == {}


def test_invalid_name_is_refused(catalog):
    assert not catalog.set_tags("", ["x"])["ok"]
    assert not catalog.set_tags("x" * 500, ["x"])["ok"]
    assert not catalog.set_tags("ok", "not-a-list")["ok"]


# ── 자격증명 (97번 3단계) ──────────────────────────────────────────────────
#
# 여기서 못박는 것은 §2의 두 축이다:
#   1. **원문은 어떤 공개 경로에도 실리지 않는다** — 값이 응답에 실리면
#      터널 너머 공개 인터넷으로 나간다(96번에서 credits 누출을 막은 방식).
#   2. **자격증명은 이름이 아니라 검증된 대상에 묶인다**(§2-5) — 같은 이름으로
#      다른 명령이 걸린 서버에 키가 자동으로 흘러가면 안 된다.


def test_secret_never_appears_in_public_shape(catalog):
    catalog.set_cred("notion", "token", "sk-super-secret-value")
    pub = catalog.public_creds()
    assert "sk-super-secret-value" not in json.dumps(pub, ensure_ascii=False)
    assert pub[0]["masked"] == "sk-s…alue"


def test_set_cred_response_carries_no_secret(catalog):
    out = catalog.set_cred("notion", "token", "sk-super-secret-value")
    assert "sk-super-secret-value" not in json.dumps(out, ensure_ascii=False)


def test_auto_env_name_is_the_default_but_can_be_overridden(catalog):
    """기본은 자동 생성, 덮어쓰기 허용 — 2026-09-16 확정."""
    a = catalog.set_cred("notion-mcp", "token", "x")["cred"]
    assert a["env"] == "FSH_MCP_NOTION_MCP_TOKEN"
    b = catalog.set_cred("notion-mcp", "token", "x", env="NOTION_TOKEN")["cred"]
    assert b["env"] == "NOTION_TOKEN"


def test_same_server_and_key_updates_instead_of_piling_up(catalog):
    catalog.set_cred("notion", "token", "one")
    catalog.set_cred("notion", "token", "two")
    creds = catalog.list_creds()
    assert len(creds) == 1 and creds[0]["secret"] == "two"


def test_bad_env_name_is_refused(catalog):
    """이 문자열이 곧 남의 설정 파일에 써넣는 값이다."""
    for bad in ("has space", "$X", "1LEADING", "a-b", "A B"):
        out = catalog.set_cred("n", "k", "v", env=bad)
        assert not out["ok"], bad


def test_blank_env_means_use_the_default_name(catalog):
    """화면의 빈 입력칸이 그대로 올라온다 — 오류가 아니라 "기본값을 쓰겠다"다."""
    for blank in (None, "", "   "):
        out = catalog.set_cred("notion", "token", "v", env=blank)
        assert out["ok"] and out["cred"]["env"] == "FSH_MCP_NOTION_TOKEN"


def test_empty_or_oversized_secret_is_refused(catalog):
    assert not catalog.set_cred("n", "k", "")["ok"]
    assert not catalog.set_cred("n", "k", "x" * (catalog.MAX_SECRET_LEN + 1))["ok"]


def test_env_for_matches_by_server_name(catalog):
    catalog.set_cred("notion", "token", "sek")
    out = catalog.env_for([{"name": "notion"}])
    assert out == {"FSH_MCP_NOTION_TOKEN": "sek"}


def test_env_for_refuses_when_the_target_changed(catalog):
    """§2-5 — 같은 이름인데 실행 명령이 바뀌었다. 키를 주면 안 된다."""
    fp = catalog.fingerprint({"command": "npx", "args": ["-y", "notion"]})
    catalog.set_cred("notion", "token", "sek", fingerprint_=fp)
    same = catalog.env_for([{"name": "notion", "fingerprint": fp}])
    other = catalog.env_for([{"name": "notion", "fingerprint": "deadbeefdeadbeef"}])
    assert same == {"FSH_MCP_NOTION_TOKEN": "sek"}
    assert other == {}, "대상이 바뀐 서버에 키가 흘러갔다"


def test_env_for_without_a_fingerprint_falls_back_to_name(catalog):
    """지문을 기록한 적 없는 구형 항목 — 대상을 특정한 적이 없다는 뜻이다."""
    catalog.set_cred("notion", "token", "sek")
    assert catalog.env_for([{"name": "notion", "fingerprint": "whatever"}]) == {
        "FSH_MCP_NOTION_TOKEN": "sek"}


def test_env_for_ignores_servers_with_no_cred(catalog):
    catalog.set_cred("notion", "token", "sek")
    assert catalog.env_for([{"name": "github"}]) == {}


def test_delete_cred(catalog):
    cid = catalog.set_cred("notion", "token", "sek")["cred"]["id"]
    assert catalog.delete_cred(cid)["ok"]
    assert catalog.list_creds() == []
    assert not catalog.delete_cred(cid)["ok"]


def test_fingerprint_changes_with_command_and_url(catalog):
    base = {"command": "npx", "args": ["-y", "x"]}
    assert catalog.fingerprint(base) == catalog.fingerprint(dict(base))
    assert catalog.fingerprint(base) != catalog.fingerprint({"command": "npx", "args": ["-y", "y"]})
    assert catalog.fingerprint({"url": "https://a"}) != catalog.fingerprint({"url": "https://b"})


# ── 참조 기록 (회수용, §2-5) ───────────────────────────────────────────────

def test_refs_track_what_we_wrote_regardless_of_env_name(catalog):
    """이름 규칙으로 추적하면 사용자가 이름을 덮어쓴 순간 추적이 끊긴다."""
    catalog.record_ref(tool="claude", scope="global", source="/a/.claude.json",
                       server="notion", env="NOTION_TOKEN")
    refs = catalog.list_refs()
    assert len(refs) == 1 and refs[0]["env"] == "NOTION_TOKEN"
    catalog.forget_ref("claude", "global", "/a/.claude.json", "notion", "NOTION_TOKEN")
    assert catalog.list_refs() == []


def test_recording_the_same_ref_twice_does_not_duplicate(catalog):
    for _ in range(3):
        catalog.record_ref(tool="claude", scope="global", source="/a", server="n", env="E")
    assert len(catalog.list_refs()) == 1


def test_creds_and_tags_share_one_file_without_clobbering(catalog):
    catalog.set_tags("notion", ["검증용"])
    catalog.set_cred("notion", "token", "sek")
    catalog.set_tags("notion", ["검증용", "앱개발용"])
    assert len(catalog.list_creds()) == 1
    assert catalog.get_tags()["notion"] == ["검증용", "앱개발용"]
