"""D2/D7: network_access.py 단위 테스트.

CIDR 파싱, IP 허용/거부, bind host 결정, WS 미들웨어 IP 필터.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

import network_access as na


# --- CIDR 파싱 + is_allowed ---

def test_localhost_allows_loopback():
    spec = na.parse_access_spec("localhost")
    assert spec.is_allowed("127.0.0.1")
    assert spec.is_allowed("127.0.0.2")

def test_localhost_allows_ipv6_loopback():
    spec = na.parse_access_spec("localhost")
    assert spec.is_allowed("::1")

def test_localhost_rejects_lan():
    spec = na.parse_access_spec("localhost")
    assert not spec.is_allowed("192.168.1.5")
    assert not spec.is_allowed("10.0.0.1")

def test_lan_allows_private_ranges():
    spec = na.parse_access_spec("localhost,lan")
    assert spec.is_allowed("127.0.0.1")
    assert spec.is_allowed("192.168.1.5")
    assert spec.is_allowed("10.0.0.1")
    assert spec.is_allowed("172.16.0.1")

def test_lan_rejects_public():
    spec = na.parse_access_spec("localhost,lan")
    assert not spec.is_allowed("8.8.8.8")
    assert not spec.is_allowed("1.1.1.1")

def test_all_allows_everything():
    spec = na.parse_access_spec("all")
    assert spec.allow_all
    assert spec.is_allowed("8.8.8.8")
    assert spec.is_allowed("192.168.1.1")

def test_invalid_cidr_skipped():
    # 잘못된 CIDR이 있어도 나머지는 파싱됨
    spec = na.parse_access_spec("localhost,NOT_A_CIDR")
    assert spec.is_allowed("127.0.0.1")

def test_none_ip_rejected():
    spec = na.parse_access_spec("localhost")
    assert not spec.is_allowed(None)

def test_empty_ip_rejected():
    spec = na.parse_access_spec("localhost,lan")
    assert not spec.is_allowed("")


# --- IPv6 LAN 범위 ---

def test_ipv6_fc00_is_lan():
    spec = na.parse_access_spec("lan")
    assert spec.is_allowed("fc00::1")

def test_ipv6_fe80_is_lan():
    spec = na.parse_access_spec("lan")
    assert spec.is_allowed("fe80::1")


# --- resolve_bind_host ---

def test_localhost_binds_to_127():
    spec = na.parse_access_spec("localhost")
    assert na.resolve_bind_host(spec) == "127.0.0.1"

def test_lan_binds_to_0000():
    spec = na.parse_access_spec("localhost,lan")
    assert na.resolve_bind_host(spec) == "0.0.0.0"

def test_all_binds_to_0000():
    spec = na.parse_access_spec("all")
    assert na.resolve_bind_host(spec) == "0.0.0.0"


# --- get_current_spec (환경변수 기반) ---

def test_env_network_mode_localhost(monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "localhost")
    monkeypatch.delenv("VT_ACCESS_SPEC", raising=False)
    spec = na.get_current_spec()
    assert spec.is_allowed("127.0.0.1")
    assert not spec.is_allowed("192.168.1.1")

def test_env_network_mode_all(monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    monkeypatch.delenv("VT_ACCESS_SPEC", raising=False)
    spec = na.get_current_spec()
    assert spec.allow_all


# --- D9: Tailscale 네트워크 모드 (회사망 등에서 vt ssh로 접속하는 경로) ---

def test_tailscale_keyword_allows_cgnat_range():
    spec = na.parse_access_spec("tailscale")
    assert spec.is_allowed("100.64.0.1")
    assert spec.is_allowed("100.127.255.254")
    assert not spec.is_allowed("8.8.8.8")
    assert not spec.is_allowed("192.168.1.5")  # 일반 LAN은 tailscale 키워드 단독으론 불허


def test_network_mode_to_spec_tailscale_includes_localhost():
    spec_str = na.network_mode_to_spec("tailscale")
    spec = na.parse_access_spec(spec_str)
    assert spec.is_allowed("127.0.0.1")       # localhost는 항상 허용
    assert spec.is_allowed("100.101.102.103")  # tailnet CGNAT
    assert not spec.is_allowed("8.8.8.8")      # 공인 인터넷은 차단


def test_tailscale_mode_rejects_plain_lan():
    """tailscale 모드는 lan을 포함하지 않음 — 신뢰 안 하는 LAN(회사망)과 구분되는 게 핵심."""
    spec = na.parse_access_spec(na.network_mode_to_spec("tailscale"))
    assert not spec.is_allowed("192.168.1.5")
    assert not spec.is_allowed("10.0.0.1")


def test_env_network_mode_tailscale(monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "tailscale")
    monkeypatch.delenv("VT_ACCESS_SPEC", raising=False)
    spec = na.get_current_spec()
    assert spec.is_allowed("100.64.1.1")
    assert spec.is_allowed("127.0.0.1")
    assert not spec.is_allowed("8.8.8.8")


def test_tailscale_mode_binds_to_0000():
    """localhost 전용이 아니므로 0.0.0.0에 바인드되어야 원격 tailnet에서 접속 가능."""
    spec = na.parse_access_spec(na.network_mode_to_spec("tailscale"))
    assert na.resolve_bind_host(spec) == "0.0.0.0"


# --- D2: WS 미들웨어 IP 필터 통합 검증 (httpx로 실제 미들웨어 실행) ---

def test_middleware_allows_loopback_in_localhost_mode():
    """parse_access_spec("localhost")로 얻은 spec이 TestClient(127.0.0.1)를 허용함."""
    spec = na.parse_access_spec("localhost")
    # TestClient는 127.0.0.1에서 요청하므로 허용됨
    assert spec.is_allowed("127.0.0.1")
    # LAN은 차단됨
    assert not spec.is_allowed("192.168.0.1")


def test_middleware_blocks_wan_in_lan_mode():
    """localhost,lan 모드에서 WAN IP 차단."""
    spec = na.parse_access_spec("localhost,lan")
    assert not spec.is_allowed("54.192.0.0")
    assert not spec.is_allowed("8.8.8.8")


# --- D9: tailscale 모드 (회사망 등에서 Tailscale+SSH 접속 시나리오) ---

def test_tailscale_allows_cgnat_range():
    spec = na.parse_access_spec("tailscale")
    assert spec.is_allowed("100.64.0.1")
    assert spec.is_allowed("100.100.100.100")
    assert spec.is_allowed("100.127.255.254")

def test_tailscale_rejects_lan_and_public():
    spec = na.parse_access_spec("tailscale")
    assert not spec.is_allowed("192.168.1.5")
    assert not spec.is_allowed("8.8.8.8")

def test_network_mode_to_spec_tailscale():
    assert na.network_mode_to_spec("tailscale") == "localhost,tailscale"

def test_tailscale_mode_allows_loopback_and_tailnet_only():
    spec = na.parse_access_spec(na.network_mode_to_spec("tailscale"))
    assert spec.is_allowed("127.0.0.1")
    assert spec.is_allowed("100.101.102.103")
    assert not spec.is_allowed("192.168.1.5")
    assert not spec.is_allowed("8.8.8.8")

def test_tailscale_mode_binds_to_0000():
    # 100.64.0.0/10은 loopback이 아니므로 0.0.0.0 bind 필요 (실제 tailscale0/utun 인터페이스로 들어옴)
    spec = na.parse_access_spec(na.network_mode_to_spec("tailscale"))
    assert na.resolve_bind_host(spec) == "0.0.0.0"

def test_env_network_mode_tailscale(monkeypatch):
    monkeypatch.setenv("VT_NETWORK_MODE", "tailscale")
    monkeypatch.delenv("VT_ACCESS_SPEC", raising=False)
    spec = na.get_current_spec()
    assert spec.is_allowed("100.64.0.1")
    assert not spec.is_allowed("192.168.1.1")
    assert not spec.is_allowed("8.8.8.8")
