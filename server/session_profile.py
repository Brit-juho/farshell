"""Account (profile) injection for NEW tmux sessions — hotswap H1.

A person picks a registered profile; the session is created with that profile's
`CLAUDE_CONFIG_DIR` / `CODEX_HOME` in its own environment, and nothing else changes:
no running session is touched, no credential file is copied or swapped, no global
`~/.claude` is replaced, and there is no automatic switching.

Rules (Orca's slot check): a chosen profile that cannot be used is REFUSED. It is never
replaced by the default `~/.claude`, because a session that silently runs on another
account is worse than no session.

Secrets: a long-lived token never reaches a command line (`ps`) or a log. The session is
created by a tmux config file (0600, deleted at once) read with `source-file`, so tmux's
argv only names the file. The values live in that session's environment, like any env var.

API-endpoint profiles (`.farshell-provider.json`: endpoint, key/auth token, headers, models) get
exactly the environment the managed launch builds (`agent_profile_runtime.provider_environment`,
computed in one place) plus `CLAUDE_CONFIG_DIR`. Every such value goes through the same 0600 file; a
key given as `api_key` is not an environment value at all (the profile's `apiKeyHelper` reads it).
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

import agent_profile_local as local
import agent_profiles as profiles
import fsguard
import tmux_runner
import vt_paths
from i18n import tr

OPTION = "@fsh_profile"

# What overrides the chosen account, per tool (Claude "Authentication precedence"; the managed
# launch refuses the same names). Removed from the new session's environment — whether they come
# from the server's environment or from tmux's global environment.
OVERRIDES = {
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
               "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS"),
    "codex": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID"),
}
# An API-endpoint profile also decides the model names; another account's (server/tmux-global) model
# overrides must not mix into it. Removed unless the profile sets them itself.
API_EXTRA_OVERRIDES = ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                       "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                       "ANTHROPIC_DEFAULT_FABLE_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL")
TOOL_VARIABLE = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
MIN_TMUX = (3, 2)   # `new-session -e`


class SlotRefused(Exception):
    """A deliberate, localized refusal with a stable machine-readable code."""

    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class Selection:
    profiles: list = field(default_factory=list)   # public profile rows
    kinds: dict = field(default_factory=dict)      # profile id -> "login" | "token"

    @property
    def ids(self):
        return [p["id"] for p in self.profiles]

    def public(self):
        return [{"id": p["id"], "name": p["name"], "tool": p["tool"]} for p in self.profiles]


def _ready():
    """The registry exists. Never creates it (a person without profiles must stay untouched)."""
    return (vt_paths.state_dir() / "agent-profiles" / "registry.sqlite3").exists()


# --- authentication state (local files only, never a network call) -----------------------

def auth_state(profile):
    """`{state: ok|broken|missing, kind: login|token|api|None, code, reason}` for one profile row."""
    tool, pid = profile["tool"], profile["id"]
    folder = vt_paths.state_dir() / "agent-profiles" / pid / tool
    if not folder.is_dir():
        return {"state": "broken", "kind": None, "code": "folder_missing", "reason": tr("프로필 폴더를 찾지 못했습니다")}
    try:
        root = profiles.root(pid)
    except (fsguard.FsDenied, profiles.Refused, OSError):
        return {"state": "broken", "kind": None, "code": "folder_unsafe", "reason": tr("프로필 폴더의 위치·소유자·권한이 안전하지 않습니다")}
    import agent_profile_secret
    import agent_profile_token
    try:
        token = agent_profile_token.read(root) if tool == "claude" else None
        private = agent_profile_secret.read(root)
    except (ValueError, OSError, fsguard.FsDenied):
        return {"state": "broken", "kind": None, "code": "credentials_unreadable", "reason": tr("인증 파일을 읽지 못했습니다(권한이나 형식 문제)")}
    if private is not None:
        # An API-endpoint profile: usable when its file is well formed (the same validation the managed
        # launch applies). Nothing of the file's content leaves this function.
        bad = None
        if tool != "claude":
            bad = tr("API 설정은 Claude 프로필에서만 쓸 수 있습니다")
        elif token is not None:
            bad = tr("API 인증과 장기 토큰이 함께 설정되어 있습니다. 하나만 남기세요")
        else:
            import agent_profile_runtime
            try:
                for value in agent_profile_runtime.provider_environment(pid).values():
                    _quote(value, multiline=True)     # a value tmux's config syntax cannot carry
            except (profiles.Refused, ValueError, OSError, fsguard.FsDenied, SlotRefused):
                bad = tr("API 설정의 형식이 올바르지 않습니다. 설정을 다시 저장하세요")
        if bad:
            return {"state": "broken", "kind": "api", "code": "unsupported_auth", "reason": bad}
        return {"state": "ok", "kind": "api", "code": None, "reason": None}
    if token is not None:
        if token["expires_at"] <= time.time():
            return {"state": "broken", "kind": "token", "code": "token_expired", "reason": tr("장기 토큰이 만료됐습니다. 새로 등록하세요")}
        return {"state": "ok", "kind": "token", "code": None, "reason": None}
    from usage.profiles import _read_credentials
    if _read_credentials(profile) is not None:
        return {"state": "ok", "kind": "login", "code": None, "reason": None}
    filename = ".credentials.json" if tool == "claude" else "auth.json"
    try:
        exists = fsguard.profile_file(root, filename).exists()
    except fsguard.FsDenied:
        exists = True
    if exists:
        return {"state": "broken", "kind": "login", "code": "credentials_unreadable", "reason": tr("로그인 정보를 읽지 못했습니다. 다시 로그인하세요")}
    return {"state": "missing", "kind": None, "code": "not_logged_in", "reason": tr("이 프로필은 로그인되어 있지 않습니다")}


def check_slot(profile):
    """Refuse unless this profile can be injected right now. Returns the auth kind."""
    if profile.get("archived_at") is not None:
        raise SlotRefused("profile_archived", tr("보관된 프로필입니다. 복원한 뒤 쓰세요"))
    if profile.get("state") != "ready":
        raise SlotRefused("profile_not_ready", tr("프로필 복구를 먼저 완료하세요"))
    state = auth_state(profile)
    if state["code"]:
        raise SlotRefused(state["code"], state["reason"])
    return state["kind"]


# --- choosing ---------------------------------------------------------------------------

def _lookup(profile_id):
    try:
        return profiles.get(profile_id)
    except profiles.Refused:
        raise SlotRefused("profile_not_found", tr("그런 프로필이 없습니다"))


def select(ref):
    """`ref` is a profile id or "default" ("none" is handled by the caller). None = inject nothing."""
    if not isinstance(ref, str) or not (ref == "default" or profiles.ID.fullmatch(ref)):
        raise SlotRefused("invalid_profile", tr("프로필 값이 올바르지 않습니다"), 400)
    if not _ready():
        if ref == "default":
            return None
        raise SlotRefused("profile_not_found", tr("그런 프로필이 없습니다"))
    if ref == "default":
        chosen = []
        for tool, pid in sorted(local.defaults().items()):
            profile = _lookup(pid)
            try:
                check_slot(profile)
            except SlotRefused as error:
                # A marked default that cannot be used must not fall back to another account.
                raise SlotRefused("default_invalid", tr("기본 계정을 쓸 수 없습니다: {why}", why=error.message))
            chosen.append(profile)
        candidates = chosen
    else:
        candidates = [_lookup(ref)]
    if not candidates:
        return None
    selection = Selection()
    for profile in candidates:
        selection.kinds[profile["id"]] = check_slot(profile)
        selection.profiles.append(profile)
    return selection


def environment(selection):
    """(plain, secret, tools): variables to set. `secret` holds only values read at spawn time."""
    plain, secret = {}, {}
    for profile in selection.profiles:
        plain[TOOL_VARIABLE[profile["tool"]]] = str(profiles.root(profile["id"]))
        if selection.kinds[profile["id"]] == "api":
            import agent_profile_runtime
            try:
                values = agent_profile_runtime.provider_environment(profile["id"])
            except (profiles.Refused, ValueError, OSError, fsguard.FsDenied):
                values = None
            if values is None:
                raise SlotRefused("unsupported_auth", tr("API 설정의 형식이 올바르지 않습니다. 설정을 다시 저장하세요"))
            # All of it travels through the 0600 file: a header or token is indistinguishable from a
            # harmless value by name, so nothing is treated as "plain".
            secret.update(values)
        if selection.kinds[profile["id"]] == "token":
            from agent_profile_token import environment_value
            value = environment_value(profile["id"])
            if not value:
                raise SlotRefused("token_missing", tr("장기 토큰을 찾지 못했습니다"))
            secret["CLAUDE_CODE_OAUTH_TOKEN"] = value
    plain["FSH_PROFILE_ID"] = ",".join(selection.ids)
    return plain, secret, sorted({p["tool"] for p in selection.profiles})


def removed_names(selection, plain, secret):
    names = []
    for tool in sorted({p["tool"] for p in selection.profiles}):
        extra = API_EXTRA_OVERRIDES if tool == "claude" and "api" in {
            selection.kinds.get(p["id"]) for p in selection.profiles if p["tool"] == "claude"} else ()
        names.extend(n for n in (*OVERRIDES[tool], *extra) if n not in plain and n not in secret and n not in names)
    return names


def present_overrides(names):
    """Which of `names` actually exist in the server's or tmux's global environment (names only)."""
    found = {n for n in names if os.environ.get(n)}
    text = tmux_runner.run_text(["show-environment", "-g"]) or ""
    for line in text.splitlines():
        key = line.split("=", 1)[0]
        if key in names:
            found.add(key)
    return sorted(found)


# --- inheriting a parent's account (hotswap H3: delegated child sessions) ---------------

def _parent_ids(key):
    """Profile ids the SERVER recorded for the session with this identity key (`#{session_id}:#{session_created}`).

    The tmux option `@fsh_profile` is not used as the source: the agent running inside the session can
    `tmux set-option` it, and delegation must never let an agent choose an account. Two records count — a
    session created with an injected account (`injected_sessions`) and a managed `profile-<hex>` launch
    (`sessions`). Returns (ids, mismatch)."""
    ids, mismatch = [], False
    with profiles.database() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='injected_sessions'").fetchone():
            ids += [r["profile_id"] for r in db.execute(
                "SELECT profile_id FROM injected_sessions WHERE created=? ORDER BY profile_id", (key,))]
        for row in db.execute("SELECT profile_id, state FROM sessions WHERE created=?", (key,)):
            ids.append(row["profile_id"])
            mismatch = mismatch or row["state"] == "mismatch"
    return list(dict.fromkeys(ids)), mismatch


def inherit(parent_key, parent_pane, agent):
    """The account a delegated child must run under: the parent session's, never one the caller names.

    Returns a Selection restricted to the child agent's tool (a Claude child needs only the parent's Claude
    profile; an unrelated Codex profile that is unusable must not block it), or None when the parent has no
    recorded account (nothing is injected — the behaviour before profiles existed). A parent profile that
    cannot be used right now raises SlotRefused; it is NEVER replaced by the default account."""
    if not _ready():
        return None
    ids, mismatch = _parent_ids(parent_key)
    if not ids:
        # A marker without a server record cannot be trusted (an agent can write the option itself).
        tag = (tmux_runner.run_text(["display-message", "-p", "-t", parent_pane, "#{@fsh_profile}"]) or "").strip()
        if tag:
            raise SlotRefused("parent_profile_unverified",
                              tr("부모 세션의 계정 기록을 확인하지 못했습니다. 새 세션에서 다시 시작하세요"))
        return None
    if mismatch:
        raise SlotRefused("parent_profile_mismatch", tr("부모 세션이 기록된 계정으로 돌고 있지 않습니다"))
    selection = Selection()
    for pid in ids:
        profile = _lookup(pid)
        if profile["tool"] != agent:
            continue
        selection.kinds[pid] = check_slot(profile)
        selection.profiles.append(profile)
    return selection if selection.profiles else None


# --- creating the tmux session ----------------------------------------------------------

_version = None


def tmux_version():
    global _version
    if _version is None:
        import subprocess
        try:
            out = subprocess.run(["tmux", "-V"], capture_output=True, text=True, timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        match = re.search(r"(\d+)\.(\d+)", out)
        _version = (int(match.group(1)), int(match.group(2))) if match else None
    return _version


_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _quote(value, *, multiline=False):
    """A tmux config string. Control characters cannot be quoted safely, so they are refused — except, for
    environment values (`multiline`), newline/return/tab, which tmux's own `\\n`, `\\r`, `\\t` escapes carry
    (a custom-headers value is one header per line). `\\`, `"` and `$` are escaped, so a value can neither
    end its string, start a second command (`;`) nor expand a variable; `#{}`/`~` stay literal in `-e`."""
    if any((ord(c) < 32 and not (multiline and c in _ESCAPES)) or ord(c) == 127 for c in value):
        raise SlotRefused("invalid_value", tr("제어 문자가 든 값은 쓸 수 없습니다"), 400)
    text = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
    for raw, escaped in _ESCAPES.items():
        text = text.replace(raw, escaped)
    return '"' + text + '"'


def _script(name, cols, rows, cwd, plain, secret, removed, ids):
    # The shell is started as `unset <overrides>; exec $SHELL -l`, so the variables are truly absent
    # (tmux `new-session -e VAR=` would only set them empty, and tmux has no way to unset one).
    shell = (("unset " + " ".join(removed) + "; ") if removed else "") + 'exec "${SHELL:-/bin/sh}" -l'
    line = ["new-session", "-d", "-s", _quote(name), "-x", str(int(cols)), "-y", str(int(rows)), "-c", _quote(cwd)]
    for key, value in {**plain, **secret}.items():
        line += ["-e", _quote(f"{key}={value}", multiline=True)]
    line.append(_quote(shell))
    lines = [" ".join(line), f"set-option -t {_quote('=' + name + ':')} {OPTION} {_quote(','.join(ids))}"]
    # New windows and panes of this session must not see the overrides either (tmux global env).
    lines += [f"set-environment -t {_quote('=' + name)} -r {n}" for n in removed]
    # One command list: tmux stops it at the first failure. As separate lines a failed `new-session`
    # (the name already exists) would still run the rest and mark SOMEONE ELSE's session.
    return " ; ".join(lines) + "\n"


def _session_id(name):
    value = tmux_runner.run_text(["display-message", "-p", "-t", f"={name}:", "#{session_id}"])
    value = (value or "").strip()
    return value if re.fullmatch(r"\$\d+", value) else None


def sync_settings(selection):
    """Bring each chosen Claude profile's settings in line with the global ones, which also places the
    usage-collecting statusLine wrapper (hotswap H2). The global files are only read. A settings problem
    never stops a session from being created; `fsh profile sync-settings --dry-run` shows it."""
    import agent_profile_settings
    for profile in selection.profiles:
        if profile["tool"] == "claude":
            try:
                agent_profile_settings.sync(profile["id"])
            except Exception:  # noqa: BLE001
                pass


def spawn(name, cols, rows, cwd, selection):
    """Create the session with the chosen account. Returns `(rc, error_text, info)`."""
    sync_settings(selection)
    version = tmux_version()
    if version is None or version < MIN_TMUX:
        return 1, "tmux>=3.2 required", {}
    plain, secret, tools = environment(selection)
    removed = removed_names(selection, plain, secret)
    text = _script(name, cols, rows, cwd, plain, secret, removed, selection.ids)
    folder = fsguard.private_profile_directory(profiles.directory() / "launch")
    path = folder / ("session-" + secrets.token_hex(16) + ".conf")
    before = _session_id(name)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        # `start-server` first: source-file alone does not start one, and the first session may be this one.
        rc, _, _ = tmux_runner.run(["start-server", ";", "source-file", str(path)], timeout=5)
    finally:
        path.unlink(missing_ok=True)
    if rc != 0:
        # Never leave a half-configured session (created but without its marker) behind — but only one
        # THIS call created. A session that already had the name is somebody else's and stays untouched.
        after = _session_id(name)
        if after and after != before:
            tmux_runner.run(["kill-session", "-t", after], timeout=3)
        # tmux's own message is not passed on: it may quote the config line that carried a token.
        return rc, "", {}
    return 0, "", {"cleared_env": present_overrides(removed)}


def record(name, selection):
    """Remember which profiles a session was created with (the marker itself is the tmux option)."""
    created = tmux_runner.run_text(["display-message", "-p", "-t", f"={name}:", "#{session_id}:#{session_created}"])
    created = (created or "").strip()
    now = time.time()
    with profiles.database() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS injected_sessions (
            name TEXT NOT NULL, created TEXT NOT NULL, profile_id TEXT NOT NULL REFERENCES profiles(id),
            profile_revision INTEGER NOT NULL, started_at REAL NOT NULL, PRIMARY KEY (name, created, profile_id))""")
        for profile in selection.profiles:
            db.execute("INSERT OR REPLACE INTO injected_sessions VALUES (?,?,?,?,?)",
                       (name, created, profile["id"], profile["revision"], now))
        db.execute("DELETE FROM injected_sessions WHERE rowid NOT IN "
                   "(SELECT rowid FROM injected_sessions ORDER BY started_at DESC LIMIT 500)")


