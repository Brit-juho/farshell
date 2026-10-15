"""폴더 lease 본체 — 정규화·겹침·충돌·수명·권한·생존·사람 우선·속도·check·HTTP 라우트.

`VT_AUTHZ_ENFORCE` 는 비워 둔다(강제는 test_authz_lease_enforce.py). 여기는 권고 모드에서도 성립하는 규칙이다.
"""

from __future__ import annotations

import os
import time

import pytest

from authz import audit, leases, store
from authz import principals as P

A = P.agent("$1:101", name="alpha")
B = P.agent("$2:102", name="bravo")
H = P.human("dev1")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    root = (tmp_path / "roots").resolve()
    (root / "proj" / "src").mkdir(parents=True)
    (root / "proj" / "docs").mkdir()
    (root / "other").mkdir()
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(root))
    alive = {"v": {A.attrs["key"], B.attrs["key"]}}
    monkeypatch.setattr(leases, "alive_keys", lambda fresh=False: alive["v"])
    leases.reset_rate()
    monkeypatch.setattr(leases, "_alive_cache", {"t": 0.0, "v": None})
    grant(A, root)
    grant(B, root)

    class E:
        pass
    e = E()
    e.root, e.alive, e.tmp = root, alive, tmp_path
    return e


def grant(p, root, scopes=("fs.write",), gid=None):
    with store.txn() as d:
        d["grants"]["items"].append({
            "id": gid or f"g-{p.attrs['name']}-{len(d['grants']['items'])}", "subject": p.id, "resource": f"fs:local{root}/*",
            "scopes": list(scopes), "sconds": {"fs.write": {"ops": ["write"]}} if "fs.write" in scopes else {},
            "schema": 2, "state": "active", "approved_by": "human:test", "approved_via": "manual", "expires": None, "conds": {}})


def code(fn, *a, **k):
    with pytest.raises(leases.LeaseError) as e:
        fn(*a, **k)
    return e.value.code, e.value.status, e.value


# --- 정규화 -------------------------------------------------------------------------------------


def test_canonicalize_table(env):
    r = str(env.root)
    link = env.root / "link"
    os.symlink(env.root / "proj", link)
    ok = {
        f"{r}/proj": f"{r}/proj",
        f"{r}/proj/": f"{r}/proj",
        f"{r}/proj/./src": f"{r}/proj/src",
        f"{r}//proj///src": f"{r}/proj/src",
        f"{r}/proj/src/*.py": f"{r}/proj/src/*.py",
        f"{r}/link/src": f"{r}/proj/src",                 # 정적 prefix 는 realpath
        f"{r}/link/*": f"{r}/proj/*",
        f"{r}/nothere/new": f"{r}/nothere/new",           # 아직 없는 경로(새 worktree 등)도 정규화된다
        f"{r}/*": f"{r}/*",
    }
    for raw, want in ok.items():
        assert leases.canonicalize(raw) == want, raw
    bad = {
        "": "bad_glob", "relative/path": "bad_glob", f"{r}/proj/../other": "bad_glob", f"{r}/p\x00x": "bad_glob",
        "x" * 1100: "bad_glob",
        "/etc/passwd": "outside_roots", "/": "outside_roots", "/*": "outside_roots",
        str(env.tmp / "elsewhere"): "outside_roots",
        f"{r}/proj/.git": "outside_roots", f"{r}/proj/.ssh/*": "outside_roots", f"{r}/proj/*/.ssh": "outside_roots",
        f"{r}/proj/.*": "outside_roots", f"{r}/proj/*/.env": "outside_roots", f"{r}/proj/.env": "outside_roots",
    }
    for raw, want in bad.items():
        assert code(leases.canonicalize, raw)[0] == want, raw[:40]


def test_symlink_escaping_the_roots_is_rejected(env):
    os.symlink(env.tmp, env.root / "escape")
    assert code(leases.canonicalize, f"{env.root}/escape/x")[0] == "outside_roots"


# --- 겹침 ---------------------------------------------------------------------------------------

