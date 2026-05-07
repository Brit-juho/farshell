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
