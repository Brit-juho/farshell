"""Hotswap H3 — a delegated child session inherits its parent session's account.

Real tmux on an isolated socket, a sandbox state dir, fake profile folders with fake credential strings.
No real CLI, keychain or account is touched; the child's launch command is captured, never run.
Each case runs with the delegation engine off and on (`VT_AUTHZ_ENFORCE=deleg`).
"""
import asyncio
import json
import subprocess
import time
import types

import pytest

import agent_profiles as profiles
import delegation
import session_profile
import tmux_runner
from share_identity import Caller
from test_session_profile import (API_AUTH, API_KEY, API_URL, _api, DANGEROUS, FAKE_TOKEN, _dump, _login, _make, _spawn, _token,  # noqa: F401  (fixtures)
                                  sandbox, tmux)


@pytest.fixture(params=["off", "deleg"])
def world(request, sandbox, tmux, monkeypatch):
    if request.param == "deleg":
        monkeypatch.setenv("VT_AUTHZ_ENFORCE", "deleg")
    else:
        monkeypatch.delenv("VT_AUTHZ_ENFORCE", raising=False)
    monkeypatch.delenv("VT_AGENT_SPAWN", raising=False)
    monkeypatch.delenv("VT_SAFE_MODE", raising=False)
    launched = []
    monkeypatch.setattr(delegation, "_push", lambda *a: None)
    monkeypatch.setattr(delegation, "_maybe_drain", lambda s: None)
    import tmux_target
    monkeypatch.setattr(tmux_target, "send_to_tmux", lambda pane, text: launched.append((pane, text)) or True)
    return types.SimpleNamespace(tmux=tmux, launched=launched, mode=request.param)


def _parent(name, selection=None, record=True):
    """A real tmux session, created the way H1 creates one (or a plain one). Returns its Caller."""
    if selection is not None:
        rc, reason, _ = _spawn(name, selection)
        assert rc == 0, reason
        if record:
            session_profile.record(name, selection)
    else:
        assert tmux_runner.run(["new-session", "-d", "-s", name, "-c", "/tmp"], timeout=5)[0] == 0
    ident = delegation._session_identity(name)
    sid, created = ident["key"].split(":")
    return Caller(sid=sid, created=int(created), name=name, pane_id=ident["pane"])


def _show(socket, session, args):
    return subprocess.run(["tmux", "-L", socket, args[0], "-t", f"={session}:", *args[1:]],
                          capture_output=True, text=True).stdout


def _tree_text():
    import vt_paths
    out = []
    for path in vt_paths.state_dir().rglob("*"):
        if "agent-profiles" in path.parts:       # the profile folder is where the token lawfully lives
            continue
        if path.is_file() and path.stat().st_size < 5_000_000 and path.suffix != ".sqlite3":
            out.append(path.read_text(errors="replace"))
    return "\n".join(out)


def _fresh_dump(world):
    world.tmux.out.unlink(missing_ok=True)


# --- inheritance -------------------------------------------------------------------------

def test_child_runs_under_the_parent_account(world):
    pid = _make("alice")
    parent = _parent("main", session_profile.select(pid))
    _fresh_dump(world)
    r = delegation.spawn(parent, task="do it", title="kid")
    assert r["account"] == {"inherited": True, "profiles": [{"id": pid, "name": "alice", "tool": "claude"}]}
    env = _dump(world.tmux)
    assert env["CLAUDE_CONFIG_DIR"] == str(profiles.root(pid)) and env["FSH_PROFILE_ID"] == pid
    assert pid in _show(world.tmux.socket, "kid", ["show-options", "@fsh_profile"])
    assert f"CLAUDE_CONFIG_DIR={profiles.root(pid)}" in _show(world.tmux.socket, "kid", ["show-environment"])
    with profiles.database() as db:       # the badge record exists, keyed by the child's identity
        key = delegation._session_identity("kid")["key"]
        assert [x[0] for x in db.execute("SELECT profile_id FROM injected_sessions WHERE created=?", (key,))] == [pid]
    item = delegation.get(r["delegation"])
    assert item["account"] == [{"id": pid, "name": "alice", "tool": "claude"}]
    assert delegation.public(item, parent.key)["account"] == item["account"]
    assert world.launched and world.launched[0][1].startswith("command claude ")


