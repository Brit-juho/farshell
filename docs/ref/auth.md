# Auth, devices, boundary values

## Shared machine token (`VT_AUTHZ_ENFORCE=legacy`)

Off by default; combine as `share,deleg,msg,peer,legacy`. The machine token is readable by every process of your
user (`~/.vt.env`, the environment), so with the flag on it stops being a key to the whole API. Turning the flag off restores the default
behaviour at once. Without the flag, token use is only *reported* (`legacy_token_use.jsonl`).

**What counts as a machine-token request.** One authenticated by `Authorization: Bearer <token>`, by `?token=<token>`, or by the token
carried as the `vt_session` cookie value. Browser login sessions (signed cookie) and the unix socket are never machine-token requests.

| Rule | With the flag on |
|---|---|
| Scope | The token may only call the method+path pairs the unix socket already allows for in-repo clients (table in `API.md` «Unix socket»: hooks, the MCP fallback, clipboard/voice/TUI daemons, tmux notify, ports sync, agent message/shared/delegation endpoints, `queue/run`, `host-grants/*/change`). Anything else is **403 `legacy_token_scope`**. A WebSocket handshake with the token is refused at the handshake (no JSON body) and counted in `legacy_token_denied.jsonl`. Allowed requests are still counted in `legacy_token_use.jsonl`; refused ones in `legacy_token_denied.jsonl` (same shape: minute bucket, path, user agent, count; no query, no content) |
| Transport | `?token=` is refused with 400 `token_query_unsupported` and the token as a cookie value with 401 `token_cookie_unsupported`, whether or not the value is right. Only the `Authorization` header remains. Access logs also mask any query value whose name contains `token`, `ticket`, `otp`, `secret`, `password`/`passwd`, `apikey`/`api_key` or `auth` |
| Web app | `core/env.js` and the login gate do not read `?token=`: the parameter is removed from the address bar and the normal login (or `fsh mobile` link) is used |
| No password | If only the token is set (no `fsh password`) there is nobody who can approve. Features that need an approval — sharing a session, agent messages to an unrelated session, `fsh host allow-*` — answer 403 `approval_unavailable_no_password` instead of waiting forever, and `fsh doctor` and the server start log say so once. Grants that already exist keep working. A signed cookie on such a server is not a person either (`require_human_session` refuses it). A server with neither password nor token is unchanged |

The route check is made in the token middleware, before the route runs, so the answer for a person-only route changes from
`human_session_required` to `legacy_token_scope` for a token caller (still 403). Peer-signed requests, the exempt paths in the exceptions
table below and the unix socket are not touched. `DELETE /api/tmux/kill/{name}` stays reachable because `fsh manage` uses it over the
socket and falls back to the token.

**Migrating an outside script.** `fsh doctor` (and `python -m authz legacy-denied`) lists the refused (path, user agent) pairs of the last
24 hours. For each: run it on the same Mac through the unix socket (`fsh` commands, or `curl --unix-socket ~/.vt/run/fsh.sock`, which
needs no token); use a browser login (`fsh mobile`) for anything a person does; or ask whether the route belongs on the allowlist
(`authz/sock.py` `ROUTES`, which then also opens it on the socket). A script on another machine cannot use the socket: pair the Macs
(`fsh host`) instead of sharing the token.

Tests: `server/tests/test_authz_legacy_enforce.py` (every allowlisted route works with the token, a sample of sensitive ones is refused,
`?token=` and cookie-token refusal, log masking, no-password gating, signed cookie on a token-only server, rollback, doctor output) and
`server/tests/authz_char/test_char_legacy_enforced.py` (the whole characterization matrix with the flag on; only the Scope, Transport and
No password rules change a server answer; the Web app rule is frontend-only).

## Authorization order

The table below shows the middleware order and the additional checks applied
at login, in routes and during terminal input. Token-auth exemptions do not
automatically exempt a request from the IP filter or resource checks.

**Starlette runs the *last*-registered middleware outermost** (`add_middleware`
prepends to `user_middleware`), so execution order is the **reverse** of the
registration order in `main.py`.