# --- naming the badge (session list) ----------------------------------------------------

_names = {}
_names_lock = threading.Lock()
NAME_TTL = 5


def describe(ids_text):
    """`@fsh_profile` text -> [{id, name, tool}] from the registry (name None if it cannot be read)."""
    ids = [i for i in (ids_text or "").split(",") if profiles.ID.fullmatch(i)]
    if not ids:
        return []
    now = time.time()
    with _names_lock:
        hit = _names.get(tuple(ids))
        if hit and now - hit[0] < NAME_TTL:
            return hit[1]
    rows = {}
    if _ready():
        try:
            with profiles.database() as db:
                for i in ids:
                    row = db.execute("SELECT id,name,tool FROM profiles WHERE id=?", (i,)).fetchone()
                    if row:
                        rows[i] = {"id": row["id"], "name": row["name"], "tool": row["tool"]}
        except Exception:  # noqa: BLE001 — the list must still render; the badge just has no name
            rows = {}
    result = [rows.get(i) or {"id": i, "name": None, "tool": None} for i in ids]
    with _names_lock:
        if len(_names) > 64:
            _names.clear()
        _names[tuple(ids)] = (now, result)
    return result


# --- picker -----------------------------------------------------------------------------

def _usage(profile_id, tool=None):
    from usage.profiles import cached_row
    row = cached_row(profile_id, tool)
    if not row or not row.get("windows"):
        return None
    now = time.time()
    windows = []
    for w in row["windows"]:
        reset = w.get("resets_at")
        left = None
        if isinstance(reset, str):
            try:
                from datetime import datetime
                left = max(0, int(datetime.fromisoformat(reset).timestamp() - now))
            except ValueError:
                left = None
        windows.append({"id": w.get("id"), "label": w.get("label"), "pct": w.get("pct"),
                        "resets_at": reset, "resets_in_sec": left})
    return {"windows": windows, "checked_at": row.get("checked_at"), "stale": bool(row.get("stale")),
            "source": row.get("source") or ("oauth-usage" if row.get("provider") == "claude" else "codex-usage")}


def picker():
    """A light list for the account chooser: no secrets, no paths, no network."""
    if not _ready():
        return {"profiles": [], "defaults": {}}
    listing = profiles.listing()["profiles"]
    marked = local.defaults()
    out = []
    for row in listing:
        state = auth_state(row)
        usable = row["state"] == "ready" and state["code"] is None
        out.append({"id": row["id"], "name": row["name"], "tool": row["tool"], "purpose": row["purpose"],
                    "auth": state["state"], "auth_kind": state["kind"],
                    "selectable": usable, "reason": None if usable else (state["reason"] or tr("프로필 복구를 먼저 완료하세요")),
                    "default": marked.get(row["tool"]) == row["id"], "usage": _usage(row["id"], row["tool"])})
    active = {r["id"] for r in listing}
    return {"profiles": out,
            "defaults": {tool: {"id": pid, "valid": pid in active} for tool, pid in marked.items()}}
