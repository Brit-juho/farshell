"""Codex config.toml 훅 등록기의 최소 수정·보존 계약."""

from __future__ import annotations

import tomllib

import pytest

import codex_hooks as ch


@pytest.fixture
def root(tmp_path):
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "agent_hook.sh").write_text("#!/bin/sh\n")
    return tmp_path


def test_install_adds_all_six_events_and_is_idempotent(root):
    original = 'model = "keep"\n'
    merged, changes = ch.apply_install(original, root)
    assert changes
    parsed = tomllib.loads(merged)
    assert parsed["model"] == "keep"
    assert set(parsed["hooks"]) >= set(ch.EVENTS)
    for event in ch.EVENTS:
        command = parsed["hooks"][event][0]["hooks"][0]["command"]
        assert command == ch.hook_command(event, root)
    second, second_changes = ch.apply_install(merged, root)
    assert second == merged and second_changes == []


def test_install_preserves_codex_trust_state(root):
    first, _ = ch.apply_install('model = "keep"\n', root)
    with_state = first.replace(
        ch.END,
        '[hooks.state]\n[hooks.state."hook-id"]\ntrusted_hash = "sha256:keep"\n\n' + ch.END,
    )
    updated, _ = ch.apply_install(with_state, root)
    parsed = tomllib.loads(updated)
    assert parsed["hooks"]["state"]["hook-id"]["trusted_hash"] == "sha256:keep"


def test_update_replaces_old_repo_path_but_keeps_other_settings(root, tmp_path):
    old_root = tmp_path / "old"
    (old_root / "server").mkdir(parents=True)
    first, _ = ch.apply_install('model = "keep"\n', old_root)
    updated, changes = ch.apply_install(first, root)
    assert changes
    assert str(old_root) not in updated
    assert tomllib.loads(updated)["model"] == "keep"


def test_uninstall_removes_only_owned_block(root):
    installed, _ = ch.apply_install('model = "keep"\n', root)
    removed, changes = ch.apply_uninstall(installed)
    assert changes == ["Codex 훅 블록 제거"]
    assert tomllib.loads(removed) == {"model": "keep"}


def test_broken_toml_is_rejected_without_guessing(root):
    with pytest.raises(tomllib.TOMLDecodeError):
        ch.apply_install('[[[broken', root)


def test_unclosed_marker_is_rejected(root):
    with pytest.raises(ValueError, match="끝 마커"):
        ch.apply_install(ch.BEGIN + "\n", root)