| # | Layer | Transport | Decides | Implementation | On failure |
|---:|---|---|---|---|---|
| −1 | Security headers `SecurityHeadersMiddleware` <!-- mw --> | HTTP responses | Not a gate — adds `X-Frame-Options: DENY` + `Content-Security-Policy: frame-ancestors 'none'` (no clickjacking of a terminal), `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, and a `Permissions-Policy` that turns off camera, geolocation, payment, USB, serial, Bluetooth and HID (microphone and clipboard stay). Outermost, so 401/403 refusals carry them too; a header a route already set (the file preview's `sandbox` CSP) is kept. **`Referrer-Policy` is `same-origin`, not `no-referrer`**, so same-site form POSTs stay checkable by `OriginGuardMiddleware`. A full CSP (`script-src`) is not set because `index.html` has inline scripts; that is future work | `server/security_headers.py` | — |
| 0 | Host allowlist `HostGuardMiddleware` <!-- mw --> | HTTP + WS | Is the `Host` a name an attacker cannot point at this Mac with DNS? IP literals, `localhost`/`*.localhost`, single-label names and `*.local`, `*.ts.net`, `*.trycloudflare.com`, ngrok domains, `VT_TUNNEL_HOSTNAME`, `VT_NGROK_DOMAIN`, the hosts of `VT_ALLOWED_ORIGINS`, and `VT_ALLOWED_HOSTS` (comma list, `*.example.com` allowed). No `Host` passes (non-browser). **DNS rebinding** makes `Origin` and `Host` both the attacker's name, so #1 passes it — this layer refuses the name itself | `server/host_guard.py` | 403 `host_not_allowed` (logged once per host per hour) |
| 1 | Origin guard `OriginGuardMiddleware` <!-- mw --> | HTTP + WS | Is the request's origin this server itself? No `Origin` header (non-browser) passes; `""`/`"null"` (opaque origin) is rejected | `server/main.py`, `OriginGuardMiddleware` | 403 `cross_origin` |
| 2 | CORS (opt-in) `CORSMiddleware` <!-- mw:cors --> | HTTP | Is this an explicitly allowed external origin? If `_allowed_origins()` is empty, **this layer doesn't exist at all** | `server/main.py` (conditional `add_middleware`) | Browser can't read the response body |
| 2′ | Pre-auth failure limit `PreauthMiddleware` <!-- mw --> | HTTP | Is this source locked (10 failed credentials in 60 s → 5 minutes), or is a credential-exchange endpoint (`/api/auth`, `/api/peer/pair`) over its global 300/min? Never applied to unix-socket requests | `server/main.py`, `PreauthMiddleware` → `server/authz/preauth.py` | 429 `rate_limited` + `Retry-After` |
| 3 | Token auth `TokenAuthMiddleware` <!-- mw --> | **HTTP only** | Is the session cookie / `?token=` / `Bearer` valid? If `auth.is_protected()` is false, everyone passes | `server/main.py`, `TokenAuthMiddleware` → `auth.check_request` | 401 `unauthorized` |
| 3′ | WS handshake auth | **WS only** | Same question as #3, done by the route itself at handshake time + a separate expiry re-check | route-level `_ws_auth` (e.g. `routes/pty.py`) | Handshake refused |
| 4 | Network access `NetworkAccessMiddleware` <!-- mw --> | HTTP + WS | Does the client IP fall inside `VT_NETWORK_MODE`'s CIDR ranges? | `server/main.py`, `NetworkAccessMiddleware` | 403 `ip_not_allowed` |
| 5 | Device + OTP | Login time | Is this a registered device? Does the TOTP code match? **Not a middleware** — checked directly by login and a few routes | `server/auth/devices.py`, `server/auth/totp.py` | Login refused / per-route |
| 6 | Per-resource verification | The route itself | Share-link token signature/expiry/mode · port PIN · port session · peer HMAC + time window + nonce | `routes/share.py`, `routes/system.py` (`/p/authorize`), `routes/peer.py` | Per-route |
| 7 | File boundary (`fsguard`) | The route itself | Root resolution → `resolve()` + `is_relative_to` → deny list | `server/fsguard.py` (3 layers) | 403 / 404 |
| 8 | Dangerous command (`safe_mode`) | PTY input · queue | Does it match a dangerous pattern? Only when `VT_SAFE_MODE=1` | `server/safe_mode.py` | Input blocked / queue `blocked` |
| 9 | Elevated session | The route itself | Git-account writes etc. require re-confirming the password | `server/auth/__init__.py`, `require_elevated` | Elevation required |

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
| `/api/health/live` | 3 | 1, 4 | Nothing — it returns only `ok`/`pid`/`uptime`. The detailed `/api/health/ready` stays behind auth (it names paths and operations) |
| `/s/{token}` | 3 | 1, 4 | `routes/share.py` verifies the share token's signature, expiry and mode (device/pin) directly |
| `/p/authorize` | 3 | 1, 4 | `routes/system.py` checks session + device directly. On failure it redirects to `/?next=` rather than a JSON 401 — this is a top-level navigation, not an API call |
| `/api/peer/*` | 3 | 1, 4 | `routes/peer.py` verifies HMAC signature, time window and nonce replay directly. **A peer can only ever reach routes in that one file** |
| Requests that arrived on the unix socket (`scope["fsh_sock"]`, only the socket Gate can set it) | 3, 4 | 1 | `authz/sock.py` `Gate`: kernel peer uid == server uid, route allowlist, principal resolved from the peer pid. See «Unix socket». Human-only routes still refuse (`request_kind` is `none`) |
| `/api/tmux/kick` | 3 | 1, 4 | `routes/system.py` compares the `X-VT-Kick` header with a random secret made at server start (`tmux_hooks.py`) — only the server's own tmux hooks know it. The route only nudges the session-list watcher; it returns nothing |

The single most useful line in that table: **nothing turns off layer 4 (the IP
filter).** Otherwise that fact only shows as the difference between two exception
lists that a reader has to diff by hand.

### Two things this table makes visible (observed, not changed)

Writing the table down is the point — these two become visible. Neither is a
vulnerability, and neither is changed by the table; they're recorded as
footnotes for whoever works on this next.

**The cheapest gate sits innermost.** The IP filter (#4) runs *after* token auth (#3).
Both are AND gates so the end result is the same, but the path there differs: a
disallowed IP with no token gets a **401**, not a 403 — that response tells the
client "auth is turned on here" before the IP filter ever gets a say, and a
disallowed IP's request still runs the full session/HMAC check before being rejected.
Do not reorder them without a reason — `/`/`/static`'s exception handling
would need to move in lockstep, and that changes what the login screen does before
anyone's looked closely at it.

**#3 and #3′ implement the same check twice.** HTTP re-runs `check_request` on every
request via the middleware; WS checks once at handshake plus a separate expiry
re-check. The 24-hour session gap described under «A device's sessions die with the device» came directly out of this
duplication — whoever next adds a condition to #3 has to remember #3′ exists and
needs the same condition, because it's a replacement for #3, not a shortcut around it.

## Every route declares its authorization class

Who may call a route is written down once, in one table: `server/authz/route_table.py`. Every HTTP and WebSocket route of the app, including
the static mount, has exactly one entry saying which class it belongs to, so a route cannot ship without somebody having decided who may call it.

| Class | Meaning |
|---|---|
| `public` | Open before login (the paths in «Exceptions» above). The entry says what checks the caller instead of the token middleware |
| `human` | A logged-in person only; `elevated` when the route also needs the re-confirmed password. Never on the unix-socket or machine-token allowlist |
| `socket` | In the unix-socket allowlist (`authz/sock.py` `ROUTES`); the entry records which allowlist line opens it. The machine-token scope of `VT_AUTHZ_ENFORCE=legacy` is the same list |
| `legacy_token` | On the machine-token allowlist but not the socket one (none today: the two lists are one) |
| `peer` | Another Mac signing with its pairing secret (`/api/peer/*`); the entry names its row in `peer_svc.NEEDS`, which holds the required scope and conditions |
| `relay` | Host-switch relay, both the receiving side (`/api/peer/relay*`) and the browser-facing side (`/api/hosts/{id}/relay`, `/ws/hosts/{id}/relay`) |
| `authenticated` | Any authenticated caller (cookie, machine token or socket); anything finer is logic inside the route. If the route asks the engine, the entry names the scope and conditions |

**Adding a route means adding its entry.** `server/tests/test_authz_route_table.py` walks the app's real route tree (included routers, WebSocket
routes, mounts) and fails when a route has no entry, when an entry has no route, or when the declared class disagrees with the real gate: a
`human` route that the socket or machine-token allowlist opens, a `socket` route missing from the allowlist, an allowlist line for a route the
app does not have, a peer route without a `peer_svc.NEEDS` row, a path on the token-middleware exempt list that is not `public`. The failure
message prints the line to add. The table changes no behaviour: the real gates (router dependencies, the middleware exempt list in `main.py`,
`sock.ROUTES`, `peer_svc.NEEDS`) stay authoritative and the test only checks that the table says the same thing.

## Features

| Feature | Description |
|------|------|
| Web login password | Set via `fsh password` → stores only an scrypt hash (`VT_AUTH_PASSWORD_HASH`); the plaintext is never stored. On login, issues a 24h session cookie signed with `VT_AUTH_SESSION_KEY` (not the plaintext or a token). Human-facing auth. `server/auth/` |
| Device registration + OTP gate | Login is **always** by password. OTP is a gate required only "when registering a device seen for the first time." A registered device gets a `vt_device` long-lived cookie (90 days) and afterward passes with just the password — since it's per-device rather than per-IP, a phone switching between LTE and wifi doesn't get disconnected. **OTP stays fully disabled until `fsh otp setup`**, and device registrations quietly accumulate in the meantime, so turning it on later doesn't lock out devices already in use. Stored at `~/.vt/devices.json` (0600, sha256 hashes only). Every read-modify-write of it (register, revoke, rename, prune, the daily `last_seen`/`ip_last` update in `verify_device`) re-reads the file under one lock (`devices._locked` → `store_lock.exclusive` on `devices.json.lock`, which also excludes other threads and the `fsh device` CLI) and writes atomically; read-only checks read without the lock. Without it, a `last_seen` update running in a thread (the share route calls `verify_device` via `asyncio.to_thread`) saved its earlier snapshot and brought a just-revoked device back or dropped a just-registered one (`test_auth_devices_lock.py`). `fsh device revoke <id>` immediately invalidates that device's session cookie as well |
| Login lockout | Password and OTP failures lock **per source** for 10 minutes after 5 (`auth/lockout.py`). The source key is `authz_preauth.source_of` — the same as the pre-auth limit: a tunnel request arrives from loopback, so it is keyed by `CF-Connecting-IP` (rightmost `X-Forwarded-For` otherwise). It must not be the connecting IP: every tunnel visitor would share the `127.0.0.1` key, and anyone with the URL could fail 5 times every 10 minutes and keep the owner locked out. Rotating sources is bounded by a **new-device cap** across all sources (`NEW_DEVICE_FAIL_WINDOWS`: 20 per minute, 30 per hour, 429 `password_locked` with `scope: "new_device"`); a request carrying a valid `vt_device` cookie is exempt, so strangers filling the cap only delay a new device's registration. A local process forging the header only picks its own lock key. Elevation (`/api/auth/elevate`) uses the same per-source key. Tests: `test_auth_lockout_tunnel.py` |
| No server without auth | `lib/auth_bootstrap.sh` `_vt_ensure_auth`, called by `_start_server` (start, voice, clip, restart) and by `cmd_start` before the public-tunnel check. If `python -m auth status` reports no password and no token, it runs `python -m auth generate-password` (20 characters from a 31-letter lowercase/digit alphabet ≈ 99 bits, `xxxxx-xxxxx-xxxxx-xxxxx`), stores only the scrypt hash with `vt_env_set` (plus `VT_AUTH_SESSION_KEY` if missing) and prints the plaintext **once** to the terminal. Not a terminal (`[ -t 1 ]` false: updater, launchd, hooks) → nothing is created and the start fails, because the plaintext would land in a log. Every start path and network scope goes through this check (`--network all`, `fsh voice`/`fsh clip`, lan/tailscale/localhost). A machine token is not generated because the login form refuses tokens. `--force` only covers the case where no password could be created. `run_server.sh` (dev) is not covered — the server itself only warns, since isolated test servers run without a password. Tests: `test_auth_bootstrap.py` |
| Device lifetime | Each record fixes an absolute `expires_at` at registration: 90 days by default, or `24h`/`1h` picked on the login screen (`remember`), or 1 day via `fsh mobile --temp`. Nothing can make it longer than `DEVICE_TTL`: unknown `remember` values are 400, a registered device ignores `remember`, and `register_device` clamps. Older records without the field use `added_at + DEVICE_TTL`, so their behaviour is unchanged. Expired devices stay listed as expired for 7 days (`DEVICE_EXPIRED_GRACE`), then the next write drops them along with their `device-settings/<id>.json`; `fsh device prune` drops them immediately |
| Device metadata | `ua` (≤300 chars), parsed `client` (OS + browser major), `ip_first`/`ip_last`, `via`, `method` (`password`/`password+otp`/`ticket`). **Display only — never an auth input.** Tunnel requests arrive from loopback, so the IP comes from `CF-Connecting-IP`, read only when the request comes from loopback; a local process could forge it, which is why it stays out of the IP filter (`VT_TRUST_PROXY` is a separate switch). `ip_last` updates at most once a day, like `last_seen` |
| One-time device registration ticket | The QR/URL from `fsh mobile`/`fsh handoff` carries a 5-minute one-time ticket (`?ticket=`) instead of a persistent token. Physical access to the Mac is already proven at the moment the QR is shown, so scanning it equals approving registration. A persistent token must not be embedded in the URL: it would sit permanently in logs, history, and QR images. **Tickets carry a `purpose`**: the exposed-port `session` mode issues its 60-second tickets into the same `tickets.json`, and `/api/auth` must not accept any ticket — a port ticket (it travels in the exposed app's `/__fsh/cb?t=` URL) would register a 90-day device and give a shell without password or OTP. `consume_ticket(raw, purpose)` requires the purpose (`device` / `port-share`), and a ticket of the wrong purpose is refused **without being burned**. Records without the field are classified by their `share` binding. Tests: `test_auth_device_lifetime.py` (`test_port_share_ticket_cannot_register_a_device`), `test_port_proxy_auth.py` |
| Cross-site blocking | `OriginGuardMiddleware` (`server/main.py`) — returns 403 for both HTTP and WS if the Origin isn't itself. The only path that auth/OTP alone can't block (if the browser already has a cookie, auth passes). Also removes the default `*` CORS — opt in via `VT_ALLOWED_ORIGINS` if needed |
| API token auth | The `VT_AUTH_TOKEN` environment variable is a machine-facing token (daemons, hooks, the TUI). Via URL `?token=xxx` or `Authorization: Bearer xxx`. **It is not a second password — the login form rejects it.** Accepting it there would be a human-usable credential that `fsh password` could never change or expire ("I changed the password and the old one still logs me in"). A `?token=` link therefore does not trade itself for a session cookie (it keeps working via the query parameter, unless `VT_AUTHZ_ENFORCE=legacy` is on, see «Shared machine token»); register phones with `fsh mobile`'s one-time ticket instead. The web app itself does not read `?token=` from the address bar: it removes it and shows the normal login. (Legacy names `VT_TOKEN`/`VT_PASSWORD_HASH`/`VT_SECRET_KEY` are still recognized as fallbacks) |

## Who is calling: browser session, machine token or peer

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

Defaults per principal: a human gets the whole local vocabulary except `browser.js`, `browser.sensitive`, `relay` (with the `peer` flag a human also gets `relay` on other Macs' resources) (and
`authz.admin` unless elevated); an agent gets, on **its own** session, `session.view`, `session.read`, `session.input` (`line`),
plus `agent.report`, `authz.request`, its spawn quota and (flag-dependent) messages - `session.manage` and `raw` input need an explicit
grant; a peer gets only the internal `ping` until a grant exists; `cli` callers may only close delegations.

**Principal kinds (5)**: `human`, `local`, `agent`, `peer`, `legacy_token` (temporary). `local` covers the CLI and the service
daemons (clipboard, voice, TUI, tmux notify …) with the attribute `name` = `cli` or `svc:<name>` (attribution only - same user, not a
boundary); ids are `local:cli:uid<uid>:pid<pid>` and `local:svc:<name>`. A hook script running in an agent pane is the same `agent`
principal with the attribute `via=hook`. An unauthenticated caller and a pair are not principals (a pair only builds grant bundles
for member principals).

**Storage and rollback.** Grants and approvals are stored with `schema: 2` (the 17 scopes + `sconds`; the document `version` is 2).
Records stored with the older, finer names (67 of them) are converted **when read** by a fixed table (`scopes.LEGACY_TO_COARSE`,
tested for every old name) and written in the new form the next time that document is saved; merged names keep their values as a union
(for example `fs.drop` -> `fs.write` with `ops=[drop]`), so nothing is widened beyond the merged groups (list/meta/clients.view ->
`session.view`; read/stream -> `session.read`; admin/audit -> `authz.admin`; event/report -> `agent.report`). The audit chain's existing
lines are never rewritten. Before the first overwrite of an old-form document the server makes a `pre-schema2` backup. `fsh migrate-auth
--status` shows the stored schema per document, `--verify` compares the store with the old files, `--backup`/`--restore <ts>` return the
exact old bytes. To roll back: revert the commit, then `fsh migrate-auth --restore <ts>`.

## Agent session sharing under the engine (`VT_AUTHZ_ENFORCE=share`)

Off by default, so shipping changes nothing. `VT_AUTHZ_ENFORCE` is a comma list (`share`, `deleg`, `msg`, `peer` and `legacy` have an effect; unknown tokens are ignored and shown by `flags.unknown()`). With `share` on, `authz/share_svc.py` is the authority and
`agent_share.py` keeps only the mechanics (`read_pane`, `send_keys`, `check_send_*`, tmux options); its public functions
(`get_grant`, `request_access`, `decide`, `revoke`, `drop_target`, `list_state`, `snapshot`, `authorize`, `audit`) branch on the flag.

| Piece | Where | Rule |
|---|---|---|
| Decision | `engine.decide(agent:local/<sid>:<created>, session.read / session.input (mode=line), session:local/<key>)` | The principal is the pane identity `share_identity` verified; `X-Fsh-*` headers, body fields and any other claim are hints only. Ceiling = the session's share policy (`read` -> view+read, `readwrite` -> +direct `line` input, `full` -> +`raw`; `full` is defined and unused). Rate: 30 reads / 10 sends per minute per (agent, session) — `agent_share.READ_PER_MIN`/`SEND_PER_MIN` feed `ctx["limits"]` |
| Requests | `approvals.request` | Only if the engine allows `authz.request`; one pending per (agent, session, kind); a denial = 300 s cooldown per (agent, session, kind); 24 h expiry, 20 per agent, 200 total |
| Decisions | `approvals.decide` / `revoke` | Human principal only (`require_human_session` still guards the router); never wider than the request or the share mode; the approver must **already hold** the requested permission — `approvals.check_authority` asks the engine (`decide` with `ctx.approving`) for every (scope, condition) of the request on behalf of the approving human, so an elevated reason (for example `session.input[raw]`, `relay` groups that write, `browser.js`) is refused for a non-elevated human (`elevation_required`; unreachable today because share grants top out at `line` input) and a deny rule covering the approver is `approver_lacks_scope`; agent, hook, local cli, service, peer and machine-token callers are refused before that (`human_session_required`); **first decision wins** — a decided request stays as a resolved record (`authz:resolved:<kind>:<id>` in `approvals.json`, not a legacy origin, so it never reaches `agent_grants.json` or `--verify`) for 10 minutes: the same decision again returns the original result (`replayed: true`, no second grant or audit row), a different one is 409 `already_decided`, concurrent clicks are serialized by the store lock; the deny cooldown is unchanged; escalation (`scopes.escalation`) is written to the audit row. Auto-approval windows come later: the code never reads `windows.json` (`approvals.auto_approve()` is `None`) |
| Store | `~/.vt/authz/{grants,approvals,shares,meta}.json` | Records are exactly `migrate.translate_agent_*` output (ids `ag-<id>`, `ap-<id>`, origin `legacy:agent_grants:<id>`, new-form scopes), so `fsh migrate-auth --verify` reads dual-write as "no difference" and an engine record converts back to the old shape losslessly |
| Dual-write | `share_svc._txn` | Inside one authz transaction the old `agent_grants.json` is rewritten from engine state (lock order authz -> agent_grants, same as `migrate.restore`). If the old file changed since the engine last wrote it (flag was off, or hand edit) its records are **adopted** first (`migrate._sync`, by original id); an unreadable old file is never adopted and never overwritten: a change that would have to be projected onto it is refused (503 `store_unreadable`). Turning the flag off therefore leaves the old code exactly the state the engine had. Same-uid processes can edit either store (FarShell does not claim to stop a shell of the same user) |
| Share policy | `~/.vt/authz/shares.json` is the source of truth; tmux `@fsh_share_nick`/`@fsh_share_mode` are the display mirror, written together (`put_share` writes the store first) | Drift resolves to the **lower** level (nick from the store). Store has it, tmux does not -> not shared. tmux only -> not shared once the first snapshot is done (`meta.share_adopted`); the first time the flag is on the existing tmux shares are adopted. If you ran with the flag off and shared sessions, run `fsh migrate-auth --apply` before turning it on again, otherwise those shares count as unshared (safe side) |
| Audit | `~/.vt/audit/authz.jsonl` (0600, hash chain) | Changes (share, unshare, decide, revoke) are appended **before** the commit; failure -> 503 `audit_unavailable` and nothing is saved. Reads/sends/lists/requests are best effort. Successful `read`/`list`: first per (principal, resource) per 10 minutes plus `suppressedSince`. Never input, screen or message text. `GET /api/agent/share/audit` is served from it; the old `agent_access.log` is still written in full |
| Failure | engine exception on read/send | 503 `authz_unavailable`, access denied (not "fall back to the old decision" — the engine is the authority once the flag is on). Corrupt `grants.json`/`approvals.json`/`shares.json`/`meta.json` -> agent access fails closed (`store_corrupt`) and writes to that file are refused (see «Unreadable stores») |

Not covered (the machine-token bypass): the machine token still reaches `/api/sessions/{id}/keys`, `/api/queue`, `/api/tmux/kill/*` directly, so a
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
| Closed delegations | `Verdict.ended` | An ended grant is denied by the engine, but if the ledger says the caller owned it the old state errors are returned (409 `delegation_closed`, 409 `already_reported`, or 200 `{"already": true}` on a repeated close) — all of them are no-ops |
| Spawn directory | `deleg_svc.check_fs` | An explicit `cwd`, and the git toplevel a `worktree` is cut from, need `fs.read` on `fs:local/<realpath>`; the default rule `agent_spawn_fs_roots` only holds when `fsguard.resolve_under_roots` (VT_BROWSE_ROOTS + deny list, symlinks resolved) accepts the path. Outside -> 403 `cwd_outside_roots`. The default cwd (the parent pane's own directory) is not checked. With `lease` on, the cwd is also refused while another holder's write lease covers it (see «Folder leases») |
| Folder-grant inheritance | ledger `item["fs"]`, derived `di-…` / `dw-…` grants | Spawned **without** a worktree: if the parent holds an active fs grant covering the folder, the child gets a *shared reference* (`inherit: true`, `inherit_from` = parent grant id, scopes/resource copied); no parent grant -> nothing is recorded. Spawned **with** a worktree: one grant `fs.write` on the new worktree path for the child only (`approved_by: system:spawn`); the parent's original-folder grant is **not** inherited. Nothing else is inherited (session read/input, messages, browser, …). The inherited record is revoked (`revoked_reason: parent_grant_ended`, audit `deleg.cascade`) at the next sync after the parent grant disappears, expires or is revoked; closing/orphaning the delegation ends all of them. The fs grants are records plus cascade; what keeps another session out of the folder is the lease issued under `lease` (see «Folder leases» — the worktree grant stays as the engine-side record, the lease is the enforced layer). |
| `fsh delegate close` | `delegation._cli`, `POST /api/delegations/{id}/close-by-cli` | With the flag on the CLI asks the server over the unix socket. The socket peer is the principal: outside a pane = `cli`, which the engine lets **close only** (`deleg.control` with `role=parent` and `ops:[close]`; it cannot send, spawn, approve or grant - a provisional policy); inside a pane = `agent`, parent only. A server error or denial is final (no direct write afterwards). Only if the socket is unusable (server not running, `VT_AUTHZ_SOCKET=0`, an older server) does the CLI write the ledger itself, still as principal `cli` through the engine and the audit. Same-uid processes can always edit the file directly |
| Audit | `~/.vt/audit/authz.jsonl` | `deleg.spawn/send/close/report/cwd/identify` (decision, principal, `sizes.chars`, `sha8`; task, follow-up and report text are content and are never stored), `deleg.grant/end/cascade/adopt` for grant lifecycle (written before the commit; failure -> 503 `audit_unavailable`). Every refusal is recorded. The old `agent_access.log` is not written for delegations while the flag is on |
| Failure | engine exception, corrupt `grants.json`/`meta.json` | 503 `authz_unavailable` for spawn/send/close/report (fail closed); the corrupt file is not overwritten; reads (`list`, `role`) still work |

New refusals (only with the flag on): 403 `cwd_outside_roots`, 403 `safe_mode`, 403 `spawn_denied` (no grant, should not happen), 503
`authz_unavailable`/`audit_unavailable`. Consequence: a person who runs `fsh delegate close` in a FarShell tmux pane that is not
the parent is treated as an agent and refused — use the web UI row menu or a terminal outside tmux. Tests:
`server/tests/test_authz_deleg_enforce.py` (derivation, red-team, spawn directory, inheritance, audit, rollback, close-through-server) and
`authz_char/test_char_delegation_enforced.py` (matrix with `EXPECTED_CHANGE` = the spawn-directory cases); `test_delegation.py`, the shadow
replay and the authz_char matrix also run with `VT_AUTHZ_ENFORCE=deleg` and `share,deleg`.

## Folder leases and revocation cleanup (`VT_AUTHZ_ENFORCE=lease`)

A **lease** says «this path is mine for now». The API (`/api/leases`, `fsh lease`, MCP `lease_*`) is always there; the flag only
decides whether FarShell **refuses** writes that run into another holder's lease and whether revocations run the cleanup saga below.
Flag off = nothing that existed changes (byte-identical responses; the authz characterization matrix and `fsh migrate-auth --verify`
stay clean).

### What is enforced, what is only advisory

| | |
|---|---|
| **Enforced** (flag `lease`) | Writes FarShell performs for a non-human caller: `POST /api/git/stage`, `/unstage`, `/commit` (every staged path, renames count both names), files delivered to a session's working folder — `POST /api/upload?session_id=`, `POST /api/files/{id}/insert`, a peer's `POST /api/peer/file` with a session (the `fs.write` op `drop`) — and the working folder of `POST /api/delegations`. Refusal: **403 `lease_conflict`** `{holder, expires, op, paths, conflicts[]}` (holder = nickname only). The file-store deletes (`DELETE /api/files/{id}`, `POST /api/files/delete`) act on FarShell's own attachment store, which sits in hidden state and can never be leased, so they are not hooked |
| **Advisory** | Anything an agent does with its own tools (shell, editor, `git` run in its pane). The unix user's processes can ignore a lease, edit the lease file or skip the guard with `git commit --no-verify`. **A lease does not stop a program running as the same user** — same limit as the rest of the permission store: a process of the same user can edit it. Do not describe it as a lock |
| **Optional guard** | `fsh lease guard install` — a pre-commit hook that asks the server and refuses a commit that touches another holder's leased files. Server unreachable: **warning only, the commit goes through**, and one line (time, reason, file count — no paths) is appended to `~/.vt/authz/guard-skipped.log`; `fsh doctor` shows the last 24h count |
| **Humans** | Never blocked. A person writing inside someone else's leased path produces an `lease.override` audit line and a content-free `lease_notice` event on `/ws-workspace`; nothing is refused |

Callers FarShell cannot match to a holder (machine token without a verifiable pane, peers, a pane-less `cli`) count as «someone else»:
any write/exclusive lease over the path refuses them. A corrupt `leases.json` refuses non-humans with 503 `lease_unavailable`.

### The lease

`{id "ls-…", holder, resource "fs:local/<realpath glob>", mode read|write, exclusive, ttl, reason, created, expires, max_expires,
derived_from, deleg, grant_id, approved_via}` in `~/.vt/authz/leases.json` (0600, same lock and atomic write as the other authz documents).

| Rule | Detail |
|---|---|
| Canonical glob | The static prefix (path components before the first `*?[`) is `realpath`ed; `..` is refused; the prefix must be inside `VT_BROWSE_ROOTS` and outside the deny list and hidden-state folders (`fsguard`), literal `.git`/`.ssh`/… components and `.*` in the rest are refused. A path without glob characters means the whole folder. Not-yet-existing paths (a new worktree) are fine |
| Overlap | Conservative: static prefixes must be ancestor-related; literal vs glob uses `fnmatch` (ancestors included); glob vs glob overlaps when their leading literal text is compatible. When unsure it conflicts. The UI-facing wording is «may overlap» |
| Conflict | Another holder's **write × write** conflicts; write × read only when either is `exclusive`; read × read never. Same holder and same delegation lineage never conflict. Two non-exclusive write leases held by members of the same active pair do not conflict (`leases.same_pair()`; only while `VT_AUTHZ_ENFORCE` has `pair`). 409 `lease_conflict` `{holder, expires, conflicts[]}` |
| Time | Default 30 min, max 8 h (`ttl_too_long` above). Renewal re-sets the remaining time to the original ttl but never beyond `created + 8h` (not an escalation), never shrinks, and a derived lease follows its parent |
| Ends | At expiry; when the holder's session is gone (tmux unreadable = keep, as for grants); on admin revoke (`DELETE /api/authz/leases/{id}`); when the grant that allowed it is revoked/expired (`grant_id`); when its parent lease ends |
| Needs | `fs.write` (ops `write`) for a write lease, `fs.read` for a read lease, decided by the engine on `fs:local/<glob>` (a folder also matches `<folder>/*`). Without it: 403 `forbidden` with `need`. Humans have both by default; a pane-less `cli` has neither. System-issued delegation leases (`approved_via: system`) skip this |
| Limits | 30 lease changes per minute per principal (429 `rate_limited`), 50 leases per holder |
| Audit | `lease.acquire/renew/release/revoke/expire/override` in the hash chain; the `reason` text is never stored in the audit (length only); a failed audit write is 503 `audit_unavailable` and nothing changes. Denials go to the deny log |

### Delegation (inheritance)

Under `lease`, `POST /api/delegations`: a **same-folder child** gets a lease derived from every lease its parent holds over that folder
(`derived_from` = the parent lease, `approved_via: system`, expiry follows the parent) so parent, child and sibling children write together
while strangers are refused. A child spawned **with a worktree** gets its own **exclusive write lease on the new worktree path**
(system-issued, `deleg` = the delegation id) and does **not** inherit the parent's original-folder lease. Closing the delegation, the
child or parent session ending, or releasing the parent lease ends the child's leases (a parent release cascades to everything derived).
If the new worktree path is already under someone else's lease the spawn fails cleanly with 403 `lease_conflict`.

### Revocation saga (`server/authz/saga.py`)

When `lease` is on, **grant revoke** (`DELETE /api/authz/grants/{id}`), **panic** and **host deny-control/deny-agent** run a saga after the
existing revoke path (which is unchanged). Steps — each idempotent, each recorded in `~/.vt/authz/sagas.json` as it completes:
`mark_revoking` → `close_streams` (revoke bus) → `drop_queued` → `cancel_messages` → `close_delegations` (reason `revoked`) →
`release_leases` → `pair_grants` → `pair_leases` → `pair_messages` → `pair_delegations` → `pair_verify` (the five pair steps do nothing unless the saga is for an ended pair) → `verify` (re-reads every store; anything left restarts the saga from the top) → `mark_revoked` → `pair_finalize`. A server that dies midway
resumes at the next start and every 30 s (`lifespan` sweep); a step that keeps failing backs off and is `stuck` after 20 attempts
(`fsh doctor` reports unfinished sagas). Later features add their own steps with `saga.register(name, fn, before="verify")`.

Tests: `test_authz_leases.py` (canonical table, overlap table, conflict matrix, TTL/renew, liveness, human, routes),
`test_authz_lease_enforce.py` (each write route flag on/off, inheritance, saga crash injection at every step), `test_lease_guard.py`
(real `git commit` through the hook), `test_lease_cli_mcp.py`, `authz_char/test_char_leases.py`.

## Pairs (`VT_AUTHZ_ENFORCE=pair`)

A **pair** is two participants — two agent sessions, or one person and one agent — working on the same bundle of sessions and folders, one
as **driver** and one as **navigator**. A pair is **not a principal**: it never authenticates anyone and holds no power of its own. It is a
bundle of grants that a person approves once, tagged `pair_id` and removed together. The whole feature sits behind a master switch:
**without `pair` in `VT_AUTHZ_ENFORCE` every `/api/pairs` route answers `409 pair_disabled`, pair grants (if any are left in the store) have
no effect, and nothing else in the system changes** (responses are byte-identical; the characterization matrix and `fsh migrate-auth --verify`
stay clean). Pair grants are honoured by the enforcement points whose own flag is on — to use a pair end to end run with
`share,msg,lease,pair` (and `deleg` if the driver should spawn).

### The ceremony

1. **Create** (`POST /api/pairs`, `fsh pair start`, MCP `pair_create`). A person, or an agent that holds `pair.manage`, names the bundle —
   sessions, folders, whether the driver gets write leases on the folders, and which *elevated* extras to ask for — and the role the creator
   takes (the joiner gets the other one). An agent gets `pair.manage` the usual way: `request_access` on `pair:*` (this scope is requestable only
   while the pair switch is on; an automatic-approval window can never cover it). A participant reference is `{local: <key>}` or a session name;
   `{host, session}` for another Mac is `501 pair_remote_unsupported`.
2. **Code** (`POST /api/pairs/{id}/code`). An 8-character Crockford base32 code (`ABCD-EFGH`; `O`/`I`/`L` are read as `0`/`1`/`1`) and a
   two-word check phrase. Valid for 5 minutes, single use, at most 10 new codes per creator per hour. Asking again while a code is pending
   returns the **same** code (idempotent, not counted); creating a pair with a *different* bundle for the same creator and target
   immediately invalidates the creator's earlier unjoined pair and its code, so a narrow re-issue never leaves a wide old code alive.
   A wrong code counts against every pending code, and the fifth wrong attempt burns them (the creator issues a new one).
3. **Join** (`POST /api/pairs/join {code}`, `fsh pair join`, MCP `pair_join`). A verified agent session presents the code. The code is spent
   and a pending approval of kind `pair` (id `pq-…`) appears in the shared inbox. There is **no bearer token**: the joiner learns the outcome
   by calling `GET /api/pairs/{id}/status` as itself (socket peer pid chain) — a lost response never needs a re-issue.
4. **Approval** (`POST /api/authz/approvals/{id}/decision`, people only — an agent can never approve). One screen shows the bundle in words,
   both parties' check phrases, the preset each role gets and the elevated items asked for. Elevated items (raw key input, session
   management, relay, browser script/sensitive) are given **only if ticked** (`elevated: ["raw_input", …]`, a subset of what was asked) and
   ticking requires a re-authenticated session (`403 elevation_required` otherwise). `phrase` is optional; if given it must equal the check
   phrase (3 misses reject the request). Widening a pair's permissions later is always a new approval.

### Roles and handoff

| Role | Grants (on the bundle) |
|---|---|
| navigator | the «Collaborate» preset: `session.view`, `session.read`, `msg.send`, `fs.read` |
| driver | navigator plus `session.input` (line mode), `fs.write` (write), `deleg.spawn` from the member's own quota |

The preset never includes the `browser` scope; a pair member who needs the browser broker receives a grant for it separately. Elevated extras attach to whoever is the driver.
Grants are written per agent member on each bundle session (except the member's own), each bundle folder (`fs:local/<folder>/*`) and the
other member's messaging resource; they carry `approved_via: manual`, expire at the pair's end and skip the share-policy ceiling (the role
is the ceiling). A person member needs no grants — people already have the human defaults and can type whatever the role says, while
agent input keeps yielding to the existing human-typing guard.

**There is exactly one driver at any time.** Handoff: the driver offers (`POST …/handoff`), the navigator accepts (`POST …/handoff/accept`).
Accepting swaps the roles and rewrites the grants in one store transaction and increments the **generation** number; an offer older than
2 minutes, or made at an older generation, is refused and the pair stays as it was. A person who is not the current driver (a human
navigator, or someone watching) can take the wheel at once with the same call — that is the human override. Messaging: members of one
**active** pair may message each other without a separate approval (`VT_AUTHZ_ENFORCE=msg` relation `pair`). Leases: with `lease` on, the driver
holds a write lease on each folder, moved to the new driver on handoff; two non-exclusive write leases of members of the same active pair
never conflict (anyone else's still do).

### Ending

A pair ends on an explicit end (`DELETE /api/pairs/{id}`), a person's revoke (revoking any of its grants in the permissions screen, or
`DELETE` by someone who is not a member), idle time (`VT_PAIR_IDLE_MIN`, default 120 minutes; the member sessions' own activity counts, and it
is skipped while tmux cannot be read), a member session disappearing, the 8-hour maximum, or panic. Ending first turns the grants to
`revoking` (the engine refuses from that instant) and releases the pair's leases, then runs the **revocation saga**: cancel queued
messages between the members and their queue items, close delegations a member opened while the pair was in effect, verify that nothing
is left, delete the revoked grant records. It is idempotent and resumes after a crash (`fsh pair revoke <id> --verify` waits for it).

### Limits to state plainly

The permission store lives in the user's home directory. **A process of the same user can read and edit it** (and call the unix socket);
a pair does not stop that — it attributes who may do what, shuts accidents down early and makes tampering visible in the audit chain. The
check phrase protects against the wrong agent joining, not against the same account. Pair audit lines (`pair.create/code/join/approve/
handoff_offer/handoff/end`) carry counts, state and generation only — never session names, paths, codes or phrases.

Tests: `test_authz_pairs.py` (code format/TTL/single use/burn/rate with a fake clock, idempotent reuse and invalidation, approval ticks and
re-authentication, role presets, handoff incl. concurrent threads, relations, saga crash injection at every step, ending triggers, file
modes under `umask 022`), `test_pair_cli_mcp.py`, `authz_char/test_pairs_http.py`, `authz_char/test_char_pairs.py`.

## Agent messages under the engine (`VT_AUTHZ_ENFORCE=msg`)

Off by default: without `msg` in `VT_AUTHZ_ENFORCE`, message handling is unchanged. Combine as `share,deleg,msg`. With `msg` on, `authz/msg_svc.py` decides, stores and audits
agent-to-agent messages; `agent_msg.py` keeps the mechanics (queue, history, hop counting, envelope). Turning the flag off restores the old
behaviour at once; the message permissions stay in `~/.vt/authz/` and apply again when the flag is turned back on.

| Piece | Where | Rule |
|---|---|---|
| Sender | `routes/agent_msg._verify_sender_sync` | The sender is the authenticated caller: over the unix socket the session whose pane the peer pid belongs to, otherwise `share_identity.verify_caller` (`X-Fsh-*` + the process that owns the loopback connection). The body's `pane`/`tmux`/`cwd` never choose the sender; a mismatch is only recorded (`msg.claim`, reason `claim_mismatch`, no content). A caller that cannot be verified gets 403 `identity_unverified` (so does a person using a browser session or the bare machine token — send from a pane). Unknown host/session is 404 first |
| Related sessions | `msg_svc.relation` | `msg.send` on `msg:local/<receiver key>` is allowed **without approval** between a delegation parent and its child and between two children of the same parent. Only open delegations (running, reported) count; closed or orphaned ones do not. Read from `delegations.json`, whether or not the delegation flag is on. A hook for sessions that were paired (`pair_related`) exists and returns False unless pairing is enabled |
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

## Other Macs under the engine (`VT_AUTHZ_ENFORCE=peer`)

Off by default: without `peer` in `VT_AUTHZ_ENFORCE`, peer handling is unchanged. Combine as `share,deleg,msg,peer`. With `peer` on, `authz/peer_svc.py` decides what another Mac
(`/api/peer/*`) may do, `authz/host_gate.py` decides who may operate another Mac from this server (`/api/hosts/*`, `/ws/hosts/*`,
`/ws/remote/*`) and `authz/host_grant_svc.py` turns `fsh host allow-*/deny-*` into approval requests. Turning the flag off
restores the behaviour without the engine at once. Every level and route gives the **same answer and the same error body** as with `peer` off; the only differences are
the ones listed under "Operating another Mac from this server" below.

**What `control` means.** A Mac you set to `control` can type into every terminal here, create and kill sessions, receive files and use the
whole relayed API: it is a **remote shell** on this Mac, the same exposure as an SSH login. `view` can list and read sessions and search
scrollback (including the working directory and output) but cannot change anything. Granting `control` is a decision about a machine you
trust as much as yourself.

| Piece | Where | Rule |
|---|---|---|
| Authentication | `routes/peer_protocol.py` | The HMAC signature (body hash, time window, nonce, rate limits — all identical with `peer` off) only proves **which Mac** is calling: the principal `peer:<id>`. What it may do is decided by grants. |
| Grants | `peer_svc._snapshot` | Translated on the spot from `hosts.json` (`view` -> `session.view` + `session.read`; `control` -> also `session.input` raw, `session.manage` (create, kill, rename, detach), `fs.write` (drop only) and `relay` for every group; `agentMessages` -> `msg.send` for the agent principal), so a `fsh host` change takes effect at once and the stored copy can never be stale. Rules in the engine store (deny rules included) apply on top. |
| One declaration table | `peer_svc.NEEDS` | Each peer route lists the (scope, conditions) it needs in this single table; the capability wrapper reads it, and a test fails for any `/api/peer/*` route without an entry. An action missing from the table falls back to its old level, never to "allow". |
| Open sockets | `peer_svc.GrantWatch` | Input frames on `/api/peer/ws/{name}` re-check `session.input` (raw) per frame (cached until `hosts.json`/`grants.json` change), and every 2 seconds the grant is read again: a removed Mac, or one that lost view or input, is disconnected (close 4401) within 5 seconds. The relay socket uses the same 2-second check instead of the old 30 seconds. |
| Relay | `routes/peer_relay.py` | Each allowlist rule maps to a group (`scopes.RELAY_*_GROUPS`) and the engine is asked for `relay` with that `group`. The order and the error bodies are the same as with `peer` off: the level gate comes first (a `view` Mac gets 403 `level_required` even for an allowlisted GET), then the allowlist (403 `relay_forbidden`). A deny rule for one group turns only that group into `relay_forbidden`. |
| Failure | engine exception, corrupt `grants.json` | 503 `authz_unavailable` for a peer request (fail closed). |
| Audit | `~/.vt/audit/authz.jsonl` | An engine denial of a peer request is recorded (`peer.<action>`, one line per Mac/action/reason per minute); no request content. `host_store` keeps its own per-peer log (`fsh host log`). |

### Operating another Mac from this server

`/api/hosts/{id}/...` and `/api/hosts/{id}/relay/...` (plus `/ws/hosts/{id}/relay/...` and `/ws/remote/...`) make this server act on another
Mac with its stored secret. Before, any authenticated caller could use them, including the machine token and a verified agent. With the flag
on the caller needs the scope on `host:<id>`:

| Caller | Allowed |
|---|---|
| Person logged in through the browser | Everything (list, ping, search, create/kill/rename sessions, detach clients, relay every group) |
| Server without any password or token | Everything (that server is open by design) |
| `fsh` / service daemon over the unix socket (`local`) | The host list and ping only |
| Agent, machine token (`Authorization: Bearer`, `?token=`) | Nothing (403 `host_access_denied`, WebSocket close 4003) |

Used by the web app (host list on the rail and fleet screens, the remote tab/session actions, the whole host switch) — all of it is browser
traffic with the login cookie, so people see no difference. No `fsh` command calls these routes (`fsh host ping/pair/add` talk to the other
Mac directly). A browser opened through a legacy `?token=` link (machine token instead of the login cookie) loses the host switch; log in
with the password.

### Changing a Mac's access with `fsh host` — approval, not a file write

`fsh host allow-control|deny-control|allow-agent|deny-agent` must not write `hosts.json` directly, since any process with the same user (an agent
in a terminal) could then raise a Mac's access. With the flag on the command asks the running server over the unix socket
(`POST /api/host-grants/{id}/change`; the socket peer is the principal — `local` outside a pane, `agent` inside one):

| Change | Result |
|---|---|
| `deny-control`, `deny-agent` (lower access) | Applied at once and recorded in the audit chain (`peer.grant`); already in that state -> `noop` |
| `allow-control`, `allow-agent` (raise access) | A pending request (kind `peer`, origin `authz:peer:req:`) in the same inbox as sharing and message requests; nothing changes until a **person** (browser login session) approves. `allow-control` needs a re-authenticated (elevated) person because it grants raw input. Same request is merged; a denial cools down for 5 minutes; 20 pending per caller, 200 total |

A `fsh` process without a pane (`local`), an agent, the machine token and a peer can never approve. After approval the engine store and
`hosts.json` are both written (through the `set_grant_level`/`set_grant_agent` writers), so `fsh migrate-auth --verify`
stays clean. Without the flag, without the socket or against an older server the command writes `hosts.json` directly. Someone who
can edit `~/.vt` as the same user can still change the file by hand — the audit chain and `fsh doctor` are what detect that, not this route.
The command exits 0 when it only created a request; it says so and nothing has changed until the request is approved.

Tests: `server/tests/authz_char/test_peer_enforce.py` (declaration coverage, 2-second revocation, per-frame re-check, relay order and groups,
host scopes per caller, request/approve/deny lifecycle, CLI fallbacks, rollback, `--verify`) and `authz_char/test_char_peer_enforced.py` (the
matrix: peer, relay, machine-token groups identical; the listed host-operation changes). `test_char_hosts.py` and the host-switch half of
`test_char_relay.py` pin the default behaviour with the flag off.

## Other Macs, grant based (protocol v2, `VT_AUTHZ_ENFORCE=peer`)

Off by default: without `peer` in `VT_AUTHZ_ENFORCE` the `/api/peer/v2/*` routes answer 404 like a missing route, the sender adds no `X-Peer-Obo`/`X-Peer-Proto`
headers, `host` arguments are refused (409 `remote_disabled`), `tools/list` of the MCP server is unchanged and no new file is created (`peer_outbox.json`). Remote
delegation also needs `deleg`; joining a pair also needs `pair`.

**What changes for v1 peers: nothing.** A v1 Mac (no obo, no `X-Peer-Proto`) keeps its migrated `view`/`control` grant and every old route answers as before. The
signature string only grows when an obo is present (`…\nobo:<principal>` after the optional body hash, `host_store/signing.py`), so a signature without an obo is
byte-identical to the old one. Protocol v2 adds `proto: 2` to the ping answer (only when the flag is on).

**On behalf of (obo).** A signature proves which Mac calls (`peer:A`). To act for one of its agents the Mac adds `X-Peer-Obo: agent:<A's id>/<session key>`, which is
**part of the signature** and **A's voucher, not an authentication**: B builds the principal `peer:A/obo:agent:A/<key>` and decides by **its own grants** for that
principal, approved by a person on B (there is no approval on the asking side). A can only vouch for agents of its own id (`agent:<A>/…`). Swapping the obo,
adding one to an unsigned request or stripping it all fail authentication; a body hash is required on every v2 call. Dropping the obo can only **reduce** what a call
may do: grants for `peer:A/obo:…` do not apply to `peer:A`, while a host-level grant applies to both, and an agent route without an obo is 400. Replays are refused by
the nonce cache as before.

| Piece | Where | Rule |
|---|---|---|
| Gate | `routes/peer_protocol.capability_v2` | Flag off -> 404. Signature + required body hash + obo, 600 calls per minute per peer, an audit line per call (`peer_audit.log`). The handlers ask the engine; the gate never decides a permission. |
| Shared sessions | `authz/remote.py` | `GET /v2/session/shared`, `POST /v2/session/read\|send`. A remote agent only sees **shared** sessions; the share policy is the ceiling (`ceilings.applies` covers obo principals; host-level peer grants stay exempt). Reading needs a grant from a person on B (202 pending, then retry); typing a line needs a grant approved by a **re-authenticated (elevated)** person (for a peer everything beyond view/read is elevated). Read-only share: 403 at once. The grant is evaluated without a cache each call and again inside the pane lock before the first key. |
| Surface ceiling | `scopes.PEER_SURFACE_SCOPES`, `engine.decide` | An obo principal can use at most `ping`, `authz.request`, `session.view/read/input`, `msg.send`, `deleg.spawn/control` — **whatever grants exist**. Inbox/authz admin, pair management, session management, relay, browser and files are `surface_forbidden`. The relay allowlist (`relay_policy`) does not reach any v2, inbox, pair, delegation or agent-share route, and a second tunnel listener is **not** built: the ceiling is enforced in the engine. |
| Requests | `inbox.create_remote_request` | Same limits as local agents (6/min, 20 pending per principal, 200 total, 5 minute denial cooldown); only `session.view/read/input` and `deleg.spawn` are requestable. The request shows in the normal inbox (`kind: generic`, subject `peer:A/obo:…`); the approver check treats the requester as a peer (`approving_kind`). |
| Remote delegation | `delegation.py`, `authz/remote.py` | Parent and child ledgers on both Macs share the delegation id; the other side is recorded as `host` and a key `<host>/<key>` (a local tmux key never contains `/`). Remote spawn needs an elevated-approved `deleg.spawn` grant (`once` is consumed by the call), no cwd/worktree, this host's quota (3 open, 5/min) and `VT_AGENT_SPAWN`. Instructions and close go to `peer:<host>` through the outbox; reports travel the other way and are rebuilt by the receiver from its own ledger (`remote_seen` makes redelivery harmless). Control needs the engine's `deleg.control` for the derived obo grant **and** the ledger's key. |
| Pair | `authz/pairs.py` | An obo agent can join a pair created on B (`POST /v2/pair/join`); member principal `peer:A/obo:…`, no local session key. A pair code that names a specific local session cannot be used from another Mac. |
| Revocation | `authz/remote.py` | Revoking a grant given to a Mac's agent, removing a Mac (`fsh host rm`, `revoke-all`) or an emergency stop writes a signed notice to `~/.vt/peer_outbox.json` (0600) and tries once at once; it retries with backoff (5s … 1h, 30 tries) while the server runs. The receiver **never deletes on the notice alone**: it asks the sender (`GET /v2/authz/grants`) and accepts only if the sender answers authenticated and the grant is gone («unregistered» for the host scope); unreachable or 5xx = not accepted. Accepting closes its open proxied terminals to that Mac and the delegations with it. `fsh host rm` tells the Mac before it deletes its own route back, so an unreachable Mac at that moment learns from its closed stream instead. |
| Open terminals | `peer_svc.GrantWatch` | Every 2 seconds the sender's grant is read again and a removed or downgraded Mac is disconnected (4401); the receiving proxy closes the browser socket with it. Total from revoke to the other side's closed stream: under 5 seconds (tested end to end). |
| Secret rotation | `host_store`, `peer_client.rotate_sync` | `fsh host rotate <id>` replaces the secret of **my outgoing connection** on both sides; no expiry. Two steps so that a failure at any point keeps the previous secret: (1) the other Mac creates a new secret and holds it pending for 10 minutes (both secrets work), (2) I swap my file in one write and confirm with a request signed by the new secret, which promotes it there. Failures: step 1 fails -> nothing changed; confirm answered 401 -> I restore the previous secret; confirm lost (network) -> `rotation: "unconfirmed"` and the next `fsh host rotate` settles it (confirmed or rolled back). The last rotation time is on both sides (`fsh host list`) and in the audit (`peer.rotate` with `rotated_at`, `previous_rotated_at`). The other direction of a mutual pairing is rotated from the other Mac. The new secret travels in the response of a signed request, the same exposure as pairing; it needs `peer` on the Mac you run it on and on the other one. |

Not supported: a physically separate tunnel listener for the peer surface, remote `cwd`/worktree for delegation, shared-session
streaming for obo agents (agents poll `read`), and a direction-independent single-command rotation of both secrets of a mutual pairing.

## Browser broker (`VT_AUTHZ_ENFORCE=browser`)

Agents can open **isolated browser tabs** and drive them through the server: `server/browser/{broker,policy,tabs,config}.py`, adapters in
`server/browser/adapters/`, routes `routes/browser.py` (API: «Browser broker» in `API.md`), CLI `fsh browser`, MCP `browser_*`, Settings > Permissions > Browser.

**Master switch and consent.** Off by default. Without `browser` in `VT_AUTHZ_ENFORCE` the routes answer a plain 404, MCP tools are hidden (`whoami`
only announces `features.browser` when it is on), no file is created and no browser process is started. When on, each adapter is still off until a person (or the
terminal's `fsh`) turns it on in `~/.vt/authz/browser.json` (0600); the first time ever needs an explicit consent (`consent: true`, a real boolean). A broken or
unexpected settings file is read as **off** (a string `"true"` or `1` is off too) and is never overwritten. Agents cannot turn adapters on.

**Adapters** (`BrowserAdapter`: `capabilities()`, `profile`, open/list/navigate/snapshot/screenshot/click/type/eval/cookies/storage/network/close, plus `frame_urls`,
`settle`, `pop_blocked`, `blank`). `playwright` (isolated headless Chromium, one browser context per tab, one worker thread) and `cdp` (a headless Chromium it starts
itself with a throwaway profile under `~/.vt/browser/`, the DevTools port bound to `127.0.0.1`, never exposed by any route, and it never attaches to a running
browser) are real. The other four adapter slots (local CLI adapters) are placeholders that implement only the interface: `capabilities()` says
`available:false` with a reason and every operation is refused. Optional packages are imported lazily; a missing one shows as unavailable and never breaks the server.
**`profile=user` (attaching to the user's own Chrome) is not implemented**: an adapter that declares it is unavailable, `profile:"user"` and `attach` are 501
`unsupported_profile` (the `attach` permission check still happens first). Downloads are refused as well.

**Permissions.** Three scopes, resource `browser:local/new` (open, lists) or `browser:local/tab/<id>`: `browser` (ops open, navigate, read, input, close — nothing by default for agents;
a logged-in person has it), `browser.js` (`eval`; **nobody has it by default**, not people either; elevated approval), `browser.sensitive` (ops cookies, storage, network,
download, attach; elevated). Every operation asks the engine first; an engine error, a damaged grant store or an unreadable tab ledger refuses (fail closed). Optional
grant conditions: `conds.url_allowlist` (below) and `conds.values` (cookie/storage values). The relay group `browser` exists for other Macs but is **not** in the default
relay group list, presets or `*`: a grant must name it, and over a relay `eval`, cookies, storage and network are refused outright (values never cross a peer link).
Pairs do not give `browser` in their presets (a pair's «browser_js»/«browser_sensitive» elevated items still work on `browser:local/*`).

**Tab ownership.** The agent that opens a tab owns it. Someone else needs a grant that names that tab (`browser:local/tab/<id>` or `browser:tab:<id>`) or a grant
from the same pair; a wildcard `browser:local/*` is not enough. People and the terminal can list and close any tab but do not operate others' tabs.

**URL policy** — one function (`policy.check_url`) used before navigating, at every redirect hop, for subframes, and again after each click/type/eval/navigate:
the default (`url_allowlist: []`) allows only `about:blank` and `localhost`/`127.0.0.1`/`[::1]` on ports FarShell has exposed (the tunnel registry, minus dangerous ports such as
remote debugging). Denied outright: `file:`, `chrome:`, `chrome-extension:`, `devtools:`, `view-source:`, top-level `data:`/`blob:`, `javascript:`, other `about:`, `ws:`, `ftp:`,
credentials in the URL. RFC1918, link-local, CGNAT and metadata addresses (also written as decimal/octal/hex numbers or inside IPv6) need the **exact IP, CIDR or host:port** in the
allowlist — `*.example.com` never covers an IP or special name and a lone `*` is ignored. Names are normalized the way a browser does (IDNA/punycode, NFKC, `\`, percent-decoding,
trailing dot) and compared exactly, so look-alike domains do not match. A listed *name* is resolved and must not resolve to a non-public address unless that address is
listed too; this is one layer against DNS rebinding, **not a complete defence** (the browser resolves again). The allowlist comes from the caller's own `browser` grants.
In the browser, adapters block hops/subframes/subresources in the network layer; the broker re-reads every frame URL after an action (waiting briefly for late navigations), and
on a violation puts the tab back on `about:blank` and refuses (`url_blocked`). Sub-resources (images, scripts, XHR) are checked more loosely: public hosts pass, but loopback
(unexposed port), private, link-local and metadata targets are blocked unless listed. Tabs are addressed only by `snapshot` refs (`e1`…), never by selectors.

**Redaction.** Cookies and storage return names/domains/paths/flags only; values need `values:true` **and** a grant with `conds.values` (checked by the engine, so a
grant without it is refused with `cond_values`). `network` always redacts `Cookie`, `Set-Cookie`, `Authorization`, `Proxy-Authorization` and bodies over 64 KB. Audit lines never
hold values, script text, typed text, or URL query/fragment (origin + path hash only). Scripts and sensitive reads are not run if their audit line cannot be written.

**Limits.** Per tab: 60 operations/min in all, 30 navigations/min, 10 `eval`/min; 4 tabs per owner and 12 in all; an idle tab is closed after 60 minutes (a tab handed to a person is not).

**Failure and generations.** If an adapter goes down its open tabs become `orphaned` with the owner kept and every operation except close is refused
(`tab_orphaned`, 503). The server takes a host lease at start (`~/.vt/authz/tabs.json`, dir 0700, file 0600 with explicit chmod) and bumps a generation number; a tab from an earlier
generation, or with the lease held by another process, is refused — a restart orphans old tabs rather than adopting them. A damaged `tabs.json` refuses operations and is not overwritten.

**Revocation.** Revoking a grant, pair, subject or an emergency stop (with `lease` in `VT_AUTHZ_ENFORCE`, as for the other revocation steps) runs the saga step `browser_tabs`:
tabs are **not closed**, only their ownership is released (`state: released`; nobody can operate them, a person can close them). A tab the owner handed over with `browser_handoff`
is marked `humanTaken` and is never touched by revocation.

**Limits to state plainly.** The adapters are code in this repository driving a real browser: a page can still do what any web page can inside the allowed origins. The
broker keeps agents inside the policy; it does not make the allowed pages trustworthy, and a process of the same user account can read `~/.vt` directly. The real adapters
drive a real browser through the `playwright` package.

## Re-check right before typing

A decision made when a request arrives can be stale by the time the keys are sent: a grant can be revoked, a delegation closed or a
share lowered in between. With the matching flag on, the engine is asked **again inside the per-pane lock, before the first key**
(`tmux_target.send_to_tmux(..., guard=)`). Flag off: no re-check, and `send_to_tmux` is called exactly as before.

| Path | Flag | Re-check | Outcome when it no longer holds |
|---|---|---|---|
| Queue item from a delegation (task, follow-up: parent `deleg.control` role=parent op=send; result: child role=child op=report; turn-ended / needs-human / soft-limit notices: the delegation must exist and be open; the `gone` notice: it must exist) | `deleg` | `deleg_svc.recheck_queued` in `queue_runner` | nothing typed; the item becomes `blocked` with reason `grant_revoked`, `delegation_closed` or `relationship_gone`; audit `deleg.queue` (denied, no content) |
| Queue item from an agent message (local sender: `msg.send` with the relation or grant; peer sender: the `hosts.json` grant incl. `agentMessages`) | `msg` | `msg_svc.recheck_queued`; the sender identity travels in the queue item's `source.authz` (identity only, never text) | nothing typed; `blocked` with `grant_revoked`; the message record turns `blocked`; audit `msg.queue` |
| Direct input to a shared session (`/api/agent/shared/send`) | `share` | `agent_share.recheck_send`: fresh tmux snapshot + fresh store read, same decision as `authorize` but **without** counting the per-minute limit | 403 `grant_revoked`, nothing typed (also when the session is gone or the share was lowered to read-only) |

The engine failing or the store being unreadable at that moment closes (queue: `blocked` with the retriable reason `authz_unavailable`;
share: 503 `authz_unavailable`). `grant_revoked`/`delegation_closed`/`relationship_gone` are not retried automatically; unblocking the
item re-runs the check. Items queued while the flag was off carry no `source.authz` and are not re-checked.

## Child limits and ownership integrity

- **A child's grants are the intersection of the parent's grants and what is requested** (`scopes.child_grants(parent_grants, requested)`;
  delegation derives the child's folder share through it). These never pass to a child even if the parent holds them: `browser.js`,
  `browser.sensitive`, `session.manage`, `relay`, `authz.admin`, `pair.manage`, and `session.input` with `raw` (a child gets `line`
  at most). A child has no way to approve anything — approving needs a signed-in person's session, which no scope grants. A child keeps
  `authz.request` (its only way to ask the person); each request it creates records the open delegation it belongs to as a `delegation`
  field on the stored request, for display in the approvals inbox. The field does not appear in API responses and is not used in decisions.
- **The parent/child edge is immutable.** A write to `delegations.json` that changes a delegation's `parent.key`, its `child.key` (once
  filled at spawn) or its `id` is refused (`edge_immutable`) and audited. Adding delegations, filling the reserved empty child key once,
  state changes and removing old entries are unaffected.
- **Control is the strict parent only.** `send_to_child`/`close` work for the parent session of that delegation — not for the child
  itself, a sibling, another parent's child, a grandparent or any other session — whether or not `deleg` is on (with it on, the engine
  grant and the ledger's parent key must both agree). Depth is 1; if the depth limit is ever raised, control still stops at the direct
  parent. An entry whose parent equals its child is treated as not existing.
- **Ownership keys include the session creation time** (`<session_id>:<session_created>`). A session recreated under the same name, or a
  new session continuing a resumed/forked conversation, has a different key and neither takes over a parent's or child's role nor receives its
  hook notices. Tests: `test_authz_recheck.py`, `test_authz_child_limits.py`, `test_authz_ownership.py`.

## How a grant was approved: `approved_via`

Every grant record the engine writes carries `approved_via` next to `approved_by`: `manual` (a person decided it: session-share and
message approvals), `window` (an auto-approval window decided it — reserved; windows do not exist), `system` (the engine's own rules and
records derived from the delegation ledger) or `migrated` (copied from an older store by `fsh migrate-auth` or by adopting the
old `agent_grants.json`). A record without the field reads as `migrated`; re-running the migration never rewrites such a record just
to add it, and never replaces a `manual` value with `migrated`. Grant listings show it (`approvedVia`, see `API.md`).
It is provenance only, never authority: **a grant approved via `window` can never be used to open or extend a window**
(`engine.validate_window(..., basis=[the grants the caller relies on])` reports `window_derived_grant`), so any future window inherits the rule from the engine's guard function.

## Unreadable stores refuse writes

A store file that cannot be read or parsed (`~/.vt/authz/*.json`, and the old `~/.vt/agent_grants.json` that is still
mirrored) is **never overwritten**. Reads stay fail closed (agents, peers and the CLI are denied while any of `grants.json`,
`approvals.json`, `windows.json` or `shares.json` is unreadable; a human session keeps working). A change that would write the unreadable file is refused with
503 `store_unreadable` and nothing else in the same transaction is saved. Writes to other, healthy files are not blocked.
The broken file is left exactly as it was (never deleted, never repaired); a copy of its bytes is kept next to it as
`<name>.corrupt-<id>` (0600, one copy per distinct content). Fix or restore the file by hand (`fsh migrate-auth --restore <ts>`
restores a backup), and the next change goes through. `fsh migrate-auth --status` prints a «저장소 읽기 불가» line per unreadable
file (reason, path, number of copies) and `fsh doctor` reports the same as a failed check.

## Deny log and the chain

Denials (policy refusals, rate limits, lockouts, authentication failures) go to `~/.vt/audit/denials.jsonl` — not hash-chained,
0600, 1 MB × 3 files, at most 60 lines a minute plus a `denylog.dropped` count line, and a failed write never fails a request.
The chain `~/.vt/audit/authz.jsonl` keeps allows and every authz/pair/lease/grant change (a person's refusal of a request is a change,
so it stays). Which one a row goes to is decided in `audit.append` (`denylog.is_denial`: `decision == "deny"` and not a `.decide`/`.revoke`/
`.grant` … action). Existing denials in the chain are not moved. `GET /api/authz/audit` merges both and reports `chain: ok|broken` from a server-side verification.

## Pre-auth failure limit

`server/authz/preauth.py` counts failures before a caller has proven anything — an invalid cookie/token (not a correctly signed but
expired cookie, and not a request without credentials), a wrong login or one-time ticket, a failed peer signature or pairing ticket.
10 failures in 60 s from one source lock that source for 5 minutes (429 `rate_limited` + `Retry-After`; the login page stays open);
`/api/auth` and `/api/peer/pair` share a global 300/min cap. It is in memory (a restart clears it), the unix socket is exempt, and the
Origin check is not skipped for loopback. **Known trade-off:** behind a tunnel that sends no client-address header every remote user
shares one source key (the loopback address), so ten bad guesses can lock remote access (not the socket or the local `fsh`) for five
minutes. The `CF-Connecting-IP` and `X-Forwarded-For` headers are used when present.

## Unix socket: who is calling without a token

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

Authorization on the socket is "equivalent to the machine token, only for the allowlisted routes": the middleware skips layers 3 and
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

Limits worth knowing: a process an agent daemonized (re-parented to pid 1) does not reach its pane and looks like `cli`; the
same-uid threat model still holds (an agent can read `~/.vt`). `fsh doctor` and `fsh status` show the
socket state (listening, self-test, permissions) and **machine-token requests in the last 24h**, counted in
`~/.vt/authz/legacy_token_use.jsonl` (minute buckets of path + user agent + count; no query string, no content; 1 MB x 2 files).
`python -m authz sock-status [--json]` prints the same.

`server/hooks/tmux_client_notify.sh` does not put the token in the URL. It uses the socket; without one it passes
`Authorization: Bearer` through a curl config on a `/dev/fd` pipe (`curl -K`), so the token is never in argv/`ps`.
`server/tests/test_no_token_in_argv.py` fails on any script that runs curl with `token=` or a token variable in its arguments.

## Inbox and grants: one list, one decision route

`/api/authz/approvals` lists every pending request (shared-session access, agent messages, host changes from `fsh host allow-*`, and
`generic` requests an agent makes with `POST /api/authz/requests`); `/api/authz/grants` lists every grant. The decision and revoke
routes do not re-implement anything: they pick the module that owns the kind (`agent_share`, `msg_svc`, `host_grant_svc`, the delegation
ledger) and call its existing decide/revoke function, so first-decision-wins, idempotent replay, the approver-authority check
(a person can approve only what they themselves could do; elevated reasons need an elevated session) and the audit rows are the same
as through the old `/api/agent/share/*` routes, which stay as shims. API detail: `API.md` «Approval inbox, grants and audit».

| Topic | Rule |
|---|---|
| Who may call | Decide/revoke/panic: browser login session only (panic: elevated). Reads: login session, or the `fsh` CLI on the unix socket (a `cli` caller outside tmux) — the CLI never approves or revokes (`fsh authz revoke`/`panic` only point to the web/phone screen). An agent sees only its own requests and grants |
| What a person chooses | `once` (a grant with `conds.max_uses = 1`, counted where an enforcement point consumes it), `session` (while the requester's and the resource's sessions live), or deny (5-minute cooldown). The approver can only narrow scopes and time, never widen |
| Presets | The UI shows four bundles — 보기 (view), 협업 (collab), 운전 (drive), 제어 (control; elevated) — and the smallest bundle that covers a request or grant; scopes and conditions are on the detail view and in the audit |
| `generic` grants | Written to `grants.json` with origin `authz:grant:<id>` (never projected to the old files), honoured by the engine wherever an enforcement point asks. With every flag off they change no response |
| Requests by agents | `authz.request` is an agent default; 6 a minute per caller, 20 pending per caller, 200 in all, 24 h expiry, 5-minute cooldown after a refusal. `authz.request` and `authz.admin` cannot be requested; `pair.manage` can be requested only on a server with `pair` enforced. The agent's free-text `reason` is shown to the approver and never audited (length and sha8 only). A delegated child's request carries the parent delegation id |
| Events | `authz_request`, `authz_resolved`, `authz_revoked` on `/ws-workspace` with ids and kinds only; the old `agent_share_request*` events are still sent for one release |
| Panic | Revokes non-human grants through each kind's own revoke path, clears pending requests, lowers peer grants to view (the pairing stays) and closes open delegations; human defaults, share policy and logins are untouched. Audited per item plus one `authz.panic` row |

### Inbox, grants and audit screens

The web UI has one inbox for every kind. A decision window shows **one request at a time** (oldest first, no "allow all": it would approve
requests that arrive later); the requester (with delegation lineage), target and exact scopes/conditions are shown verbatim, and the request is
worded with the preset names. Choices are deny / once / this session; a kind that does not take once (`msg`, `peer`) shows the reason, a `share`
request that the server refuses with `choice_unavailable` keeps the window open with the server's reason. The old approval modal's guards are kept:
focus starts on deny, buttons are ignored for 400 ms after the window appears, Esc/backdrop/X mean "later" (nothing is sent). A request with
`elevated` reasons is marked and allowing it re-asks for the password (the existing elevate prompt) when the server answers `elevation_required`.
Events carry only ids and kinds, so the screen re-reads `GET /api/authz/approvals`; the old `agent_share_request*` events are used only until a
new `authz_*` event has been seen, and a burst of both causes one re-read. The pending count is a badge on the settings entry points (the FarShell menu button in the title bar, phone nav).

Settings > Permissions hosts the pending list, the grants list (filters go to the server; revoke asks for confirmation; built-in defaults are listed
separately and cannot be revoked), the audit timeline (reverse chronological, filters, optional denials, a chain badge that is neutral when `ok` and a
warning when `broken`; only whitelisted fields are drawn, never content) and the guarded emergency stop (two-step confirmation, then the elevate prompt).

### Pair approval, Pairs and Folder leases screens

A `pair` request uses the same decision window but one fixed layout, so everything needed to decide is on one screen: who is pairing with
whom (nicknames; a person without a name is shown as "you"), the bundle in words (sessions, folders, whether leases are taken), the preset name
each role gets (see/collab/drive/control, `… within` when the role is narrower than the preset) with the exact scope and conditions in a detail
line, **both check phrases side by side** with the instruction to compare them with what each agent displayed (a warning if the two differ), and
the elevated items as one checkbox each — **none is pre-ticked**. The decision button states what it grants (each member's role and preset plus
the ticked elevated items). Only ticking an elevated item leads to one re-authentication (the existing elevate prompt); allowing without ticks
sends `elevated: []` and never asks for the password. Deny keeps the default focus, buttons are ignored for 400 ms, Esc means "later", and there
is no "allow all". A server refusal (`phrase_mismatch`, `elevation_required`, `wider_than_requested`, `already_decided`) is shown with the
server's own reason and the window stays open; a refusal that closed the request (`phrase_mismatch_locked`, `member_gone`) is shown as a toast
and the request leaves the list. The window sits above the phone's bottom navigation, not under it.

Settings > Permissions has a **Pairs** section (state, members with roles, bundle, time left, swap count; end/revoke after confirmation; offer a
handoff when you are the driver, accept when you are the navigator, swap at once when a person is not a member; "show code" for a pending pair
you started, with the code, its check phrase and a live countdown) and a **Folder leases** section (holder, path glob, mode, exclusive, expiry;
release after confirmation; an inherited lease is a read-only marker and has no release button; a note says leases are advisory outside
FarShell's own APIs). A server with `pair` off answers `409 pair_disabled`, which is shown as one explanatory line rather than an error; an
empty list explains that a pair is started by an agent or `fsh pair start` and that the feature must be enabled. Pair grants in the grants list
carry a pair tag that scrolls to that pair; revoking one asks first because it ends the whole pair. Audit lines for `pair.*` and `lease.*` are
worded in plain text from counts and states only. The server emits no event when a pair is created, a code is issued or a handoff happens, so
the section re-reads after its own actions, on the `pair` approval and revoke events, and on its refresh button; `lease_notice` re-reads the
leases. Event bursts are coalesced per kind.

## A device's sessions die with the device

`session_device` must check that the bound device is **alive**, not just that it exists: otherwise a device
that has expired keeps its already-issued sessions alive for up to `SESSION_TTL` (24h) —
harmless at 90 days, but a 1-hour temporary device would effectively live 25 hours. So
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

**Failure modes this prevents.** If `VT_BROWSE_ROOTS` is narrowed to `~/GitHub` but the
shell that ran `fsh` still exports the old value, the server keeps serving the whole home
directory through the public tunnel. And a rotated `VT_AUTH_TOKEN` leaves every
already-running agent session's hook returning 401, silently.

**How it works.** `BOUNDARY_KEYS` (`server/vt_env.py`, mirrored in `lib/vt_env.sh`) are
read from the file first. There are over a hundred direct `os.environ.get("VT_…")` call sites, so
instead of editing them the environment is normalised once at boot
(`apply_boundary_overrides()`).

> ⚠ **That call must come before `import auth`** — auth reads its values at import time.
> A test asserts the source order.

**Absence means different things for different keys.** A key absent from the file is
normally left alone, so one-off experiments still work. The exception is
`FILE_CLEARED_KEYS` (currently `VT_TUNNEL_HOOK`), where absence means "switched off" and
the value is removed from the environment — a hook deleted from the config would keep running
from a stale export.

**Credentials are deliberately not in that set.** Clearing `VT_AUTH_TOKEN` because the
file lacks it would leave an env-only setup serving with no auth at all. A value that
lingers errs toward locked; a value that vanishes errs toward open.

`VT_CONFIG` is deliberately not a boundary key either: it chooses *which file* to read,
and isolated test servers depend on that.

`fsh status`/`doctor` say when a window's env was corrected, and `doctor` also compares
the **running server's** boundary values against the file (the code-staleness check only watches
stale *code*).
