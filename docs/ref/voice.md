# Voice, clipboard, push

## Features

| Feature | Description |
|------|------|
| Voice Daemon | macOS hotkey (Ctrl+Shift+M) → STT → direct input into tmux |
| Session TTS | Per session: off, completion sound or read responses aloud, played in order on the chosen screen or on the server |
| Voice-only mode | Settings → Voice and notifications (음성 및 알림) → "Voice-only mode (음성 전용 모드)" button → fills the screen with a single large mic (for earbud operation) |
| Web Push | ⋯ menu → "Push notifications (푸시 알림)". Existing notifications (`/ws-notify` → Notification API) only work **while a PWA tab is alive**, so with the phone screen off you would otherwise miss "waiting for approval"; Web Push covers that case. No push is sent while at least one WS client is connected (to avoid the same notification arriving twice). **Requirements**: (1) https — Service Workers don't even register over plain http (2) iOS requires adding to the home screen as a PWA (16.4+; a subscription can't be created from a Safari tab — no workaround). **A subscription is bound to its origin** — if the trycloudflare URL changes, existing subscriptions all die, so each subscription stores its origin, mismatches are excluded from sending, and 404/410 responses are cleaned up on the spot. Notification bodies never contain commands, paths, or code (they'd show on the lock screen). The VAPID key is auto-generated at `~/.vt/vapid.json` (0600) — **deleting it invalidates every existing subscription**. SW registration is handled by `js/push/swreg.js` (kept outside `voice.js` so the SW still registers when voice isn't installed) |

### Voice Daemon (standalone macOS voice input)

A global hotkey starts recording and the text is typed into the tmux pane pinned when recording started. By default it shares the server's STT.
To use it without a server, set VT_VOICE_STANDALONE=on explicitly. On failure it never falls back to standalone input by itself.

```bash
# Run
"$VT_PYTHON" server/voice_daemon.py &

# Usage: Ctrl+Shift+M (toggle) → speak → STT → typed into the active tmux pane
# Requires allowing the terminal app under macOS System Settings → Privacy → Accessibility
```

### Per-session voice policy

In Settings → Voice and notifications (음성 및 알림) you choose off, completion sound or read responses aloud for the current session.
Without opening settings, you can check whether the server model and required items are installed and whether they are loaded in memory from the mic button in the right dock (long-press or right-click; a status dot appears at the corner only when setup is needed, in progress or failed) and from the command palette's "Voice input status (음성 입력 상태)". The status lookup never downloads or loads a model.
Pressing the mic shows setup, recording and the recognition result in the same status panel. While recording it shows the input target pinned at the start and the elapsed time, and "Stop recording (녹음 종료)" sends it. The recognition result is never cleared automatically.
Even on screens where the mobile dock is hidden, "Start recording (녹음 시작)" in this setting runs the same input action.
The default is off, and "New session defaults (새 세션 기본값)" is copied only to a session that is checking its voice policy for the first time. Existing sessions don't change. Output goes to this device or the server computer. Only one screen owns the browser output;
another screen takes it over with "Move to this device (이 기기로 옮기기)". When the connection ends, the server does not read on the screen's behalf.
The voice queue plays in order per device (up to 10 items, 2 minutes), and blocked autoplay is shown as "Tap to play (터치하여 재생)".
A new recording stops playback on that output and pauses the queue for a moment. say/afplay of other apps is not killed.

Claude and Codex Stop events go through the common server `voice_sessions` policy. The per-tool adapters of `voice_completion`
read the Claude transcript and Codex `response_item`/`event_msg` or `last_assistant_message`.
It reads up to the last 200 characters of the response and does no separate LLM summary. If identification is ambiguous, nothing is read aloud.
You can bind explicitly from that pane with `fsh voice-bind <claude|codex> <conversation ID>`.
A tool without a final-response adapter has no TTS support. The output idle watch does not generate speech.

