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
