# fsh help troubleshoot — Troubleshooting

## Start here

    fsh doctor

Checks your install and environment automatically (Python, venv, tmux, cloudflared, ffmpeg, ports, fsh CLI, PATH, `~/.vt.env`, auth, push notifications, terminal app, TUI, fzf, and more). It catches most problems.

## Common problems

### `fsh: command not found`
- `~/.local/bin` isn't on your PATH
- Run `source ~/.zshrc` or open a new terminal window
- If that doesn't help, run `~/.local/bin/fsh status` directly to confirm it works

### The `fsh voice` hotkey does nothing
- **macOS**: System Settings → Privacy → Accessibility → check the terminal app you use
- **Linux Wayland**: global hotkeys are blocked for security. Use an X11 session or the mobile mic button
- Conflict with a system shortcut: `fsh hotkey set voice <another key>`
- The daemon may not be running: check with `fsh status`

### A new iTerm window doesn't enter tmux automatically
- The Dynamic Profile isn't registered (`fsh install-profiles`), or
- You haven't added the snippet printed by `fsh shell-init zsh` to `~/.zshrc`
- Enabling just one of the two is enough. Registering both is harmless (there's a guard)
- Windows that are already open aren't affected. It applies from new windows on

### The tmux session is still alive after `fsh stop`
- **This is expected.** tmux persistence is a core feature
- Use `fsh stop --purge` to shut down the tmux server too

### You can't hear TTS
- Output may be going to an external monitor or AirPods. Check the output device
- If the server isn't running, fsh falls back to `say -v Yuna`, which sounds worse. Start the server with `fsh start` to get edge-tts mp3
- Linux: install `espeak-ng`

### No sound on mobile
- Tap the screen once (browser autoplay policy)
- Voice-only mode handles this automatically

### Port conflict
    Change VT_PORT in ~/.vt.env

### The server fails to start
    cat /tmp/vt-server.log

### No Cloudflare Tunnel URL
- Check that cloudflared is running with `fsh status`
- `cat /tmp/cloudflared.log`
- If it isn't installed: `brew install cloudflared` (macOS) / the official binary (Linux)

### Renamed a tmux session but the name didn't change
- Renaming in the web app also renames the linked tmux session
- If it didn't change, the name probably contains unsafe characters (spaces or special symbols)
- Use only letters, digits, dash and underscore

### The Voice Daemon types into the wrong session
- Lock it with `fsh voice-target <session name>`
- Or run `fsh voice-target` to see the current target

### `fsh ssh` can't get the tailnet address
- Check that `tailscale status` shows `Running`. If it shows `Stopped`, run `tailscale up`
- If it isn't installed: `brew install tailscale` (macOS) or the official install script
- `fsh doctor` has a Tailscale item too. Check there first

### `Permission denied (publickey)` when you `ssh ...` from a work laptop
- That device's public key isn't in the MacBook's `~/.ssh/authorized_keys`
- On the work laptop, copy the output of `cat ~/.ssh/id_ed25519.pub`, then on the MacBook run
  `fsh ssh --add-key "ssh-ed25519 AAAA..."`
- Or skip key registration and use `tailscale ssh user@host -- '...'` (your tailnet ACL must allow SSH)

### Tailscale itself won't connect on the corporate network
- This is rare: it happens when the firewall blocks all UDP. Tailscale usually falls back to a 443 (HTTPS) DERP
  relay automatically, so it normally works. `tailscale status` shows whether you're going through a relay
- If it still fails, the corporate proxy may whitelist outbound traffic.
  Ask your network admin (fsh can't fix this)

## Log locations

| Item | Path |
|---|---|
| Server | `/tmp/vt-server.log` |
| Cloudflare Tunnel | `/tmp/cloudflared.log` |
| Voice Daemon | `/tmp/vt-voice-daemon.log` |
| PID files | `/tmp/vt-pids/` |
| Voice target lock | `~/.vt/voice_target` |
| Environment config | `~/.vt.env` |
