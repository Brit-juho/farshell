"""브라우저 broker — 판정·소유·URL 정책·eval 게이트·값 가림·한도·adapter 다운(F7)·임대/세대(N-28)·회수(N-15). 가짜 어댑터로 돈다(실제 브라우저 없음)."""

from __future__ import annotations

import json
import os
import stat

import pytest

import browser.adapters as A
from authz import audit, saga, store
from authz import principals as P
from authz.errors import ShareError
from browser import broker, config, policy, tabs
from browser_kit import FakeAdapter, element, grant, page, seed

AL = P.agent("$1:101", name="alpha")
BO = P.agent("$2:102", name="bravo")
H = P.human("dev1")
HE = P.human("dev1", elevated=True)
CLI = P.cli(501, 4242)

HOME = "http://localhost:3000/"


def web():
    return {
        "about:blank": page(),
        HOME: page("Home", "hello", elements=[element("button", "Go", goto=HOME + "next"), element("textbox", "Name"),
                                              element("link", "Evil", goto="http://169.254.169.254/latest"),
                                              element("button", "Frame", frame="http://10.0.0.9/inner"),
                                              element("button", "Later", delayed="http://10.0.0.9/later"),
                                              element("button", "Ext", goto="http://example.com/")],
                   cookies=[{"name": "sid", "value": "SECRETCOOKIE", "domain": "localhost", "path": "/", "secure": False, "httpOnly": True}],
                   storage={"local": {"token": "SECRETTOKEN"}, "session": {}},
                   network=[{"url": HOME + "api", "method": "GET", "status": 200, "requestHeaders": {"Cookie": "sid=SECRETCOOKIE", "Accept": "*/*"},
                             "responseHeaders": {"Set-Cookie": "sid=SECRETCOOKIE2"}, "postData": "p" * (64 * 1024 + 5)}]),
        HOME + "next": page("Next", "second"),
        HOME + "redir": page("r", redirect="http://localhost:3000/redir2"),
        HOME + "redir2": page("r2", redirect="http://169.254.169.254/latest"),
        HOME + "okredir": page("o", redirect=HOME + "next"),
        HOME + "framed": page("F", frames=["http://10.0.0.9/inner"]),
        HOME + "sub": page("S", subresources=["http://169.254.169.254/x"]),
        "http://169.254.169.254/latest": page("meta", "SECRETMETA"),
        "http://10.0.0.9/inner": page("inner"),
        "http://10.0.0.9/later": page("later"),
        "http://example.com/": page("Ex", "public"),
        "http://10.0.0.7/": page("priv", "private ok"),
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "browser")
    broker.reset_for_tests()
    tabs.drop_lease()
    fake = FakeAdapter(web())
    A.register("fake", lambda: fake)
    config.set_enabled("fake", True, consent=True)
    monkeypatch.setattr(broker, "exposed_ports", lambda: {3000})
    monkeypatch.setattr(policy, "resolve_host", lambda h: ["93.184.216.34"])

    class E:
        pass
    e = E()
    e.fake, e.tmp = fake, tmp_path
    yield e
    broker.shutdown()
    A.unregister("fake")
    broker.reset_for_tests()


def code(fn, *a, **k):
    with pytest.raises(ShareError) as e:
        fn(*a, **k)
    return e.value


def give(p, scopes=("browser",), **kw):
    seed(grant(p.id, scopes, **kw))


def opened(p=AL, **kw):
    return broker.open_tab(p, {"adapter": "fake", **kw})["id"]


def audit_text() -> str:
    """허용 판정이 남는 주 체인 + 거부 기록(별도 파일) 전부."""
    from authz import denylog
    out = ""
    for p in (audit.path(), denylog.path()):
        try:
            out += p.read_text(encoding="utf-8")
        except OSError:
            pass
    return out


# --- 판정: 기본은 아무것도 못 한다 ---------------------------------------------------------------------

def test_agent_without_grant_cannot_open(env):
    e = code(broker.open_tab, AL, {"adapter": "fake"})
    assert (e.code, e.status) == ("forbidden", 403)
    assert e.extra["need"] == {"resource": "browser:local/new", "scopes": ["browser"], "ops": ["open"]}


def test_open_navigate_snapshot_click_close_happy_path(env):
    give(AL)
    tid = opened(url=HOME)
    assert tabs.get(tid)["owner"] == AL.id and tabs.get(tid)["state"] == "open"
    snap = broker.op(AL, tid, "snapshot")
    assert snap["title"] == "Home" and [e["ref"] for e in snap["elements"]][:2] == ["e1", "e2"]
    assert all("native" not in e for e in snap["elements"])                   # 어댑터 고유 참조는 밖으로 나가지 않는다
    assert broker.op(AL, tid, "click", {"ref": "e1"}) == {"ok": True}
    assert broker.op(AL, tid, "snapshot")["title"] == "Next"
    assert broker.op(AL, tid, "screenshot")["bytes"] > 0
    broker.op(AL, tid, "snapshot")
    assert broker.close_tab(AL, tid) == {"closed": tid}
    assert tabs.get(tid) is None and env.fake.closed