OVERLAP = [
    # (a, b, 겹치는가)
    ("/r/p", "/r/p", True), ("/r/p", "/r/p/src", True), ("/r/p/src", "/r/p", True),            # 디렉터리는 하위를 포함
    ("/r/p", "/r/q", False), ("/r/p", "/r/pp", False), ("/r/p/a", "/r/p/b", False),
    ("/r/p/*", "/r/p/src", True), ("/r/p/src", "/r/p/*", True), ("/r/p/*.py", "/r/p/x.py", True),
    ("/r/p/*.py", "/r/p/x.md", True),                                                           # `*` 은 `/` 를 넘으니 하위 x.md/y.py 가 맞을 수 있다
    ("/r/p/src/*", "/r/p/docs/*", False), ("/r/p/src/*.py", "/r/p/docs/*.md", False),
    ("/r/p/*", "/r/p/*.py", True), ("/r/p/*.py", "/r/p/*.md", True),                            # 모르면 충돌
    ("/r/p/b*", "/r/p/x/y", False), ("/r/p/b*", "/r/p/bc/d", True), ("/r/p/b*", "/r/p/b", True),
    ("/r/p/?", "/r/p/a", True), ("/r/p/[ab]", "/r/p/ab", True),
    ("/r/*/c", "/r/p", True), ("/r/*/c", "/r/p/c/d", True), ("/r/p/*", "/r/q/*", False), ("/r/*", "/r/q/*", True),
]


@pytest.mark.parametrize("a,b,want", OVERLAP)
def test_overlap_table(a, b, want):
    assert leases.overlaps(a, b) is want and leases.overlaps(b, a) is want       # 대칭


def test_covers_includes_ancestors():
    assert leases.covers("/r/p", "/r/p/src/a.py") and leases.covers("/r/p/*.py", "/r/p/src/a.py")
    assert not leases.covers("/r/p/src", "/r/p/docs/a.py") and not leases.covers("/r/p/src", "/r/p")


# --- 충돌 행렬 ----------------------------------------------------------------------------------


def acq(p, path, **k):
    return leases.acquire(p, f"{path}", **k)


def test_conflict_matrix(env):
    r = str(env.root)
    w = acq(A, f"{r}/proj", mode="write")
    assert w["holder"] == "alpha" and w["mode"] == "write" and w["remaining"] > 1700
    c, s, e = code(acq, B, f"{r}/proj/src", mode="write")                     # write × write (하위 경로도)
    assert (c, s) == ("lease_conflict", 409)
    body = e.body()
    assert body["holder"] == "alpha" and body["expires"] == pytest.approx(w["expires"], abs=1)
    assert set(body) <= {"error", "reason", "holder", "expires", "conflicts"}               # 닉네임·만료·충돌 요약뿐 — 주체 id·key 없음
    assert "$1" not in str(body) and "agent:" not in str(body)
    assert code(acq, B, f"{r}/proj/*.py", mode="write")[1] == 409
    acq(B, f"{r}/other", mode="write")                                                       # 안 겹침
    assert code(acq, B, f"{r}/proj", mode="read", exclusive=True)[1] == 409                # exclusive read × write
    acq(B, f"{r}/proj/docs", mode="read")                                                    # 비exclusive read × write 는 공존


def test_read_read_never_conflicts_and_exclusive_blocks_others(env):
    r = str(env.root)
    acq(A, f"{r}/proj", mode="read")
    acq(B, f"{r}/proj", mode="read")
    ex = acq(A, f"{r}/other", mode="write", exclusive=True)
    assert code(acq, B, f"{r}/other", mode="read")[1] == 409                                # exclusive write 는 읽기 lease 도 막는다
    leases.release(A, ex["id"])
    acq(B, f"{r}/other", mode="read")


def test_same_holder_never_conflicts_with_itself(env):
    r = str(env.root)
    acq(A, f"{r}/proj")
    acq(A, f"{r}/proj/src")
    assert len(leases.list_for(A, everyone=False)) == 2


def test_pair_exception_is_a_stub_that_never_applies(env, monkeypatch):
    r = str(env.root)
    assert leases.same_pair(A.id, B.id) is False
    acq(A, f"{r}/proj")
    assert code(acq, B, f"{r}/proj")[1] == 409                                               # non-exclusive 여도 pair 가 아니면 충돌
    monkeypatch.setattr(leases, "same_pair", lambda a, b: True)                              # pair 가 생기면 예외가 켜진다
    acq(B, f"{r}/proj")
    assert code(acq, B, f"{r}/proj/src", exclusive=True)[1] == 409                          # exclusive 는 pair 여도 충돌


def test_lease_limit_per_holder_and_bad_inputs(env):
    r = str(env.root)
    assert code(acq, A, f"{r}/proj", mode="rw")[0] == "bad_mode"
    assert code(acq, A, f"{r}/proj", ttl=0)[0] == "bad_ttl"
    assert code(acq, A, f"{r}/proj", ttl="x")[0] == "bad_ttl"


