"""D7: auto_responder.py 단위 테스트 (trust prompt cooldown, bypass warning)."""

import os, time


def test_disabled_by_default():
    os.environ.pop("VT_AUTO_TRUST", None)
    import importlib
    import auto_responder
    importlib.reload(auto_responder)

    writes = []
    r = auto_responder.AutoResponder(lambda sid, d: writes.append((sid, d)))
    r.feed("s1", b"Yes, I trust this folder?")
    assert writes == []


def test_enabled_trust_pattern():
    os.environ["VT_AUTO_TRUST"] = "1"
    import importlib
    import auto_responder
    importlib.reload(auto_responder)

    writes = []
    r = auto_responder.AutoResponder(lambda sid, d: writes.append((sid, d)))
    r.feed("s2", b"output\nYes, I trust this folder?\n")
    assert len(writes) == 1
    assert writes[0] == ("s2", b"\r")
    os.environ.pop("VT_AUTO_TRUST")


def test_cooldown_prevents_double_response():
    os.environ["VT_AUTO_TRUST"] = "1"
    import importlib
    import auto_responder
    importlib.reload(auto_responder)

    writes = []
    r = auto_responder.AutoResponder(lambda sid, d: writes.append((sid, d)))
    r.feed("s3", b"Yes, I trust this folder?")
    r.feed("s3", b"Yes, I trust this folder?")  # 5초 미경과 → 무시
    assert len(writes) == 1
    os.environ.pop("VT_AUTO_TRUST")


def test_different_sessions_independent():
    os.environ["VT_AUTO_TRUST"] = "1"
    import importlib
    import auto_responder
    importlib.reload(auto_responder)

    writes = []
    r = auto_responder.AutoResponder(lambda sid, d: writes.append((sid, d)))
    r.feed("s4", b"Yes, I trust this folder?")
    r.feed("s5", b"Yes, I trust this folder?")
    sids = [w[0] for w in writes]
    assert "s4" in sids and "s5" in sids
    os.environ.pop("VT_AUTO_TRUST")


def test_remove_clears_session_state():
    os.environ["VT_AUTO_TRUST"] = "1"
    import importlib
    import auto_responder
    importlib.reload(auto_responder)

    writes = []
    r = auto_responder.AutoResponder(lambda sid, d: writes.append((sid, d)))
    r.feed("s6", b"Yes, I trust this folder?")
    r.remove("s6")
    # remove 후엔 cooldown 초기화되므로 다시 응답 가능
    r.feed("s6", b"Yes, I trust this folder?")
    assert len(writes) == 2
    os.environ.pop("VT_AUTO_TRUST")