def test_op_names_scopes_are_checked_per_operation(env):
    give(AL, sconds={"browser": {"ops": ["open", "read"]}})                    # navigate·input·close 없음
    tid = opened()
    assert broker.op(AL, tid, "snapshot")["url"] == "about:blank"
    e = code(broker.op, AL, tid, "navigate", {"url": HOME})
    assert e.status == 403 and e.extra["need"]["ops"] == ["navigate"]
    e = code(broker.close_tab, AL, tid)
    assert e.extra["need"]["ops"] == ["close"]


def test_type_and_click_need_snapshot_ref_not_selectors(env):
    give(AL)
    tid = opened(url=HOME)
    assert code(broker.op, AL, tid, "click", {"ref": "e1"}).code == "unknown_ref"        # snapshot 전
    broker.op(AL, tid, "snapshot")
    for bad in ("#id", "div > a", "//a", "text=Go", "e999", "e1; alert(1)", 5, None):
        assert code(broker.op, AL, tid, "click", {"ref": bad}).code in ("unknown_ref", "bad_request")
    assert broker.op(AL, tid, "type", {"ref": "e2", "text": "Neo", "submit": True}) == {"ok": True}
    assert code(broker.op, AL, tid, "type", {"ref": "e2", "text": "x" * 10001}).status == 413
    broker.op(AL, tid, "navigate", {"url": HOME + "next"})
    assert code(broker.op, AL, tid, "click", {"ref": "e1"}).code == "unknown_ref"        # 이동하면 번호는 무효


# --- 소유 (R9.2) ------------------------------------------------------------------------------------------

def test_other_agent_cannot_operate_even_with_wildcard_grant(env):
    give(AL)
    give(BO)
    tid = opened(AL)
    e = code(broker.op, BO, tid, "snapshot")
    assert (e.code, e.status, e.extra["why"]) == ("forbidden", 403, "not_owner")
    assert code(broker.close_tab, BO, tid).extra["why"] == "not_owner"
    assert code(broker.handoff, BO, tid).status == 403
    assert broker.list_tabs(BO)["tabs"] == []                                   # 목록에도 남의 탭은 안 보인다
    assert [t["id"] for t in broker.list_tabs(AL)["tabs"]] == [tid]


def test_named_tab_grant_lets_another_agent_operate(env):
    give(AL)
    tid = opened(AL)
    seed(grant(BO.id, ["browser"], resource=f"browser:local/tab/{tid}", sconds={"browser": {"ops": ["read"]}}))
    assert broker.op(BO, tid, "snapshot")["title"] == ""
    assert code(broker.op, BO, tid, "navigate", {"url": HOME}).extra["need"]["ops"] == ["navigate"]   # 이름 붙인 grant 의 ops 안에서만
    other = opened(AL)
    assert code(broker.op, BO, other, "snapshot").status == 403                    # 다른 탭엔 효력이 없다


def test_r92_style_tab_resource_grant(env):
    give(AL)
    tid = opened(AL)
    seed(grant(BO.id, ["browser"], resource=f"browser:tab:{tid}"))
    assert broker.op(BO, tid, "snapshot")["url"] == "about:blank"


def test_same_pair_members_share_tabs(env, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "browser,pair")
    seed(grant(AL.id, ["browser"], pair_id="pr-aaaaaaaaaa"), grant(BO.id, ["browser"], pair_id="pr-aaaaaaaaaa"))
    tid = opened(AL)
    assert tabs.get(tid)["pair"] == "pr-aaaaaaaaaa"
    assert broker.op(BO, tid, "snapshot")["url"] == "about:blank"
    seed(grant(P.agent("$3:103", name="charlie").id, ["browser"], pair_id="pr-bbbbbbbbbb"))
    assert code(broker.op, P.agent("$3:103", name="charlie"), tid, "snapshot").extra["why"] == "not_owner"


def test_human_and_cli_manage_but_do_not_operate_foreign_tabs(env):
    give(AL)
    tid = opened(AL)
    e = code(broker.op, H, tid, "snapshot")                                        # 사람 기본 권한에 browser 가 있어도 소유자가 아니다
    assert e.extra["why"] == "not_owner"
    assert [t["id"] for t in broker.list_tabs(H)["tabs"]] == [tid]
    assert [t["id"] for t in broker.list_tabs(CLI)["tabs"]] == [tid]
    assert broker.close_tab(CLI, tid) == {"closed": tid}


