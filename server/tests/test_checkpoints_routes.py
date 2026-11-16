"""`/api/checkpoints*` — 사람 세션만(기계 토큰·소켓 주체 거부), 중계 밖, 감사, 검증, 가림, 끔 스위치.
그리고 `POST /api/agent/event`가 캡처를 기다리지 않고 응답하는지(훅 계약)."""
from __future__ import annotations

import json
import os
import subprocess
import time

import pytest
from starlette.testclient import TestClient

import auth as _auth
import checkpoint_jobs
import checkpoint_store
import checkpoints
import main
import relay_policy
from authz import audit as authz_audit
from authz import principals as P
from authz import route_table
from shared_testkit import _Sock

PW = "s3cret-pw"
ROUTES = [("get", "/api/checkpoints?repo=%s", None), ("get", "/api/checkpoints/diff?repo=%s&id=v1/aaaaaaaaaaaa/bbbbbbbbbbbbbbbb/000001", None),
          ("put", "/api/checkpoints/settings", {"repo": "%s", "enabled": True})]


def g(repo, *args):
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.invalid", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.invalid"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=e).stdout


def _auth_env(tmp_path, monkeypatch):
    state_dir = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state_dir)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state_dir / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state_dir / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state_dir / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "test-session-signing-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password(PW))
    monkeypatch.setattr(_auth, "VT_AUTH_TOKEN", "machine-tok")
    monkeypatch.setenv("VT_STATE_DIR", str(state_dir))
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    monkeypatch.setenv("VT_BROWSE_ROOTS", str(tmp_path))
    checkpoint_store._cache = (None, frozenset())
    checkpoint_jobs._reset_for_tests()
    return state_dir


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    g(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a1\n")
    g(r, "add", "-A")
    g(r, "commit", "-q", "-m", "init")
    return r


@pytest.fixture
def client(tmp_path, monkeypatch):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        assert c.post("/api/auth", json={"token": PW}).status_code == 200
        yield c


@pytest.fixture
def anon(tmp_path, monkeypatch):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        yield c


def audit_rows(tmp_path):
    p = tmp_path / "vt" / "audit" / "authz.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def call(c, spec, repo, **kw):
    method, path, body = spec
    return getattr(c, method)(path.replace("%s", str(repo)), **({"json": {k: (str(repo) if v == "%s" else v) for k, v in body.items()}} if body else {}), **kw)


def make_turn(repo, sess="0123456789abcdef"):
    base = checkpoints.capture(repo, kind="baseline", sess_key=sess, agent="claude", turn=0)
    (repo / "a.txt").write_text("a1\nnew line\n")
    (repo / "added.txt").write_text("x\ny\n")
    (repo / ".env").write_text("SECRET=1\n")
    end = checkpoints.capture(repo, kind="turn-end", sess_key=sess, agent="claude", turn=1, pre_trusted=False)
    assert base["status"] == end["status"] == "ok"
    return base, end


# --- 인가 -----------------------------------------------------------------------


@pytest.mark.parametrize("spec", ROUTES)
def test_login_is_required(anon, repo, spec):
    assert call(anon, spec, repo).status_code in (401, 403)


@pytest.mark.parametrize("spec", ROUTES)
def test_machine_token_agent_and_peer_are_refused(tmp_path, monkeypatch, repo, spec):
    _auth_env(tmp_path, monkeypatch)
    with TestClient(main.app) as c:
        r = call(c, spec, repo, headers={"Authorization": "Bearer machine-tok"})
        assert r.status_code == 403 and r.json()["error"] == "human_session_required"
    r = call(TestClient(_Sock(main.app, P.agent("$1:101", name="alpha"))), spec, repo)
    assert r.status_code in (401, 403)
    assert not checkpoint_store.any_enabled() and audit_rows(tmp_path) == []


def test_cookie_plus_machine_token_is_not_a_person(client, repo):
    r = client.put("/api/checkpoints/settings", json={"repo": str(repo), "enabled": True}, headers={"Authorization": "Bearer machine-tok"})
    assert r.status_code == 403 and not checkpoint_store.any_enabled()


def test_routes_are_declared_human_and_out_of_the_relay_and_socket_lists():
    for m, path in (("GET", "/api/checkpoints"), ("GET", "/api/checkpoints/diff"), ("PUT", "/api/checkpoints/settings")):
        rule = route_table.TABLE[(m, path)]
        assert rule.cls == route_table.HUMAN and rule.elevated is False
        assert not relay_policy.http_allowed(m, path), "다른 Mac의 체크포인트를 건드리면 안 된다"
        assert route_table.socket_entry(m, path) is None


# --- 설정 -----------------------------------------------------------------------


def test_enable_and_disable_are_audited_without_paths(client, repo, tmp_path):
    r = client.put("/api/checkpoints/settings", json={"repo": str(repo), "enabled": True})
    assert r.status_code == 200 and r.json() == {"ok": True, "enabled": True}
    key = checkpoints.repo_key(repo)["key"]
    assert checkpoint_store.is_enabled(key)
    assert client.get("/api/checkpoints", params={"repo": str(repo)}).json()["enabled"] is True
    rows = [x for x in audit_rows(tmp_path) if x.get("action") == "ckpt.settings"]
    assert [x["result"] for x in rows] == ["started", "ok"]
    assert all(x["detail"]["enabled"] is True and len(x["detail"]["repo"]) == 8 and x["scopes"] == ["ckpt.settings"] for x in rows)
    assert str(repo) not in json.dumps(rows) and key not in json.dumps(rows)
    assert client.put("/api/checkpoints/settings", json={"repo": str(repo), "enabled": False}).status_code == 200
    assert not checkpoint_store.is_enabled(key)


def test_a_linked_worktree_enables_the_whole_repository(client, repo, tmp_path):
    wt = tmp_path / "wt"
    g(repo, "worktree", "add", "-q", "-b", "feat", str(wt))
    assert client.put("/api/checkpoints/settings", json={"repo": str(wt), "enabled": True}).status_code == 200
    assert client.get("/api/checkpoints", params={"repo": str(repo)}).json()["enabled"] is True


@pytest.mark.parametrize("body", [None, [], {"repo": "x"}, {"enabled": True}, {"repo": 5, "enabled": True},
                                  {"repo": "x", "enabled": "yes"}, {"repo": "x", "enabled": 1}, {"repo": "x", "enabled": True, "extra": 1}])
def test_bad_bodies_are_400(client, body, tmp_path):
    r = client.put("/api/checkpoints/settings", content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"] == "bad_request"
    assert audit_rows(tmp_path) == []


def test_not_a_repo_outside_roots_and_missing(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert client.put("/api/checkpoints/settings", json={"repo": str(plain), "enabled": True}).json()["error"] == "not_a_repo"
    assert client.put("/api/checkpoints/settings", json={"repo": "/etc", "enabled": True}).status_code == 403
    assert client.put("/api/checkpoints/settings", json={"repo": str(tmp_path / "nope"), "enabled": True}).status_code in (403, 404)
    assert not checkpoint_store.any_enabled()


def test_audit_failure_refuses_and_changes_nothing(client, repo, monkeypatch):
    def boom(rec):
        raise OSError("disk full")
    monkeypatch.setattr(authz_audit, "append", boom)
    r = client.put("/api/checkpoints/settings", json={"repo": str(repo), "enabled": True})
    assert r.status_code == 503 and r.json()["error"] == "audit_unavailable"
    assert not checkpoint_store.any_enabled()


# --- 읽기 -----------------------------------------------------------------------


def test_list_shape_and_no_contents(client, repo):
    base, end = make_turn(repo)
    r = client.get("/api/checkpoints", params={"repo": str(repo)})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    b = r.json()
    assert b["repo"] is True and b["enabled"] is False and b["limits"]["fileMB"] == 2
    assert len(b["sessions"]) == 1
    s = b["sessions"][0]
    assert s["id"] == "0123456789abcdef" and s["agent"] == "claude"
    rows = {c["kind"]: c for c in s["checkpoints"]}
    assert rows["baseline"]["turn"] == 0 and rows["turn-end"]["turn"] == 1 and rows["turn-end"]["preTrusted"] is False
    summ = rows["turn-end"]["summary"]
    assert summ["files"] == 2 and summ["added"] == 3 and summ["deleted"] == 0 and summ["from"] == base["id"]      # .env는 캡처되지 않았다
    text = r.text
    assert "SECRET" not in text and "new line" not in text and str(repo) not in text                              # 내용·경로 없음


def test_list_for_a_plain_directory_and_unknown_repo(client, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert client.get("/api/checkpoints", params={"repo": str(plain)}).json() == {"repo": False}
    assert client.get("/api/checkpoints", params={"repo": "/etc"}).status_code == 403


def test_files_list_and_redacted_file_diff(client, repo):
    base, end = make_turn(repo)
    r = client.get("/api/checkpoints/diff", params={"repo": str(repo), "id": end["id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["to"] == end["id"] and body["from"] == base["id"]
    assert {f["path"]: (f["added"], f["deleted"]) for f in body["files"]} == {"a.txt": (1, 0), "added.txt": (2, 0)}
    d = client.get("/api/checkpoints/diff", params={"repo": str(repo), "id": end["id"], "file": "a.txt"}).json()
    assert "+new line" in d["diff"] and d["truncated"] is False
    d_all = client.get("/api/checkpoints/diff", params={"repo": str(repo), "id": end["id"], "file": "added.txt", "from": base["id"]}).json()
    assert "+x" in d_all["diff"]


def test_diff_hides_a_denied_name_even_if_it_got_into_a_checkpoint(client, repo, monkeypatch):
    base = checkpoints.capture(repo, kind="baseline", sess_key="0123456789abcdef", agent="claude", turn=0)
    (repo / "k.pem").write_text("KEY-MATERIAL\n")
    with monkeypatch.context() as m:                       # 캡처가 .pem을 담게 일부러 푼다(이 블록 안에서만)
        m.setattr(checkpoints.fsguard, "repo_relpath_denied", lambda p: False)
        end = checkpoints.capture(repo, kind="turn-end", sess_key="0123456789abcdef", agent="claude", turn=1)
    r = client.get("/api/checkpoints/diff", params={"repo": str(repo), "id": end["id"], "file": "k.pem", "from": base["id"]})
    assert r.status_code == 200 and "KEY-MATERIAL" not in r.text
    assert "KEY-MATERIAL" not in client.get("/api/checkpoints/diff", params={"repo": str(repo), "id": end["id"]}).text


@pytest.mark.parametrize("params", [
    {"id": "../etc"}, {"id": "v1/zz/yy/1"}, {"id": "v1/aaaaaaaaaaaa/bbbbbbbbbbbbbbbb/000001", "against": "current"},
    {"id": "v1/aaaaaaaaaaaa/bbbbbbbbbbbbbbbb/000001", "from": "nope"}])
def test_bad_ids_and_modes_are_400(client, repo, params):
    r = client.get("/api/checkpoints/diff", params={"repo": str(repo), **params})
    assert r.status_code == 400 and r.json()["error"] == "bad_request"


def test_unknown_id_and_hostile_file_names(client, repo):
    base, end = make_turn(repo)
    assert client.get("/api/checkpoints/diff", params={"repo": str(repo), "id": "v1/aaaaaaaaaaaa/bbbbbbbbbbbbbbbb/000009"}).status_code == 404
    for bad in ("/etc/passwd", "../x", "-p", "a\0b"):
        r = client.get("/api/checkpoints/diff", params={"repo": str(repo), "id": end["id"], "file": bad})
        assert r.status_code == 404, bad                                   # 위험한 file은 없는 것으로 취급


def test_global_switch_off_makes_every_route_404(client, repo, monkeypatch):
    monkeypatch.setenv("VT_CHECKPOINTS", "0")
    for spec in ROUTES:
        r = call(client, spec, repo)
        assert r.status_code == 404 and r.json()["error"] == "disabled"
    assert not checkpoint_store.any_enabled()


# --- 훅 계약 ----------------------------------------------------------------------


def test_hook_response_does_not_wait_for_the_capture(client, repo, monkeypatch):
    """느린 캡처(2초)가 걸려도 `POST /api/agent/event`는 바로 응답한다. 새 훅 이벤트도, 응답 내용도 그대로다."""
    assert client.put("/api/checkpoints/settings", json={"repo": str(repo), "enabled": True}).status_code == 200
    started = []

    def slow(s, kind, pre_trusted):
        started.append(kind)
        time.sleep(2.0)
        return {"status": "ok"}
    monkeypatch.setattr(checkpoint_jobs, "_capture_job", slow)
    seen = []
    orig = checkpoint_jobs.on_agent_event

    def spy(event, state, payload, ts, **kw):
        seen.append((event, state is not None))
        return orig(event, state, payload, ts, **kw)
    monkeypatch.setattr(checkpoint_jobs, "on_agent_event", spy)
    body = {"event": "session_start", "agent": "claude", "payload": {"session_id": "hook-sess", "cwd": str(repo), "source": "startup"}}
    t0 = time.monotonic()
    r = client.post("/api/agent/event", json=body)
    took = time.monotonic() - t0
    assert r.status_code == 200 and r.json()["ok"] is True
    assert took < 1.5, f"훅 응답이 캡처를 기다렸다({took:.2f}초)"
    assert seen and seen[0][0] == "session_start"


def test_real_hook_events_through_the_route_make_checkpoints(client, repo):
    """실제 훅 모양의 `POST /api/agent/event`(session_start → prompt → pre → 파일 수정 → stop)로 ref가 생기고 목록·diff로 읽힌다."""
    assert client.put("/api/checkpoints/settings", json={"repo": str(repo), "enabled": True}).status_code == 200

    def post(event, **extra):
        body = {"event": event, "agent": "claude", "ts": time.time_ns(),
                "payload": {"session_id": "e2e-sess", "cwd": str(repo), **extra}}
        assert client.post("/api/agent/event", json=body).status_code == 200

    def wait_for(n):
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if len(checkpoints.list_refs(repo)) >= n and not any(s.running or s.pending for s in checkpoint_jobs._sessions.values()):
                return
            time.sleep(0.05)
        raise AssertionError("체크포인트가 만들어지지 않았다")

    post("session_start", source="startup")
    wait_for(1)
    (repo / "human.txt").write_text("사람\n")
    post("prompt", prompt="do it")
    wait_for(2)
    time.sleep(0.05)
    post("pre", tool_name="Write", tool_input={"file_path": "x"})
    (repo / "agent.txt").write_text("에이전트\n")
    post("stop")
    wait_for(3)
    b = client.get("/api/checkpoints", params={"repo": str(repo)}).json()
    rows = b["sessions"][0]["checkpoints"]
    assert [(r["kind"], r["turn"]) for r in sorted(rows, key=lambda r: r["seq"])] == [("baseline", 0), ("pre-turn", 1), ("turn-end", 1)]
    end = next(r for r in rows if r["kind"] == "turn-end")
    assert end["preTrusted"] is True and end["summary"]["files"] == 1
    files = client.get("/api/checkpoints/diff", params={"repo": str(repo), "id": end["id"]}).json()["files"]
    assert [f["path"] for f in files] == ["agent.txt"]
