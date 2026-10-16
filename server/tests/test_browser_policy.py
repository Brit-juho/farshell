"""브라우저 URL 정책(R9.3, N-27)과 값 가림(R9.5) — 순수 함수 표 시험. DNS 는 쓰지 않는다(`dns=False` 또는 가짜 확인기)."""

from __future__ import annotations

import pytest

from browser import policy as P

EXPOSED = {3000, 8080}


def chk(url, allowlist=(), top_level=True, dns=False):
    return P.check_url(url, allowlist=allowlist, exposed_ports=EXPOSED, top_level=top_level, dns=dns)


# --- 기본: about:blank 와 노출한 localhost 포트뿐 ---------------------------------------------------

@pytest.mark.parametrize("url", [
    "about:blank", "ABOUT:BLANK",
    "http://localhost:3000/", "http://localhost:3000/a/b?x=1#f", "https://localhost:8080", "http://127.0.0.1:3000/", "http://[::1]:3000/",
    "http://LocalHost:3000", "http://localhost.:3000/",
])
def test_default_allows_blank_and_exposed_localhost(url):
    assert chk(url).allow, chk(url)


@pytest.mark.parametrize("url,reason", [
    ("http://localhost:3001/", "port_not_exposed"),        # 노출하지 않은 포트
    ("http://localhost/", "port_not_exposed"),             # 80 은 노출 안 됨
    ("http://example.com/", "not_allowlisted"),            # 공인 호스트도 허용 목록이 필요하다
    ("https://127.0.0.2:3000/", "not_allowlisted"),        # 다른 루프백 주소
    ("http://0.0.0.0:3000/", "not_allowlisted"),
    ("http://foo.localhost:3000/", "not_allowlisted"),
])
def test_default_denies_the_rest(url, reason):
    d = chk(url)
    assert not d.allow and d.reason == reason


# --- 스킴 표 ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("url,reason", [
    ("file:///etc/passwd", "scheme_file"),
    ("FILE:///etc/passwd", "scheme_file"),
    ("chrome://settings", "scheme_chrome"),
    ("chrome-extension://abcdef/popup.html", "scheme_chrome-extension"),
    ("devtools://devtools/bundled/inspector.html", "scheme_devtools"),
    ("view-source:http://localhost:3000/", "scheme_view-source"),
    ("data:text/html,<h1>x</h1>", "scheme_data"),
    ("blob:http://localhost:3000/uuid", "scheme_blob"),
    ("javascript:alert(1)", "scheme_javascript"),
    ("JaVaScRiPt:alert(1)", "scheme_javascript"),
    ("jav\tascript:alert(1)", "scheme_javascript"),         # 탭·개행은 브라우저가 지운다
    ("about:srcdoc", "scheme_about"),
    ("about:config", "scheme_about"),
    ("ws://localhost:3000/", "scheme_ws"),
    ("ftp://localhost/", "scheme_ftp"),
    ("gopher://x/", "scheme_not_allowed"),
    ("localhost:3000", "scheme_not_allowed"),               # `localhost:` 가 스킴으로 읽힌다
    ("//localhost:3000/", "no_scheme"),
    ("/etc/passwd", "no_scheme"),
    ("", "empty_url"),
])
def test_scheme_table(url, reason):
    d = chk(url, allowlist=["*", "localhost", "example.com"])
    assert not d.allow and d.reason == reason, (url, d)


def test_subframes_allow_data_blob_blank_but_not_javascript_or_file():
    for u in ("data:text/html,x", "blob:http://localhost:3000/u", "about:blank"):
        assert chk(u, top_level=False).allow, u
        assert not chk(u, top_level=True).allow or u == "about:blank"
    for u in ("javascript:1", "file:///x", "chrome://x", "view-source:http://localhost:3000"):
        assert not chk(u, top_level=False).allow, u


def test_non_string_and_oversize():
    assert not P.check_url(None, dns=False).allow
    assert not P.check_url("http://localhost:3000/" + "a" * 5000, exposed_ports=EXPOSED, dns=False).allow


# --- 사설·링크 로컬·메타데이터 IP ---------------------------------------------------------------------

