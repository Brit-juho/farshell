"""에이전트 세션 공유 — 격리 tmux 소켓 + 임시 상태 폴더 + ASGI 테스트 클라이언트로 끝에서 끝까지.

실제 tmux 를 쓴다(다른 tmux 테스트와 같은 방식: 전용 소켓, 끝나면 그 소켓만 정리). tmux 가 없는
환경에서 skip 하지 않는다 — 이 기능의 보안 판정이 전부 tmux 가 주는 값(`#{session_id}`·`pane_pid`)에
기대므로 가짜로 대체하면 핵심을 검증하지 못한다. 서버 프로세스와 실제 `~/.vt`·`-L fsh` 는 건드리지 않는다.

기계 토큰·비밀번호 값은 조각을 이어 만든다(scripts/guard_personal.py 의 토큰 모양 검사를 피하려는 게 아니라
애초에 진짜 값이 아니라는 표시).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.testclient import TestClient

import agent_share
import share_identity
import tmux_runner
import tmux_sockets

PASSWORD = "pw-" + "for-share-tests"
MACHINE = "-".join(["machine", "tok", "for", "share", "tests"])


class Env:
    """격리 tmux 위의 세션 만들기·식별 헤더 만들기."""

    def __init__(self, sock: str):
        self.sock = sock

    def mk(self, name: str, cmd: str = "echo ready-marker; exec cat") -> None:
        rc, _, err = tmux_runner.run(["new-session", "-d", "-s", name, "-x", "100", "-y", "30", cmd], timeout=5.0)
        assert rc == 0, err
        self.wait(name, "ready-marker" if "ready-marker" in cmd else None)

    def kill(self, name: str) -> None:
        tmux_runner.run(["kill-session", "-t", f"={name}"], timeout=5.0)

    def rename(self, old: str, new: str) -> None:
        assert tmux_runner.run(["rename-session", "-t", f"={old}", new])[0] == 0

    def row(self, name: str):
        return next(r for r in share_identity.list_pane_rows() if r.name == name)

    def sess(self, name: str):
        return next(s for s in agent_share.snapshot().values() if s.name == name)

    def screen(self, name: str) -> str:
        return agent_share.read_pane(self.sess(name), 200)["text"]

    def wait(self, name: str, text: str | None, timeout: float = 5.0) -> None:
        if text is None:
            return
        end = time.time() + timeout
        while time.time() < end:
            try:
                if text in self.screen(name):
                    return
            except StopIteration:
                pass
            time.sleep(0.05)
        raise AssertionError(f"{name} 화면에 {text!r} 가 안 나타남")

    def ident(self, name: str, pid: int | None = None, pane: str | None = None, tmux: str | None = None) -> dict:
        r = self.row(name)
        return {"x-fsh-pane": pane or r.pane_id,
                "x-fsh-tmux": tmux or f"/private/tmp/tmux-501/{self.sock},123,0",
                "x-fsh-pid": str(pid if pid is not None else r.pane_pid)}


@pytest.fixture
def env(tmp_path, monkeypatch):
    sock = f"fsh-share-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(tmux_runner, "VT_TMUX_SOCKET", sock)
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setattr(agent_share, "_rate", {})
    yield Env(sock)
    tmux_sockets.kill_server(sock)


@pytest.fixture
def app_auth(tmp_path, monkeypatch):
    import auth as _auth

    state = tmp_path / "vt"
    monkeypatch.setattr(_auth, "STATE_DIR", state)
    monkeypatch.setattr(_auth, "DEVICES_PATH", state / "devices.json")
    monkeypatch.setattr(_auth, "TOTP_PATH", state / "totp.json")
    monkeypatch.setattr(_auth, "TICKETS_PATH", state / "tickets.json")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "share-test-signing-key")
    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", _auth.hash_password(PASSWORD))
    monkeypatch.setattr(_auth, "VT_AUTH_TOKEN", MACHINE)
    monkeypatch.setenv("VT_NETWORK_MODE", "all")
    return _auth


@pytest.fixture
def web(env, app_auth):
    """(human 브라우저 세션 클라이언트, 에이전트 쪽 기계 토큰 클라이언트, env). 쿠키 저장소가 서로 분리돼 있다."""
    import main

    with TestClient(main.app) as human:
        r = human.post("/api/auth", json={"token": PASSWORD})
        assert r.status_code == 200, r.text
        agent = TestClient(main.app)
        agent.headers["Authorization"] = f"Bearer {MACHINE}"
        yield human, agent, env


def share(human, name, nick, mode="read"):
    return human.post(f"/api/tmux/{name}/share", json={"nick": nick, "mode": mode})


def aread(agent, env, who, nick, **kw):
    return agent.post("/api/agent/shared/read", json={"nickname": nick, **kw}, headers=env.ident(who))


def asend(agent, env, who, nick, text, **kw):
    return agent.post("/api/agent/shared/send", json={"nickname": nick, "text": text, **kw}, headers=env.ident(who))


def approve(human, rid, level=None):
    body = {"decision": "allow"}
    if level:
        body["level"] = level
    return human.post(f"/api/agent/share/requests/{rid}/decision", json=body)


def setup_pair(web, mode="read", nick="shell"):
    human, agent, env = web
    env.mk("target")
    env.mk("claude-a", "cat")
    assert share(human, "target", nick, mode).status_code == 200
    return human, agent, env


# ── 1. 닉네임·공유·해제 ───────────────────────────────────────────────────────────────


def test_nick_validation_and_uniqueness(web):
    human, _, env = web
    env.mk("s1", "cat")
    env.mk("s2", "cat")
    for bad in ("", "a b", "한글", "x" * 33, "a/b", "a;b", None, 5):
        r = share(human, "s1", bad)
        assert r.status_code == 400 and r.json()["error"] == "bad_nick", bad
    assert share(human, "s1", "x" * 32).status_code == 200            # 경계 32자
    assert share(human, "s1", "ok_name-1", "bogus").json()["error"] == "bad_mode"
    assert share(human, "s1", "dev").status_code == 200
    r = share(human, "s2", "DEV")                                    # 대소문자 무시 중복
    assert r.status_code == 409 and r.json()["error"] == "nick_taken"
    assert share(human, "s1", "dev", "readwrite").json()["share"] == {"nick": "dev", "mode": "readwrite"}  # 같은 세션 모드 변경
    assert share(human, "nope", "zz").status_code == 404
    assert share(human, "s2", "other").status_code == 200


def test_kill_named_session_can_be_shared_and_unshared(web):
    human, _, env = web
    env.mk("kill", "cat")
    assert share(human, "kill", "k").status_code == 200
    assert human.delete("/api/agent/share/sessions/kill").status_code == 200
    assert env.sess("kill").share is None


def test_renamed_kill_session_unshares_without_ending_it(web):
    human, _, env = web
    env.mk("target", "cat")
    assert share(human, "target", "shell").status_code == 200
    env.rename("target", "kill")
    r = human.delete("/api/agent/share/sessions/kill")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert env.sess("kill").share is None


def test_share_marks_session_list_and_unshare_clears(web):
    human, _, env = web
    env.mk("t1", "cat")
    r = human.get("/api/tmux/sessions")
    assert r.json()[0]["share"] is None
    share(human, "t1", "dev", "readwrite")
    row = next(s for s in human.get("/api/tmux/sessions").json() if s["name"] == "t1")
    assert row["share"] == {"nick": "dev", "mode": "readwrite"}
    # tmux 옵션이 실제로 심겼다(서버 재시작을 견디는 근거)
    out = tmux_runner.run_text(["show-options", "-v", "-t", "t1", "@fsh_share_nick"])
    assert out.strip() == "dev"
    r = human.delete("/api/agent/share/sessions/t1")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert next(s for s in human.get("/api/tmux/sessions").json() if s["name"] == "t1")["share"] is None


def test_share_field_rides_the_existing_list_sessions_call(monkeypatch):
    """공유 표시를 읽느라 tmux 호출이 늘지 않는다 — list-sessions 포맷에 합쳐진다."""
    import asyncio

    import routes.tmux as tmux_route

    calls = []

    async def fake(args, timeout=None):
        calls.append(list(args))
        if args[0] == "list-sessions":
            return "a\t1\t0\t\tlocal\tdev\treadwrite\nb\t1\t0\t\t\tbad nick\tread\nc\t1\t0\t\t\tok\tweird\nd\t1\t0\t\t\t\t\n"
        return "a\tzsh\t/x\t%1\n"

    monkeypatch.setattr(tmux_runner, "run_text_async", fake)
    out = asyncio.run(tmux_route.collect_tmux_sessions())
    assert [s["share"] for s in out] == [
        {"nick": "dev", "mode": "readwrite"}, None, {"nick": "ok", "mode": "read"}, None]  # 규칙 밖 닉네임=null, 모르는 모드=read
    assert len(calls) == 2
    assert all("#{@fsh_share_nick}" in c[-1] and "#{@fsh_share_mode}" in c[-1] for c in calls if c[0] == "list-sessions")


# ── 2. 권한 흐름 ─────────────────────────────────────────────────────────────────────


def test_pending_then_approve_then_read(web):
    human, agent, env = setup_pair(web)
    r = aread(agent, env, "claude-a", "shell")
    assert r.status_code == 202
    body = r.json()
    assert body["state"] == "pending" and body["requestId"] and "승인되면 같은 호출을 다시" in body["reason"]
    # 같은 (A,S,kind) 는 중복 생성하지 않는다
    assert aread(agent, env, "claude-a", "shell").json()["requestId"] == body["requestId"]
    pend = human.get("/api/agent/share/requests").json()["requests"]
    assert len(pend) == 1 and pend[0]["agent"]["name"] == "claude-a" and pend[0]["target"]["nick"] == "shell"
    assert pend[0]["kind"] == "read"
    d = approve(human, body["requestId"])
    assert d.status_code == 200 and d.json()["grant"]["read"] is True and d.json()["grant"]["write"] is False
    r = aread(agent, env, "claude-a", "shell")
    assert r.status_code == 200 and r.json()["state"] == "ok" and "ready-marker" in r.json()["text"]
    assert human.get("/api/agent/share/requests").json()["requests"] == []


def test_read_approval_does_not_grant_write_and_write_needs_its_own(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    approve(human, rid)
    r = asend(agent, env, "claude-a", "shell", "echo hi", enter=False)
    assert r.status_code == 202 and r.json()["kind"] == "write"          # 읽기 허용이 쓰기를 주지 않는다
    assert env.screen("target").count("echo hi") == 0
    wid = r.json()["requestId"]
    assert approve(human, wid, "readwrite").status_code == 200
    r = asend(agent, env, "claude-a", "shell", "echo hi-typed")
    assert r.status_code == 200 and r.json()["chars"] == 13
    env.wait("target", "echo hi-typed")


def test_write_allow_with_lower_level_gives_read_only(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    wid = asend(agent, env, "claude-a", "shell", "x").json()["requestId"]
    g = approve(human, wid, "read").json()["grant"]
    assert g["read"] is True and g["write"] is False
    assert asend(agent, env, "claude-a", "shell", "x").status_code == 202    # 쓰기는 다시 물어야 한다


def test_level_cannot_exceed_request_kind_or_share_mode(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    assert approve(human, rid, "readwrite").json()["error"] == "bad_level"   # 읽기 요청에 쓰기를 줄 수 없다
    wid = asend(agent, env, "claude-a", "shell", "x").json()["requestId"]
    share(human, "target", "shell", "read")                                  # 그 사이에 읽기 전용으로 낮춤
    r = approve(human, wid, "readwrite")
    assert r.status_code == 409 and r.json()["error"] == "read_only_share"


def test_read_only_share_rejects_send_immediately_without_request(web):
    human, agent, env = setup_pair(web, mode="read")
    r = asend(agent, env, "claude-a", "shell", "rm -rf /tmp/x")
    assert r.status_code == 403 and r.json()["error"] == "read_only_share"
    assert "읽기 전용" in r.json()["reason"]
    assert human.get("/api/agent/share/requests").json()["requests"] == []   # 승인 요청도 만들지 않는다


def test_lowering_mode_takes_effect_immediately(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    assert asend(agent, env, "claude-a", "shell", "echo one").status_code == 200
    share(human, "target", "shell", "read")
    r = asend(agent, env, "claude-a", "shell", "echo two")
    assert r.status_code == 403 and r.json()["error"] == "read_only_share"
    assert aread(agent, env, "claude-a", "shell").status_code == 200         # 읽기는 그대로
    share(human, "target", "shell", "readwrite")                             # 다시 올리면 기존 쓰기 허용이 살아난다
    assert asend(agent, env, "claude-a", "shell", "echo three").status_code == 200


@pytest.mark.parametrize("bad", ['{"grants": {"x": 1}, "requests": []}',
                                       '{"grants": [], "requests": "wrong"}', '{broken json'])
def test_corrupt_grants_preserved_before_next_save(web, bad):
    human, agent, env = setup_pair(web)
    path = agent_share._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bad)
    assert human.get("/api/agent/share/grants").status_code == 200
    assert human.get("/api/agent/share/grants").json()["grants"] == []
    assert path.read_text() == bad  # 읽기만으로는 원본을 덮지 않는다
    assert aread(agent, env, "claude-a", "shell").status_code == 202
    backups = list(path.parent.glob("agent_grants.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == bad
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert len(json.loads(path.read_text())["requests"]) == 1


def test_grant_survives_cli_restart_in_same_tmux_session(web):
    """/new·/clear·CLI 재시작 = 같은 tmux 세션의 다른 프로세스. 권한은 Claude session_id 가 아니라 tmux 세션에 묶인다."""
    human, agent, env = web
    env.mk("target")
    env.mk("claude-a", "sh -c 'sleep 301 & sleep 302 & wait'")
    share(human, "target", "shell")
    pane_pid = env.row("claude-a").pane_pid
    kids = []
    for _ in range(40):
        kids = [int(x) for x in subprocess.run(["pgrep", "-P", str(pane_pid)], capture_output=True, text=True).stdout.split()]
        if len(kids) >= 2:
            break
        time.sleep(0.05)
    assert len(kids) >= 2
    h1, h2 = env.ident("claude-a", pid=kids[0]), env.ident("claude-a", pid=kids[1])
    rid = agent.post("/api/agent/shared/read", json={"nickname": "shell"}, headers=h1).json()["requestId"]
    approve(human, rid)
    for h in (h1, h2, env.ident("claude-a")):   # 서로 다른 "CLI 인스턴스"(pid)에서 같은 권한
        assert agent.post("/api/agent/shared/read", json={"nickname": "shell"}, headers=h).status_code == 200


def test_rename_follows_but_recreated_same_name_does_not_inherit(web):
    human, agent, env = setup_pair(web)
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    env.rename("claude-a", "claude-renamed")                                  # 이름 변경 — 권한 유지
    assert aread(agent, env, "claude-renamed", "shell").status_code == 200
    env.rename("claude-renamed", "claude-a")
    # 대상 S 를 죽였다가 같은 이름·같은 닉네임으로 다시 만든다 — 옛 권한을 물려받지 못한다
    env.kill("target")
    env.mk("target")
    share(human, "target", "shell")
    assert aread(agent, env, "claude-a", "shell").status_code == 202
    assert human.get("/api/agent/share/grants").json()["grants"] == []        # 죽은 세션의 권한은 조회 때 정리됐다
    # 에이전트 A 쪽을 죽였다 다시 만들어도 마찬가지
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    assert aread(agent, env, "claude-a", "shell").status_code == 200
    env.kill("claude-a")
    env.mk("claude-a", "cat")
    assert aread(agent, env, "claude-a", "shell").status_code == 202


def test_deny_cooldown_and_expiry(web, monkeypatch):
    human, agent, env = setup_pair(web)
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    assert human.post(f"/api/agent/share/requests/{rid}/decision", json={"decision": "deny"}).status_code == 200
    r = aread(agent, env, "claude-a", "shell")                                # 5분 동안 같은 요청을 다시 만들지 않는다
    assert r.status_code == 403 and r.json()["error"] == "request_denied" and r.json()["retryAfter"] > 0
    assert human.get("/api/agent/share/requests").json()["requests"] == []
    monkeypatch.setattr(agent_share, "DENY_COOLDOWN", 0)
    assert aread(agent, env, "claude-a", "shell").status_code == 202          # 쿨다운이 지나면 새 요청
    # 24시간 만료
    path = agent_share._path()
    data = json.loads(path.read_text())
    assert [r["state"] for r in data["requests"]] == ["pending"]
    assert data["requests"][0]["expires"] - data["requests"][0]["created"] == agent_share.REQUEST_TTL == 24 * 3600
    data["requests"][0]["expires"] = time.time() - 1
    path.write_text(json.dumps(data))
    assert agent_share.list_state(agent_share.snapshot())["requests"] == []
    assert aread(agent, env, "claude-a", "shell").status_code == 202           # 만료 뒤엔 새로 만든다


def test_revoke_write_only_then_all_and_unshare_clears_everything(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    gid = human.get("/api/agent/share/grants").json()["grants"][0]["id"]
    assert human.delete(f"/api/agent/share/grants/{gid}?scope=write").status_code == 200
    assert aread(agent, env, "claude-a", "shell").status_code == 200
    assert asend(agent, env, "claude-a", "shell", "x").status_code == 202
    assert human.delete(f"/api/agent/share/grants/{gid}?scope=bogus").status_code == 400
    assert human.delete(f"/api/agent/share/grants/{gid}").status_code == 200
    assert human.delete(f"/api/agent/share/grants/{gid}").status_code == 404
    assert aread(agent, env, "claude-a", "shell").status_code == 202          # 철회 → 다시 보류
    r = human.delete("/api/agent/share/sessions/target")
    assert r.json()["clearedRequests"] >= 1
    assert aread(agent, env, "claude-a", "shell").status_code == 404          # 공유 해제 → 닉네임 자체가 없다
    assert human.get("/api/agent/share/requests").json()["requests"] == []


def test_agent_list_shows_my_permissions_and_hides_names(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    env.mk("claude-b", "cat")
    share(human, "claude-b", "peer-b", "read")
    d = agent.get("/api/agent/shared", headers=env.ident("claude-a")).json()
    assert d["me"] == "claude-a"
    by = {s["nick"]: s for s in d["sessions"]}
    assert set(by) == {"shell", "peer-b"}
    assert by["shell"]["permission"] == {"read": "none", "write": "none"}
    assert by["peer-b"]["permission"]["write"] == "unavailable"               # 읽기 전용 공유
    assert "target" not in json.dumps(d) and "cwd" not in json.dumps(d)       # 세션 이름·cwd 는 싣지 않는다
    aread(agent, env, "claude-a", "shell")
    by = {s["nick"]: s for s in agent.get("/api/agent/shared", headers=env.ident("claude-a")).json()["sessions"]}
    assert by["shell"]["permission"]["read"] == "pending"


def test_cannot_target_own_session(web):
    human, agent, env = setup_pair(web)
    share(human, "claude-a", "me-a")
    r = aread(agent, env, "claude-a", "me-a")
    assert r.status_code == 400 and r.json()["error"] == "self"


# ── 3. 호출자 식별 ────────────────────────────────────────────────────────────────────


def test_identity_failures(web):
    human, agent, env = setup_pair(web)
    ok = aread(agent, env, "claude-a", "shell")
    assert ok.status_code == 202
    cases = {
        "소켓 불일치": env.ident("claude-a", tmux="/private/tmp/tmux-501/default,123,0"),
        "pane 없음(env 에 없음)": {k: v for k, v in env.ident("claude-a").items() if k != "x-fsh-pane"},
        "존재하지 않는 pane": env.ident("claude-a", pane="%9999"),
        "pane 형식 오류": env.ident("claude-a", pane="%1; kill-server"),
        "pid 체인 불일치(다른 칸·이 테스트 프로세스)": env.ident("claude-a", pid=os.getpid()),
        "다른 칸의 pid": env.ident("claude-a", pid=env.row("target").pane_pid),
        "pid 없음": {k: v for k, v in env.ident("claude-a").items() if k != "x-fsh-pid"},
        "pid 쓰레기": env.ident("claude-a", pid="abc"),
        "TMUX 없음(tmux 밖)": {k: v for k, v in env.ident("claude-a").items() if k != "x-fsh-tmux"},
    }
    for label, hdr in cases.items():
        for method, path, body in (("post", "/api/agent/shared/read", {"nickname": "shell"}),
                                   ("post", "/api/agent/shared/send", {"nickname": "shell", "text": "x"}),
                                   ("get", "/api/agent/shared", None)):
            r = getattr(agent, method)(path, **({"json": body} if body is not None else {}), headers=hdr)
            assert r.status_code == 403 and r.json()["error"] == "identity_unverified", (label, path, r.text)
            assert r.json()["reason"]
    # 실패한 호출은 승인 요청도 만들지 않았다(처음의 하나뿐)
    assert len(human.get("/api/agent/share/requests").json()["requests"]) == 1


def test_other_pane_cannot_borrow_my_grant(web):
    """B 가 'A 의 pane id' 를 주장해도 자기 프로세스 체인이 A 의 pane 에 안 닿는다."""
    human, agent, env = setup_pair(web)
    env.mk("claude-b", "cat")
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    forged = env.ident("claude-b", pane=env.row("claude-a").pane_id)           # pid 는 B 것, pane 은 A 것
    r = agent.post("/api/agent/shared/read", json={"nickname": "shell"}, headers=forged)
    assert r.status_code == 403 and r.json()["error"] == "identity_unverified"
    # B 가 자기 이름으로 부르면 권한이 없다
    assert aread(agent, env, "claude-b", "shell").status_code == 202


def test_chain_reaches_depth_and_cycles():
    tree = {10: 9, 9: 8, 8: 7, 7: 1}
    assert share_identity.chain_reaches(10, 8, parent=tree.get) is True
    assert share_identity.chain_reaches(8, 8, parent=tree.get) is True        # 자기 자신 포함
    assert share_identity.chain_reaches(10, 99, parent=tree.get) is False
    assert share_identity.chain_reaches(10, 7, parent=tree.get, max_depth=3) is False   # 깊이 제한
    assert share_identity.chain_reaches(10, 7, parent=tree.get, max_depth=10) is True
    assert share_identity.chain_reaches(5, 4, parent={5: 6, 6: 5}.get) is False         # 순환
    assert share_identity.chain_reaches(5, 1, parent={5: 1}.get) is False               # init(1)로는 닿지 않는다


def test_parse_lsof_owner_picks_the_client_side_only():
    out = "p111\nn127.0.0.1:7777->127.0.0.1:54321\np222\nn127.0.0.1:54321->127.0.0.1:7777\n"
    assert share_identity.parse_lsof_owner(out, 54321, 7777) == 222         # 서버 쪽 끝(로컬 포트 7777)은 제외
    assert share_identity.parse_lsof_owner(out, 54321, 9999) is None
    assert share_identity.parse_lsof_owner("", 1, 2) is None
    two = "p1\nn127.0.0.1:50000->127.0.0.1:7777\np2\nn127.0.0.1:50000->127.0.0.1:7777\n"
    assert share_identity.parse_lsof_owner(two, 50000, 7777) is None        # 모호하면 신고 값으로 내려간다


def test_socket_owner_overrides_a_lying_pid(web, monkeypatch):
    """직접 연결이면 서버가 소켓 소유 pid 를 찾아 **신고 값을 무시**한다."""
    human, agent, env = setup_pair(web)
    mine, other = env.row("claude-a").pane_pid, env.row("target").pane_pid
    monkeypatch.setattr(share_identity, "socket_owner_pid", lambda *a, **k: mine)
    lying = env.ident("claude-a", pid=other)                                  # 신고는 다른 칸의 pid
    r = agent.post("/api/agent/shared/read", json={"nickname": "shell"}, headers=lying)
    assert r.status_code == 202                                               # 실제 소유자(mine)로 통과
    monkeypatch.setattr(share_identity, "socket_owner_pid", lambda *a, **k: os.getpid())
    r = agent.post("/api/agent/shared/read", json={"nickname": "shell"}, headers=env.ident("claude-a"))
    assert r.status_code == 403                                               # 신고가 맞아도 실제 소유자가 칸 밖이면 거부


# ── 4. 사람 전용 경계 ─────────────────────────────────────────────────────────────────


HUMAN_ONLY = [
    ("post", "/api/tmux/target/share", {"nick": "shell", "mode": "read"}),
    ("delete", "/api/agent/share/sessions/target", None),
    ("get", "/api/agent/share/requests", None),
    ("post", "/api/agent/share/requests/abcdef123456/decision", {"decision": "allow"}),
    ("get", "/api/agent/share/grants", None),
    ("delete", "/api/agent/share/grants/abcdef123456", None),
    ("get", "/api/agent/share/audit", None),
]


def test_machine_token_cannot_share_approve_or_revoke(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    gid = human.get("/api/agent/share/grants").json()["grants"][0]["id"]
    for method, path, body in HUMAN_ONLY:
        path = path.replace("abcdef123456", rid if "requests" in path else gid)
        kw = {"json": body} if body is not None else {}
        for hdrs in ({}, env.ident("claude-a")):                               # 식별 헤더를 실어도 소용없다
            r = getattr(agent, method)(path, headers=hdrs, **kw)
            assert r.status_code == 403 and r.json()["error"] == "human_session_required", (method, path, r.text)
        # 쿼리 토큰으로도 불가
        r = getattr(TestClient(__import__("main").app), method)(f"{path}?token={MACHINE}", **kw)
        assert r.status_code == 403, (method, path)
    # 어느 것도 상태를 바꾸지 못했다 — 보류 요청은 그대로, 권한도 그대로
    assert [r["id"] for r in human.get("/api/agent/share/requests").json()["requests"]] == [rid]
    assert len(human.get("/api/agent/share/grants").json()["grants"]) == 1
    assert human.get("/api/tmux/sessions").json()
    # 같은 경로가 쿠키 세션으로는 된다
    assert human.get("/api/agent/share/grants").status_code == 200
    assert human.get("/api/agent/share/audit").status_code == 200


def test_machine_token_together_with_a_cookie_is_not_human(web):
    """쿠키가 있어도 Authorization/?token 이 같이 오면 사람의 브라우저 요청이 아니다."""
    human, _, _ = web
    cookie = human.cookies.get("vt_session")
    import main
    c = TestClient(main.app)
    r = c.get("/api/agent/share/grants", headers={"Authorization": f"Bearer {MACHINE}"}, cookies={"vt_session": cookie})
    assert r.status_code == 403
    assert c.get("/api/agent/share/grants", cookies={"vt_session": cookie}).status_code == 200


def test_unauthenticated_and_no_password_server_cannot_approve(tmp_path, monkeypatch, env):
    import auth as _auth
    import main

    monkeypatch.setattr(_auth, "VT_AUTH_PASSWORD_HASH", "")
    monkeypatch.setattr(_auth, "VT_AUTH_SESSION_KEY", "")
    monkeypatch.setattr(_auth, "VT_AUTH_TOKEN", "")
    c = TestClient(main.app)                                                   # 인증이 아예 꺼진 서버
    for method, path, body in HUMAN_ONLY:
        r = getattr(c, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403 and r.json()["error"] == "human_session_required", (method, path)


def test_peer_signature_cannot_use_the_feature(web):
    human, agent, env = setup_pair(web)
    import main
    import peer_client

    peer = {"id": "peer1", "secret": "00" * 32, "label": "remote"}
    c = TestClient(main.app)                                                   # 쿠키·토큰 없이 peer 서명만
    for method, path, body in HUMAN_ONLY + [("post", "/api/agent/shared/read", {"nickname": "shell"}),
                                              ("get", "/api/agent/shared", None)]:
        hdrs = peer_client._signed_headers(peer, method.upper(), path, "")
        r = getattr(c, method)(path, headers=hdrs, **({"json": body} if body is not None else {}))
        assert r.status_code in (401, 403), (method, path, r.status_code)
    import relay_policy

    for method, path, _ in HUMAN_ONLY:                                         # 호스트 전환 중계로도 닿지 않는다
        assert relay_policy.http_allowed(method.upper(), path) is False, path
    for path in ("/api/agent/shared", "/api/agent/shared/read", "/api/agent/shared/send"):
        assert relay_policy.http_allowed("GET", path) is False and relay_policy.http_allowed("POST", path) is False


def test_auth_kind_helper(app_auth):
    class R:
        def __init__(self, cookies=None, headers=None, query=None):
            self.cookies, self.headers, self.query_params = cookies or {}, headers or {}, query or {}

    a = app_auth
    assert a.request_kind(R()) == "none"
    assert a.request_kind(R(headers={"authorization": f"Bearer {MACHINE}"})) == "machine"
    assert a.request_kind(R(query={"token": MACHINE})) == "machine"
    assert a.request_kind(R(headers={"authorization": "Bearer wrong"})) == "none"
    sess = a.make_session("-")
    assert a.request_kind(R(cookies={"vt_session": sess})) == "session"
    assert a.request_kind(R(cookies={"vt_session": sess}, headers={"authorization": "Bearer x"})) == "none"
    assert a.request_kind(R(cookies={"vt_session": MACHINE})) == "machine"    # 레거시: 쿠키로 실린 기계 토큰은 사람이 아니다
    assert a.request_kind(R(cookies={"vt_session": sess[:-2] + "00"})) == "none"


# ── 5. 입력·속도·상태 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["a\nb", "a\rb", "a\tb", "\x1b[31mred", "esc\x1b", "nul\x00", "del\x7f", "bell\x07",
                                  "c1\x9b", "ls ", "rtl‮", "iso⁦x", "zero\u200bwidth", "join\u200d", ""])
def test_send_rejects_control_chars_newlines_and_empty(web, text):
    human, agent, env = setup_pair(web, mode="readwrite")
    r = asend(agent, env, "claude-a", "shell", text)
    assert r.status_code == 400 and r.json()["error"] in ("bad_text", "empty"), repr(text)
    assert human.get("/api/agent/share/requests").json()["requests"] == []


def test_send_length_limit_and_types(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    assert asend(agent, env, "claude-a", "shell", "a" * 4001).json()["error"] == "too_long"
    assert asend(agent, env, "claude-a", "shell", "a" * 4000).status_code == 200
    assert asend(agent, env, "claude-a", "shell", "한글 ok ✓").status_code == 200   # 일반 유니코드는 허용
    assert asend(agent, env, "claude-a", "shell", 123).status_code == 400
    assert asend(agent, env, "claude-a", "shell", "x", enter="yes").json()["error"] == "bad_enter"
    r = agent.post("/api/agent/shared/send", json={"text": "x"}, headers=env.ident("claude-a"))
    assert r.json()["error"] == "bad_nick"


def test_enter_only_when_asked(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    asend(agent, env, "claude-a", "shell", "typed-no-enter")
    env.wait("target", "typed-no-enter")
    assert env.screen("target").count("typed-no-enter") == 1                   # cat 이 줄을 받아 되풀이하지 않았다 = Enter 없음
    asend(agent, env, "claude-a", "shell", "typed-enter", enter=True)
    end = time.time() + 5
    while time.time() < end and env.screen("target").count("typed-enter") < 2:
        time.sleep(0.05)
    assert env.screen("target").count("typed-enter") == 2                      # Enter → cat 이 되풀이


def test_literal_send_does_not_interpret_tmux_key_names(web):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    asend(agent, env, "claude-a", "shell", "Enter C-c -l Escape")
    env.wait("target", "Enter C-c -l Escape")


def test_rate_limits(web, monkeypatch):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    monkeypatch.setattr(agent_share, "SEND_PER_MIN", 2)
    monkeypatch.setattr(agent_share, "READ_PER_MIN", 3)
    assert [asend(agent, env, "claude-a", "shell", "y").status_code for _ in range(3)] == [200, 200, 429]
    assert [aread(agent, env, "claude-a", "shell").status_code for _ in range(4)] == [200, 200, 200, 429]
    assert aread(agent, env, "claude-a", "shell").json()["error"] == "rate_limited"


@pytest.mark.parametrize("state,code", [("waiting", "target_waiting"), ("working", "target_busy")])
def test_send_refused_while_agent_is_waiting_or_working(web, monkeypatch, state, code):
    human, agent, env = setup_pair(web, mode="readwrite")
    approve(human, asend(agent, env, "claude-a", "shell", "x").json()["requestId"], "readwrite")
    import agent_status

    monkeypatch.setattr(agent_status, "known_status_for_session", lambda name, *a, **k: state)
    r = asend(agent, env, "claude-a", "shell", "1")
    assert r.status_code == 409 and r.json()["error"] == code
    assert env.screen("target").count("1") == 0 or "ready-marker" in env.screen("target")
    assert aread(agent, env, "claude-a", "shell").status_code == 200           # 읽기는 상태와 무관
    for ok_state in ("idle", "done", None):                                    # 쉬고 있거나 모르면(일반 셸) 허용
        monkeypatch.setattr(agent_status, "known_status_for_session", lambda name, *a, _s=ok_state, **k: _s)
        assert asend(agent, env, "claude-a", "shell", "z").status_code == 200


# ── 6. 읽기 상한 ──────────────────────────────────────────────────────────────────────


def test_read_line_and_size_caps(web, monkeypatch):
    human, agent, env = web
    env.mk("target", "sh -c 'seq 1 3000; echo end-marker; exec cat'")
    env.mk("claude-a", "cat")
    env.wait("target", "end-marker")
    share(human, "target", "shell")
    approve(human, aread(agent, env, "claude-a", "shell").json()["requestId"])
    r = aread(agent, env, "claude-a", "shell").json()                          # 기본 200줄
    assert r["lines"] <= agent_share.DEFAULT_LINES and r["text"].splitlines()[-1] == "end-marker"
    r = aread(agent, env, "claude-a", "shell", lines=10).json()
    assert r["lines"] == 10 and r["text"].splitlines()[-1] == "end-marker"     # 마지막 lines 줄
    r = aread(agent, env, "claude-a", "shell", lines=99999).json()             # 최대 2000 으로 잘린다
    assert 0 < r["lines"] <= agent_share.MAX_LINES
    for bad in (0, -1, "10", 1.5, True):
        assert aread(agent, env, "claude-a", "shell", lines=bad).json()["error"] == "bad_lines"
    monkeypatch.setattr(agent_share, "MAX_OUTPUT_BYTES", 120)                  # 크기 상한 — 뒤쪽 우선
    r = aread(agent, env, "claude-a", "shell", lines=500).json()
    assert r["truncated"] is True and r["bytes"] <= 120 and r["text"].splitlines()[-1] == "end-marker"


# ── 7. 감사 기록 ──────────────────────────────────────────────────────────────────────


def test_audit_log_shape_has_no_plaintext(web, tmp_path):
    human, agent, env = setup_pair(web, mode="readwrite")
    secret = "echo super-secret-" + "payload-42"
    approve(human, asend(agent, env, "claude-a", "shell", secret).json()["requestId"], "readwrite")
    asend(agent, env, "claude-a", "shell", secret, enter=True)
    aread(agent, env, "claude-a", "shell", lines=5)
    asend(agent, env, "claude-a", "shell", "bad\nline")
    agent.get("/api/agent/shared", headers=env.ident("claude-a", pane="%999"))
    log = tmp_path / "vt" / "agent_access.log"
    assert oct(log.stat().st_mode & 0o777) == "0o600"
    raw = log.read_text()
    assert "super-secret" not in raw and "payload-42" not in raw             # 원문은 남기지 않는다
    recs = [json.loads(x) for x in raw.splitlines()]
    acts = [(r["action"], r["result"]) for r in recs]
    for want in (("share", "ok"), ("request", "pending"), ("decide", "allowed"), ("send", "ok"), ("read", "ok"),
                 ("list", "identity_unverified")):
        assert want in acts, (want, acts)
    assert next(r for r in recs if r["result"] == "identity_unverified")["by"] == "agent"
    send = next(r for r in recs if r["action"] == "send" and r["result"] == "ok")
    import hashlib
    assert send["sha8"] == hashlib.sha256(secret.encode()).hexdigest()[:8] and send["chars"] == len(secret)
    assert send["enter"] is True and send["nick"] == "shell" and send["agent"] == {"name": "claude-a", "id": env.row("claude-a").sid}
    assert isinstance(send["ts"], float) and send["by"] == "agent"
    rd = next(r for r in recs if r["action"] == "read" and r["result"] == "ok")
    assert rd["lines"] <= 5
    dec = next(r for r in recs if r["action"] == "decide")
    assert dec["by"] == "user" and dec["level"] == "readwrite"
    # 조회 API 는 최근 순
    ents = human.get("/api/agent/share/audit?limit=3").json()["entries"]
    assert len(ents) == 3 and ents[0]["ts"] >= ents[1]["ts"]


def test_audit_rotates_one_generation_and_stays_private(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    monkeypatch.setattr(agent_share, "AUDIT_MAX_BYTES", 300)
    for i in range(30):
        agent_share.audit("read", "ok", nick=f"n{i}", lines=i)
    d = tmp_path / "vt"
    assert (d / "agent_access.log.1").is_file() and (d / "agent_access.log").is_file()
    assert not (d / "agent_access.log.2").exists()                             # 한 단계만
    for f in ("agent_access.log", "agent_access.log.1"):
        assert oct((d / f).stat().st_mode & 0o777) == "0o600"
    rows = agent_share.read_audit(500)
    assert rows[0]["nick"] == "n29" and len(rows) >= 3


# ── 8. 저장소·동시성 ──────────────────────────────────────────────────────────────────


def test_store_is_private_persistent_and_flock_serialises(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    target = agent_share.Sess("$1", 100, "t", "%1", "cat", {"nick": "t", "mode": "read"})
    agents = [agent_share.Sess(f"${i + 10}", 200 + i, f"a{i}", f"%{i + 10}", "cat", None) for i in range(40)]
    snap = {s.key: s for s in [target, *agents]}
    errs = []

    def work(a):
        try:
            req, created = agent_share.request_access(snap, a, target, "read")
            assert created
            agent_share.decide(req["id"], "allow", "read", snap)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    with ThreadPoolExecutor(16) as ex:
        list(ex.map(work, agents))
    assert not errs, errs
    st = agent_share.list_state(snap)
    assert len(st["grants"]) == 40 and st["requests"] == []                   # 갱신 유실 없음
    p = tmp_path / "vt" / "agent_grants.json"
    assert oct(p.stat().st_mode & 0o777) == "0o600" and oct(p.parent.stat().st_mode & 0o777) == "0o700"
    # 프로세스가 바뀌어도(파일에서 다시 읽는다) 유지된다
    assert len(json.loads(p.read_text())["grants"]) == 40
    # 대상이 사라진 스냅샷에서는 다음 조회 때 정리된다
    snap2 = {s.key: s for s in agents}
    assert agent_share.list_state(snap2)["grants"] == []
    assert json.loads(p.read_text())["grants"] == []
    # 같은 (A,S,kind) 동시 요청도 하나로
    snap3 = {s.key: s for s in [target, agents[0]]}
    res = []
    ts = [threading.Thread(target=lambda: res.append(agent_share.request_access(snap3, agents[0], target, "write"))) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for _, created in res if created) == 1 and len({r["id"] for r, _ in res}) == 1


def test_corrupt_grants_file_means_no_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    (tmp_path / "vt").mkdir()
    (tmp_path / "vt" / "agent_grants.json").write_text("{ not json")
    assert agent_share.list_state(None) == {"grants": [], "requests": []}   # 읽기 실패는 권한 없음(안전한 쪽)


def test_pending_request_cap_per_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "vt"))
    a = agent_share.Sess("$9", 1, "a", "%9", "cat", None)
    targets = [agent_share.Sess(f"${i}", 10 + i, f"t{i}", f"%{i}", "cat", {"nick": f"t{i}", "mode": "read"}) for i in range(25)]
    snap = {s.key: s for s in [a, *targets]}
    codes = []
    for t in targets:
        try:
            agent_share.request_access(snap, a, t, "read")
            codes.append(200)
        except agent_share.ShareError as e:
            codes.append(e.status)
    assert codes[:agent_share.MAX_PENDING_PER_AGENT] == [200] * agent_share.MAX_PENDING_PER_AGENT
    assert codes[agent_share.MAX_PENDING_PER_AGENT:] == [429] * 5


# ── 9. 사용자 알림 ────────────────────────────────────────────────────────────────────


def test_pending_request_is_pushed_to_workspace_websocket(web):
    human, agent, env = setup_pair(web)
    with human.websocket_connect("/ws-workspace") as ws:
        assert ws.receive_json()["type"] == "workspace_snapshot"
        assert ws.receive_json()["type"] == "tmux_sessions"
        rid = aread(agent, env, "claude-a", "shell").json()["requestId"]
        aread(agent, env, "claude-a", "shell")                                 # 중복 호출은 이벤트를 또 만들지 않는다
        msg = ws.receive_json()
        assert msg["type"] == "agent_share_request"
        req = msg["request"]
        assert req["id"] == rid and req["kind"] == "read" and req["agent"]["name"] == "claude-a"
        assert req["target"]["nick"] == "shell"
        approve(human, rid)
        done = ws.receive_json()
        assert done == {"type": "agent_share_request_resolved", "id": rid, "state": "allowed"}


def test_push_body_never_carries_session_names(web, monkeypatch):
    human, agent, env = setup_pair(web)
    import push
    import routes.system as system_routes

    sent = []
    monkeypatch.setattr(push, "available", lambda: True)
    monkeypatch.setattr(push, "send", lambda title, body="", url="/": sent.append((title, body)) or {"ok": True})
    monkeypatch.setattr(system_routes, "_workspace_clients", set())          # 열린 브라우저 없음 → 푸시
    aread(agent, env, "claude-a", "shell")
    assert len(sent) == 1
    assert "claude-a" not in sent[0][0] + sent[0][1] and "shell" not in sent[0][0] + sent[0][1]



def test_proxy_headers_cannot_skip_the_socket_owner_check(monkeypatch):
    """프록시 헤더를 붙여 소켓 소유자 확인을 건너뛰고 신고 pid 로 내려가던 길(2026-10-06 적대 시험)이 닫혔다 —
    실제 소켓(IP 호스트)에서 온 호출은 소유 프로세스를 확인하지 못하면 신고 pid 가 맞아도 거부한다."""
    row = share_identity.PaneRow(pane_id="%1", pane_pid=os.getpid(), sid="$1", created="1", name="a",
                             active=True, command="zsh", nick="", mode="")
    monkeypatch.setattr(share_identity, "socket_owner_pid", lambda *a, **k: None)   # lsof 없음/조회 실패
    monkeypatch.setattr(share_identity.pane_resolve, "is_our_socket", lambda _t: True)
    with pytest.raises(share_identity.IdentityError):
        share_identity.verify_caller("%1", "/tmp/x,1,0", os.getpid(), client=("127.0.0.1", 50000),
                                     headers={"x-real-ip": "127.0.0.1"}, server_port=7777, rows=[row])