def test_dangerous_variables_are_removed_in_the_child_too(world, monkeypatch):
    pid = _make("alice")
    for name in DANGEROUS:
        monkeypatch.setenv(name, "ambient-" + name)
    parent = _parent("main", session_profile.select(pid))
    _fresh_dump(world)
    delegation.spawn(parent, task="x", title="kid")
    env = _dump(world.tmux)
    claude_side = session_profile.OVERRIDES["claude"]
    assert not [n for n in claude_side if n in env]              # same rule as H1: the chosen tool's overrides go
    assert all(env[n] == "ambient-" + n for n in DANGEROUS if n not in claude_side)   # the other tool's are untouched
    shown = _show(world.tmux.socket, "kid", ["show-environment"]).splitlines()
    assert not [n for n in claude_side if any(line.startswith(n + "=") for line in shown)]


def test_a_long_lived_token_reaches_the_child_but_no_argv_log_ledger_or_audit(world, caplog):
    pid = _make("work", login=False)
    _token(pid)
    parent = _parent("main", session_profile.select(pid))
    _fresh_dump(world)
    caplog.set_level("DEBUG")
    argv_mark = len(world.tmux.argv)
    r = delegation.spawn(parent, task="x", title="kid")
    assert _dump(world.tmux)["CLAUDE_CODE_OAUTH_TOKEN"] == FAKE_TOKEN
    assert not any(FAKE_TOKEN in part for call in world.tmux.argv[argv_mark:] for part in call)
    assert FAKE_TOKEN not in caplog.text
    assert FAKE_TOKEN not in subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    assert FAKE_TOKEN not in _tree_text() and FAKE_TOKEN not in json.dumps(r)
    assert not list((profiles.directory() / "launch").glob("session-*.conf"))


def test_parent_without_an_account_is_unchanged(world):
    _make("alice")                                   # a registry exists, but this parent used none
    parent = _parent("main")
    _fresh_dump(world)
    argv_mark = len(world.tmux.argv)
    r = delegation.spawn(parent, task="x", title="kid")
    assert "account" not in r and "account" not in delegation.get(r["delegation"])
    assert _show(world.tmux.socket, "kid", ["show-options", "@fsh_profile"]).strip() == ""
    assert not any("source-file" in call for call in world.tmux.argv[argv_mark:])
    assert "CLAUDE_CONFIG_DIR" not in _dump(world.tmux)


def test_only_the_child_agents_tool_matters(world):
    claude = _make("alice")
    codex = _make("personal", "codex")
    # a session created with BOTH tools' accounts (two defaults)
    both = session_profile.Selection(profiles=[profiles.get(claude), profiles.get(codex)],
                                     kinds={claude: "login", codex: "login"})
    parent = _parent("both", both)
    _fresh_dump(world)
    a = delegation.spawn(parent, task="x", title="c1", agent="claude")
    assert [p["id"] for p in a["account"]["profiles"]] == [claude]
    env = _dump(world.tmux)
    assert "CODEX_HOME" not in env and env["CLAUDE_CONFIG_DIR"] == str(profiles.root(claude))
    delegation.close(a["delegation"], caller_key=parent.key)
    _fresh_dump(world)
    b = delegation.spawn(parent, task="x", title="c2", agent="codex")
    assert [p["id"] for p in b["account"]["profiles"]] == [codex]
    env = _dump(world.tmux)
    assert "CLAUDE_CONFIG_DIR" not in env and env["CODEX_HOME"] == str(profiles.root(codex))


def test_an_unusable_profile_of_the_other_tool_does_not_block(world):
    claude = _make("alice")
    codex = _make("personal", "codex")
    both = session_profile.Selection(profiles=[profiles.get(claude), profiles.get(codex)],
                                     kinds={claude: "login", codex: "login"})
    parent = _parent("both", both)
    (profiles.root(codex) / "auth.json").unlink()
    assert delegation.spawn(parent, task="x", title="kid", agent="claude")["account"]["profiles"][0]["id"] == claude
    with pytest.raises(delegation.DelegationError) as e:
        delegation.spawn(parent, task="x", title="kid2", agent="codex")
    assert e.value.account == {"code": "not_logged_in"}


def test_parent_with_a_profile_for_another_tool_only_injects_nothing(world):
    codex = _make("personal", "codex")
    parent = _parent("main", session_profile.select(codex))
    r = delegation.spawn(parent, task="x", title="kid", agent="claude")
    assert "account" not in r
    assert _show(world.tmux.socket, "kid", ["show-options", "@fsh_profile"]).strip() == ""


# --- refusal -----------------------------------------------------------------------------