# --- 수명 ---------------------------------------------------------------------------------------


def test_ttl_default_max_and_renew_cap(env):
    r = str(env.root)
    l = acq(A, f"{r}/proj")
    assert l["ttl"] == leases.DEFAULT_TTL == 1800 and 1790 < l["remaining"] <= 1800
    assert code(acq, B, f"{r}/other", ttl=leases.MAX_TTL + 1)[0] == "ttl_too_long"
    m = acq(B, f"{r}/other", ttl=leases.MAX_TTL)
    assert 8 * 3600 - 5 < m["remaining"] <= 8 * 3600
    # 갱신: 처음 ttl 로 다시 맞춘다. 만든 때부터 8시간을 넘기지 못한다
    items, _ = leases.read_items()
    rec = next(x for x in items if x["id"] == l["id"])
    rec["expires"] = time.time() + 5
    with store.txn() as d:
        next(x for x in d["leases"]["items"] if x["id"] == l["id"]).update(expires=rec["expires"])
    r1 = leases.renew(A, l["id"])
    assert r1["remaining"] > 1700 and r1["capped"] is False
    r2 = leases.renew(A, l["id"], ttl=leases.MAX_TTL)
    assert r2["remaining"] <= 8 * 3600 and r2["capped"] is False
    with store.txn() as d:                                                                   # 만든 지 7시간 → 갱신해도 1시간만 남는다
        x = next(x for x in d["leases"]["items"] if x["id"] == l["id"])
        x["created"] -= 7 * 3600
        x["max_expires"] -= 7 * 3600
        x["expires"] = min(x["expires"], x["max_expires"])
    r3 = leases.renew(A, l["id"], ttl=leases.MAX_TTL)
    assert r3["capped"] is True and 3500 < r3["remaining"] <= 3600
    assert code(leases.renew, B, l["id"])[1] == 404                                           # 남의 lease 는 없는 것처럼
    assert code(leases.renew, A, "ls-nope")[1] == 404


def test_expired_lease_frees_the_path(env):
    r = str(env.root)
    l = acq(A, f"{r}/proj")
    with store.txn() as d:
        next(x for x in d["leases"]["items"] if x["id"] == l["id"])["expires"] = time.time() - 1
    assert leases.list_for(A, everyone=True) == []
    acq(B, f"{r}/proj")                                                                      # 만료된 것은 충돌하지 않는다
    assert [x["action"] for x in audit.read_recent(20, "lease.")][0:2] == ["lease.acquire", "lease.expire"]


def test_holder_session_death_frees_the_path_and_unreadable_tmux_keeps(env):
    r = str(env.root)
    acq(A, f"{r}/proj")
    env.alive["v"] = {B.attrs["key"]}                                                        # A 의 세션이 사라졌다
    assert leases.list_for(B, everyone=True) == []
    acq(B, f"{r}/proj")
    env.alive["v"] = None                                                                    # tmux 를 못 읽으면 산 것으로 본다(grant 와 같은 fail-safe)
    assert len(leases.list_for(B, everyone=True)) == 1
    assert code(acq, A, f"{r}/proj")[1] == 409


def test_grant_revoke_ends_the_lease(env):
    r = str(env.root)
    g = f"g-x"
    grant(P.agent("$9:109", name="nine"), env.root, gid=g)
    nine = P.agent("$9:109", name="nine")
    env.alive["v"] |= {"$9:109"}
    l = acq(nine, f"{r}/other")
    assert leases.list_for(nine, everyone=False)[0]["id"] == l["id"]
    with store.txn() as d:
        next(x for x in d["grants"]["items"] if x["id"] == g)["state"] = "revoked"
    assert leases.list_for(nine, everyone=False) == []                                       # 근거 grant 가 사라지면 즉시 무효
    assert leases.release_by_grant(g) == [l["id"]]                                           # saga 가 부르는 즉시 정리도 같은 결과
    acq(A, f"{r}/other")


