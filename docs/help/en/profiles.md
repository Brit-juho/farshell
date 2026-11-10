# FarShell launch profiles

The Launch profiles tab in MCP · Skills · Plugins creates dedicated Claude and
Codex settings roots. Several profiles can belong to one logical account; a
shared label is not proof of provider identity.

```bash
fsh profile list
fsh profile create development --tool claude
fsh profile create review --tool claude --account <account-id>
fsh profile create login-test --tool codex --test
fsh profile login <profile-id>
fsh profile status <profile-id>
fsh profile sources
fsh profile retirement
```

Log in through the native CLI in an interactive terminal on the target host.
OAuth tokens are not cloned from clauth. The web shows login commands and status,
not tokens. Profile separation is not OS sandboxing; common, project and policy
settings may still be inherited under native CLI rules.

Reviewed configuration imports preserve their sources and recheck file hashes.
A separate review can clone API configuration into a Claude test profile. Keys,
endpoints and explicit environment variables are stored privately with mode 0600;
OAuth credentials are excluded. Only explicit profile authentication settings are
used; another account's keys from the server shell are not inherited.

API authentication settings lets you review changes or removal of keys, endpoints
and environment variables. Secrets are never displayed again. Exit the profile’s
CLI normally before editing. Convert to work profile preserves login and settings
while disabling test-only forced expiration; conversion does not establish clauth
replacement readiness.

Manage MCP additions, edits and enabled state, direct-skill enabled state, user
hook additions, removal, disabling and restoration, and plugins. FarShell lifecycle hooks cannot be
removed. Plugins are installed natively from a fixed local snapshot after digest
review. New and recovered installations remain disabled until a separate activation
review. Download public GitHub repositories by a fixed 40-character commit SHA and
subdirectory; installation requires a separate review. Fixed dependencies within
one marketplace, updates, removal and recovery are supported. Codex updates use
native removal and reinstallation.

Renaming preserves login and configuration revision. Archiving preserves login and
conversations and refuses running profiles or unfinished jobs. Restore archived
profiles from the separate archived list.

`fsh profile import-config` and `import-conversations` run the same reviewed flow
in bulk from a terminal. Conversation import checks source hashes and ownership.
Unknown or contested clauth shared conversations cannot be imported. Copy
completion, native Codex reading and verified native resume are separate results.
The replacement readiness check requires a verified native resume only for the five
most recently modified conversations of each source; older ones count once they are
imported with a matching source hash, and excluded unowned conversations are listed
as a limitation. Rollback refuses to overwrite files edited after import.

Global sharing: `fsh profile share <id>` links the global `~/.claude` skills, agents, commands,
hooks, plugins, `CLAUDE.md` and conversation history (`projects`) into the profile folder. The global
copy is the source, so installing or updating once reaches every profile. What stays per profile is the
login or token, `.claude.json`, `settings.json`, and any real extension (skill, agent, command, hook)
that has no global twin or differs from it. A copy identical to the global one is replaced by a link and
the original is moved under `.pre-share/`; nothing is deleted. Plugins share their install location and
what differs per account is the enable/disable setting. A profile that shares the history needs no
conversation import and only has its resume verified. Inside a profile folder only a link to exactly the
same-named global entry is accepted, and removing the global folder affects every profile at once. New
global entries are linked automatically when a profile is launched.

Settings sync: `fsh profile sync-settings <id>` (also when a profile is launched) only reads the global
`settings.json` and the MCP server definitions in `~/.claude.json` and aligns the profile key by key. A key
the profile has not touched since the last apply follows the global one (a removal included); a key the
profile changed keeps its value and a later global change does not overwrite it. `--take-global <key>`
returns a key to the global value and `--dry-run` shows key names only. Only the on/off state of plugins,
skills, hooks and MCP servers differs per profile; each plugin's configuration, the skill files and the MCP
definitions with the keys they carry are shared. `fsh profile toggle <id> plugin|skill <name> on|off` changes
that profile only, and switching an MCP server or hook off in the MCP, skills and plugins manager is kept.
The model, `apiKeyHelper`, `forceLogin*` and `env` keys that start with `ANTHROPIC_` or `AWS_` or contain
KEY, TOKEN, SECRET or HEADERS are account keys and are copied in neither direction. Hooks follow event by
event except FarShell's status hooks; an event with some hooks switched off here and hooks carrying a stale
clauth runtime path are skipped. A remote MCP server that signs in with OAuth stores that login in the
profile's own config directory, so each profile signs in to it once (copying a refresh token would put two
holders on one chain). MCP definitions are applied the next time when a session is running.