def test_an_api_endpoint_parent_hands_the_same_environment_to_the_child(world, monkeypatch, caplog):
    pid = _make("gateway", login=False)
    _api(pid, key=API_KEY, env={"ANTHROPIC_BASE_URL": API_URL, "ANTHROPIC_AUTH_TOKEN": API_AUTH})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-api-key")
    parent = _parent("main", session_profile.select(pid))
    _fresh_dump(world)
    caplog.set_level("DEBUG")
    argv_mark = len(world.tmux.argv)
    r = delegation.spawn(parent, task="x", title="kid")
    assert r["account"] == {"inherited": True, "profiles": [{"id": pid, "name": "gateway", "tool": "claude"}]}
    env = _dump(world.tmux)
    assert env["ANTHROPIC_BASE_URL"] == API_URL and env["ANTHROPIC_AUTH_TOKEN"] == API_AUTH
    assert env["CLAUDE_CONFIG_DIR"] == str(profiles.root(pid)) and "ANTHROPIC_API_KEY" not in env
    ps = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
    for secret in (API_KEY, API_AUTH):
        assert not any(secret in part for call in world.tmux.argv[argv_mark:] for part in call)
        assert secret not in caplog.text and secret not in _tree_text() and secret not in json.dumps(r)
        assert secret not in json.dumps(delegation.get(r["delegation"])) and secret not in ps


def test_an_api_parent_with_a_broken_setup_refuses_the_child(world):
    pid = _make("gateway", login=False)
    _api(pid)
    parent = _parent("main", session_profile.select(pid))
    _api(pid, env={"ANTHROPIC_BASE_URL": "ftp://x.example.invalid"})
    with pytest.raises(delegation.DelegationError) as e:
        delegation.spawn(parent, task="x", title="kid")
    assert (e.value.code, e.value.account) == ("parent_profile_unavailable", {"code": "unsupported_auth"})
    assert not tmux_runner.has_session("kid") and not world.launched


def _break_login(pid):
    (profiles.root(pid) / ".credentials.json").unlink()


def _archive(pid):
    with profiles.database() as db:
        db.execute("UPDATE profiles SET archived_at=? WHERE id=?", (time.time(), pid))


def _remove_folder(pid):
    import shutil
    shutil.rmtree(profiles.root(pid))


@pytest.mark.parametrize("breaker,code", [(_break_login, "not_logged_in"), (_archive, "profile_archived"),
                                          (_remove_folder, "folder_missing")])
def test_unusable_parent_profile_refuses_and_never_falls_back(world, breaker, code):
    pid = _make("alice")
    parent = _parent("main", session_profile.select(pid))
    breaker(pid)
    with pytest.raises(delegation.DelegationError) as e:
        delegation.spawn(parent, task="x", title="kid")
    err = e.value
    assert (err.code, err.status, err.account) == ("parent_profile_unavailable", 409, {"code": code})
    assert err.body()["account"] == {"code": code}
    assert not tmux_runner.has_session("kid")                       # no default-account child
    assert delegation.list_all() == []                              # nothing reserved
    assert not world.launched
    text = _tree_text()
    assert f"account:{code}" in text and "alice" not in text.replace("sample-repo", "")   # reason only, no names/secrets


def test_refusal_does_not_use_up_the_spawn_rate(world):
    pid = _make("alice")
    parent = _parent("main", session_profile.select(pid))
    _break_login(pid)
    for i in range(delegation.SPAWN_PER_MIN + 2):
        with pytest.raises(delegation.DelegationError) as e:
            delegation.spawn(parent, task="x", title=f"k{i}")
        assert e.value.code == "parent_profile_unavailable"


def test_expired_token_refuses(world):
    pid = _make("work", login=False)
    _token(pid)
    parent = _parent("main", session_profile.select(pid))
    import agent_profile_token
    agent_profile_token.set_token(pid, FAKE_TOKEN, expires_at=time.time() - 10)
    with pytest.raises(delegation.DelegationError) as e:
        delegation.spawn(parent, task="x", title="kid")
    assert e.value.account == {"code": "token_expired"}


def test_a_marker_without_a_server_record_is_refused(world):
    pid = _make("alice")
    parent = _parent("main")
    tmux_runner.set_option("main", "@fsh_profile", pid)             # what an agent could do to itself
    with pytest.raises(delegation.DelegationError) as e:
        delegation.spawn(parent, task="x", title="kid")
    assert e.value.account == {"code": "parent_profile_unverified"} and not tmux_runner.has_session("kid")