PRIVATE = ["http://10.0.0.5/", "http://172.16.0.1/", "http://172.31.255.255/", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/",
           "http://169.254.1.1/", "http://100.64.0.1/", "http://100.100.100.200/", "http://[fd00:ec2::254]/", "http://[fe80::1]/", "http://[fc00::1]/",
           "http://[::ffff:10.0.0.1]/", "http://[::ffff:a00:1]/", "http://[64:ff9b::a00:1]/", "http://[2002:a00:1::]/"]


@pytest.mark.parametrize("url", PRIVATE)
def test_private_ranges_denied_without_allowlist(url):
    d = chk(url)
    assert not d.allow and d.reason == "not_allowlisted", (url, d)


@pytest.mark.parametrize("url", PRIVATE)
def test_wildcards_never_open_ips(url):
    assert not chk(url, allowlist=["*", "*.com", "*.0.0.1"]).allow


def test_explicit_ip_or_cidr_opens_private():
    assert chk("http://10.0.0.5/", allowlist=["10.0.0.5"]).allow
    assert chk("http://10.9.9.9/", allowlist=["10.0.0.0/8"]).allow
    assert not chk("http://11.0.0.1/", allowlist=["10.0.0.0/8"]).allow
    assert chk("http://192.168.1.10:8080/", allowlist=["192.168.1.10:8080"]).allow
    assert not chk("http://192.168.1.10/", allowlist=["192.168.1.10:8080"]).allow       # 포트가 다르다
    assert chk("http://[::ffff:10.0.0.1]/", allowlist=["10.0.0.0/8"]).allow             # IPv6 로 감싸도 IPv4 로 본다
    assert chk("http://169.254.169.254/", allowlist=["169.254.169.254"]).allow           # 메타데이터도 이름으로 적으면 열린다


# --- 숫자 모양 호스트 우회 ----------------------------------------------------------------------------

@pytest.mark.parametrize("url,allowed", [
    ("http://2130706433:3000/", True),          # 127.0.0.1 의 십진
    ("http://0x7f000001:3000/", True),
    ("http://0177.0.0.1:3000/", True),
    ("http://127.1:3000/", True),
    ("http://0x7f.1:3000/", True),
    ("http://[::ffff:127.0.0.1]:3000/", True),
    ("http://[::ffff:7f00:1]:3000/", True),
    ("http://2130706433:3001/", False),
    ("http://167772165/", False),             # 10.0.0.5
    ("http://0xA.0.0.5/", False),
    ("http://012.0.0.5/", False),              # 8진 → 10.0.0.5
])
def test_numeric_host_forms_are_normalized(url, allowed):
    assert chk(url).allow is allowed, url


@pytest.mark.parametrize("url", ["http://256.1.1.1/", "http://1.2.3.4.5/", "http://0xZZ/", "http://08.0.0.1/", "http://1..2/"])
def test_bad_numeric_hosts_rejected(url):
    d = chk(url, allowlist=["*"])
    assert not d.allow


# --- IDN · 대소문자 · 인코딩 --------------------------------------------------------------------------

def test_idn_is_normalized_to_punycode_and_compared_exactly():
    d = chk("http://bücher.example/")
    assert d.verdict.host == "xn--bcher-kva.example"
    assert chk("http://bücher.example/", allowlist=["xn--bcher-kva.example"]).allow
    assert chk("http://bücher.example/", allowlist=["bücher.example"]).allow          # 목록도 같은 방식으로 정규화
    assert not chk("http://bucher.example/", allowlist=["xn--bcher-kva.example"]).allow


def test_homograph_does_not_match_ascii_entry():
    cyr = "http://exаmple.com/"            # 가운데 а 는 키릴 문자
    d = chk(cyr, allowlist=["example.com"])
    assert not d.allow and d.verdict.host.startswith("xn--")


def test_fullwidth_and_ideographic_dots_fold_to_ascii():
    assert chk("http://ｌｏｃａｌｈｏｓｔ:3000/").allow
    assert chk("http://ｅｘａｍｐｌｅ。ｃｏｍ/", allowlist=["example.com"]).allow


def test_percent_encoded_host_decoded():
    assert chk("http://%6c%6f%63%61%6c%68%6f%73%74:3000/").allow
    assert chk("http://localhost%2e:3000/").allow
    assert not chk("http://local%00host:3000/").allow


