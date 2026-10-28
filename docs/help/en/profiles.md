# FarShell launch profiles

The Launch profiles tab in MCP, Skills and Plugins creates dedicated Claude and
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

Long-lived token: register a refresh-token-free `claude setup-token` value with
`fsh profile token set <id>` and the profile's sessions run on it instead of a login. With nothing
to refresh, sessions cannot be signed out by racing on one refresh chain. The token is kept
write-only in a 0600 file, passed as `CLAUDE_CODE_OAUTH_TOKEN` to that profile's sessions only,
and never appears in a screen, API or log. Its lifetime is assumed to be about a year (not a
server guarantee) and a warning starts 30 days before. It has no usage or account scope, so usage
percentages and account identity cannot be checked; the readiness report lists that as a limitation.
It cannot be combined with API authentication.
`fsh profile token set <id> --from-clauth <name>` imports the long-lived token clauth already holds
(`session-token.static.json`). A login with a refresh token or a scope beyond inference is not copied,
because two holders would race on one chain; clauth's file is left untouched.

`VT_USAGE_PROVIDER=native` reads managed native OAuth quotas without falling back
to the clauth feed or default Codex credentials. Exact official HTTPS endpoints
support DeepSeek and OpenRouter balances, plus Z.ai and MiniMax international
quota. Alibaba, other endpoints and other regions remain unsupported and receive
no keys. Alibaba console credentials are not collected. `none` disables usage display.

Verify a short live request sends a small Claude model request and may incur
provider charges. Prepare native refresh test expires only a test login. It
refuses live profile sessions, and failure may require logging in again. Work
profiles cannot use this operation.

Check clauth replacement readiness lists missing configuration, inference,
refresh, usage, session and conversation-resume evidence. It does not delete or
stop clauth. A separate source OAuth review uses the existing access token only
for official account identity lookup and comparison with the target. It never
copies or refreshes source tokens. Verify actual conversation response observes
verified Claude or Codex sessions and completed assistant responses without
sending a new request. Automated inference requests support Claude only. Full
replacement requires real concurrent-account and OAuth refresh validation.

Management APIs are listed in the [API Reference](../../../API.md). They require a
human login directly on the target host and password elevation for changes.
Machine tokens, agents and peers cannot apply these changes directly.