STT gets a target token when recording starts and uses the same pane for stop and resend. If the target disappears,
the text is not sent to another pane and copying the recognized text is offered instead. For the confirmation step before input, see "Confirm before sending" below (both upload paths are the same). The first click prepares the model; the next one records.
Browser recording is at most 60 seconds and an upload is 4MiB. The old local-microphone request returns 410.
Continuous recording (restarting by itself after every utterance) is not supported. Only the chunk path has server VAD auto stop ("Auto stop (자동 종료)", default off) and
partial results while recording ("Show partial results (부분 결과 표시)", default on) — see "Processing while recording" below. The earbud media key is off by default on every connection.

TTS auto is edge-tts → macOS say, and it makes local speech as a WAV the browser can play, passing along the real audio MIME. edge sends the text outside.
Choosing local uses only say, with no external request. Choosing edge never switches to another engine on failure.
When the server fails during a completion hook, there is no fallback that reads aloud directly through the OS. The existing global read-aloud setting never turns a new session on automatically.
For the protocol see [API.md](../../API.md).

### Pinning the input target (browser, per device)

The default is "Follow the session I'm viewing (현재 보는 세션 따라가기)" — when recording starts, `window.activeId` is the target. If you **pin** a specific session in the voice status panel (`#voice-pin-select`) or in
"Input target (입력 대상)" under Settings → Voice and notifications (음성 및 알림), input goes to that session regardless of focus.

- The value is `vt_voice_pin` in this browser's localStorage (the tmux session **name**). If storage is blocked, `window.__vtVoicePinMemory` makes it work on this screen only.
  Why the name and not the web PTY id: the PTY id changes every time a screen is opened, so the pin would be lost after a reload.
- At recording time it finds the open session id with that name in `window.sessions` and sends it to `/voice/target` (if several have the same name, the active screen wins).
  Remote-host sessions and non-tmux sessions are not candidates.
- If the pinned session isn't open, it **does not substitute the active session** and blocks recording before the microphone, model setup and the server request
  ("The pinned input target no longer exists" (고정한 입력 대상이 없습니다), mic button status dot error). The server also never sends a token whose target disappeared to another pane.
- The implementation is `frontend/js/voice/target-pin.js`. The voice.js bundle (`recording.js`) and the app.js bundle (`status-panel.js` and settings) each take their own copy,
  so it holds no module state and does not import `core/store.js` (warning at the top of `recording.js`).
- The read-aloud (TTS) policy stays per session. The Voice Daemon doesn't look at this setting.

### Confirm before sending (`voice.confirm`)

