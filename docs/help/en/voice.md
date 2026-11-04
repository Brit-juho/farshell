# fsh help voice — How voice input works

Voice input has two paths: the browser microphone and the Voice Daemon. Voice-only mode is a browser screen mode.

## 1. Voice Daemon (global desktop hotkey)

    fsh voice

- Runs in the background and works while you're in Notion or a browser
- Default hotkey: `Ctrl+Shift+M` (changeable, see `fsh help hotkeys`)
- Your earphones' Play/Pause button toggles it too (macOS media key)
- After STT, it types the text + Enter straight into the active tmux pane, so it runs immediately (no confirm screen, so "Confirm before sending" doesn't apply; only risky commands are refused)
- Stop it with `fsh stop`

By default the voice target is tmux's most-recent active pane,
but `fsh voice-target <session name>` locks it to a specific session (details in `fsh help target`).

The daemon keeps working while another app (a browser, say) has focus, so the moment recording
starts, a desktop notification such as "-> dev:0.0" shows which pane this utterance will go to
(a lock icon appears when locked). To turn this off, set `VT_VOICE_TARGET_NOTIFY=off`.

## 2. Mobile mic button

The mic button in the web UI's voice bar, or Settings → "Voice and notifications" → "Current session voice" → "Start recording":
- Tap to start recording, tap again to stop
- The first tap prepares the model. When it's ready, tap again to record
- STT → typed into the session/pane that was pinned when recording started. The target stays put even if you switch tabs mid-recording
- 60 seconds at most. If the target disappears, you get the recognized text to copy
- By default the target follows the session you are viewing when recording starts
- **Pin the input target**: in the voice panel (long-press or right-click the mic) or Settings → "Voice and notifications" → "Input target", pick a session and voice goes there even while you view another one. While pinned, the mic button gets a hollow dot and the target name shows "(pinned)" during recording. It is stored in this browser only
- If the pinned session closes, FarShell does not quietly switch to the active session; it blocks recording with "The pinned input target no longer exists". Pick a target again or go back to "Follow the session I'm viewing"

### Confirm before sending

Settings → "Voice and notifications" → "Confirm before sending" (default "Confirm for plain shells"):

| Value | Behavior |
|---|---|
| Send without confirming | Types the transcript immediately (risky commands too) |
| Confirm for plain shells (default) | Straight in for AI agent screens such as Claude or Codex; confirm first for plain shells and for screens it can't identify (vim, ssh, ...) |
| Always confirm | Always confirm |

- In the confirm step the transcript is **not typed**; the panel shows it in an editable box. Edit it and press "Send" to type it, or "Cancel" to send nothing. A pending confirmation lasts 5 minutes and works once.
- If the transcript contains a risky command such as `rm -rf`, `sudo`, `curl ... | sh`, `git push --force`, `git reset --hard`, `mkfs`, `dd of=` or `> /dev/sdX`, it is always confirmed, even on AI agent screens, unless the setting is "Send without confirming". Only English spellings are matched (a Korean transcription of "sudo" is not).
- If the target session disappears while you confirm, nothing is sent elsewhere; only "Copy transcript" remains.
- The Voice Daemon (`fsh voice`) has no confirm screen, so it types straight in as before. It does refuse risky commands and tells you with a notification.

## 3. Voice-only mode

The "Voice only" button in the web UI. It hides the terminal and shows only a big mic button.
Use it when driving with earphones or operating by touch without looking at the screen.

## TTS (voice output)

The settings next to the mic set the current session to Off / Sound only / Read responses aloud. The default is Off.
- It uses the completion hooks and final-response adapters for Claude and Codex. It reads up to the last 200 characters of a response.
- Output goes to this device or the server computer. Only one browser screen receives it; switch with "Move to this device".
- If your phone disconnects, the server Mac doesn't read in its place. Other sessions you've turned on are read in order on the same device.
- If autoplay is blocked, tap "Tap to play". "Play test sound" runs a real TTS test.
- If several agents run in the same folder, link one from its pane with `fsh voice-bind <claude|codex> <conversation ID>`.
- TTS auto: edge-tts (external service) → macOS say. If you don't want anything sent out, choose local voices only.
- Set a minimum work duration and whether to skip error responses per session. Only server audio can be muted depending on screen visibility.
- It works in the browser alone, with no voice daemon. Other tools are supported only if they have a response adapter.

"Voice only on the Mac" has been removed. For a global Mac shortcut, use `fsh voice`.
The daemon also shares the server's STT. For standalone use, set `VT_VOICE_STANDALONE=on` explicitly; reading input back aloud is off by default.
There is no automatic continuous recording. You start and stop every utterance with an explicit toggle.

## STT engines and models

**Speech recognition runs on the computer that runs FarShell (the server).** Phones, other PCs and remote devices
only record audio and upload it. So the server's OS decides which engines you can use.

Pick a model in Settings → "Voice and notifications" → "Voice input". Only models this server can use appear as cards,
and one is marked **"Recommended"**. Each card shows a one-line summary, accuracy (CER%), speed per sentence and memory measurements,
disk size, and what it needs (packages, executables). The measurements use 20 synthetic sentences on Apple Silicon, so treat them as a rough guide.

| Model | Where | Notes |
|---|---|---|
| Qwen3-ASR 0.6B (MLX) | Apple silicon Mac | **Recommended** on a Mac. Stable on instructions that mix English and Korean, about 1GB |
| Qwen3-ASR 1.7B (MLX) | Apple silicon Mac | Highest accuracy, about 2.5GB memory |
| Qwen3-ASR 0.6B (llama.cpp) | Mac, Linux and WSL2 | **Recommended** elsewhere. Runs on the CPU without a GPU, experimental |
| Qwen3-ASR 1.7B (llama.cpp) | Mac, Linux and WSL2 | Larger model, not measured yet |
| Apple speech recognition | macOS 26+ on Apple silicon | Needs the helper and Korean system assets, lightweight |

**Models are never downloaded automatically.** The server, daemon and preloading use only what you've already downloaded, and stop with "Download it first" if it's missing.
A download starts only when you pick a card in Settings and click "Download" in the confirmation dialog that shows the size. Each requirement is
confirmed separately, step by step: (1) Engine — if you approve, the app downloads the llama.cpp executable (about 12-18MB) from the official release and puts it in `~/.vt/engines/`
(pinned version, sha256 verified). For the pip package (mlx-qwen3-asr), it only shows the install command and doesn't install it automatically.
(2) Model files — downloaded from HuggingFace.

- **Custom (advanced)**: for a model not in the list, pick an engine and enter `owner/repo`. Paths and URLs aren't accepted.
- The environment variable `VT_STT_MODEL` takes priority over settings. The server and the voice daemon follow the same choice.
- When idle, the model is unloaded to free memory (`VT_STT_IDLE_SEC`, default 600 seconds). Reloading takes 1-4 seconds on next use.
- The llama.cpp engine runs as a separate process, listens only on 127.0.0.1, and gets a random key per run. It isn't exposed through the tunnel.
- Whisper models chosen in earlier versions are no longer supported. Settings shows a notice and you need to pick a new model.

To delete models and engines: use "Manage downloads" in Settings. It also removes weights that sit in a shared cache outside the folder (`hub/blobs/`),
so the space really comes back. Deleting the selected model shows a warning, and voice input won't work until you download it again.

## Recording upload

The browser records in a supported format (webm/opus → mp4 (AAC) → the browser default, in that order) and uploads it to the server. The server
converts any format to 16kHz mono before handing it to the engine. If the upload fails (network or 5xx), it retries twice; if that still fails,
the "Resend" button lets you send the same recording again. If the same recording arrives twice, the terminal receives it only once.
The `voice.stt timing` entry in the server log shows decode, wait and recognition times.

## FAQ

- **Q. Does STT type only the text, or Enter too?**
  - A. Text + Enter. In the browser, with the default setting ("Confirm for plain shells"), a plain shell or a risky command shows a confirm step first. AI agent screens get the text straight away and it runs immediately.

- **Q. Does it work when claude isn't running?**
  - A. Yes. If the active tmux pane is a shell, the input goes to the shell.

- **Q. Does it work with the codex CLI?**
  - A. Yes. It sends to the active pane without telling claude and codex apart.

- **Q. Does background noise get typed automatically?**
  - A. No. You start and stop explicitly with the hotkey/button toggle.
