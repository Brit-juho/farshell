"""A3 회귀: X-Forwarded-For 스푸핑으로 IP 화이트리스트를 우회할 수 없어야 한다.

예전엔 XFF 최좌측(클라이언트가 임의 삽입)을 신뢰해 `X-Forwarded-For: 127.0.0.1`
한 줄로 필터를 뚫었다. 이제 CF-Connecting-IP 우선 + XFF 최우측(신뢰 홉이 추가한 값)만
신뢰한다.
"""

import pytest
from starlette.testclient import TestClient

import main


@pytest.fixture
def client(monkeypatch):
    # lan 모드로 필터를 켠다. TestClient의 기본 client host("testclient")는 LAN이 아니므로
    # 프록시 헤더가 없으면 항상 차단된다.
    monkeypatch.setenv("VT_NETWORK_MODE", "lan")
    monkeypatch.delenv("VT_ACCESS_SPEC", raising=False)
    with TestClient(main.app) as c:
        yield c


def test_no_proxy_trust_blocks_unknown_client(client):
    # VT_TRUST_PROXY 미설정 → 헤더 무시, raw client(testclient)는 LAN 아님 → 403
    r = client.get("/api/sessions", headers={"X-Forwarded-For": "10.0.0.5"})
    assert r.status_code == 403


def test_leftmost_xff_spoof_is_rejected(client, monkeypatch):
    monkeypatch.setenv("VT_TRUST_PROXY", "1")
    # 공격자가 LAN IP를 최좌측에 심고, 실제(신뢰 홉이 추가한) 값은 공인 IP.
    # 최우측(8.8.8.8)만 신뢰하므로 차단돼야 한다.
    r = client.get("/api/sessions", headers={"X-Forwarded-For": "192.168.1.5, 8.8.8.8"})
    assert r.status_code == 403


def test_rightmost_xff_lan_is_allowed(client, monkeypatch):
    monkeypatch.setenv("VT_TRUST_PROXY", "1")
    r = client.get("/api/sessions", headers={"X-Forwarded-For": "10.0.0.5"})
    assert r.status_code == 200


def test_cf_connecting_ip_trusted_over_xff(client, monkeypatch):
    monkeypatch.setenv("VT_TRUST_PROXY", "1")
    # XFF가 공인 IP라도 Cloudflare의 CF-Connecting-IP(LAN)를 우선 신뢰.
    r = client.get(
        "/api/sessions",
        headers={"CF-Connecting-IP": "10.1.2.3", "X-Forwarded-For": "8.8.8.8"},
    )
    assert r.status_code == 200


def test_cf_connecting_ip_public_is_rejected(client, monkeypatch):
    monkeypatch.setenv("VT_TRUST_PROXY", "1")
    r = client.get("/api/sessions", headers={"CF-Connecting-IP": "8.8.8.8"})
    assert r.status_code == 403


def test_static_paths_bypass_filter(client):
    # 정적/루트 경로는 필터 예외 — 자산 로드는 막지 않는다.
    r = client.get("/manifest.json")
    assert r.status_code in (200, 404)  # 파일 존재 여부와 무관하게 403은 아님