def test_unknown_tab_and_unknown_op(env):
    give(AL)
    assert code(broker.op, AL, "bt-nope", "snapshot").status == 404
    assert code(broker.op, AL, "bt-nope", "download").status == 404


def test_per_owner_tab_cap(env):
    give(AL)
    for _ in range(broker.MAX_TABS_PER_OWNER):
        opened()
    assert code(broker.open_tab, AL, {"adapter": "fake"}).status == 429


# --- eval 게이트 (R9.4) --------------------------------------------------------------------------------------

def test_eval_blocked_without_js_grant_for_agent_and_human(env):
    give(AL)
    tid = opened()
    e = code(broker.op, AL, tid, "eval", {"script": "1+1"})
    assert e.status == 403 and e.extra["need"]["scopes"] == ["browser.js"]
    seed(grant(H.id, ["browser"]))
    htid = opened(H)
    assert code(broker.op, H, htid, "eval", {"script": "1"}).extra["need"]["scopes"] == ["browser.js"]   # 사람 기본에도 없다
    assert env.fake.calls.count(("eval", "f1")) == 0


def test_eval_with_js_grant_runs_and_audits_size_not_text(env):
    give(AL)
    give(AL, ("browser.js",))
    env.fake.evals["document.title"] = "Home"
    tid = opened(url=HOME)
    assert broker.op(AL, tid, "eval", {"script": "document.title"}) == {"result": "Home", "truncated": False}
    text = audit_text()
    assert "browser.eval" in text and "document.title" not in text


def test_eval_wildcard_scope_grant_still_needs_named_js_and_url_not_a_bypass(env):
    give(AL, ("browser",))
    tid = opened()
    assert code(broker.op, AL, tid, "eval", {"script": "location='http://10.0.0.9'"}).status == 403


def test_eval_result_is_capped_and_script_size_limited(env):
    give(AL)
    give(AL, ("browser.js",))
    tid = opened()
    env.fake.evals["big"] = "z" * 70000
    r = broker.op(AL, tid, "eval", {"script": "big"})
    assert r["truncated"] and len(r["result"]) == 64 * 1024
    assert code(broker.op, AL, tid, "eval", {"script": "x" * 20001}).status == 413


def test_eval_that_navigates_is_rechecked_after_the_action(env):
    give(AL)
    give(AL, ("browser.js",))
    env.fake.intercepts = False
    tid = opened(url=HOME)
    env.fake.web["http://localhost:3000/"]["elements"] = []
    t = env.fake.tabs[tabs.get(tid)["ref"]]
    orig = env.fake.eval

    def evil(ref, script):
        t["url"] = "http://10.0.0.9/inner"
        return orig(ref, script)
    env.fake.eval = evil
    e = code(broker.op, AL, tid, "eval", {"script": "location='http://10.0.0.9/inner'"})
    assert e.code == "url_blocked" and t["url"] == "about:blank"


# --- 민감 정보: 쿠키·storage·network (R9.5) -----------------------------------------------------------------

def test_sensitive_ops_blocked_without_grant(env):
    give(AL)
    tid = opened(url=HOME)
    for name, op in (("cookies", "cookies"), ("storage", "storage"), ("network", "network")):
        e = code(broker.op, AL, tid, name)
        assert e.status == 403 and e.extra["need"] == {"resource": f"browser:local/tab/{tid}", "scopes": ["browser.sensitive"], "ops": [op]}
    assert code(broker.op, AL, tid, "cookies", {"values": True}).status == 403


def test_sensitive_grant_is_per_op(env):
    give(AL)
    give(AL, ("browser.sensitive",), sconds={"browser.sensitive": {"ops": ["cookies"]}})
    tid = opened(url=HOME)
    assert broker.op(AL, tid, "cookies")["cookies"]
    assert code(broker.op, AL, tid, "storage").status == 403
    assert code(broker.op, AL, tid, "network").status == 403


def test_cookie_and_storage_values_need_values_condition(env):
    give(AL)
    give(AL, ("browser.sensitive",), sconds={"browser.sensitive": {"ops": ["cookies", "storage", "network"]}})
    tid = opened(url=HOME)
    c = broker.op(AL, tid, "cookies")
    assert c["cookies"][0]["name"] == "sid" and c["cookies"][0]["valueRedacted"] and "SECRETCOOKIE" not in json.dumps(c)
    s = broker.op(AL, tid, "storage")
    assert "token" in s["storage"]["local"] and "SECRETTOKEN" not in json.dumps(s)
    for name in ("cookies", "storage"):
        e = code(broker.op, AL, tid, name, {"values": True})                     # 값을 청했지만 grant 에 values 조건이 없다
        assert e.status == 403 and e.extra["why"] == "cond_values"
    assert "SECRETCOOKIE" not in audit_text()


