# Auth, devices, boundary values

## Authorization order (P2-5, ADR-42)

The table below shows the middleware order and the additional checks applied
at login, in routes and during terminal input. Token-auth exemptions do not
automatically exempt a request from the IP filter or resource checks.

**Starlette runs the *last*-registered middleware outermost** (`add_middleware`
prepends to `user_middleware`), so execution order is the **reverse** of the
registration order in `main.py`.

| # | Layer | Transport | Decides | Implementation | On failure |
|---:|---|---|---|---|---|
| 1 | Origin guard `OriginGuardMiddleware` <!-- mw --> | HTTP + WS | Is the request's origin this server itself? No `Origin` header (non-browser) passes; `""`/`"null"` (opaque origin) is rejected | `server/main.py`, `OriginGuardMiddleware` | 403 `cross_origin` |
| 2 | CORS (opt-in) `CORSMiddleware` <!-- mw:cors --> | HTTP | Is this an explicitly allowed external origin? If `_allowed_origins()` is empty, **this layer doesn't exist at all** | `server/main.py` (conditional `add_middleware`) | Browser can't read the response body |
| 3 | Token auth `TokenAuthMiddleware` <!-- mw --> | **HTTP only** | Is the session cookie / `?token=` / `Bearer` valid? If `auth.is_protected()` is false, everyone passes | `server/main.py`, `TokenAuthMiddleware` → `auth.check_request` | 401 `unauthorized` |
| 3′ | WS handshake auth | **WS only** | Same question as #3, done by the route itself at handshake time + a separate expiry re-check | route-level `_ws_auth` (e.g. `routes/pty.py`) | Handshake refused |
| 4 | Network access `NetworkAccessMiddleware` <!-- mw --> | HTTP + WS | Does the client IP fall inside `VT_NETWORK_MODE`'s CIDR ranges? | `server/main.py`, `NetworkAccessMiddleware` | 403 `ip_not_allowed` |
| 5 | Device + OTP | Login time | Is this a registered device? Does the TOTP code match? **Not a middleware** — checked directly by login and a few routes | `server/auth/devices.py`, `server/auth/totp.py` | Login refused / per-route |
| 6 | Per-resource verification | The route itself | Share-link token signature/expiry/mode · port PIN · port session · peer HMAC + time window + nonce | `routes/share.py`, `routes/system.py` (`/p/authorize`), `routes/peer.py` | Per-route |
| 7 | File boundary (`fsguard`) | The route itself | Root resolution → `resolve()` + `is_relative_to` → deny list | `server/fsguard.py` (3 layers) | 403 / 404 |
| 8 | Dangerous command (`safe_mode`) | PTY input · queue | Does it match a dangerous pattern? Only when `VT_SAFE_MODE=1` | `server/safe_mode.py` | Input blocked / queue `blocked` |
| 9 | Elevated session | The route itself | Git-account writes etc. require re-confirming the password (ADR-22) | `server/auth/__init__.py`, `require_elevated` | Elevation required |

**Every layer is an AND gate. A later layer can only make the result stricter, never
undo what an earlier layer blocked.** Passing one layer only answers that layer's
question — it is not a universal bypass. Two things follow directly:

- **Adding a path to an exception list turns that layer off for that path.** Don't add
  one without writing, in the table below, which numbered layer it turns off and what
  takes its place.
- **A route that only exercises part of the chain (WS) still keeps the same relative
  order.** #3′ *replaces* #3, it doesn't skip past it — a change to #3's logic needs the
  same check applied to #3′.

### Exceptions — what's turned off, and what takes its place

| Path(s) | Layer(s) off | Layer(s) still applied | What replaces the missing layer |
|---|---|---|---|
| `/`, `/sw.js`, `/manifest.json`, `/favicon.ico` | 3, 4 (HTTP) | 1 | Nothing — this is the login gate itself, it has to render unauthenticated |
| `/static/*` | 3, 4 (HTTP) | 1 | Nothing — static assets |
| `/api/auth`, `/api/auth/status`, `/api/auth/logout` | 3 | 1, 4 | These routes **are** the authentication mechanism |
| `/s/{token}` | 3 | 1, 4 | `routes/share.py` verifies the share token's signature, expiry and mode (device/pin) directly |
| `/p/authorize` | 3 | 1, 4 | `routes/system.py` checks session + device directly. On failure it redirects to `/?next=` rather than a JSON 401 — this is a top-level navigation, not an API call |
| `/api/peer/*` | 3 | 1, 4 | `routes/peer.py` verifies HMAC signature, time window and nonce replay directly. **A peer can only ever reach routes in that one file** |
| Requests that arrived on the unix socket (`scope["fsh_sock"]`, only the socket Gate can set it) | 3, 4 | 1 | `authz/sock.py` `Gate`: kernel peer uid == server uid, route allowlist, principal resolved from the peer pid. See «Unix socket». Human-only routes still refuse (`request_kind` is `none`) |
| `/api/tmux/kick` | 3 | 1, 4 | `routes/system.py` compares the `X-VT-Kick` header with a random secret made at server start (`tmux_hooks.py`) — only the server's own tmux hooks know it. The route only nudges the session-list watcher; it returns nothing (2026-09-30) |