The confirmation step before input is the global setting `voice.confirm` (`off | shell | on`, default `shell`;
`workspace.json` settings, and a value the server doesn't know is treated as `shell`), and `server/voice_confirm.py` makes the policy decision.

| Value | Behavior |
|---|---|
| `off` | Never confirm. Dangerous patterns aren't confirmed either |
| `shell` | Type straight away if the pane is an AI agent. Confirm for a plain shell or when **the kind is uncertain** (vim, ssh, python, tmux lookup failure) |
| `on` | Always confirm |

- Pane detection applies the `agent_detector` verdict (`KNOWN_AGENTS`, including the scan of child processes under a shell) to the one target pane (`voice_confirm.pane_kind`).
  Only `agent` is typed straight away; `shell` and `unknown` are confirmed.
- **Dangerous patterns** are always confirmed regardless of pane kind unless the setting is `off`. The list lives in one place, `voice_confirm.DANGEROUS_PATTERNS`,
  and `server/tests/test_voice_confirm.py` locks it (rm -r/-f, sudo, curl/wget | sh, git push -f/--force, git reset --hard, git clean -f,
  mkfs, dd of=, `> /dev/…` (null, stdout and stderr excluded), chmod/chown -R, fork bomb). The check runs after NFKC normalization and only on the English spelling.
- When confirmation is needed, `/voice/input` and the completing request of the chunk path (`/voice/chunk`) **only recognize and do not type.** The response carries `pending_confirm: true`, `text`, `confirm_token`,
  `confirm_reason` (`shell|unknown|always|dangerous`), `confirm_ttl_sec` and `target_name`. The web shows it in the panel's edit box (`#voice-confirm`),
  and "Input (입력)" calls `POST /voice/inject {token, text, voice_id}`. "Cancel (취소)" sends nothing.
- Only clients that can draw a confirmation screen go through the confirmation step — the web sends `X-Voice-Confirm: 1`. A caller without this header (the Voice Daemon,
  old clients) has no way to confirm, so it types straight away as before, except that dangerous patterns are not typed and are reported as `error` (when the setting isn't `off`).
- Validation in `/voice/inject`: the token's owning principal (`voice_targets.principal`) must match; the target pane is re-validated with `valid()` (if it vanished: `target_gone` 409,
  token discarded, never sent to another pane); control characters are stripped (`paste_prepare.strip_unsafe_control_chars`, same as the input path); the length cap is
  4000 characters (413); empty text is 400; and `voice_id` must match the one at issue time. Concurrent requests with the same token let only one through (429 `busy`), and once input finishes
  a re-request with the same token and the same `voice_id` gets `duplicate` (no input). A missing, expired or someone else's token all return the same 409 `expired`.
  Even if the connection drops and it is cancelled, the text may already have been typed, so the token is not given back. Only a failed input-tool call (`send_failed` 502) can be retried with the same token.
- The confirmation token TTL is 300 seconds (time for the user to edit), single use, and held only in server memory (gone after a restart). The waiting limit is 256.
- When a new recording starts, the client discards the previous pending confirmation.
- **Both paths use one function**: `_deliver_text(owner, vid, target, sid, text, confirm_ui)` in `routes/voice.py` handles the verdict (`_confirm_verdict`),
  confirmation token issue and pane input, and both `/voice/input` and the completion of `/voice/chunk` go through `_voice_dedup_run` → `_voice_input_once` → `_deliver_text`.
  `confirm_ui` is that request's `X-Voice-Confirm: 1` for `/voice/input`, and for the chunk path it is a per-recording value (`Recording.confirm_ui`; it turns on if the header arrives with any
  chunk, because the request that ends the recording can be an earlier chunk arriving late. Only the safer direction ever turns on). The web chunk uploader
  (`chunk-upload.js`) puts the header on every chunk. The confirmation token is bound to that recording's `X-Voice-Id`.
- **Resend**: a pending-confirmation result also has `text`, so it stays in `_voice_done` (120 seconds) and in the chunk record's result (`rec.result`). A resend or final resend with the same `X-Voice-Id`
  does not run STT or the verdict again and returns the same `confirm_token` with `duplicate: true` (no new token, no pane input).
  The actual input happens once per token, in `/voice/inject`.
- **Client**: `uploadWithRetry` in `recording.js` receives the MediaRecorder result and the PCM session's `result()` in the same branch, so a chunk-path
  `pending_confirm` also goes to the same confirmation box (`confirm.js`). Auto stop (`endpoint`) takes the same route as `stopRecording()`, so the final after it is the same.
  Partial results (`partial`) are display-only regardless of the confirmation mode and are never typed. Input target pinning also uses the same `resolveInputTarget()`
  → `/voice/target` on both paths.

### Clipboard Daemon (macOS clipboard sync)

When connecting to the web terminal remotely/from mobile, the browser can only access
"that device's" clipboard, so copies made on the Mac (server) side don't automatically
carry over. Two paths cover this:

