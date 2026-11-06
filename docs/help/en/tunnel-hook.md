# Publish the tunnel URL automatically (VT_TUNNEL_HOOK)

An anonymous Cloudflare tunnel **gets a new URL whenever the tunnel process is recreated.**
`fsh restart` keeps the tunnel, so restarting only the server keeps the address.

```
https://example-one.trycloudflare.com   <- today
https://example-two.trycloudflare.com         <- tomorrow
```

To connect from your phone or another device, you have to copy the new URL somewhere each time.
Where that "somewhere" is depends on you: a personal Notion page, a Slack DM to yourself,
an ntfy topic, a Telegram bot, or just a text file in iCloud Drive.

**fsh doesn't know about any particular service.** Instead, it calls a command you specify
once whenever the URL changes. That command does the rest.

> If you want a fixed address, the answer isn't a hook. It's **an entrance whose address doesn't change**:
> - **Named tunnel** (`fsh tunnel setup`) — a Cloudflare account + a domain you own
> - **ngrok reserved domain** — `VT_TUNNEL_PROVIDER=ngrok` + `VT_NGROK_DOMAIN=...`
>
> **With a fixed ngrok domain, this hook fires only when the address actually changes.**
> It isn't called for a `start` that reuses the same URL.
> Calling it yourself with `fsh tunnel hook` always goes through.

---

## The contract

```bash
# ~/.vt.env (in your home directory, gitignored — nothing you write here goes into the repo)
VT_TUNNEL_HOOK='your-script or a one-line shell command'
```

The hook is called on: `fsh start` (tunnel starts),
`fsh tunnel expose`, `fsh tunnel unexpose`, `fsh stop` (tunnel ends),
and `fsh tunnel hook` (manual).

| Channel | Content |
|---|---|
| **stdin** | `label<TAB>URL` lines. The main tunnel comes first, then any ports you `expose`d |
| `$VT_TUNNEL_EVENT` | `start` / `expose` / `unexpose` / `stop` / `manual` |
| `$VT_TUNNEL_MAIN_URL` | The main tunnel URL (empty if there is none) |

### The `stop` event

`stop` is **the only event that arrives with empty stdin.** It doesn't mean "there's no URL to announce".
It means **"there's nowhere to connect to now"**, so reflect that in wherever you publish.

An anonymous tunnel's address dies every time you take it down. If you don't handle this event, the page
you published **keeps pointing at a dead URL**, and whoever reads it can't tell whether the hook is broken or the tunnel is simply off.

```bash
# branch like this inside your hook
if [ "$VT_TUNNEL_EVENT" = "stop" ]; then
  echo "Disconnected" > ~/Documents/fsh-urls.txt
else
  cat > ~/Documents/fsh-urls.txt
fi
```

The hook fires only **when a tunnel was up.** Running `fsh stop` again when it's already down
doesn't call the hook.

Example stdin:

```
터미널 (VT)	https://example-one.trycloudflare.com
개발 앱	https://example-two.trycloudflare.com
```

**If the hook fails, the tunnel keeps working.** A non-zero exit code only prints a warning.
If no hook is set, nothing happens.

To try your hook right now:

```bash
fsh tunnel hook      # shows what will be passed and actually runs it once
```

---

### When the new URL opens late

A freshly created anonymous tunnel may not answer right away. To avoid publishing a dead URL, the
hook checks on `start` that the URL responds, for about 60 seconds. If it still doesn't, it does not
give up: it **keeps waiting in the background and publishes once** (every 15 seconds by default, up
to about 10 minutes). If the URL changes while it waits (the tunnel restarted), the old URL is dropped,
and `fsh stop` takes the waiter down with everything else. Only one runs at a time. The log is
`$VT_RUN_DIR/vt-tunnel-hook-retry.log`.

| Variable | Default | Meaning |
|---|---|---|
| `VT_TUNNEL_RETRY_TRIES` | 40 | How many background re-checks |
| `VT_TUNNEL_RETRY_INTERVAL` | 15 | Seconds between re-checks |

## Examples by method

These are **suggestions.** Adapt them to the tools you use rather than copying them as is.

### 1. Write to a file (simplest, zero dependencies)

```bash
VT_TUNNEL_HOOK='cat > ~/Documents/fsh-urls.txt'
```

Put it in an iCloud/Dropbox folder and you can open it from your phone as is.

### 2. ntfy push (no account needed)

```bash
VT_TUNNEL_HOOK='curl -s -T - -H "Title: VT URL" https://ntfy.sh/my-secret-topic'
```

Install the ntfy app on your phone and subscribe to the same topic. You get a notification every time the URL changes.
It's the same service as fsh's own notifications (`VT_NOTIFY_URL`), but the purpose differs, so use a separate topic.

> The topic name is the password. Avoid guessable names.

### 3. Slack (personal DM / channel)

Create an Incoming Webhook, then:

```bash
VT_TUNNEL_HOOK='jq -Rs "{text: .}" | curl -s -X POST -H "Content-Type: application/json" -d @- "$SLACK_WEBHOOK_URL"'
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

### 4. Telegram bot

```bash
VT_TUNNEL_HOOK='curl -s -X POST "https://api.telegram.org/bot$TG_TOKEN/sendMessage" --data-urlencode "chat_id=$TG_CHAT" --data-urlencode "text=$(cat)"'
```

### 5. Notion page

There's no official script. A Notion API token and page structure are personal settings,
so they don't belong in the repo. If you build one yourself, here's the approach:

1. Create an Internal integration at https://www.notion.so/profile/integrations and get the secret
2. On the page you'll publish to, open `•••` → **Connections** and add that integration
   (skip this and the API returns 404 — the most common mistake)
3. In the hook script, scan the page's top-level blocks, **find the one block with your marker and PATCH it**
   to overwrite it. If there isn't one, append a new block.
   - If you only append, blocks pile up every time the tunnel starts. Finding and updating is the key.
   - If you delete the block by accident, it's recreated on the next run, so it heals itself.
4. Keep the token in `~/.vt.env` (`chmod 600`) and keep the script **outside the repo**,
   for example in `~/.config/vt/hooks/`.

```bash
VT_TUNNEL_HOOK='"$VT_PYTHON" ~/.config/vt/hooks/notion_publish.py'
```

### 6. Do nothing

This is the default. Often it's enough to check with `fsh status` or `fsh tunnel list` when you need to,
and connect straight from your phone with the QR code from `fsh mobile`.

---

## Notes

- **The hook value is run by a shell.** Only put commands you trust there.
- **`~/.vt.env` is parsed, not executed.** Save values with the repo's `lib/vt_env.sh`.
  The command saved in `VT_TUNNEL_HOOK` runs separately when the hook is called.
- For the main web UI, set up auth with `fsh password` or `VT_AUTH_TOKEN`.
  Extra ports have different access rights depending on the share mode (`fsh help tunnel`).
  Don't publish an `fsh mobile` URL that includes a device registration ticket.
- The hook runs **synchronously** inside the tunnel start flow. Hand long-running work
  off to the background from your script.
