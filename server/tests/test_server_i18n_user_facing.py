"""영어 모드(`VT_LANG=en`)에서 사용자(웹 화면·`fsh` CLI)에게 보이는 서버 문구가 영어로 나오는지.

한국어에서는 원문 그대로여야 한다(키가 곧 한국어 원문이다) — 같은 호출을 ko/en 으로 두 번 보고,
ko 는 원문과 글자까지 같고 en 은 한글이 하나도 없는지 본다. 에이전트용 문구(MCP 도구·`/api/agent/*`
오류)는 범위 밖이라 여기서 다루지 않는다.
"""

from __future__ import annotations

import re

import pytest

import agent_share
import delegation
import git_account_store
import queue_store
import safe_mode
import vt_paths
from authz import approvals, migrate
from authz import principals as P
from authz.errors import ShareError

_HANGUL = re.compile(r"[가-힣]")


def _both(monkeypatch, fn):
    """같은 호출을 ko·en 으로 — (ko 문구, en 문구)."""
    monkeypatch.setenv("VT_LANG", "ko")
    ko = fn()
    monkeypatch.setenv("VT_LANG", "en")
    en = fn()
    return ko, en


def _msg(call):
    def run():
        try:
            call()
        except Exception as e:  # noqa: BLE001
            return getattr(e, "reason", None) or getattr(e, "message", None) or str(e)
        raise AssertionError("예외가 나야 한다")
    return run


def _assert_pair(ko, en, ko_expected):
    assert ko == ko_expected          # 한국어는 한 글자도 바뀌지 않는다
    assert en != ko and not _HANGUL.search(en), en


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_share_nick_and_mode_validation(monkeypatch):
    ko, en = _both(monkeypatch, _msg(lambda: agent_share.check_nick("!!")))
    _assert_pair(ko, en, "닉네임은 영문·숫자·_·- 1~32자입니다")
    ko, en = _both(monkeypatch, _msg(lambda: agent_share.check_mode("rw")))
    _assert_pair(ko, en, "공유 모드는 read 또는 readwrite 입니다")


def test_approval_decision_errors(monkeypatch):
    docs = {"approvals": {"items": []}, "grants": {"items": []}, "meta": {}}
    human = P.human("dev1")
    ko, en = _both(monkeypatch, _msg(lambda: approvals.decide(docs, "nope", "maybe", None, actor=human, share_mode=None, now=1.0)))
    _assert_pair(ko, en, "decision 은 allow 또는 deny 입니다")
    ko, en = _both(monkeypatch, _msg(lambda: approvals.decide(docs, "nope", "allow", None, actor=human, share_mode=None, now=1.0)))
    _assert_pair(ko, en, "대기 중인 요청이 없습니다(이미 처리됐거나 만료됐거나 세션이 종료됨)")
    ko, en = _both(monkeypatch, _msg(lambda: approvals.revoke(docs, "nope", "all", 1.0)))
    _assert_pair(ko, en, "그런 권한이 없습니다")


def test_delegation_close_unknown_id_is_translated(monkeypatch):
    ko, en = _both(monkeypatch, _msg(lambda: delegation.close("zzz")))
    _assert_pair(ko, en, "그런 위임이 없습니다")


def test_git_account_validation_reason(monkeypatch):
    def call():
        return git_account_store.add_account("nope", "h", "l", "t")["reason"]
    monkeypatch.setenv("VT_LANG", "ko")
    ko = call()
    monkeypatch.setenv("VT_LANG", "en")
    en = call()
    assert ko.startswith("provider는 ") and ko.endswith(" 중 하나여야 합니다")
    assert en.startswith("provider must be one of") and not _HANGUL.search(en)


def test_queue_stored_reason_is_translated_at_read_time_not_stored(monkeypatch):
    item = {"id": "x", "blocked_reason": "타깃이 승인 대기 중"}
    monkeypatch.setenv("VT_LANG", "ko")
    assert queue_store.with_blocked_text(item) is item                      # 한국어는 응답이 달라지지 않는다
    assert queue_store.display_reason("타깃이 승인 대기 중") == "타깃이 승인 대기 중"
    monkeypatch.setenv("VT_LANG", "en")
    out = queue_store.with_blocked_text(item)
    assert out["blocked_text"] == "Target waiting for approval" and out["blocked_reason"] == "타깃이 승인 대기 중"
    assert queue_store.display_reason("사용자가 입력하는 중") == "A person is typing"
    assert queue_store.display_reason("알 수 없는 사유") == "알 수 없는 사유"       # 모르는 원문은 그대로


def test_safe_mode_block_reason(monkeypatch):
    monkeypatch.setenv("VT_SAFE_MODE", "1")
    monkeypatch.setenv("VT_LANG", "ko")
    assert safe_mode.check("rm -rf /etc") == (False, "rm -rf 시스템 경로")
    monkeypatch.setenv("VT_LANG", "en")
    assert safe_mode.check("rm -rf /etc") == (False, "rm -rf on a system path")


def test_path_override_error_message(monkeypatch):
    def call():
        try:
            vt_paths._expand("VT_STATE_DIR", "relative/dir")
        except vt_paths.PathOverrideError as e:
            return str(e)
    ko, en = _both(monkeypatch, call)
    assert ko == 'VT_STATE_DIR는 절대경로여야 한다 — 받은 값: "relative/dir"'
    assert en == 'VT_STATE_DIR must be an absolute path — got: "relative/dir"'


def test_migrate_auth_cli_output(monkeypatch, capsys):
    from authz import __main__ as cli
    monkeypatch.setenv("VT_LANG", "ko")
    assert cli.main(["--finalize"]) == 2
    ko = capsys.readouterr().out
    monkeypatch.setenv("VT_LANG", "en")
    assert cli.main(["--finalize"]) == 2
    en = capsys.readouterr().out
    assert ko.startswith("--finalize 는 8단계") and not _HANGUL.search(en) and en.startswith("--finalize arrives")
    monkeypatch.setenv("VT_LANG", "ko")
    assert cli.main(["--status"]) == 0
    ko_status = capsys.readouterr().out
    monkeypatch.setenv("VT_LANG", "en")
    assert cli.main(["--status"]) == 0
    en_status = capsys.readouterr().out
    assert "감사 체인" in ko_status and "백업:" in ko_status
    assert not _HANGUL.search(en_status) and "Audit chain" in en_status and "Backups:" in en_status


def test_migrate_restore_errors(monkeypatch):
    ko, en = _both(monkeypatch, _msg(lambda: migrate._load_backup("../x")))
    assert ko == "잘못된 백업 이름: '../x'" and en == "Invalid backup name: '../x'"
    ko, en = _both(monkeypatch, _msg(lambda: migrate._load_backup("20260101-000000")))
    assert "이 없습니다" in ko and not _HANGUL.search(en) and "doesn't exist" in en


def test_agent_facing_text_is_untouched_in_english(monkeypatch):
    """에이전트가 읽는 문구(보낼 글 검증 등)는 영어 모드에서도 한국어 그대로다 — 범위 밖."""
    monkeypatch.setenv("VT_LANG", "en")
    with pytest.raises(ShareError) as ei:
        agent_share.check_send_text("")
    assert ei.value.reason == "보낼 글이 비었습니다"