def test_admin_revoke_and_release_permissions(env):
    r = str(env.root)
    l = acq(A, f"{r}/proj")
    assert code(leases.release, B, l["id"])[1] == 404                                        # 남의 것은 못 놓는다
    assert code(leases.revoke_admin, H, "ls-nope")[1] == 404
    assert leases.revoke_admin(H, l["id"])["released"] == 1
    acq(B, f"{r}/proj")
    l2 = acq(A, f"{r}/other")
    assert leases.release(H, l2["id"])["released"] == 1                                      # 사람은 아무 lease 나 놓을 수 있다
    acts = [x["action"] for x in audit.read_recent(30, "lease.")]
    assert "lease.revoke" in acts and "lease.release" in acts


# --- 권한 ---------------------------------------------------------------------------------------


def test_acquire_requires_the_underlying_fs_grant(env):
    r = str(env.root)
    stranger = P.agent("$7:107", name="seven")
    env.alive["v"] |= {"$7:107"}
    c, s, e = code(acq, stranger, f"{r}/proj")
    assert (c, s) == ("forbidden", 403) and e.extra["need"]["scopes"] == ["fs.write"]
    assert e.extra["need"]["resource"] == f"fs:local{r}/proj/*"
    assert code(acq, stranger, f"{r}/proj", mode="read")[1] == 403
    grant(stranger, env.root, scopes=("fs.read",))
    acq(stranger, f"{r}/proj", mode="read")                                                  # fs.read 만으로 읽기 lease
    assert code(acq, stranger, f"{r}/proj", mode="write")[1] == 403                         # 쓰기는 fs.write 필요
    outside = P.agent("$8:108", name="eight")
    grant(outside, env.tmp / "x", scopes=("fs.write",))                                      # 다른 경로의 grant 는 소용없다
    env.alive["v"] |= {"$8:108"}
    assert code(acq, outside, f"{r}/proj")[1] == 403


def test_human_needs_no_grant_and_cli_is_denied(env):
    r = str(env.root)
    l = acq(H, f"{r}/proj")
    assert l["holder"] == "human"
    cli = P.cli(os.getuid(), 4242)
    assert code(acq, cli, f"{r}/other")[0] == "forbidden"                                    # pane 밖 cli 는 권한이 없다(승인도 못 한다)


def test_acquire_denied_when_the_grant_store_is_corrupt(env):
    r = str(env.root)
    store.doc_path("grants").write_text("{not json")
    assert code(acq, A, f"{r}/proj")[0] == "authz_unavailable"                               # 엔진을 못 쓰면 닫힌다


def test_rate_limit_is_30_per_minute_per_principal(env):
    r = str(env.root)
    ids = [acq(A, f"{r}/proj/n{i}")["id"] for i in range(30)]
    c, s, _ = code(acq, A, f"{r}/proj/n30")
    assert (c, s) == ("rate_limited", 429)
    assert code(leases.release, A, ids[0])[:2] == ("rate_limited", 429)                        # 잡기·갱신·놓기가 한 창을 나눠 쓴다
    assert acq(B, f"{r}/other")["id"]                                                         # 다른 주체는 영향 없다


def test_audit_never_holds_the_reason_text(env):
    r = str(env.root)
    acq(A, f"{r}/proj", reason="비밀 작업 이유 SECRET-XYZ")
    raw = audit.path().read_text(encoding="utf-8")
    assert "SECRET-XYZ" not in raw and '"lease.acquire"' in raw and '"chars"' in raw


# --- check -------------------------------------------------------------------------------------


def test_check_reports_per_path_conflicts(env):
    r = str(env.root)
    acq(A, f"{r}/proj/src")
    res = leases.check(B, [f"{r}/proj/src/a.py", f"{r}/proj/docs/b.md", f"{r}/proj/src/sub/c.py"])
    assert res["ok"] is False and [c["path"] for c in res["conflicts"]] == [f"{r}/proj/src/a.py", f"{r}/proj/src/sub/c.py"]
    assert res["conflicts"][0]["holder"] == "alpha" and "lease" in res["conflicts"][0]
    assert leases.check(A, [f"{r}/proj/src/a.py"])["ok"] is True                              # 보유자 본인은 막히지 않는다
    # 에이전트는 holder 를 주장해도 소용없다 — 자기 기준
    assert leases.check(B, [f"{r}/proj/src/a.py"], holder=A.id)["ok"] is False
    # cli 는 holder 를 줄 수 있다. 안 주면 모든 write lease 가 막는다
    cli = P.cli(os.getuid(), 1)
    assert leases.check(cli, [f"{r}/proj/src/a.py"], holder=A.id)["ok"] is True
    assert leases.check(cli, [f"{r}/proj/src/a.py"])["ok"] is False
    assert code(leases.check, B, "x")[0] == "bad_paths"
    assert code(leases.check, B, [1])[0] == "bad_paths"
    assert code(leases.check, B, ["a"] * (leases.MAX_CHECK_PATHS + 1))[1] == 413