def test_values_condition_returns_values_and_still_never_audits_them(env):
    give(AL)
    give(AL, ("browser.sensitive",), sconds={"browser.sensitive": {"ops": ["cookies", "storage"]}}, conds={"values": True})
    tid = opened(url=HOME)
    assert broker.op(AL, tid, "cookies", {"values": True})["cookies"][0]["value"] == "SECRETCOOKIE"
    assert broker.op(AL, tid, "storage", {"values": True})["storage"]["local"]["token"] == "SECRETTOKEN"
    text = audit_text()
    assert "SECRETCOOKIE" not in text and "SECRETTOKEN" not in text
    assert "browser.cookies" in text


def test_network_redacts_even_with_values_grant(env):
    give(AL)
    give(AL, ("browser.sensitive",), sconds={"browser.sensitive": {"ops": ["network"]}}, conds={"values": True})
    tid = opened(url=HOME)
    r = broker.op(AL, tid, "network")["requests"][0]
    assert r["requestHeaders"]["Cookie"] == "[redacted]" and r["requestHeaders"]["Accept"] == "*/*"
    assert r["responseHeaders"]["Set-Cookie"] == "[redacted]" and r["postData"] == "[redacted]"
    assert "SECRETCOOKIE" not in json.dumps(r)


def test_sensitive_audit_failure_is_fail_closed(env, monkeypatch):
    give(AL)
    give(AL, ("browser.sensitive",), sconds={"browser.sensitive": {"ops": ["cookies"]}})
    tid = opened(url=HOME)
    monkeypatch.setattr(audit, "append", lambda rec, **k: (_ for _ in ()).throw(OSError("disk full")))
    e = code(broker.op, AL, tid, "cookies")
    assert (e.code, e.status) == ("audit_unavailable", 503)
    assert ("cookies", "f1") not in env.fake.calls


# --- attach · profile=user: 구현하지 않는다 ------------------------------------------------------------------

def test_attach_needs_sensitive_grant_and_then_is_unsupported(env):
    give(AL)
    e = code(broker.open_tab, AL, {"attach": "target-1"})
    assert e.status == 403 and e.extra["need"]["scopes"] == ["browser.sensitive"] and e.extra["need"]["ops"] == ["attach"]
    give(AL, ("browser.sensitive",), sconds={"browser.sensitive": {"ops": ["attach"]}})
    e = code(broker.open_tab, AL, {"attach": "target-1"})
    assert (e.code, e.status) == ("unsupported_profile", 501)
    assert tabs.list_all() == []


def test_profile_user_is_rejected(env):
    give(AL)
    e = code(broker.open_tab, AL, {"profile": "user"})
    assert (e.code, e.status) == ("unsupported_profile", 501)


def test_adapter_declaring_user_profile_is_unavailable(env):
    class UserFake(FakeAdapter):
        profile = "user"
    A.register("userfake", lambda: UserFake(web(), name="userfake"))
    try:
        d = A.describe("userfake")
        assert d["available"] is False and d["reason"] == "profile_user_unsupported"
        e = code(broker.set_adapter, H, "userfake", {"enabled": True, "consent": True})
        assert (e.code, e.status) == ("unsupported_profile", 501)
    finally:
        A.unregister("userfake")


# --- URL 정책 (R9.3) ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("url,reason", [
    ("file:///etc/passwd", "scheme_file"), ("chrome://settings", "scheme_chrome"), ("javascript:alert(1)", "scheme_javascript"),
    ("data:text/html,x", "scheme_data"), ("blob:http://localhost:3000/u", "scheme_blob"), ("view-source:http://localhost:3000", "scheme_view-source"),
    ("http://169.254.169.254/latest", "not_allowlisted"), ("http://10.0.0.7/", "not_allowlisted"), ("http://example.com/", "not_allowlisted"),
    ("http://localhost:3001/", "port_not_exposed"),
])
def test_navigate_url_table(env, url, reason):
    give(AL)
    tid = opened()
    e = code(broker.op, AL, tid, "navigate", {"url": url})
    assert (e.code, e.status, e.extra["urlReason"]) == ("url_blocked", 403, reason)
    assert ("navigate", "f1") not in env.fake.calls                              # 어댑터에는 닿지 않았다


def test_open_with_blocked_url_leaves_no_tab(env):
    give(AL)
    assert code(broker.open_tab, AL, {"adapter": "fake", "url": "file:///etc/passwd"}).code == "url_blocked"
    assert tabs.list_all() == [] and env.fake.tabs == {}


def test_allowlist_from_grant_opens_listed_hosts_only(env):
    give(AL, conds={"url_allowlist": ["example.com", "10.0.0.7"]})
    tid = opened()
    assert broker.op(AL, tid, "navigate", {"url": "http://example.com/"})["title"] == "Ex"
    assert broker.op(AL, tid, "navigate", {"url": "http://10.0.0.7/"})["title"] == "priv"
    assert code(broker.op, AL, tid, "navigate", {"url": "http://10.0.0.8/"}).code == "url_blocked"
    assert code(broker.op, AL, tid, "navigate", {"url": "http://169.254.169.254/latest"}).code == "url_blocked"


