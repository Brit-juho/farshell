"""새 세션 이름 미리보기(GET /api/tmux/name-preview, 세션 로직 점검 단계 6). 만들 때와 같은 규칙이고 아무것도 만들지 않는다."""

import asyncio
import json

import routes.tmux as tmux_route
import session_names
import tmux_runner


def _call(cwd=""):
    out = asyncio.run(tmux_route.tmux_name_preview(cwd))
    return out if isinstance(out, dict) else (out.status_code, json.loads(out.body))


def test_preview_uses_the_creation_rule_without_creating(tmp_path, monkeypatch):
    repo = tmp_path / "farshell"
    repo.mkdir()
    calls = []
    monkeypatch.setattr(tmux_runner, "has_session", lambda name: name == "farshell")
    monkeypatch.setattr(tmux_runner, "run", lambda *a, **k: calls.append(a) or (0, b"", b""))
    assert _call(str(repo)) == {"base": "farshell", "name": "farshell-2"}
    assert calls == [], "미리보기는 tmux에 아무것도 만들지 않는다"


def test_preview_without_cwd_uses_the_default_start_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(tmux_route.platform_utils, "default_start_dir", lambda: str(tmp_path / "proj"))
    (tmp_path / "proj").mkdir()
    monkeypatch.setattr(tmux_runner, "has_session", lambda name: False)
    assert _call()["name"] == session_names.base_for(str(tmp_path / "proj"))


def test_preview_refuses_a_relative_or_missing_folder(tmp_path):
    assert _call("relative/path")[0] == 400
    assert _call(str(tmp_path / "missing"))[1]["error"] == "invalid_cwd"


def test_catalog_settings_need_elevation():
    from authz.route_table import TABLE
    assert TABLE[("POST", "/api/session-catalog/settings")].elevated is True