def test_check_follows_symlinks(env):
    r = str(env.root)
    acq(A, f"{r}/proj/src")
    os.symlink(env.root / "proj" / "src", env.root / "other" / "alias")
    assert leases.check(B, [f"{r}/other/alias/f.py"])["ok"] is False


# --- 사람 우선 ----------------------------------------------------------------------------------


def test_human_is_never_blocked_but_overrides_are_noticed(env, monkeypatch):
    monkeypatch.setenv("VT_AUTHZ_ENFORCE", "lease")
    r = str(env.root)
    l = acq(A, f"{r}/proj")
    blocked, notice = leases.enforce_write_sync(H, [f"{r}/proj/x.py"], op="git.commit")
    assert blocked is None and notice and notice["lease"] == l["id"]
    blocked, notice = leases.enforce_write_sync(B, [f"{r}/proj/x.py"], op="git.commit")
    assert blocked["error"] == "lease_conflict" and notice is None
    blocked, notice = leases.enforce_write_sync(A, [f"{r}/proj/x.py"], op="git.commit")        # 보유자 본인
    assert blocked is None and notice is None
    own = acq(H, f"{r}/other")
    assert leases.enforce_write_sync(H, [f"{r}/other/x"], op="git.stage") == (None, None)       # 자기 lease 안에서는 알림도 없다
    assert own["holder"] == "human"


# --- HTTP 라우트 -------------------------------------------------------------------------------


class _Sock:
    def __init__(self, app, principal):
        self.app, self.p = app, principal

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope["fsh_sock"] = True
            scope["state"] = {**(scope.get("state") or {}), "fsh_principal": self.p}
        await self.app(scope, receive, send)


def client(monkeypatch, principal):
    from starlette.testclient import TestClient

    import main
    return TestClient(_Sock(main.app, principal))


def test_http_roundtrip_conflict_renew_release_list(env, monkeypatch):
    r = str(env.root)
    with client(monkeypatch, A) as ca, client(monkeypatch, B) as cb, client(monkeypatch, P.cli(os.getuid(), 5)) as cc:
        res = ca.post("/api/leases", json={"glob": f"{r}/proj", "ttl": 600, "reason": "t"})
        assert res.status_code == 201, res.text
        lid = res.json()["lease"]["id"]
        x = cb.post("/api/leases", json={"glob": f"{r}/proj/src"})
        assert x.status_code == 409 and x.json()["error"] == "lease_conflict" and x.json()["holder"] == "alpha"
        assert [l["id"] for l in ca.get("/api/leases").json()["leases"]] == [lid]
        assert cb.get("/api/leases").json()["leases"] == []                                 # 에이전트는 자기 것만
        assert [l["id"] for l in cc.get("/api/leases").json()["leases"]] == [lid]           # 터미널(cli)은 전부
        assert cb.post(f"/api/leases/{lid}/renew").status_code == 404
        rr = ca.post(f"/api/leases/{lid}/renew", json={"ttl": 900})
        assert rr.status_code == 200 and rr.json()["lease"]["remaining"] > 800
        chk = cb.post("/api/leases/check", json={"paths": [f"{r}/proj/a.py"]}).json()
        assert chk["ok"] is False and chk["conflicts"][0]["holder"] == "alpha"
        assert cb.delete(f"/api/leases/{lid}").status_code == 404
        assert ca.delete(f"/api/leases/{lid}").json()["released"] == 1
        assert cb.post("/api/leases", json={"glob": f"{r}/proj/src"}).status_code == 201
        assert ca.post("/api/leases", json={"glob": "/etc"}).status_code == 403


def test_http_hook_and_svc_callers_are_refused(env, monkeypatch):
    hook = P.from_socket({"uid": os.getuid(), "pid": 1}, row=type("R", (), {"sid": "$1", "created": 101, "pane_id": "%1", "name": "alpha"})(),
                         svc_claim="hook")
    with client(monkeypatch, hook) as c:
        assert c.get("/api/leases").status_code == 403
    with client(monkeypatch, P.svc("voice", os.getuid(), 1)) as c:
        assert c.get("/api/leases").status_code == 403