def test_allowlist_of_someone_elses_grant_does_not_apply(env):
    give(AL)
    give(BO, conds={"url_allowlist": ["example.com"]})
    tid = opened(AL)
    assert code(broker.op, AL, tid, "navigate", {"url": "http://example.com/"}).code == "url_blocked"


def test_redirect_chain_blocked_at_the_bad_hop_and_tab_goes_blank(env):
    give(AL)
    tid = opened()
    assert broker.op(AL, tid, "navigate", {"url": HOME + "okredir"})["title"] == "Next"
    e = code(broker.op, AL, tid, "navigate", {"url": HOME + "redir"})
    assert e.code == "url_blocked" and e.extra["urlReason"] == "not_allowlisted"
    assert env.fake.tabs["f1"]["url"] != "http://169.254.169.254/latest"
    assert "SECRETMETA" not in json.dumps(broker.op(AL, tid, "snapshot"))


def test_subframe_loaded_by_page_is_blocked(env):
    give(AL)
    tid = opened()
    e = code(broker.op, AL, tid, "navigate", {"url": HOME + "framed"})
    assert e.code == "url_blocked"
    assert env.fake.tabs["f1"]["frames"] == []


def test_subresource_to_metadata_is_blocked_by_guard_but_page_loads(env):
    give(AL)
    tid = opened()
    assert broker.op(AL, tid, "navigate", {"url": HOME + "sub"})["title"] == "S"
    assert env.fake.tabs["f1"]["sub_blocked"][0].startswith("subresource_") and env.fake.tabs["f1"]["blocked"] == []


def test_click_induced_navigation_is_blocked_in_network_layer(env):
    give(AL)
    tid = opened(url=HOME)
    broker.op(AL, tid, "snapshot")
    e = code(broker.op, AL, tid, "click", {"ref": "e3"})                          # Evil → 169.254.169.254
    assert e.code == "url_blocked" and e.extra["urlReason"] == "not_allowlisted"
    assert env.fake.tabs["f1"]["url"] == HOME                                      # 가로채기가 막아서 이동 자체가 일어나지 않았다


def test_post_action_recheck_without_interception_subframe(env):
    give(AL)
    env.fake.intercepts = False                                                    # 가로채기가 없는 어댑터 — broker 의 행동 뒤 검사가 전부다
    tid = opened(url=HOME)
    broker.op(AL, tid, "snapshot")
    e = code(broker.op, AL, tid, "click", {"ref": "e4"})                          # 서브프레임을 붙인다
    assert e.code == "url_blocked" and "정책" in e.reason
    t = env.fake.tabs["f1"]
    assert t["url"] == "about:blank" and t["frames"] == []
    assert "browser.post_check" in audit_text()


def test_post_action_recheck_catches_delayed_navigation(env):
    give(AL)
    env.fake.intercepts = False
    tid = opened(url=HOME)
    broker.op(AL, tid, "snapshot")
    e = code(broker.op, AL, tid, "click", {"ref": "e5"})                          # 지연 이동(settle 에서 일어남)
    assert e.code == "url_blocked"
    assert env.fake.tabs["f1"]["url"] == "about:blank"


def test_post_action_recheck_covers_navigate_without_interception(env):
    give(AL)
    env.fake.intercepts = False
    tid = opened()
    e = code(broker.op, AL, tid, "navigate", {"url": HOME + "framed"})
    assert e.code == "url_blocked"


def test_click_leading_to_public_host_blocked_without_allowlist(env):
    give(AL)
    env.fake.intercepts = False
    tid = opened(url=HOME)
    broker.op(AL, tid, "snapshot")
    assert code(broker.op, AL, tid, "click", {"ref": "e6"}).code == "url_blocked"  # Ext → example.com


# --- 한도 --------------------------------------------------------------------------------------------------

def test_navigate_limit_30_per_minute_per_tab(env):
    give(AL)
    tid = opened()
    for _ in range(30):
        broker.op(AL, tid, "navigate", {"url": HOME})
    e = code(broker.op, AL, tid, "navigate", {"url": HOME})
    assert (e.code, e.status, e.extra["limit"]) == ("rate_limited", 429, "navigate")
    other = opened()
    assert broker.op(AL, other, "navigate", {"url": HOME})                        # 다른 탭은 따로 센다


def test_js_limit_10_per_minute(env):
    give(AL)
    give(AL, ("browser.js",))
    tid = opened()
    for _ in range(10):
        broker.op(AL, tid, "eval", {"script": "1"})
    e = code(broker.op, AL, tid, "eval", {"script": "1"})
    assert e.code == "rate_limited" and e.extra["limit"] == "js"
    assert broker.op(AL, tid, "snapshot")                                           # 다른 종류는 아직 된다