The single most useful line in that table: **nothing turns off layer 4 (the IP
filter).** Right now that fact only exists as the difference between two exception
lists that a reader has to diff by hand.

### Two things this table makes visible (observed, not fixed here)

Writing the table down is the point — these two are visible now. Neither is a
vulnerability, and neither is being changed in this pass; they're recorded as
footnotes for whoever picks this up next.

**The cheapest gate sits innermost.** The IP filter (#4) runs *after* token auth (#3).
Both are AND gates so the end result is the same, but the path there differs: a
disallowed IP with no token gets a **401**, not a 403 — that response tells the
client "auth is turned on here" before the IP filter ever gets a say, and a
disallowed IP's request still runs the full session/HMAC check before being rejected.
Reordering them isn't done here without a reason — `/`/`/static`'s exception handling
would need to move in lockstep, and that changes what the login screen does before
anyone's looked closely at it.

**#3 and #3′ implement the same check twice.** HTTP re-runs `check_request` on every
request via the middleware; WS checks once at handshake plus a separate expiry
re-check. The 24-hour session hole described just below came directly out of this
duplication — whoever next adds a condition to #3 has to remember #3′ exists and
needs the same condition, because it's a replacement for #3, not a shortcut around it.

## Features

| Feature | Description |
|------|------|
| Web login password | Set via `fsh password` → stores only an scrypt hash (`VT_AUTH_PASSWORD_HASH`); the plaintext is never stored. On login, issues a 24h session cookie signed with `VT_AUTH_SESSION_KEY` (not the plaintext or a token). Human-facing auth. `server/auth/` |
| Device registration + OTP gate | Login is **always** by password. OTP is a gate required only "when registering a device seen for the first time." A registered device gets a `vt_device` long-lived cookie (90 days) and afterward passes with just the password — since it's per-device rather than per-IP, a phone switching between LTE and wifi doesn't get disconnected. **OTP stays fully disabled until `fsh otp setup`**, and device registrations quietly accumulate in the meantime, so turning it on later doesn't lock out devices already in use. Stored at `~/.vt/devices.json` (0600, sha256 hashes only). `fsh device revoke <id>` immediately invalidates that device's session cookie as well |
| Device lifetime (2026-10) | Each record fixes an absolute `expires_at` at registration: 90 days by default, or `24h`/`1h` picked on the login screen (`remember`), or 1 day via `fsh mobile --temp`. Nothing can make it longer than `DEVICE_TTL`: unknown `remember` values are 400, a registered device ignores `remember`, and `register_device` clamps. Older records without the field use `added_at + DEVICE_TTL`, so their behaviour is unchanged. Expired devices stay listed as expired for 7 days (`DEVICE_EXPIRED_GRACE`), then the next write drops them along with their `device-settings/<id>.json`; `fsh device prune` drops them now |
| Device metadata | `ua` (≤300 chars), parsed `client` (OS + browser major), `ip_first`/`ip_last`, `via`, `method` (`password`/`password+otp`/`ticket`). **Display only — never an auth input.** Tunnel requests arrive from loopback, so the IP comes from `CF-Connecting-IP`, read only when the request comes from loopback; a local process could forge it, which is why it stays out of the IP filter (`VT_TRUST_PROXY` is a separate switch). `ip_last` updates at most once a day, like `last_seen` |
| One-time device registration ticket | The QR/URL from `fsh mobile`/`fsh handoff` carries a 5-minute one-time ticket (`?ticket=`) instead of a persistent token. Physical access to the Mac is already proven at the moment the QR is shown, so scanning it equals approving registration. The old approach of embedding a persistent token in the URL left that value permanently sitting in logs, history, and QR images |
| Cross-site blocking | `OriginGuardMiddleware` (`server/main.py`) — returns 403 for both HTTP and WS if the Origin isn't itself. The only path that auth/OTP alone can't block (if the browser already has a cookie, auth passes). Also removes the default `*` CORS — opt in via `VT_ALLOWED_ORIGINS` if needed |
| API token auth | The `VT_AUTH_TOKEN` environment variable is a machine-facing token (daemons, hooks, the TUI). Via URL `?token=xxx` or `Authorization: Bearer xxx`. **It is not a second password — the login form rejects it (2026-09-17).** It used to be accepted there, which meant a human-usable credential that `fsh password` could never change or expire; the observed symptom was "I changed the password and the old one still logs me in". A legacy `?token=` link therefore no longer trades itself for a session cookie (it keeps working via the query parameter); register phones with `fsh mobile`'s one-time ticket instead. (Legacy names `VT_TOKEN`/`VT_PASSWORD_HASH`/`VT_SECRET_KEY` are still recognized as fallbacks) |

## Who is calling: browser session, machine token or peer (2026-10-06)

Authenticated does not mean human. The machine token (`VT_AUTH_TOKEN`) is readable by every agent process on the Mac
(`~/.vt.env`, environment), so anything that must be decided **by a person** cannot accept it. `auth.request_kind(request)` tells
the three apart — `session` (a valid signed `vt_session` cookie and **no** `Authorization` header or `?token=` alongside it),
`machine` (Bearer, `?token=`, or the token carried as a cookie value), `none` — and the middleware leaves the result on
`request.state.auth_kind` (diagnostic only). The gate itself is the FastAPI dependency `auth.require_human_session`, attached to
the **whole router** (`human_router` in `routes/agent_share.py`) rather than per handler: machine token, peer signature, a bad
cookie and an unauthenticated call all get **403 `human_session_required`**. A server with no password set cannot prove anyone is
human, so it is 403 there as well.

What this protects: sharing a session with agents, approving/denying an agent's access request, revoking a grant and reading the
access log (`API.md` «Agent session sharing»). The agent-facing endpoints of the same feature take the machine token plus a
pane/pid identity check but can approve nothing. Peers never reach any of it: `/api/peer/*` has no route for it and
`relay_policy.py` does not allow the paths. Tests: `server/tests/test_agent_share.py` (machine token on every human endpoint,
cookie + token together, no-password server, peer-signed requests, relay allowlist).

## Permission vocabulary: 17 scopes plus conditions

The engine (`server/authz/`) decides with a small vocabulary. A **scope** names a *kind of power*; the details live in the
resource selector and in **conditions** written per scope in the grant (`sconds`). Grants without a condition use a
least-privilege default; a decision whose context lacks a value a scope requires is **denied** (`cond_missing`), exactly like an
unknown scope or resource.

| Scope | Meaning | Conditions (`sconds`) | Default when absent | Context value the call site must give |
|---|---|---|---|---|
| `session.view` | list, name/cwd/origin, attach view | - | - | - |
| `session.read` | capture, live output, scrollback search | - | - | - |
| `session.input` | send input to a session | `modes` line / raw; `via` direct / queue | `modes=[line]`, `via=[direct]` | `mode` (required) |
| `session.manage` | create / kill / rename / detach | `ops` | `ops=[]` (explicit) | `op` (required) |
| `msg.send` | deliver to an inbox | - | - | - |
| `deleg.spawn` | create a sub-session | quota | - | - |
| `deleg.control` | parent: send/close; child: report | `role` parent / child | `role=[]` (explicit) | `role` (required) |
| `fs.read` | read through the FarShell API | `path_glob` | - | - |
| `fs.write` | write / commit / drop | `ops` write / commit / drop | `ops=[write]` | `op` (required) |
| `browser` | open, navigate, read, input, close | `ops`, `url_allowlist` | all five ops | `op` (required) |
| `browser.js` | run script | - | elevated | - |
| `browser.sensitive` | cookies, storage, network, download, attach | `ops` | `ops=[]`, elevated | `op` (required) |
| `relay` | use another Mac's relay (one value per allow-listed group) | `groups` | `groups=[]` (explicit) | `group` (required; `*` asks "any group") |
| `authz.request` | ask for a permission | - | agents | - |
| `authz.admin` | decide, issue, revoke, read the audit | - | humans only | - |
| `pair.manage` | create / end a pair | - | - | - |
| `agent.report` | status events of the caller's own pane | - | hook / agent on its own pane | - |

`ping` and `api.access` are internal meta checks of the authentication layer, not part of the vocabulary. Reading one's own
sent/received message history is always allowed to the participants and is **not** a scope (others' conversations never are).
Implications are explicit and condition-aware: `session.input` with `raw` also allows `line`; `fs.write` with `write` also allows
`fs.read`. A grant may use wildcards (`session.*`, `*`) which expand over this vocabulary.

**Elevated approval** (a re-authenticated human) is decided from (scope, conditions), for any non-human principal: `session.input` with
`raw`; `relay` with a group in `files.write`, `git.write`, `queue`, `ws.term`; `browser.js`; `browser.sensitive` (any op); for a
peer, any scope beyond `session.view`/`session.read`; a remote `deleg.spawn`. A change is an **escalation** when the new effective set
is not a subset of the old one - a new scope, or any condition value added (`modes`, `ops`, `groups`, `role`), a wider selector, a later
expiry; escalations are written to the audit row.

**Presets** (the only thing the approval UI shows; details appear in the grant detail and the audit):

| Preset | Contents | Elevated |
|---|---|---|
| View (보기) | `session.view`, `session.read` | no |
| Collaborate (협업) | View + `msg.send`, `fs.read`, `browser` (`ops=[read]`) | no |
| Drive (운전) | Collaborate + `session.input` (`line`), `fs.write` (`write`), `browser` (all ops), `deleg.spawn` | no |
| Control (제어) | Drive + `session.input` (`line`,`raw`), `session.manage` (all ops), `relay` (all groups) | **yes** |

Defaults per principal: a human gets the whole local vocabulary except `browser.js`, `browser.sensitive`, `relay` (and
`authz.admin` unless elevated); an agent gets, on **its own** session, `session.view`, `session.read`, `session.input` (`line`),
plus `agent.report`, `authz.request`, its spawn quota and (flag-dependent) messages - `session.manage` and `raw` input need an explicit
grant; a peer gets only the internal `ping` until a grant exists; `cli` callers may only close delegations.

**Principal kinds (5)**: `human`, `local`, `agent`, `peer`, `legacy_token` (temporary). `local` covers the CLI and the service
daemons (clipboard, voice, TUI, tmux notify …) with the attribute `name` = `cli` or `svc:<name>` (attribution only - same user, not a
boundary); ids are `local:cli:uid<uid>:pid<pid>` and `local:svc:<name>`. A hook script running in an agent pane is the same `agent`
principal with the attribute `via=hook`. An unauthenticated caller and a pair are not principals (a pair only builds grant bundles
for member principals).

**Storage and rollback.** Grants and approvals are stored with `schema: 2` (the 17 scopes + `sconds`; the document `version` is 2).
Records stored with the earlier, finer names (67 of them) are converted **when read** by a fixed table (`scopes.LEGACY_TO_COARSE`,
tested for every old name) and written in the new form the next time that document is saved; merged names keep their values as a union
(for example `fs.drop` -> `fs.write` with `ops=[drop]`), so nothing is widened beyond the merged groups (list/meta/clients.view ->
`session.view`; read/stream -> `session.read`; admin/audit -> `authz.admin`; event/report -> `agent.report`). The audit chain's earlier
lines are never rewritten. Before the first overwrite of an old-form document the server makes a `pre-schema2` backup. `fsh migrate-auth
--status` shows the stored schema per document, `--verify` compares the store with the old files, `--backup`/`--restore <ts>` return the
exact old bytes. To roll back: revert the commit, then `fsh migrate-auth --restore <ts>`.

## Agent session sharing under the engine (`VT_AUTHZ_ENFORCE=share`)

Off by default, so shipping changes nothing. `VT_AUTHZ_ENFORCE` is a comma list (`share`, `deleg` and `msg` have an effect; unknown tokens are ignored and shown by `flags.unknown()`). With `share` on, `authz/share_svc.py` is the authority and
`agent_share.py` keeps only the mechanics (`read_pane`, `send_keys`, `check_send_*`, tmux options); its public functions
(`get_grant`, `request_access`, `decide`, `revoke`, `drop_target`, `list_state`, `snapshot`, `authorize`, `audit`) branch on the flag.

| Piece | Where | Rule |
|---|---|---|
| Decision | `engine.decide(agent:local/<sid>:<created>, session.read / session.input (mode=line), session:local/<key>)` | The principal is the pane identity `share_identity` verified; `X-Fsh-*` headers, body fields and any other claim are hints only. Ceiling = the session's share policy (`read` -> view+read, `readwrite` -> +direct `line` input, `full` -> +`raw`; `full` is defined and unused). Rate: 30 reads / 10 sends per minute per (agent, session) — `agent_share.READ_PER_MIN`/`SEND_PER_MIN` feed `ctx["limits"]` |
| Requests | `approvals.request` | Only if the engine allows `authz.request`; one pending per (agent, session, kind); a denial = 300 s cooldown per (agent, session, kind); 24 h expiry, 20 per agent, 200 total |
| Decisions | `approvals.decide` / `revoke` | Human principal only (`require_human_session` still guards the router); never wider than the request or the share mode; elevated reasons (for example `session.input[raw]`) need an elevated human (`elevation_required`; unreachable today because grants top out at `line` input); escalation (`scopes.escalation`) is written to the audit row. Auto-approval windows come later: the code never reads `windows.json` (`approvals.auto_approve()` is `None`) |
| Store | `~/.vt/authz/{grants,approvals,shares,meta}.json` | Records are exactly `migrate.translate_agent_*` output (ids `ag-<id>`, `ap-<id>`, origin `legacy:agent_grants:<id>`, new-form scopes), so `fsh migrate-auth --verify` reads dual-write as "no difference" and an engine record converts back to the old shape losslessly |
| Dual-write | `share_svc._txn` | Inside one authz transaction the old `agent_grants.json` is rewritten from engine state (lock order authz -> agent_grants, same as `migrate.restore`). If the old file changed since the engine last wrote it (flag was off, or hand edit) its records are **adopted** first (`migrate._sync`, by original id); a corrupt old file is never adopted and never overwritten without a `.corrupt-*` backup. Turning the flag off therefore leaves the old code exactly the state the engine had. Same-uid processes can edit either store (FarShell does not claim to stop a shell of the same user) |
| Share policy | `~/.vt/authz/shares.json` is the source of truth; tmux `@fsh_share_nick`/`@fsh_share_mode` are the display mirror, written together (`put_share` writes the store first) | Drift resolves to the **lower** level (nick from the store). Store has it, tmux does not -> not shared. tmux only -> not shared once the first snapshot is done (`meta.share_adopted`); the first time the flag is on the existing tmux shares are adopted. If you ran with the flag off and shared sessions, run `fsh migrate-auth --apply` before turning it on again, otherwise those shares count as unshared (safe side) |
| Audit | `~/.vt/audit/authz.jsonl` (0600, hash chain) | Changes (share, unshare, decide, revoke) are appended **before** the commit; failure -> 503 `audit_unavailable` and nothing is saved. Reads/sends/lists/requests are best effort. Successful `read`/`list`: first per (principal, resource) per 10 minutes plus `suppressedSince`. Never input, screen or message text. `GET /api/agent/share/audit` is served from it; the old `agent_access.log` is still written in full |
| Failure | engine exception on read/send | 503 `authz_unavailable`, access denied (not "fall back to the old decision" — the engine is the authority once the flag is on). Corrupt `grants.json`/`approvals.json`/`shares.json`/`meta.json` -> agent access fails closed (`store_corrupt`) until the next write backs the bytes up |

Not covered yet (the machine-token bypass): the machine token still reaches `/api/sessions/{id}/keys`, `/api/queue`, `/api/tmux/kill/*` directly, so a
process that holds the token can bypass the share grant on those routes. `server/tests/authz_char/test_char_agent_share_enforced.py`
pins that. Tests: `server/tests/test_authz_share_enforce.py` (lifecycle + dual-write parity, rollback, lying hints, revocation,
cooldown, limits, drift, audit), the whole `test_agent_share.py` and authz_char matrix also run with `VT_AUTHZ_ENFORCE=share`.

## Agent delegation under the engine (`VT_AUTHZ_ENFORCE=deleg`)

Off by default (shipping changes nothing); combine with `share` as `share,deleg`. With `deleg` on, `authz/deleg_svc.py` is the
authority for `delegation.py`'s decisions; the mechanics (tmux, queue, outbox, state transitions) stay in `delegation.py`. Public
status codes and bodies are unchanged except the three listed under «New refusals».

| Piece | Where | Rule |
|---|---|---|
| Spawn | `engine.decide(agent, deleg.spawn, deleg-quota:<agent>)` | Default rule `agent_spawn_quota` with conditions: open children < 3, depth 1 (a session that was ever a child cannot spawn), 5 per minute globally (the ledger's `spawns` list is the counter), `VT_AGENT_SPAWN`, and — new, only under the flag — **safe_mode** (`VT_SAFE_MODE`) |
| Parent control | `deleg.control` (`role=parent`) on `deleg:local/<id>` | `send_to_child`/`close`. Granted to the parent only (derived grant); a child, a sibling or another parent gets 404 `not_found` as before |
| Child report | `deleg.control` (`role=child`) on `deleg:local/<id>` | Granted to the child only. A session that is not a child: 403 `not_delegated` |
| Ownership ledger | `~/.vt/delegations.json` | Still written and still the source of truth for *who is parent/child*. Engine grants are **derived** from it by `migrate.translate_delegation` (same function as `fsh migrate-auth`): parent -> `deleg.control` (`role=parent`), child -> `deleg.control` (`role=child`), `approved_by: system:spawn`, ids `dc-<id>`/`dr-<id>`, origin `legacy:delegations:<id>:…`. Closed or orphaned delegation -> `state: ended`; deleted from the ledger -> deleted. An explicit `state: revoked` is sticky (re-deriving or `--apply` never revives it) |
| Dual-write | `delegation._locked()` / `_write()` | Under the flag the lock order is **authz lock -> ledger lock** (same as 3a and `migrate.restore`). Every ledger write first syncs the grants in the same critical section, then writes the ledger; if the grant store or its audit line cannot be written the ledger is not changed (503). On lock entry a ledger whose sha256 differs from `meta.deleg_ledger_sig` (flag was off, or a hand edit) is **adopted** (re-derived, one `deleg.adopt` audit line). Flag off: the authz store is not touched (instant rollback) |
| Closed delegations | `Verdict.ended` | An ended grant is denied by the engine, but if the ledger says the caller owned it the old state errors are returned (409 `delegation_closed`, `already`, 409 `already_reported`) — all of them are no-ops |
| Spawn directory | `deleg_svc.check_fs` | An explicit `cwd`, and the git toplevel a `worktree` is cut from, need `fs.read` on `fs:local/<realpath>`; the default rule `agent_spawn_fs_roots` only holds when `fsguard.resolve_under_roots` (VT_BROWSE_ROOTS + deny list, symlinks resolved) accepts the path. Outside -> 403 `cwd_outside_roots`. The default cwd (the parent pane's own directory) is not checked. File leases will replace this later |
| Folder-grant inheritance | ledger `item["fs"]`, derived `di-…` / `dw-…` grants | Spawned **without** a worktree: if the parent holds an active fs grant covering the folder, the child gets a *shared reference* (`inherit: true`, `inherit_from` = parent grant id, scopes/resource copied); no parent grant -> nothing is recorded. Spawned **with** a worktree: one grant `fs.write` on the new worktree path for the child only (`approved_by: system:spawn`); the parent's original-folder grant is **not** inherited. Nothing else is inherited (session read/input, messages, browser, …). The inherited record is revoked (`revoked_reason: parent_grant_ended`, audit `deleg.cascade`) at the next sync after the parent grant disappears, expires or is revoked; closing/orphaning the delegation ends all of them. **Records and cascade only — fs grants are not enforced yet (file leases come later).** |
| `fsh delegate close` | `delegation._cli`, `POST /api/delegations/{id}/close-by-cli` | With the flag on the CLI asks the server over the unix socket. The socket peer is the principal: outside a pane = `cli`, which the engine lets **close only** (`deleg.control` with `role=parent` and `ops:[close]`; it cannot send, spawn, approve or grant - a provisional policy); inside a pane = `agent`, parent only. A server error or denial is final (no direct write afterwards). Only if the socket is unusable (server not running, `VT_AUTHZ_SOCKET=0`, an older server) does the CLI write the ledger itself, still as principal `cli` through the engine and the audit. Same-uid processes can always edit the file directly |
| Audit | `~/.vt/audit/authz.jsonl` | `deleg.spawn/send/close/report/cwd/identify` (decision, principal, `sizes.chars`, `sha8`; task, follow-up and report text are content and are never stored), `deleg.grant/end/cascade/adopt` for grant lifecycle (written before the commit; failure -> 503 `audit_unavailable`). Every refusal is recorded. The old `agent_access.log` is not written for delegations while the flag is on |
| Failure | engine exception, corrupt `grants.json`/`meta.json` | 503 `authz_unavailable` for spawn/send/close/report (fail closed); the corrupt file is not overwritten; reads (`list`, `role`) still work |

New refusals (only with the flag on): 403 `cwd_outside_roots`, 403 `safe_mode`, 403 `spawn_denied` (no grant, should not happen), 503
`authz_unavailable`/`audit_unavailable`. Consequence: a person who runs `fsh delegate close` in a FarShell tmux pane that is not
the parent is treated as an agent and refused — use the web UI row menu or a terminal outside tmux. Tests:
`server/tests/test_authz_deleg_enforce.py` (derivation, red-team, spawn directory, inheritance, audit, rollback, close-through-server) and
`authz_char/test_char_delegation_enforced.py` (matrix with `EXPECTED_CHANGE` = the spawn-directory cases); `test_delegation.py`, the shadow
replay and the authz_char matrix also run with `VT_AUTHZ_ENFORCE=deleg` and `share,deleg`.

## Agent messages under the engine (`VT_AUTHZ_ENFORCE=msg`)

Off by default (shipping changes nothing); combine as `share,deleg,msg`. With `msg` on, `authz/msg_svc.py` decides, stores and audits
agent-to-agent messages; `agent_msg.py` keeps the mechanics (queue, history, hop counting, envelope). Turning the flag off restores the old
behaviour at once; the message permissions stay in `~/.vt/authz/` and apply again when the flag is turned back on.

| Piece | Where | Rule |
|---|---|---|
| Sender | `routes/agent_msg._verify_sender_sync` | The sender is the authenticated caller: over the unix socket the session whose pane the peer pid belongs to, otherwise `share_identity.verify_caller` (`X-Fsh-*` + the process that owns the loopback connection). The body's `pane`/`tmux`/`cwd` never choose the sender; a mismatch is only recorded (`msg.claim`, reason `claim_mismatch`, no content). A caller that cannot be verified gets 403 `identity_unverified` (so does a person using a browser session or the bare machine token — send from a pane). Unknown host/session is 404 first |
| Related sessions | `msg_svc.relation` | `msg.send` on `msg:local/<receiver key>` is allowed **without approval** between a delegation parent and its child and between two children of the same parent. Only open delegations (running, reported) count; closed or orphaned ones do not. Read from `delegations.json`, whether or not the delegation flag is on. A hook for sessions that were paired (`pair_related`) exists and returns False until pairing is built |
| Other pairs | `msg_svc.request_access` / `decide` | No permission -> a pending request (kind `msg`, origin `authz:msg:req:`) and HTTP 202. Only a person (browser login session) decides, from the same inbox as sharing requests; an agent, the machine token, a cli process or a peer cannot. Approval creates a grant for exactly (sending session -> receiving session) (`gm-<id>`, origin `authz:msg:grant:`, no expiry); revoking it makes the next send pending again. Denial starts a 5-minute cooldown (403 `request_denied`); pending requests expire after 24 h, 20 per agent and 200 in total. Both records are removed when either session no longer exists. Sessions are identified by tmux session id + creation time, so a new session with a reused name does not inherit anything |
| Receiving from another Mac | `routes/peer.py` | The peer principal `peer:<id>/obo:agent:<id>/-` needs `msg.send` on `msg:local/-`. The grant used is translated on the spot from `hosts.json` (`control` level + `agentMessages`), so it cannot drift from what `fsh host allow-agent` set; the level itself is still checked by the capability wrapper. The outcome equals the old `control` + `agentMessages` check for every combination. The body hash signature is untouched. Whether to accept a message is decided on the Mac that owns the receiving session; no request is created for a remote sender |
| Limits | `msg_svc.check_rate`, `agent_msg` constants | 20 messages per minute per sending host (the engine counts, `agent_msg._rate` is the counter, 429 `rate_limited`) and 10 hops per conversation (429 `hop_limit`) — same numbers and error order as before |
| Audit | `~/.vt/audit/authz.jsonl` | `msg.send` (ok / denied / unverified / rate_limited / forwarded), `msg.request`, `msg.claim`, and `msg.decide`/`msg.revoke` (written before the commit; failure -> 503 `audit_unavailable`, nothing saved). Only length (`sizes.chars`) and `sha8` of the text. Principal `agent:local/<session key>`; `agent:unverified` when the caller could not be verified |
| Failure | engine exception, corrupt `grants.json`/`approvals.json` | 503 `authz_unavailable` for sends that need a grant and for remote messages (fail closed); a related pair still works because it needs no stored grant |

What does not change with the flag: the machine token still reaches routes outside this feature directly (see the sharing section). Tests:
`server/tests/authz_char/test_msg_enforce.py` (relations, forged sender, unverified callers, grant lifecycle, cooldown, limits,
peer parity over all levels, rate/hop parity, rollback, shadow, `fsh migrate-auth --verify`, audit without text) and
`authz_char/test_char_agent_msg_enforced.py` (the matrix with the listed changes); `test_agent_msg.py`, the shadow replay and the authz_char matrix also run
with `VT_AUTHZ_ENFORCE=msg` and `share,deleg,msg`.

## Unix socket: who is calling without a token (2026-10-15)

A second uvicorn server on the same event loop listens on `<state dir>/run/fsh.sock` (default `~/.vt/run/fsh.sock`, directory
0700, socket 0600 set **before** `listen`). The caller is whatever the kernel says: peer uid (macOS `LOCAL_PEERCRED`, Linux
`SO_PEERCRED`) and peer pid (macOS `LOCAL_PEERPID`, Linux `SO_PEERCRED`), read in `connection_made` and put in
`scope["fsh_peer"]`. Nothing in the request is an identity (`X-Fsh-Pane`, body `pane`, `from` are hints).

| Step | Rule |
|---|---|
| Connection | Peer uid != server uid -> closed without parsing. Unreadable credentials -> 403 `peer_unidentified` |
| Self-test | At startup the server connects to itself and requires the pid/uid it read to equal its own. **Failure = fail closed**: the socket is torn down and removed, status `failed`. Until it passes, everything but the self-test path is 503 `socket_not_armed` |
| Ambiguity (socket stack only) | `Authorization`, a `vt_session` cookie, `?token=` or `X-Peer-*` on a socket request -> 400 `ambiguous_principal`. The TCP stack is unchanged |
| Route allowlist | Only the method+path pairs in-repo clients use (table in `API.md` «Unix socket»); anything else 403 `socket_route_not_allowed` |
| Principal | Peer pid's parent chain reaches a `pane_pid` of **our** tmux -> `agent:local/<sid>:<created>`; with `X-Fsh-Svc: hook` -> the same agent with the attribute `via=hook`. Otherwise (outside a pane) a declared service name -> `local:svc:<name>` (attribution only), else `local:cli:uid<uid>:pid<pid>`. A process inside a pane that declares a service stays an agent. **None of them is `human`** (no elevation, `request_kind` is `none`, so `require_human_session` refuses) |
| Fail closed | Peer already exited (pid reuse risk) -> 403 `peer_gone`; tmux unreadable -> 503 `identity_unavailable` |

Authorization in this phase is "equivalent to the machine token, only for the allowlisted routes": the middleware skips layers 3 and
4 for requests the Gate passed, and the routes decide as before. `share_identity.verify_caller` takes the socket peer pid
(`peer_pid=`) and never runs `lsof` for it; the TCP path still uses `lsof` and fails closed when it cannot find the owner.

Clients (`server/fsh_client.py`, `lib/fsh_client.sh`) prefer the socket and fall back to `VT_AUTH_TOKEN`: no socket file, stale
file, `VT_AUTHZ_SOCKET=0`, a non-loopback `VT_SERVER`, a socket that is not ours / not 0600, or a socket-layer rejection
(`socket_route_not_allowed`, `socket_not_armed`, `peer_unidentified`, `peer_gone`, `identity_unavailable`). A timeout after the
connection is **not** retried over TCP. Socket file name follows the port (`fsh.sock` for 7777, else `fsh-<port>.sock`) so
two servers sharing a state dir cannot be confused. `VT_AUTHZ_SOCKET_PATH` overrides the path (AF_UNIX path limit: 100 bytes).

Not opened (a warning is logged and clients use the token): `VT_AUTHZ_SOCKET=0`; WSL path under `/mnt/` (permissions do not
work there — keep the state dir on the Linux filesystem); path too long; another live listener on the path (never unlinked);
a non-socket file in the way (never deleted). Native Windows is unsupported: use the WSL wrapper.

Limits worth knowing: a process an agent daemonized (re-parented to pid 1) no longer reaches its pane and looks like `cli`; the
same-uid threat model still holds (an agent can read `~/.vt`, see risk 1 in the plan). `fsh doctor` and `fsh status` show the
socket state (listening, self-test, permissions) and **machine-token requests in the last 24h**, counted in
`~/.vt/authz/legacy_token_use.jsonl` (minute buckets of path + user agent + count; no query string, no content; 1 MB x 2 files).
`python -m authz sock-status [--json]` prints the same.

`server/hooks/tmux_client_notify.sh` no longer puts the token in the URL. It uses the socket; without one it passes
`Authorization: Bearer` through a curl config on a `/dev/fd` pipe (`curl -K`), so the token is never in argv/`ps`.
`server/tests/test_no_token_in_argv.py` fails on any script that runs curl with `token=` or a token variable in its arguments.

## A device's sessions die with the device

`session_device` used to check only that the bound device **still existed**. A device
that had expired kept its already-issued sessions alive for up to `SESSION_TTL` (24h) —
harmless at 90 days, but a 1-hour temporary device would effectively live 25 hours. Now
both the cookie check (`verify_device`) and the session check (`session_device`,
`session_elevated_until`) go through the same `_alive` judgement in `auth/devices.py`,
and `/api/auth` issues the session with `min(SESSION_TTL, device remaining)`. Open
WebSockets are closed by the existing 60-second `spawn_session_watchdog`, which re-runs
`check_request`. Any new place that trusts a device id has to use `find_alive_device`,
not `_find_device`.

## Boundary values beat stale env

The config rule is "environment wins over `~/.vt.env`", and for ports, paths and
instance isolation that is right. For **values that define a security boundary** it is
dangerous: a stale copy silently widens the boundary.

**It happened twice on 2026-09-17.** `VT_BROWSE_ROOTS` was narrowed to `~/GitHub` and
the server still served the whole home directory through the public tunnel, because the
shell that ran `fsh` still exported the old value. And a rotated `VT_AUTH_TOKEN` left
every already-running agent session's hook returning 401 — 490 of them, silently.

**How it works.** `BOUNDARY_KEYS` (`server/vt_env.py`, mirrored in `lib/vt_env.sh`) are
read from the file first. There are 69 direct `os.environ.get("VT_…")` call sites, so
instead of editing them the environment is normalised once at boot
(`apply_boundary_overrides()`).

> ⚠ **That call must come before `import auth`** — auth reads its values at import time.
> A test asserts the source order.

**Absence means different things for different keys.** A key absent from the file is
normally left alone, so one-off experiments still work. The exception is
`FILE_CLEARED_KEYS` (currently `VT_TUNNEL_HOOK`), where absence means "switched off" and
the value is removed from the environment — a hook deleted from the config kept running
from a stale export.

**Credentials are deliberately not in that set.** Clearing `VT_AUTH_TOKEN` because the
file lacks it would leave an env-only setup serving with no auth at all. A value that
lingers errs toward locked; a value that vanishes errs toward open.

`VT_CONFIG` is deliberately not a boundary key either: it chooses *which file* to read,
and isolated test servers depend on that.

`fsh status`/`doctor` say when a window's env was corrected, and `doctor` also compares
the **running server's** boundary values against the file — N28 only ever watched for
stale *code*.