def test_backslash_and_whitespace_tricks():
    assert chk("http:\\\\localhost:3000/x").allow            # 브라우저는 \ 를 / 로 읽는다
    assert chk("  http://localhost:3000/ \n").allow
    d = chk("http://localhost:3000\\@evil.com/")          # 브라우저에게 `\` 는 권한부의 끝 — 실제 목적지는 localhost:3000 이고 정책도 같은 곳을 본다
    assert d.verdict.host == "localhost" and d.verdict.port == 3000
    d = chk("http://evil.com\\@localhost:3000/")          # 반대로 겉보기 localhost 가 아니라 evil.com 으로 읽는다
    assert d.verdict.host == "evil.com" and not d.allow


def test_userinfo_rejected_even_when_host_listed():
    assert chk("http://localhost:3000@evil.com/", allowlist=["evil.com"]).reason == "userinfo"
    assert chk("http://user:pw@localhost:3000/").reason == "userinfo"


@pytest.mark.parametrize("url", ["http://exa mple.com/", "http://a<b.com/", "http://[::1/", "http://[::1]x/", "http://localhost:99999/", "http://localhost:0/", "http://localhost:abc/"])
def test_malformed(url):
    assert not chk(url, allowlist=["*"]).allow


# --- 허용 목록 문법 ------------------------------------------------------------------------------------

def test_allowlist_grammar():
    al = ["example.com", "*.corp.test", "https://api.test:8443", "10.0.0.0/8", "*", "ftp://x", "", None, 5]
    assert chk("https://example.com/", al).allow and chk("http://example.com/", al).allow          # 스킴 없는 항목 = http·https, 기본 포트
    assert not chk("http://example.com:8080/", al).allow
    assert chk("https://a.b.corp.test/", al).allow
    assert not chk("https://corp.test/", al).allow                                                   # apex 는 덮지 않는다
    assert chk("https://api.test:8443/x", al).allow and not chk("http://api.test:8443/", al).allow
    assert not chk("http://anything.example.org/", al).allow                                         # 단독 `*` 는 무시


def test_wildcard_does_not_cover_special_names():
    al = ["*.localhost", "*.internal", "*.google.internal"]
    assert not chk("http://foo.localhost:3000/", al).allow
    assert not chk("http://metadata.google.internal/", al).allow
    assert chk("http://metadata.google.internal/", ["metadata.google.internal"]).allow


# --- DNS(rebinding 한 겹) -----------------------------------------------------------------------------

def test_allowlisted_name_resolving_to_private_needs_explicit_ip(monkeypatch):
    table = {"app.corp.test": ["10.0.0.7"], "ok.corp.test": ["93.184.216.34"], "meta.corp.test": ["169.254.169.254"], "v6.corp.test": ["::ffff:10.0.0.7"]}
    monkeypatch.setattr(P, "resolve_host", lambda h: table.get(h))
    al = ["*.corp.test"]
    assert chk("http://ok.corp.test/", al, dns=True).allow
    assert chk("http://app.corp.test/", al, dns=True).reason == "dns_private_address"
    assert chk("http://meta.corp.test/", al, dns=True).reason == "dns_private_address"
    assert chk("http://v6.corp.test/", al, dns=True).reason == "dns_private_address"
    assert chk("http://app.corp.test/", al + ["10.0.0.7"], dns=True).allow
    assert chk("http://gone.corp.test/", al, dns=True).reason == "dns_unresolved"


# --- 하위 리소스 -----------------------------------------------------------------------------------------

def test_subresource_policy_opens_public_but_not_internal():
    sub = lambda u, al=(): P.check_subresource(u, allowlist=al, exposed_ports=EXPOSED)  # noqa: E731
    assert sub("https://cdn.example.com/a.js").allow
    assert sub("data:image/png;base64,AAAA").allow and sub("blob:http://localhost:3000/u").allow
    assert sub("http://localhost:3000/api").allow
    assert not sub("http://localhost:3001/api").allow
    assert not sub("http://169.254.169.254/latest").allow
    assert not sub("http://10.0.0.5/x").allow and sub("http://10.0.0.5/x", ["10.0.0.5"]).allow
    assert not sub("file:///etc/passwd").allow and not sub("chrome://x").allow