Choosing an account for a new session: the `+` menu's "Another account, this time only" creates a new tmux
session with the chosen account, and "Change default account" (or `fsh profile default <name>`) sets, per tool
(Claude, Codex), the default for sessions you create from now on. Only that session gets the profile's config
folder (`CLAUDE_CONFIG_DIR` / `CODEX_HOME`), and variables that would override the chosen account
(`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, ...) are removed from it.
Running sessions are not changed and nothing switches automatically. A profile whose folder is missing, that is not
signed in or whose token has expired is refused with a reason; the session is never created on the default
`~/.claude` instead. The list shows the account name, auth state, usage and reset (when a value was already read,
otherwise "unknown") and when it was updated, and can be sorted by "most headroom first". The small name tag on a
rail row or session card is the account chosen **at creation** (the shell can change its own environment). Sessions an
agent creates for a task (delegation) **inherit the parent session's account**: a parent created with account B gets a
child on B (same rail tag), and a parent without an account behaves as before. Only the profile for the tool the child
runs (Claude or Codex) is inherited. An agent cannot choose an account (a `profile` field is rejected), and when the
parent's account cannot be used right now (folder missing, archived, not signed in, expired token) the delegation is
refused instead of falling back to the default account. A child created on another Mac does not inherit. It works
only when you are connected directly to this Mac; register only your own accounts. Profiles set up with an API
endpoint can be chosen too: the endpoint, auth values, headers and model names in that profile's setup go into that
session only (a key never appears in a command line, log or screen) and every other account's authentication variable is
removed. Usage is unknown for these profiles. A profile whose setup file is malformed, or that also has a long-lived
token, is refused with a reason.
**If the endpoint is `http://` (and not this Mac itself), the key crosses the network unencrypted.** A trusted
LAN gateway can be a deliberate setup, so it is not refused; the review screen only warns. Use `https://` when you can.
Proxy (`*_PROXY`), certificate (`SSL_CERT_*`, `NODE_EXTRA_CA_CERTS`), shell (`SHELL`, `ZDOTDIR`), `PATH` and `GIT_*`
variables cannot be put into a profile's environment.

An account's token sits in that session's environment, so **any process of the same user (including an agent running
in that session) can read it.** "An agent cannot choose an account" is a rule of the FarShell screen, API and
delegation paths, not an OS barrier, and an agent fooled by a prompt injection could send the token out. Register only
accounts you are willing to hand to the agents you run in that session. Creating a session also refreshes that
profile's statusLine wrapper automatically (the global `~/.claude` is only read; other settings, hooks and MCP follow
only when you run `fsh profile sync-settings`), and a session created with an account may be named only with letters,
digits, `_` and `-`.

Profile names may repeat within one tool and are still created, but they are confusing in the picker and name tags, so
keep them distinct (with a repeat, `fsh claude <name>` asks for the ID and creation shows a "same name exists" flag).
Names with control, bidi-control or zero-width characters are refused.

Long-lived token: register a refresh-token-free `claude setup-token` value with
`fsh profile token set <id>` and the profile's sessions run on it instead of a login. With nothing
to refresh, sessions cannot be signed out by racing on one refresh chain. The token is kept
write-only in a 0600 file, passed as `CLAUDE_CODE_OAUTH_TOKEN` to that profile's sessions only,
and never appears in a screen, API or log. Its lifetime is assumed to be about a year (not a
server guarantee) and a warning starts 30 days before. It has no usage or account scope, so usage
percentages and account identity cannot be checked; the readiness report lists that as a limitation.
It cannot be combined with API authentication.
Getting a new token (official route): run `claude setup-token` in a terminal; it prints a one-year token.
Copy it, run `fsh profile token set <id>` and paste it at the prompt (the input is hidden and stays out of
shell history). Use `--stdin` only when piping; there is no way to pass the token as a command-line argument
(it would show in `ps` and shell history). The `--from-clauth` import option was removed.

`VT_USAGE_PROVIDER=native` reads managed native OAuth quotas without falling back
to the clauth feed or default Codex credentials. Exact official HTTPS endpoints
support DeepSeek and OpenRouter balances, plus Z.ai and MiniMax international
quota. Alibaba, other endpoints and other regions remain unsupported and receive
no keys. Alibaba console credentials are not collected. `none` disables usage display.

Usage is read from official sources. A Claude profile's `statusLine` gets FarShell's wrapper
(`bin/fsh-statusline`), which keeps the `rate_limits` (5-hour and weekly usage with reset times) that this
account's sessions received, per profile. The original statusline command still runs unchanged (with none, only the
wrapper is added), and the global `~/.claude/settings.json` is never modified. If you change the statusline in a
profile yourself, that value is respected and the profile then has no usage source. For a long-lived-token profile
this is the only usage source, and it exists only after a session of that account got its first reply (an account
not used for a while shows "N hours ago"). A Codex profile is read by starting `codex app-server` briefly, only
when no Codex process uses that `CODEX_HOME`, at most every 5 minutes (`VT_USAGE_APPSERVER=0` turns it off). The
picker, the status bar and the usage panel show value, reset, source and age; an official value wins over the
older lookup, and a window past its reset is not shown.

Verify a short live request sends a small Claude model request and may incur
provider charges. Prepare native refresh test expires only a test login. It
refuses live profile sessions, and failure may require logging in again. Work
profiles cannot use this operation.

Check clauth replacement readiness lists missing configuration, inference,
refresh, usage, session and conversation-resume evidence. It does not delete or
stop clauth; `clauth_stopped` is true only when no clauth launchd job or daemon process
exists (the `clauth mcp` plugin client does not count). The `credentials-not-copied` and
`mcp-credentials-not-migrated` items no longer appear as blockers. A separate source OAuth review uses the existing access token only
for official account identity lookup and comparison with the target. It never
copies or refreshes source tokens. Concurrency: a refreshing login counts only when two different profiles on one account run at the same time.
A long-lived token or API key profile has nothing to refresh, so two sessions of the same profile running at once
count; that observation is recorded against the authentication and required again when the token changes. A check run
from a shell also judges sessions by the state the server recorded and whether the tmux session still exists.

Verify actual conversation response observes
verified Claude or Codex sessions and completed assistant responses without
sending a new request. Automated inference requests support Claude only. Full
replacement requires real concurrent-account and OAuth refresh validation.

Management APIs are listed in the [API Reference](../../../API.md). They require a
human login directly on the target host and password elevation for changes.
Machine tokens, agents and peers cannot apply these changes directly.
