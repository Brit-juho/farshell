# Event log: notifications, errors and requests

Code: `frontend/js/core/{event-log,event-log-send,redact,global-errors}.js`, `frontend/js/panels/event-log/`,
`frontend/js/ui/toast.js`, `server/client_log.py`, `server/routes/client_log.py`. Endpoints: [`API.md`](../../API.md)
«Client event log». UI rules: [`DESIGN.md`](../../DESIGN.md) «Toasts» and «Status bar».

## Why it exists

A toast disappears after 3–6 seconds and an error toast used to carry only `e.message`: the stack, the HTTP status, the
request path and the server's error code were thrown away. On a phone there are no developer tools, so a failure that
was missed could not be looked at again. Every event that reaches a person (toast, API failure, script error, lost
connection) is now also written to one log, and the failures are copied to the server.

The way a toast is *shown* did not change (`showToast`, see DESIGN.md). What was missing was a record.

## What is recorded

One shape everywhere (`v: 1`): `id, ts, last, count, level, source, msg, detail?, req?, rel?, relGuess?, ctx`.

| Source | Event | Level |
|--------|-------|-------|
| `toast` | every `showToast` call (`key` + `info` is a progress line and goes in as `debug`) | its own: `error` `warn` `info` `success` |
| `api` | `vtFetch`/`vtFetchConditional`/`apiFetch`/`timedFetch` failure: network refusal, deadline, 5xx | `warn` |
| `api` | 4xx, or a request the caller aborted (404/409 are normal flow for many callers) | `debug` |
| `js` | `error` and `unhandledrejection` (AbortError ignored), a script/stylesheet that failed to load (a stale chunk after a deploy), a CSP violation | `error` |
| `console` | `reportError(scope, err)` — replaces the old bare `console.error('[FarShell x]', e)`; viewer degrade paths use `logEvent({level:'warn'})` | `error` / `warn` |
| `net` | reachability verdict changes (`offline`/`gateway`/`hung`) and recovery with the seconds it was down | `warn` / `info` |
| `ws` | the terminal reconnect overlay appeared (a drop shorter than the 1.5 s grace is not recorded) and recovery | `warn` / `info` |
| `ui` | an error state drawn by `ui/empty.js errorState` or by an authz/share `errorText` | `warn` |

**Request metadata** (`req`): `method, path, status, ms, code, reason, host`. `code`/`reason` are the server JSON's `error`
and `reason` only. **Request and response bodies are never recorded** — they can hold file contents or screens.
`api.js` attaches the same object to the thrown `Error` as `.request`; the thrown error and the return values are
unchanged. `showToast(msg, 'error', { error: e })` takes the stack and `.request` from `e`.

**Toast ↔ request link.** With `{ error: e }` the link is certain (`rel`). Without it, an error/warn toast links to the
newest `api` entry from the last 3 seconds and sets `relGuess` — the panel labels it «guessed». Do not present a guess
as fact.

**Merging.** The same level + source + message + method + path + status within 60 s is one line with `count` and `last`.
Ring buffer: 500 entries, `debug` at most 200 (`debug` goes first). It lives in memory and `sessionStorage`
(`vt.eventlog.v1`, ≤400 KB), so it survives a reload. The log never throws and never records its own failures
(recording a send failure would loop), and `fetch` options `noLog: true` keeps a request out of it.

**Two copies of the module.** `voice.js` is a separate bundle and pulls `core/api.js`, so `event-log.js` is also inside it.
The first copy to load owns `window.vtLogEvent`/`window.vtReportError`; a later copy hands its calls to the owner instead
of keeping a second buffer that nothing would read or send. Voice code uses the `window.vtLogEvent` bridge.

## Redaction

`core/redact.js` (browser) and `client_log.redact` (server) apply the same three rules, and both tests read
`frontend/tests/fixtures/redact-cases.json` — change a rule in all three places.

- `name=value` where `name` contains `token|ticket|otp|secret|passw(or)?d|api_?key|auth|signature|session|cookie` → `name=***`
- JSON `"key": "value"` with such a key → `"key": "***"`
- `Bearer <value>` → `Bearer ***`

The server also rewrites the home directory to `~`. Over-redaction is deliberate; hashes with an innocent name (a commit
sha) are left alone.

## Sending to the server

`core/event-log-send.js`. Only `error` and `warn`. A new entry is queued; a repeat is queued again only when its count
reaches a power of two (2, 4, 8, …). 2 s batches, ≤50 entries and ≤48 KB. Failure → 5 s → 15 s → 60 s; nothing is sent
while reachability says the server is unreachable. `pagehide`/hidden flushes with `keepalive`. 401/403 turns sending off
for the tab (a machine-token screen or a server with no password cannot use it); 429 follows `Retry-After`; 413 halves the
batch; 400 and a single 413 drop the batch. The request goes to the server that served the page, never through a host
relay (`timedFetch` changes no address or header); `req.host` records which Mac a failed request was meant for.

## Server store

`$VT_STATE_DIR/client-log/YYYY-MM-DD.jsonl`, folder 0700, files 0600. Accepts only `error`/`warn`, only known keys, cut
to length (msg 500, detail 4000), `ts` within the last 7 days. Limits: 64 KiB and 50 entries per request (also enforced on
a chunked stream), 600 lines per minute for the whole process (429), 7 days and 20 MB in total (oldest day first). What is
dropped is counted and written as one `dropped N` line on the next write. The same `id` can appear on several lines (a
growing `count`); reading keeps the newest line per id.

**Authorization:** `human` in `server/authz/route_table.py` — a logged-in person only. A machine token, an agent on the
unix socket and another Mac get 403, and the routes are in neither `relay_policy.py` nor the socket allowlist. Keep them
out: an agent must not read, write or erase this log. Cross-site requests are stopped by `OriginGuardMiddleware`.

An agent that needs the failures can read the files directly (`~/.vt/client-log/`).

## The panel

`panels/event-log/panel.js`, chunk `eventlog` (action `eventlog.open`, argument = the entry to unfold). Opened from the
palette («Log»), the status bar `log` chip, the status-bar message slot, the «Details» button on an error/warn card, and on a
phone from Settings › Tools › Log. **This screen** = this tab's ring buffer, every level; **All screens** = the server files
(errors and warnings, other devices included), read without a relay. Opening the panel marks everything seen.

The `log` chip (`shell/hud-data.ts logChip`, kind `log`) is an alert kind like `hooks`: outside the layout setting, only
present while an error or warning is unseen, red when there is an error.

## Core size

`app.js` + `core.js` must stay within 300 KiB (`frontend/tests/build-output.test.js`). The recorder, the redaction and the
sender live in core (they must catch boot errors); the panel and its helpers are a lazy chunk. Check the margin after
touching any of them.

## Tests

`frontend/tests/{event-log,event-log-send,event-log-panel,redact,toast-eventlog,api-eventlog}.test.js`,
`server/tests/{test_client_log,test_routes_client_log}.py`, and four e2e tests in `tests/e2e/test_smoke.py`
(card → panel, status-bar click, script error → chip → stack, server file 0600 → «All screens»).
