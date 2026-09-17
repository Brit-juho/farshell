# Auth, devices, boundary values

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

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
