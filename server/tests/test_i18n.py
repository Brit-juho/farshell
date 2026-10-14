"""화면 언어(VT_LANG) — 결정 순서, 저장 경로, index.html 주입, /api/lang."""
import os
import stat

import pytest
from starlette.testclient import TestClient

import i18n
import main


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    p = tmp_path / "vt.env"
    p.write_text("# test\n", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setenv("VT_CONFIG", str(p))
    for k in ("VT_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        # set 후 del — set_lang이 os.environ을 직접 고쳐도 teardown이 원래 값으로 되돌린다.
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
    return p


@pytest.mark.parametrize("raw,want", [
    ("ko", "ko"), ("ko_KR.UTF-8", "ko"), ("en-US", "en"), ("EN", "en"),
    ("fr_FR.UTF-8", None), ("", None), (None, None), ("C", None),
])
def test_normalize(raw, want):
    assert i18n.normalize(raw) == want


def test_os_locale_fallback(cfg, monkeypatch):
    assert i18n.current() == "en"  # 아무것도 없으면 영어
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    assert i18n.current() == "ko"
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")  # LC_ALL이 이기고, 모르는 언어는 영어
    assert i18n.current() == "en"
    monkeypatch.setenv("LC_ALL", "C")  # C/POSIX는 언어 정보가 없다 → 다음 변수
    assert i18n.current() == "ko"


def test_file_beats_stale_env(cfg, monkeypatch):
    monkeypatch.setenv("VT_LANG", "ko")
    assert i18n.current() == "ko"
    cfg.write_text("VT_LANG='en'\n", encoding="utf-8")
    assert i18n.current() == "en"


def test_set_lang_writes_through_vt_env_sh(cfg):
    i18n.set_lang("ko")
    assert "VT_LANG='ko'" in cfg.read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600
    i18n.set_lang("en")
    text = cfg.read_text(encoding="utf-8")
    assert text.count("VT_LANG=") == 1 and "VT_LANG='en'" in text
    assert i18n.current() == "en"


def test_set_lang_rejects_unknown(cfg):
    with pytest.raises(ValueError):
        i18n.set_lang("fr; rm -rf /")
    assert "VT_LANG" not in cfg.read_text(encoding="utf-8")


def test_render_index():
    html = ('<html lang="ko" data-skin="x"><meta name="description" content="원문" '
            'data-i18n-attr="content"><!--vt:i18n--><script src="/static/js/i18n-boot.js"></script>')
    ko = i18n.render_index(html, "ko")
    assert '<html lang="ko"' in ko and "locales" not in ko and "<!--vt:i18n-->" not in ko
    en = i18n.render_index(html, "en", "123", {"원문": 'A "quoted" <b>'})
    assert '<html lang="en"' in en
    assert '<script src="/static/js/locales/en.js?v=123"></script>' in en
    assert 'content="A &quot;quoted&quot; &lt;b&gt;"' in en


def test_real_dictionary_is_json():
    d = i18n.load_dict(main.Path(main.FRONTEND_DIR) / "js" / "locales" / "en.js")
    assert d and all(isinstance(k, str) for k in d)


@pytest.fixture
def client(cfg):
    with TestClient(main.app) as c:
        yield c


def test_index_and_status_follow_vt_lang(client, cfg):
    cfg.write_text("VT_LANG='en'\n", encoding="utf-8")
    r = client.get("/")
    assert r.status_code == 200
    assert '<html lang="en"' in r.text and "/static/js/locales/en.js" in r.text
    assert "FarShell — your macOS/Linux terminal, anywhere" in r.text
    assert client.get("/api/auth/status").json()["lang"] == "en"
    cfg.write_text("VT_LANG='ko'\n", encoding="utf-8")
    r = client.get("/")
    assert '<html lang="ko"' in r.text and "/static/js/locales/" not in r.text
    assert client.get("/api/auth/status").json()["lang"] == "ko"


def test_put_lang(client, cfg):
    r = client.put("/api/lang", json={"lang": "en"})
    assert r.status_code == 200 and r.json() == {"ok": True, "lang": "en"}
    assert "VT_LANG='en'" in cfg.read_text(encoding="utf-8")
    for bad in ({"lang": "fr"}, {"lang": None}, {}, ["en"], "en"):
        assert client.put("/api/lang", json=bad).status_code == 400
    assert client.put("/api/lang", content=b"not json",
                      headers={"content-type": "application/json"}).status_code == 400
    assert "VT_LANG='en'" in cfg.read_text(encoding="utf-8")


def test_put_lang_requires_auth(cfg, monkeypatch):
    import auth
    monkeypatch.setattr(auth, "is_protected", lambda: True)
    monkeypatch.setattr(auth, "check_request", lambda token: False)
    with TestClient(main.app) as c:
        assert c.put("/api/lang", json={"lang": "en"}).status_code == 401
        assert c.get("/api/auth/status").status_code == 200
        assert c.get("/").status_code == 200
    assert "VT_LANG" not in cfg.read_text(encoding="utf-8")
