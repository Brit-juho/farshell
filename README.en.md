# FarShell

[![Version](https://img.shields.io/badge/version-1.7.0-blue.svg)](./CHANGELOG.md)
[![Changelog](https://img.shields.io/badge/changelog-Keep%20a%20Changelog-orange.svg)](./CHANGELOG.md)
[![한국어](https://img.shields.io/badge/lang-한국어-lightgrey.svg)](./README.md)

**Your macOS/Linux terminal, anywhere — shared tmux sessions, voice input,
a mobile PWA, and Claude Code integration. Free, open source, self-hosted.**

Turn a macOS or Linux machine into a server you can control from anywhere,
by voice or from a browser. (Windows is supported only through WSL2 — no
native support.)

- Terminal on your phone — scan a QR code, land straight in tmux
- Code by voice — a hotkey (Ctrl+Shift+V) dictates into your terminal while you do something else
- Read-only code viewer/diff and a port dashboard, even remotely
- Claude Code integration — TTS summaries on completion + a prompt queue
- Entirely free — no API keys, no subscriptions, open-source STT/TTS

```
https://github.com/Brit-juho/farshell
```

---

## Install

```bash
# Terminal only (lightweight, ~50MB)
curl -fsSL https://raw.githubusercontent.com/Brit-juho/farshell/master/install.sh | bash

# Terminal + voice mode (~1.5GB, Whisper STT + edge-tts TTS)
curl -fsSL https://raw.githubusercontent.com/Brit-juho/farshell/master/install.sh | bash -s voice
```

Or clone and run locally:

```bash
git clone https://github.com/Brit-juho/farshell.git ~/farshell
cd ~/farshell
./install.sh            # terminal only
./install.sh voice      # with voice mode
```

`install.sh` will:
1. Create a Python `venv` (`.venv/`, no conda needed)
2. Install packages for the profile you chose
3. Symlink `~/.local/bin/fsh` (`vt` is also registered for backward compatibility)
4. Generate the `~/.vt.env` config file
5. Update your `PATH` (zsh/bash)

> The Whisper model downloads automatically from Hugging Face on first run (~141MB).

For the full `fsh` command reference and options, see [CLI.md](./CLI.md).

---

## Full documentation

This README is a short introduction. The complete, actively maintained docs
are in Korean — see [README.md](./README.md), [API.md](./API.md),
[ARCHITECTURE.md](./ARCHITECTURE.md), and [CLI.md](./CLI.md). If you'd like
an English translation of a specific section, please open an issue.