def test_general_limit_60_per_minute_per_tab(env):
    give(AL)
    tid = opened()
    for _ in range(60):
        broker.op(AL, tid, "snapshot")
    assert code(broker.op, AL, tid, "snapshot").extra["limit"] == "general"


def test_denied_calls_do_not_consume_the_limit(env):
    give(AL, sconds={"browser": {"ops": ["open", "read"]}})
    tid = opened()
    for _ in range(80):
        code(broker.op, AL, tid, "navigate", {"url": HOME})
    assert broker.op(AL, tid, "snapshot")


# --- F7: 어댑터 다운 → orphaned ----------------------------------------------------------------------------

def test_adapter_down_marks_tabs_orphaned_keeps_owner_and_fails_closed(env):
    give(AL)
    t1, t2 = opened(url=HOME), opened()
    env.fake.set_down()
    e = code(broker.op, AL, t1, "snapshot")
    assert (e.code, e.status) == ("adapter_down", 503)
    for t in (t1, t2):
        rec = tabs.get(t)
        assert rec["state"] == "orphaned" and rec["owner"] == AL.id            # 소유는 유지
    e = code(broker.op, AL, t2, "snapshot")
    assert (e.code, e.status) == ("tab_orphaned", 503)
    assert code(broker.op, BO, t2, "snapshot").status == 403                  # 남이 이어받지도 못한다
    assert [t["state"] for t in broker.list_tabs(AL)["tabs"]] == ["orphaned", "orphaned"]
    env.fake.set_down(False)
    assert code(broker.op, AL, t2, "snapshot").code == "tab_orphaned"        # 어댑터가 돌아와도 자동으로 되살리지 않는다
    assert broker.close_tab(AL, t2) == {"closed": t2}                         # 닫기는 된다
    assert tabs.get(t2) is None


def test_list_reconciles_when_adapter_stops_answering_or_forgets_a_tab(env):
    give(AL)
    t1, t2 = opened(), opened()
    env.fake.drop(tabs.get(t1)["ref"])
    rows = {t["id"]: t["state"] for t in broker.list_tabs(AL)["tabs"]}
    assert rows == {t1: "orphaned", t2: "open"}
    env.fake.set_down()
    assert {t["state"] for t in broker.list_tabs(AL)["tabs"]} == {"orphaned"}


def test_disabled_adapter_refuses_ops_and_orphans(env):
    give(AL)
    tid = opened()
    broker.set_adapter(H, "fake", {"enabled": False})
    assert tabs.get(tid)["state"] == "orphaned"
    assert code(broker.op, AL, tid, "snapshot").code == "tab_orphaned"
    assert code(broker.open_tab, AL, {"adapter": "fake"}).code == "adapter_disabled"


# --- N-28: 호스트 임대 + 세대 -------------------------------------------------------------------------------

def test_restart_bumps_generation_and_orphans_old_tabs(env):
    give(AL)
    tid = opened()
    g1 = tabs.current_lease()["gen"]
    tabs.drop_lease()                                                                # 서버 재시작
    broker.startup()
    assert tabs.current_lease()["gen"] == g1 + 1
    rec = tabs.get(tid)
    assert rec["state"] == "orphaned" and rec["orphanReason"] == "host_restart" and rec["owner"] == AL.id
    assert code(broker.op, AL, tid, "snapshot").code == "tab_orphaned"


def test_stale_generation_or_lost_lease_is_rejected(env):
    give(AL)
    tid = opened()
    tabs.update(tid, hostGen=tabs.current_lease()["gen"] - 1)                      # 세대가 다른 탭
    e = code(broker.op, AL, tid, "snapshot")
    assert e.code == "tab_orphaned" and "stale_generation" in e.reason
    tid2 = opened()
    with tabs.txn() as d:                                                            # 다른 프로세스가 임대를 가져갔다
        d["host"]["token"] = "someone-else"
    assert "lease_lost" in code(broker.op, AL, tid2, "snapshot").reason
    tabs.drop_lease()
    assert tabs.lease_problem(tabs.get(tid2)) == "no_lease"


def test_unreadable_tabs_file_fails_closed_and_is_not_overwritten(env):
    give(AL)
    tid = opened()
    tabs.path().write_text("{not json", encoding="utf-8")
    e = code(broker.op, AL, tid, "snapshot")
    assert e.status == 503
    assert tabs.path().read_text(encoding="utf-8") == "{not json"
    assert code(broker.open_tab, AL, {"adapter": "fake"}).status == 503


# --- N-15: 회수 → 소유권만 해제, 닫지 않는다 ------------------------------------------------------------------

