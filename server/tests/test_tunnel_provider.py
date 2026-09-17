"""VT_TUNNEL_PROVIDER — 공개 입구 제공자 전환(2026-09-17).

`/api/tunnel/status`는 HUD가 30초마다 폴링하는 화면이다. 제공자가 ngrok인데
cloudflared만 쳐다보면 **터널이 멀쩡히 살아 있는데 "끊김"이라고 표시**된다.
그 거짓말을 막는 게 이 테스트의 목적이고, 동시에 기존 cloudflare 경로가
그대로인지도 같이 고정한다.
"""

import tunnel


def _fresh(monkeypatch, provider):
    monkeypatch.setenv("VT_TUNNEL_PROVIDER", provider)
    tunnel.invalidate_status_cache()
    return tunnel.get_tunnel_status(force=True)


def test_default_provider_is_cloudflare(monkeypatch):
    """미설정이면 cloudflare — 기존 사용자의 동작이 바뀌면 안 된다."""
    monkeypatch.delenv("VT_TUNNEL_PROVIDER", raising=False)
    assert tunnel.get_provider() == "cloudflare"


def test_unknown_provider_falls_back_to_cloudflare(monkeypatch):
    """오타 하나로 원격 접속이 통째로 죽으면 안 된다 — 막지 말고 되돌린다."""
    monkeypatch.setenv("VT_TUNNEL_PROVIDER", "Cloudflre")
    assert tunnel.get_provider() == "cloudflare"


def test_provider_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setenv("VT_TUNNEL_PROVIDER", " NGROK ")
    assert tunnel.get_provider() == "ngrok"


def test_none_reports_disabled_not_broken(monkeypatch):
    """none은 '고장'이 아니라 '안 씀'이다 — running=False지만 mode로 구분된다."""
    st = _fresh(monkeypatch, "none")
    assert st["provider"] == "none"
    assert st["running"] is False
    assert st["mode"] == "disabled"
    assert st["url"] is None


def test_cloudflare_status_keeps_its_shape(monkeypatch):
    """HUD가 읽는 키들은 제공자가 늘어도 그대로여야 한다."""
    st = _fresh(monkeypatch, "cloudflare")
    for key in ("provider", "installed", "running", "pids", "url", "mode", "checked_at"):
        assert key in st
    assert st["provider"] == "cloudflare"
    assert st["mode"] in ("named", "anonymous")


def test_ngrok_status_shape_without_agent(monkeypatch):
    """에이전트가 없어도 같은 스키마로 답해야 한다(호출부가 키 유무로 분기하지 않도록)."""
    monkeypatch.setenv("VT_NGROK_API", "http://127.0.0.1:1")  # 절대 안 열리는 포트
    st = _fresh(monkeypatch, "ngrok")
    assert st["provider"] == "ngrok"
    assert st["url"] is None
    for key in ("installed", "running", "pids", "mode", "checked_at"):
        assert key in st


def test_ngrok_mode_reflects_reserved_domain(monkeypatch):
    monkeypatch.setenv("VT_NGROK_API", "http://127.0.0.1:1")
    monkeypatch.setenv("VT_NGROK_DOMAIN", "https://example.ngrok-free.dev/")
    st = _fresh(monkeypatch, "ngrok")
    assert st["mode"] == "reserved"
    # 스키마·프로토콜·슬래시를 벗겨 호스트만 남긴다
    assert st["hostname"] == "example.ngrok-free.dev"


def test_ngrok_is_protected_from_the_port_dashboard():
    """폰의 포트 대시보드에서 ngrok을 kill하면 그 화면 자체가 끊긴다.

    cloudflared·tailscaled·sshd는 이미 막혀 있었는데 ngrok만 뚫려 있었다."""
    import portscan
    assert "ngrok" in portscan._CRITICAL_NAMES
