"""작업 완료 알림 정책 — 기본 off, 시간·오류·웹 접속 조건, 설정값 정규화."""
import pytest

import voice_notify as vn


def cfg(**kw):
    base = {"voice.notifyMode": "speak"}
    base.update({f"voice.notify{k}": v for k, v in kw.items()})
    return vn.normalize(base)


def test_default_is_off():
    assert vn.decide(vn.normalize({}), 100, True, 0) == "none"
    assert vn.decide(vn.normalize(None), None, True, 0) == "none"


@pytest.mark.parametrize("bad", ["loud", "", None, 5, ["speak"]])
def test_bad_mode_falls_back_to_off(bad):
    assert vn.normalize({"voice.notifyMode": bad})["mode"] == "off"


def test_sound_and_speak_pass_through():
    assert vn.decide(vn.normalize({"voice.notifyMode": "sound"}), 5, True, 0) == "sound"
    assert vn.decide(cfg(), 5, True, 0) == "speak"


def test_min_sec_filters_short_tasks():
    c = cfg(MinSec=30)
    assert vn.decide(c, 10, True, 0) == "none"
    assert vn.decide(c, 30, True, 0) == "speak"
    assert vn.decide(c, 90, True, 0) == "speak"


def test_unknown_elapsed_is_not_swallowed():
    assert vn.decide(cfg(MinSec=30), None, True, 0) == "speak"


@pytest.mark.parametrize("raw,expect", [("abc", 0), (-5, 0), (99999, 600), (12.7, 12), (None, 0)])
def test_min_sec_normalized(raw, expect):
    assert vn.normalize({"voice.notifyMinSec": raw})["min_sec"] == expect


def test_error_response_skipped_by_default():
    assert vn.decide(cfg(), 5, False, 0) == "none"
    assert vn.decide(cfg(SuccessOnly=False), 5, False, 0) == "speak"


def test_only_unfocused():
    c = cfg(OnlyUnfocused=True)
    assert vn.decide(c, 5, True, 1) == "none"
    assert vn.decide(c, 5, True, 0) == "speak"
    assert vn.decide(cfg(), 5, True, 3) == "speak"   # 꺼져 있으면 접속 수를 안 본다