def test_editing_the_marker_cannot_pick_another_account(world):
    mine, other = _make("alice"), _make("bob")
    parent = _parent("main", session_profile.select(mine))
    tmux_runner.set_option("main", "@fsh_profile", other)
    _fresh_dump(world)
    r = delegation.spawn(parent, task="x", title="kid")
    assert [p["id"] for p in r["account"]["profiles"]] == [mine]
    assert _dump(world.tmux)["CLAUDE_CONFIG_DIR"] == str(profiles.root(mine))


def test_renaming_the_parent_keeps_the_record(world):
    pid = _make("alice")
    parent = _parent("main", session_profile.select(pid))
    tmux_runner.run(["rename-session", "-t", "=main", "renamed"])
    assert delegation.spawn(parent, task="x", title="kid")["account"]["profiles"][0]["id"] == pid


# --- managed (profile-<hex>) parents -----------------------------------------------------

def _managed(pid, name, state="running"):
    assert tmux_runner.run(["new-session", "-d", "-s", name, "-c", "/tmp"], timeout=5)[0] == 0
    ident = delegation._session_identity(name)
    with profiles.database() as db:
        db.execute("INSERT INTO sessions (name,profile_id,created,profile_revision,started_at,state) VALUES (?,?,?,?,?,?)",
                   (name, pid, ident["key"], 1, time.time(), state))
    sid, created = ident["key"].split(":")
    return Caller(sid=sid, created=int(created), name=name, pane_id=ident["pane"])


def test_a_managed_profile_session_hands_its_account_to_the_child(world):
    pid = _make("alice")
    parent = _managed(pid, "profile-0123456789abcdef")
    _fresh_dump(world)
    r = delegation.spawn(parent, task="x", title="kid")
    assert r["account"]["profiles"][0]["id"] == pid
    assert _dump(world.tmux)["CLAUDE_CONFIG_DIR"] == str(profiles.root(pid))


def test_a_managed_session_that_no_longer_matches_its_account_is_refused(world):
    pid = _make("alice")
    parent = _managed(pid, "profile-0123456789abcdef", state="mismatch")
    with pytest.raises(delegation.DelegationError) as e:
        delegation.spawn(parent, task="x", title="kid")
    assert e.value.account == {"code": "parent_profile_mismatch"}


# --- the HTTP route: an agent cannot name an account ------------------------------------

class _Request:
    def __init__(self, body):
        self._body = body
        self.headers, self.scope, self.client, self.query_params = {}, {}, None, {}

    async def json(self):
        return self._body


@pytest.mark.parametrize("field", ["profile", "profiles", "profile_id", "account"])
def test_route_rejects_an_account_field_instead_of_ignoring_it(world, monkeypatch, field):
    import routes.delegation as route
    mine, other = _make("alice"), _make("bob")
    parent = _parent("main", session_profile.select(mine))

    async def caller(request):
        return parent
    monkeypatch.setattr(route, "_caller", caller)
    response = asyncio.run(route.spawn(_Request({"task": "x", "title": "kid", field: other})))
    assert response.status_code == 400 and json.loads(response.body)["error"] == "profile_not_allowed"
    assert not tmux_runner.has_session("kid") and delegation.list_all() == []
    assert "profile_not_allowed" in _tree_text()


def test_route_without_the_field_inherits_and_reports_the_account(world, monkeypatch):
    import routes.delegation as route
    import routes.system as system_routes
    monkeypatch.setattr(system_routes, "kick_sessions", lambda: None)
    pid = _make("alice")
    parent = _parent("main", session_profile.select(pid))

    async def caller(request):
        return parent
    monkeypatch.setattr(route, "_caller", caller)
    body = asyncio.run(route.spawn(_Request({"task": "x", "title": "kid"})))
    assert body["ok"] and body["account"]["profiles"][0]["id"] == pid


def test_route_returns_the_refusal_code_and_account_reason(world, monkeypatch):
    import routes.delegation as route
    pid = _make("alice")
    parent = _parent("main", session_profile.select(pid))
    _break_login(pid)

    async def caller(request):
        return parent
    monkeypatch.setattr(route, "_caller", caller)
    response = asyncio.run(route.spawn(_Request({"task": "x", "title": "kid"})))
    assert response.status_code == 409
    assert json.loads(response.body) == {"error": "parent_profile_unavailable", "reason": response.body and json.loads(response.body)["reason"],
                                         "account": {"code": "not_logged_in"}}