- **OSC52** (no separate process needed) — a copy request carried in the PTY output
  stream. `frontend/js/term/selection.js` intercepts it via
  `term.parser.registerOscHandler(52, ...)` and `frontend/js/term/osc52.js` decides:
  - **tmux session** (remote panes too — they carry `tmuxName`): only copies made by
    tmux itself arrive (copy-mode, `set-buffer -w`). Programs inside the pane (vim,
    `printf`) do **not** reach the browser; the old wording "vim inside tmux sends OSC52"
    was wrong. Measured with `config/vt-tmux.conf` on tmux 3.7c:

    | `set-clipboard` | tmux copy-mode / `set-buffer -w` | OSC52 sent by a program in the pane |
    |---|---|---|
    | `external` (shipped, `allow-passthrough off`) | forwarded | dropped |
    | `on` | forwarded | forwarded |
    | `off` | dropped | dropped |
  - **Plain terminal** (no tmux, "open on Mac" off): the output reaches us as-is, so
    untrusted output (a file you `cat`, a server you ssh into) could overwrite the
    clipboard (pastejacking). **Blocked by default**, one warning toast per session;
    allow it with Settings › Terminal › "Allow clipboard writes from non-tmux terminals
    (OSC 52)" (`terminal.osc52Untrusted`, device scope).
  - Always ignored: scrollback replay, `mouse.autocopyOnSelect` off, empty/`?` payloads,
    invalid base64, and anything over 1 MiB (1,048,576 bytes decoded).
  - The copy toast shows the first 40 characters (newlines as ⏎) and the length.
- **Polling daemon** (`fsh clip`) — copies made outside the terminal (Safari, Finder,
  etc.) can't be caught via OSC52, so `server/clipboard_daemon.py` polls
  `NSPasteboard.changeCount` and, on a change, delivers it to the web via
  `POST /api/clipboard/push` → `/ws-notify` broadcast.

```bash
# Run (or use fsh clip)
"$VT_PYTHON" server/clipboard_daemon.py &
```

## STT structure

- **STT runs only on the server OS.** The browser only records and uploads to `/voice/input` (or the chunk path `/voice/chunk`); there is no client-side recognition.
  So the usable engines are decided by the server host (`stt_engines/env.py` detects them).
- `server/stt_service.py` handles selection (`voice.sttModel`, with the `VT_STT_MODEL` environment variable taking priority; if empty, the per-environment recommendation), engine startup,
  serialization and the catalog/prepare/delete responses, and `server/stt_engines/` implements the engines (MLX Qwen3, llama.cpp Qwen3, Apple Speech).
- **No silent downloads**: `load`/`preload`/`transcribe` never use the network. Downloading happens only in `POST /voice/stt/setup`
  (a request the settings UI makes after a confirmation dialog). The llama.cpp executable is version-pinned and sha256-verified (`llama_release.py`).
- Whisper (faster-whisper) is not supported — at the same accuracy it is CPU-only on a Mac and slow (1.14 s per sentence vs 0.1 s for MLX),
  and the llama.cpp CPU engine covers every platform. On a low-spec server (4GB RAM or less), llama.cpp at about 2GB may be a burden.