def test_saga_releases_ownership_without_closing(env):
    give(AL)
    give(BO)
    t1, t2 = opened(AL), opened(BO)
    saga_t = {"key": "$1:101"}
    broker.saga_release(saga_t)
    r1, r2 = tabs.get(t1), tabs.get(t2)
    assert r1["owner"] is None and r1["state"] == "released" and r1["released"]["reason"] == "revoked"
    assert r2["owner"] == BO.id and r2["state"] == "open"
    assert env.fake.closed == [] and tabs.get(t1)                                  # 탭은 닫히지 않았다
    assert code(broker.op, AL, t1, "snapshot").status in (403, 409)               # 풀린 탭은 아무도 조작 못 한다(옛 소유자도)
    broker.saga_release(saga_t)                                                      # 멱등
    assert tabs.get(t1)["owner"] is None


def test_saga_by_grant_pair_subject_and_all(env, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "browser,pair")
    seed(grant(AL.id, ["browser"], gid="g-a", pair_id="pr-p1"), grant(BO.id, ["browser"], gid="g-b"))
    t1, t2 = opened(AL), opened(BO)
    broker.saga_release({"grant_ids": ["g-b"]})
    assert tabs.get(t2)["owner"] is None and tabs.get(t1)["owner"] == AL.id
    broker.saga_release({"pair": "pr-p1"})
    assert tabs.get(t1)["owner"] is None
    seed(grant(AL.id, ["browser"], gid="g-a2"))
    t3 = opened(AL)
    seed(grant(H.id, ["browser"]))
    h = opened(H)
    broker.saga_release({"all": True})
    assert tabs.get(t3)["owner"] is None and tabs.get(h)["owner"] == H.id          # 비상 정지는 사람 탭을 건드리지 않는다


def test_handoff_gives_tab_to_human_and_is_not_touched_by_revocation(env):
    give(AL)
    tid = opened(url=HOME)
    out = broker.handoff(AL, tid)
    assert out["humanTaken"] is True and out["owner"] is None and out["state"] == "released"
    assert code(broker.op, AL, tid, "snapshot").status in (403, 409)
    broker.saga_release({"all": True})
    assert tabs.get(tid)["humanTaken"] is True and tabs.get(tid)["released"]["reason"] == "handoff"
    assert env.fake.closed == []
    assert broker.close_tab(H, tid) == {"closed": tid}


def test_saga_step_registration_is_idempotent(env):
    broker.register_saga_step()
    broker.register_saga_step()
    names = saga.step_names()
    assert names.count("browser_tabs") == 1 and names.index("browser_tabs") < names.index("verify")
    broker.reset_for_tests()
    assert "browser_tabs" not in saga.step_names()


def test_idle_tabs_are_reaped_but_not_human_taken(env, monkeypatch):
    give(AL)
    a, b = opened(), opened()
    broker.handoff(AL, b)
    tabs.update(a, lastUsed=1.0)
    tabs.update(b, lastUsed=1.0)
    broker._reap()
    assert tabs.get(a) is None and tabs.get(b) is not None


# --- 어댑터 켜기 · 설정 (N-26) -------------------------------------------------------------------------------

def test_only_human_or_cli_can_enable(env):
    for p in (AL, P.peer("px")):
        e = code(broker.set_adapter, p, "fake", {"enabled": True, "consent": True})
        assert e.status == 403
    assert broker.set_adapter(H, "fake", {"enabled": True})["adapters"]
    assert broker.set_adapter(CLI, "fake", {"enabled": True})


def test_first_enable_needs_explicit_consent(env):
    os.remove(config.path())
    e = code(broker.set_adapter, H, "fake", {"enabled": True})
    assert (e.code, e.status) == ("consent_required", 409) and e.extra["consentText"]
    assert code(broker.set_adapter, H, "fake", {"enabled": True, "consent": "yes"}).code == "consent_required"      # 불리언 true 만
    assert not config.is_enabled("fake")
    out = broker.set_adapter(H, "fake", {"enabled": True, "consent": True})
    assert next(a for a in out["adapters"] if a["name"] == "fake")["enabled"] and out["consented"]
    broker.set_adapter(H, "fake", {"enabled": False})
    assert broker.set_adapter(H, "fake", {"enabled": True})                          # 동의는 한 번


@pytest.mark.parametrize("content", ["", "{", "[]", "null", '{"adapters": []}', '{"adapters": {"fake": {"enabled": "true"}}}',
                                     '{"adapters": {"fake": {"enabled": 1}}}', '{"adapters": {"fake": "on"}}', '{"adapters": {"fake": {"enabled": "yes"}}}'])
