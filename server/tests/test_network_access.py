"""network_access 정책 단위 테스트 (A3/A4 IP 필터의 회귀 기반).

CIDR 매칭, 키워드 확장, bind host 결정, spec 캐시를 검증한다.
"""

import ipaddress

import network_access as na


def _spec(text):
    return na.parse_access_spec(text)


def test_localhost_allows_only_loopback():
    s = _spec("localhost")
    assert s.is_allowed("127.0.0.1") is True
    assert s.is_allowed("::1") is True
    assert s.is_allowed("10.0.0.5") is False
    assert s.is_allowed("192.168.1.9") is False


def test_lan_ranges():
    s = _spec("localhost,lan")
    for ip in ("10.0.0.1", "172.16.5.5", "192.168.1.10", "127.0.0.1"):
        assert s.is_allowed(ip) is True, ip
    # 공인 IP는 거부
    assert s.is_allowed("8.8.8.8") is False
    assert s.is_allowed("1.1.1.1") is False


def test_tailscale_cgnat_range():
    s = _spec("tailscale")
    assert s.is_allowed("100.100.1.2") is True   # CGNAT 대역
    assert s.is_allowed("10.0.0.1") is False       # 일반 LAN은 tailscale 모드에 없음


def test_allow_all():
    s = _spec("all")
    assert s.allow_all is True
    assert s.is_allowed("8.8.8.8") is True
    assert s.is_allowed(None) is True  # allow_all이면 None도 통과


def test_none_ip_rejected_when_not_allow_all():
    s = _spec("lan")
    assert s.is_allowed(None) is False


def test_ipv4_mapped_ipv6_is_unwrapped():
    s = _spec("localhost")
    # ::ffff:127.0.0.1 형태가 loopback으로 인식돼야 함
    assert s.is_allowed("::ffff:127.0.0.1") is True


def test_invalid_ip_string_rejected():
    s = _spec("lan")
    assert s.is_allowed("not-an-ip") is False
    assert s.is_allowed("testclient") is False  # TestClient 기본 client host


def test_direct_cidr_token():
    s = _spec("203.0.113.0/24")
    assert s.is_allowed("203.0.113.7") is True
    assert s.is_allowed("203.0.114.1") is False


def test_resolve_bind_host():
    assert na.resolve_bind_host(_spec("all")) == "0.0.0.0"
    assert na.resolve_bind_host(_spec("localhost")) == "127.0.0.1"
    assert na.resolve_bind_host(_spec("localhost,lan")) == "0.0.0.0"
    # 정책이 비면 안전하게 loopback
    assert na.resolve_bind_host(_spec("")) == "127.0.0.1"


def test_network_mode_to_spec():
    assert na.network_mode_to_spec("localhost") == "localhost"
    assert na.network_mode_to_spec("lan") == "localhost,lan"
    assert na.network_mode_to_spec("tailscale") == "localhost,tailscale"
    assert na.network_mode_to_spec("") == "all"


def test_get_current_spec_cache_invalidates_on_env_change(monkeypatch):
    # P2 캐시: env가 바뀌면 새 spec을 돌려줘야 한다.
    monkeypatch.setenv("VT_NETWORK_MODE", "localhost")
    monkeypatch.delenv("VT_ACCESS_SPEC", raising=False)
    s1 = na.get_current_spec()
    assert s1.is_allowed("10.0.0.1") is False

    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    s2 = na.get_current_spec()
    assert s2.allow_all is True
    assert s1 is not s2  # 캐시 키(env)가 달라져 재빌드됨