- **Model pinning (supply chain)**: HF models in the catalog are fetched by **commit hash (revision) and per-file SHA-256**, not by name.
  The table is in one place, `stt_engines/model_pins.json` (read by `stt_engines/pins.py`). A download runs `snapshot_download(revision=<40 chars>,
  allow_patterns=<files in the table>)`, hash-checks every file, and on a mismatch deletes **only the mismatching files of that revision's snapshot** and reports an error
  (other revisions and the user's old cache are untouched). `load` also checks the hash right before use — an old cache that was already downloaded is reused as is if the hash matches
  (no network), and the result is recorded with (size, mtime) in `<state>/engines/pin_verified.json` so it isn't hashed again.
  The table uses the HF API `sha` and the LFS `oid`; small non-LFS files were downloaded from that commit and hashed. Changing the table is a decision to change the model,
  so it is done only through a code change (review). **A model entered by hand (`custom:`) is not pinned** (downloaded by name only, and the UI shows 「검증 안 됨」, "not verified").
- It also deletes the shared cache outside the folder (`hub/blobs/`, `hfcache.py`), and counts size without double-counting links.
- `/voice/input` skips duplicate resends by `X-Voice-Id` (last 120 seconds). Per-stage timings are in the response `timing` and the server log.

## Chunk upload (PCM)

So that a tunnel dropping during a long recording doesn't lose the whole thing, a browser that supports AudioWorklet uploads pieces while recording.
The endpoint schema and error codes are in `/voice/chunk` in [API.md](../../API.md). Here only the structure and the reasons are written.

- **Client** (`frontend/js/voice/`, all in the voice bundle): `pcm-capture.js` (AudioContext + worklet; if the input sample rate isn't
  16kHz it resamples by area average in `pcm.js`) → `pcm.js` (PCM16 LE, 16000-byte = 500ms chunks) → `chunk-upload.js`
  (one request at a time, cleaned up by cumulative `ackSeq`, a failure resends the same seq) → bound together by `pcm-session.js`. `recording.js` only
  branches. The worklet itself is the static file `frontend/js/voice/pcm-worklet.js` (a classic script outside the bundle, requested only when recording starts), and
  `pcm-worklet-src.js` holds only the name, block size and address constants. This is because the app document CSP is `script-src 'self'`, which blocks Blob URL modules (`docs/ref/auth.md`). When the worklet is unsupported, the context isn't secure or `addModule` fails,
  it falls back by handing the same stream to the MediaRecorder + `/voice/input` path (the console `[voice] 캡처 경로:` debug log says which one).
  iOS Safari needs the AudioContext to be created inside a gesture, so it is created at the very start of `startRecording` (before any await).
- **Resend limits**: on a transient failure (network, 429/502/503/504) it keeps accumulating the recording after 3 backoff attempts and resends the same seq for up to 25 seconds
  (the server discards after 30 seconds of silence, so within that). If the server lost its state (response ackSeq < sent seq), it fails without retrying.
  After a failure, "Resend (다시 보내기)" retries with the remaining chunks (within a 15-second TTL).
- **Server** (`server/voice_chunks.py`): per recording a `(principal, voice_id)` state — the contiguous pieces `parts[seq]`, the `pending` pieces waiting while an earlier seq is missing,
  accumulated bytes and the last activity time. All of it changes in one step with no await, so there is no lock. Limits: 60 seconds total (1,920,000B,
  strict), 5 seconds per piece, 1000 pending / 30 seconds, seq < 4000, 64 concurrent recordings. A new recording by the same principal discards the previous open recording
  (STT is serial, so one). The 30-second silence discard and expiry tombstones are cleaned up on every request and by a 10-second periodic task (`sweep_loop`).
  When complete, it is wrapped as 16kHz mono WAV and handed to the **same dedup, recognition, confirmation and injection path** as `/voice/input` (`_voice_dedup_run` → `_deliver_text`) —
  the `_voice_done` cache, the 409 on a fingerprint collision and a concurrent final joining the same recognition all behave the same. If recognition raises (429 and so on), the pieces are kept
  so the same final can be resent to try again. A finished recording releases the piece memory and keeps only the result for 120 seconds.
- **Snapshots**: `Recording.contiguous_pcm()` is a copy of the whole contiguous front part, and `tail_pcm(n)` is a copy of at most the last n bytes (at a piece boundary).
  Partial recognition uses only `tail_pcm` (at most 20 seconds = 640KB).
- **Level meter**: `meter.js` (pure functions) computes a 0–1 level and a clipping indicator from RMS and an adaptive noise floor (rise 40ms, fall 450ms
  time constants; the floor drops quickly, rises only over 15 seconds and never exceeds -26dBFS). The voice bundle
  sends the `vt:voice-level` event and the app bundle's `status-panel.js` only draws the `#voice-meter` bar (the two bundles meet only through the event). With `prefers-reduced-motion`
  it drops to 5 steps and 4 updates per second. The MediaRecorder fallback path has no meter. The elapsed time is the existing "N s / 60 s" line.

## Processing while recording (server VAD auto stop and partial results)

Only the chunk path (`/voice/chunk`) has this. The MediaRecorder fallback and the Voice Daemon don't. Response fields are in [API.md](../../API.md).

- **Structure**: `server/voice_vad.py` (the pure verdict class) ← `server/voice_live.py` (a `Live` per recording, attached to `Recording.live`) ←
  `routes/voice.py`. At the first piece of a new recording it reads the settings `voice.autoStop` and `voice.partialResults` (workspace.json `settings`, global)
  once and fixes them for that recording. With both off, VAD doesn't run either. An exception in `Live` never blocks chunk intake (logged only).
  When the collector completes (final), cancels, expires, replaces or finishes the record, or the server shuts down (`voice_chunks.reset()`), `Live.close()` cancels the in-flight
  partial recognition task and discards the result.
- **VAD** (`voice_vad.EnergyVad`): it compares the 20ms-frame RMS with an adaptive noise floor. The first 200ms is calibration (the minimum RMS becomes the floor,
  not counted as speech), the floor falls with a 300ms time constant when it gets quiet and rises with a 15-second time constant only on non-speech frames (the upper and lower bounds are
  the same -66 to -26dBFS as `meter.js`). A frame that is `floor + 9dB` or more and -45dBFS or more is speech. Speech is confirmed once 300ms accumulates (counting restarts if the gap in between
  exceeds 200ms), and one second of quiet after confirmation is the endpoint. Time is counted by received audio length (independent of network delay). The reasoning for the constants is in the file comments.
  Known limitation: if the noise rises more than 9dB all at once, that noise is taken as speech and no endpoint comes (the user stops it by hand — the safer side
  compared with cutting off wrongly). A silence longer than 1 second between sentences gets cut (that is why it is off by default).
- **Auto stop**: when on and at an endpoint, the ack response carries `endpoint: true`. The client (`makeAckHandler` in `pcm-session.js`) sends the final once per recording
  through the same route as `stopRecording()`. The server never ends the recording by itself (the remaining pieces are on the client). If speech has never been confirmed,
  there is no endpoint either and the 60-second cap is unchanged.
- **Partial results**: after speech is confirmed, when new audio has grown by 1.5 seconds or more and there was new speech in between, the latest 20 seconds (`tail_pcm`) are wrapped as WAV and passed to
  `stt_service.transcribe_partial`. This function **does not wait** — it returns None (skips and tries again on the next piece) if it can't take the engine lock, if there are queued requests (`_waiting`)
  or if the chosen model isn't in memory. It doesn't load a model and doesn't change the queue length, `last_error` or `timing`. There is at most one in-flight partial recognition per recording. The result rides on every later ack as `partial {text, seq, clipped}`. The client sends only a larger seq as a
  `vt:voice-partial` event, and the status panel shows it in `#voice-partial` in the secondary text color (only while recording or recognizing; cleared on the final
  result, an error or a new recording; never typed; no aria-live). The log keeps only `voice.partial audio_ms=… stt_ms=… chars=…` and never the
  text. Recording continues even when it fails.
- **STT load**: partial recognition recognizes up to 20 seconds of audio once more roughly every 1.5 seconds while you speak. The next interval is `max(1.5 s,
  previous partial recognition time × 3)`, so engine occupancy is bounded to 1/3 or less. A partial recognition that has already entered the engine can't be stopped, so a final recognition
  arriving in the meantime (from the same recording or another user) waits for **at most one partial recognition**. The per-sentence times in the benchmark below (MLX 0.6B 0.10 s,
  CPU llama.cpp 0.25 s) are for short sentences and a 20-second window takes longer — the real time of a 20-second window hasn't been measured yet. On a low-spec server, turn "Show partial results (부분 결과 표시)" off. Why it defaults to on: the occupancy cap and the non-blocking skip leave the accuracy of the final
  result unchanged and put a bound on latency, and the felt cost is small on a single-user Mac (recommended MLX).
- **Barge-in**: stopping TTS on the start of speech detected by the server VAD is **not implemented**. At the start of recording `pauseVoice(true)` already stops this screen's
  playback and pauses the server queue (`voice_barge`), and that lasts for the recording — pausing the same screen again at speech start gains nothing. Stopping the server
  computer's output (`destination: server`) or playback on other devices would change the policy "only the recording screen's output stops", so it
  isn't done. Instead the remaining gap is closed in the client (`tts.js`): if `/ws-voice` reconnects during recording (the server's `disconnect` clears that screen's pause), it sends the pause again, and speech that arrives in that gap is not played and is reported as `cancelled` (120-second safety release).
- **Tests**: `server/tests/test_voice_vad.py` (synthetic sine wave plus noise), `test_voice_live.py` (route responses, task cleanup, lock contention, fake STT), `test_routes_voice_chunk_confirm.py` (confirm-before-send on the chunk path),
  `frontend/tests/voice-ack-handler.test.js`, `voice-pcm-flow.test.js`, `voice-status-panel.test.js` and `voice-session-flow.test.js`.

## STT engine benchmark

`scripts/stt_bench.py` measures CER, speed and memory per model on the same Korean samples. **The models the app uses are measured as
`model:<catalog id>` with no adapter** (it calls the real engine code, so measurement and real use can't diverge). The adapter convention for comparing candidate engines is in
`scripts/stt_bench_engines/README.md`. Candidate libraries are installed in a **separate temporary venv**, not the project venv, for measuring. This tool never downloads models silently.

```bash
.venv/bin/python scripts/stt_bench.py make-samples /tmp/stt-bench/samples   # 20 sentences synthesized with `say`
.venv/bin/python scripts/stt_bench.py report model:qwen3-asr-0.6b-mlx model:qwen3-asr-0.6b-gguf \
    --samples /tmp/stt-bench/samples --python <venv python for the engine>   # a separate process per engine
```

Recording real sentences and putting them in the same folder as `name.wav` (16kHz mono) + `name.txt` (reference text) is far more trustworthy.

Results (Apple Silicon, 20 synthesized sentences, average after model load):

| Engine | CER % | Seconds per sentence | Peak RSS MB | Notes |
|---|---|---|---|---|
| Qwen3-ASR 1.7B 8bit (MLX) | 3.0 | 0.19 | 2550 | model 2.3GB |
| Qwen3-ASR 0.6B 8bit (MLX) | 6.1 | 0.10 | 1160 | model 971MB |
| Apple SpeechAnalyzer ko-KR | 6.4 | 0.06 | 36 | needs the Swift helper, excludes system service memory |
| SenseVoice-Small int8 (sherpa-onnx) | 6.4 | 0.03 | 733 | word spacing is off |
| faster-whisper small | 6.6 | 1.14 | 1211 | (unsupported) CPU-only, so slow |
| faster-whisper base | 6.7 | 0.35 | 648 | (unsupported) |
| faster-whisper small Korean (ghost613) | 6.8 | 1.14 | 1482 | (unsupported) no difference from small |
| Qwen3-ASR 0.6B GGUF Q8 (llama.cpp, **CPU only**) | 6.1 | 0.25 | 1940~2340 | cross-platform candidate. Measured without a GPU; memory depends on context size |
| mlx-whisper small (fp16) | 7.2 | 0.12 | 889 | (not adopted) same size as faster-whisper small, about 10x faster |
| sherpa-onnx Zipformer ko | 24.5 | 0.03 | 331 | weak in this sentence domain |
| Moonshine ko Tiny | 54.7 | 0.06 | 346 | output cuts off midway |

Cautions when reading:
- This is **one synthesized voice (Yuna, 4 speeds) and 20 sentences**. The differences among the top results above (6.1–7.2%) are not meaningful differences in this sample.
- The reference text is written in Hangul, so recognition results written in English such as `API` or `import` are counted as wrong. That penalty is too harsh for output an LLM will read.
- Yuna is the only Korean voice in `say` that produces sound (other voices give empty files). The sample check (`_check_audible`) rejects silence immediately.
- Even for models of the same size, MLX (Metal) was about 10x faster than faster-whisper (CPU; Metal isn't supported on a Mac).
- llama.cpp's audio input is labeled **"experimental stage, may have reduced quality"** in its startup log. The output has the form `language Korean<asr_text>body`, so only the body must be cut out and used.
- Measurements are Apple Silicon CPU. We couldn't check the CPU speed of an x86 PC.
