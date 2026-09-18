# Voice, clipboard, push

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

## Features

| Feature | Description |
|------|------|
| Voice Daemon | macOS hotkey (Ctrl+Shift+M) → STT → direct input into tmux |
| Hands-free mode | Mobile 🔄 button → continuous record/STT loop |
| Voice-only mode | 🎧 button → hides the terminal and shows only a large mic (for earbud operation) |
| Web Push (P5) | ⋯ menu → "Push Notifications". Existing notifications (`/ws-notify` → Notification API) only work **while a PWA tab is alive**, so turning off the phone screen meant missing "waiting for approval." This fills that gap. No push is sent while at least one WS client is connected (to avoid the same notification arriving twice). **Requirements**: ① https — Service Workers don't even register over plain http ② iOS requires adding to the home screen as a PWA (16.4+; a subscription can't be created from a Safari tab — no workaround). **A subscription is bound to its origin** — if the trycloudflare URL changes, existing subscriptions all die, so each subscription stores its origin, mismatches are excluded from sending, and 404/410 responses are cleaned up on the spot. Notification bodies never contain commands, paths, or code (they'd show on the lock screen). The VAPID key is auto-generated at `~/.vt/vapid.json` (0600) — **deleting it invalidates every existing subscription**. SW registration is handled by `js/swreg.js` (it used to live inside `voice.js`, so the SW never registered at all when voice wasn't installed) |

### Voice Daemon (standalone macOS voice input)

A daemon that types voice input directly into tmux via a hotkey, without needing the server.

```bash
# Run
"$VT_PYTHON" server/voice_daemon.py &

# Usage: Ctrl+Shift+M (toggle) → speak → STT → typed into the active tmux pane
# Requires allowing the terminal app under macOS System Settings → Privacy → Accessibility
```

### Clipboard Daemon (macOS clipboard sync)

When connecting to the web terminal remotely/from mobile, the browser can only access
"that device's" clipboard, so copies made on the Mac (server) side don't automatically
carry over. Two paths cover this:

- **OSC52** (no separate process needed) — copies made inside terminal programs like
  `vim` or `tmux copy-mode` are already carried in the PTY output stream, so
  `frontend/js/terminal.js` intercepts them via
  `term.parser.registerOscHandler(52, ...)` and applies them to the clipboard of
  whatever device has the web page open.
- **Polling daemon** (`fsh clip`) — copies made outside the terminal (Safari, Finder,
  etc.) can't be caught via OSC52, so `server/clipboard_daemon.py` polls
  `NSPasteboard.changeCount` and, on a change, delivers it to the web via
  `POST /api/clipboard/push` → `/ws-notify` broadcast.

```bash
# Run (or use fsh clip)
"$VT_PYTHON" server/clipboard_daemon.py &
```