# --- redirect 사슬은 hop 마다 같은 함수 -------------------------------------------------------------------

def test_every_hop_of_a_chain_is_checked():
    chain = ["http://localhost:3000/start", "http://localhost:3000/next", "http://169.254.169.254/latest", "http://localhost:3000/after"]
    verdicts = [chk(u).allow for u in chain]
    assert verdicts == [True, True, False, True]          # 가운데 hop 하나만 어긋나도 그 hop 에서 걸린다


# --- 감사용 URL ---------------------------------------------------------------------------------------------

def test_audit_url_drops_query_and_fragment():
    assert P.audit_url("http://localhost:3000/a/b?token=secret#frag") == "http://localhost:3000/a/b"
    assert "secret" not in P.audit_url("https://x.test/p?q=secret")


# --- 값 가림 ---------------------------------------------------------------------------------------------------

COOKIES = [{"name": "sid", "value": "SECRET1", "domain": "localhost", "path": "/", "secure": False, "httpOnly": True, "sameSite": "Lax", "expires": 123}]


def test_cookies_redacted_by_default_and_values_on_request():
    r = P.redact_cookies(COOKIES, False)
    assert r == [{"name": "sid", "domain": "localhost", "path": "/", "secure": False, "httpOnly": True, "sameSite": "Lax", "expires": 123, "valueRedacted": True}]
    assert "SECRET1" not in str(r)
    assert P.redact_cookies(COOKIES, True)[0]["value"] == "SECRET1"


def test_storage_redacted_by_default():
    data = {"local": {"token": "T0P", "theme": "dark"}, "session": {"s": "S"}}
    r = P.redact_storage(data, False)
    assert set(r["local"]) == {"token", "theme"} and set(r["session"]) == {"s"}
    assert "T0P" not in str(r) and "dark" not in str(r) and r["session"]["s"] == "[redacted]"
    assert r["valuesRedacted"] is True
    assert P.redact_storage(data, True)["local"]["token"] == "T0P"


def test_network_redacts_headers_and_big_bodies_even_with_values():
    rows = [{"url": "http://localhost:3000/x", "requestHeaders": {"Cookie": "a=1", "AUTHORIZATION": "Bearer z", "Proxy-Authorization": "p", "Accept": "*/*"},
             "responseHeaders": {"set-cookie": "sid=1", "Content-Type": "text/html"}, "postData": "x" * (64 * 1024 + 1)},
            {"url": "http://localhost:3000/y", "responseBody": "x" * 100}]
    out = P.redact_network(rows)
    assert out[0]["requestHeaders"] == {"Cookie": "[redacted]", "AUTHORIZATION": "[redacted]", "Proxy-Authorization": "[redacted]", "Accept": "*/*"}
    assert out[0]["responseHeaders"]["set-cookie"] == "[redacted]" and out[0]["responseHeaders"]["Content-Type"] == "text/html"
    assert out[0]["postData"] == "[redacted]" and out[0]["postDataRedacted"] == "too_large"
    assert out[1]["responseBody"] == "x" * 100                       # 64KB 이하는 그대로


def test_eval_result_cap():
    v, cut = P.cap_result("a" * 10)
    assert (v, cut) == ("a" * 10, False)
    v, cut = P.cap_result("a" * (64 * 1024 + 10))
    assert cut and len(v) == 64 * 1024


# --- 한도 -------------------------------------------------------------------------------------------------------

def test_limits_per_tab_windows():
    lim = P.TabLimits()
    t0 = 1000.0
    assert all(lim.hit("t", "js", t0 + i * 0.1) is None for i in range(10))
    assert lim.hit("t", "js", t0 + 2) == "js"                              # 11번째 js
    assert lim.hit("other", "js", t0 + 2) is None                          # 다른 탭은 따로
    assert lim.hit("t", "js", t0 + 61) is None                             # 창이 지나면 풀린다
    lim = P.TabLimits()
    assert all(lim.hit("t", "navigate", t0 + i * 0.1) is None for i in range(30))
    assert lim.hit("t", "navigate", t0 + 4) == "navigate"
    lim = P.TabLimits()
    assert all(lim.hit("t", "other", t0 + i * 0.1) is None for i in range(60))
    assert lim.hit("t", "other", t0 + 7) == "general"