def test_invalid_config_reads_as_off(env, content):
    config.path().write_text(content, encoding="utf-8")
    assert config.is_enabled("fake") is False
    give(AL)
    e = code(broker.open_tab, AL, {"adapter": "fake"})
    assert e.code == "adapter_disabled"
    assert code(broker.open_tab, AL, {}).code == "no_adapter_enabled"


def test_unwritable_broken_config_is_not_overwritten(env):
    config.path().write_text("{broken", encoding="utf-8")
    e = code(broker.set_adapter, H, "fake", {"enabled": True, "consent": True})
    assert e.status == 503 and config.path().read_text(encoding="utf-8") == "{broken"


def test_unknown_adapter_and_unavailable_adapter(env):
    assert code(broker.set_adapter, H, "nope", {"enabled": True}).status == 404
    for n in ("chrome-devtools-mcp", "gstack", "browserskill", "agent-browser"):
        e = code(broker.set_adapter, H, n, {"enabled": True, "consent": True})
        assert (e.code, e.status) == ("adapter_unavailable", 409)
    assert code(broker.set_adapter, H, "fake", {"enabled": "yes"}).status == 400


def test_agents_see_adapters_only_with_a_browser_grant(env):
    assert code(broker.list_adapters, AL).status == 403
    give(AL, sconds={"browser": {"ops": ["read"]}})
    names = [a["name"] for a in broker.list_adapters(AL)["adapters"]]
    assert "fake" in names and "playwright" in names and "gstack" in names


# --- 파일 권한(umask 와 무관) ----------------------------------------------------------------------------------

@pytest.mark.parametrize("mask", [0o022, 0o077, 0o000])
def test_state_files_are_private_regardless_of_umask(env, mask):
    old = os.umask(mask)
    try:
        give(AL)
        opened()
        broker.set_adapter(H, "fake", {"enabled": True})
    finally:
        os.umask(old)
    for p in (tabs.path(), config.path()):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600, p
    assert stat.S_IMODE(os.stat(tabs.path().parent).st_mode) == 0o700


# --- 엔진 장애 = 거부 ---------------------------------------------------------------------------------------------

def test_engine_failure_is_fail_closed(env, monkeypatch):
    give(AL)
    tid = opened()
    from authz import engine
    monkeypatch.setattr(engine, "decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    for fn in (lambda: broker.op(AL, tid, "snapshot"), lambda: broker.open_tab(AL, {"adapter": "fake"}), lambda: broker.close_tab(AL, tid)):
        e = code(fn)
        assert (e.code, e.status) == ("authz_unavailable", 503)


def test_corrupt_grant_store_is_fail_closed_for_agents(env):
    give(AL)
    tid = opened()
    store.doc_path("grants").write_text("{garbage", encoding="utf-8")
    e = code(broker.op, AL, tid, "snapshot")
    assert e.status in (403, 503)


# --- relay ---------------------------------------------------------------------------------------------------------

def test_relay_cannot_run_js_or_read_values(env):
    peer = P.peer("pr1")
    seed(grant(peer.id, ["browser", "browser.js", "browser.sensitive"], sconds={"browser.sensitive": {"ops": ["cookies", "storage", "network"]}}, conds={"values": True}))
    tid = broker.open_tab(peer, {"adapter": "fake", "url": HOME}, via_relay=True)["id"]
    assert broker.op(peer, tid, "snapshot", via_relay=True)["title"] == "Home"
    for name in ("eval", "cookies", "storage", "network"):
        assert code(broker.op, peer, tid, name, {"script": "1", "values": True}, via_relay=True).code == "relay_forbidden"
    assert env.fake.calls.count(("eval", "f1")) == 0


# --- 선택 모듈 격리 --------------------------------------------------------------------------------------------------

def test_importing_the_broker_does_not_import_optional_browser_libs():
    import subprocess
    import sys
    code_ = ("import sys; sys.path.insert(0, 'server'); import browser.broker, browser.adapters as A; "
             "[A.describe(n) for n in A.names()]; "
             "assert 'playwright' not in sys.modules, 'playwright imported eagerly'; print('ok')")
    r = subprocess.run([sys.executable, "-c", code_], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    assert r.stdout.strip() == "ok", r.stderr[-400:]


def test_real_saga_run_releases_browser_tabs_and_reports_done(env, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "browser,lease")
    seed(grant(AL.id, ["browser"], gid="g-saga"))
    broker.register_saga_step()
    tid = opened(AL, url=HOME)
    rec = saga.start("grant", {"grant_ids": ["g-saga"], "key": "$1:101"}, run_now=True)
    assert rec["state"] == saga.DONE, rec
    assert "browser_tabs" in rec["steps"]
    r = tabs.get(tid)
    assert r["owner"] is None and r["state"] == "released" and env.fake.closed == []


def test_saga_without_the_browser_flag_never_registers_the_step(env, monkeypatch):
    broker.reset_for_tests()
    assert "browser_tabs" not in saga.step_names()
