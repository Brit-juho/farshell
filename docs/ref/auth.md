# Auth, devices, boundary values

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

## Authorization order (P2-5, ADR-42)

There are seven layers and, until this section, no document said what order they run
in — each layer's own reasoning was well-commented, but the relationship *between*
layers wasn't written anywhere. Three symptoms already showed why that's a problem:
the same "this bypasses standard auth, but X does its own check" justification was
written out **three separate times** in `server/main.py`'s exception list (once each
for `/s/`, `/p/authorize`, `/api/peer/`); which transport (HTTP/WS) each layer covers
was nowhere but the source; and the two exception lists (token auth vs. IP filter)
differ from each other in a way that's a deliberate defense-in-depth choice, not
something a reader could tell from just reading the code.

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
| One-time device registration ticket | The QR/URL from `fsh mobile`/`fsh handoff` carries a 5-minute one-time ticket (`?ticket=`) instead of a persistent token. Physical access to the Mac is already proven at the moment the QR is shown, so scanning it equals approving registration. The old approach of embedding a persistent token in the URL left that value permanently sitting in logs, history, and QR images |
| Cross-site blocking | `OriginGuardMiddleware` (`server/main.py`) — returns 403 for both HTTP and WS if the Origin isn't itself. The only path that auth/OTP alone can't block (if the browser already has a cookie, auth passes). Also removes the default `*` CORS — opt in via `VT_ALLOWED_ORIGINS` if needed |
| API token auth | The `VT_AUTH_TOKEN` environment variable is a machine-facing token (daemons, hooks, the TUI). Via URL `?token=xxx` or `Authorization: Bearer xxx`. **It is not a second password — the login form rejects it (2026-09-17).** It used to be accepted there, which meant a human-usable credential that `fsh password` could never change or expire; the observed symptom was "I changed the password and the old one still logs me in". A legacy `?token=` link therefore no longer trades itself for a session cookie (it keeps working via the query parameter); register phones with `fsh mobile`'s one-time ticket instead. (Legacy names `VT_TOKEN`/`VT_PASSWORD_HASH`/`VT_SECRET_KEY` are still recognized as fallbacks) |

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
