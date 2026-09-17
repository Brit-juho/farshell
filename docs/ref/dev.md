# Running, testing, install

> `CLAUDE.md`의 영역 색인에서 갈라져 나온 문서다(2026-09-18). 이 영역을 만지기 전에 읽는다.

### Running the server

```bash
# Method 1: script (automatically uses VT_PYTHON from ~/.vt.env)
./run_server.sh

# Method 2: run directly
cd server
"$VT_PYTHON" -m uvicorn main:app --host 0.0.0.0 --port 7777
```

- The Python path differs per environment — check the currently detected value with `fsh doctor`
- Packages: `requirements-core.txt` (required) + `requirements-voice.txt` (voice mode)

### Access

| Environment | URL |
|------|-----|
| Desktop | `http://localhost:7777` |
| Mobile on the same network | `http://macbook-IP:7777` (get the IP with `ipconfig getifaddr en0`) |
| Mobile via adb | `adb reverse tcp:7777 tcp:7777` → `http://localhost:7777` |
| Remote (from anywhere) | `cloudflared tunnel --url http://localhost:7777` → use the generated HTTPS URL |

### Mobile testing (adb)

```bash
# 1. Port forwarding
adb reverse tcp:7777 tcp:7777

# 2. Open Chrome
adb shell am start -a android.intent.action.VIEW -d "http://localhost:7777" com.android.chrome

# 3. Capture a screenshot
adb shell screencap -p /sdcard/test.png && adb pull /sdcard/test.png /tmp/test.png

# 4. Wake the screen (if locked)
adb shell input keyevent KEYCODE_WAKEUP && adb shell input swipe 540 2000 540 1000 300
```

### E2E test procedure

```bash
# 1. Create a session
SID=$(curl -s -X POST http://localhost:7777/api/sessions -H 'Content-Type: application/json' -d '{}' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 2. Run a command over WebSocket (Python)
python3 -c "
import asyncio, websockets
async def t():
    async with websockets.connect(f'ws://localhost:7777/ws/$SID') as ws:
        await ws.send(b'echo hello\n')
        for _ in range(5):
            try:
                d = await asyncio.wait_for(ws.recv(), timeout=1)
                if b'hello' in d: print('OK'); break
            except: break
asyncio.run(t())
"

# 3. TTS test
curl -s -X POST http://localhost:7777/voice/output \
  -H 'Content-Type: application/json' \
  -d '{"text":"test"}' -o /tmp/tts.mp3 -w "bytes: %{size_download}"

# 4. Check for zombie processes
curl -s -X DELETE "http://localhost:7777/api/sessions/$SID"
ps aux | grep defunct | grep -v grep || echo "No zombies"

# 5. File upload test
echo "hello" > /tmp/test_upload.txt
curl -s -X POST "http://localhost:7777/api/upload?session_id=$SID" \
  -F "file=@/tmp/test_upload.txt"

# 6. Session rename test
curl -s -X PATCH "http://localhost:7777/api/sessions/$SID" \
  -H 'Content-Type: application/json' -d '{"name":"my-session"}'

# 7. Scrollback test — check that previous output is visible after a browser refresh
```

### Installing for new users

**The default path is `./install.sh`** (one-line installer, added 2026-04-14). The steps below are only for when interactive guidance is needed.

```bash
# One-line install (recommended)
./install.sh            # terminal only (~50MB)
./install.sh voice      # terminal + voice mode (~1.5GB)
```

`install.sh` automatically: creates a Python venv → installs packages per profile → symlinks the fsh CLI → creates `~/.vt.env` → updates PATH.

---

### Legacy: interactive install (manual)

Only follow the steps below if install.sh doesn't work, or if you prefer a different environment such as conda/pyenv.

> **Python environment management:** all execution-related paths/ports are managed via `~/.vt.env` (user-local, gitignored) and `config/vt.defaults.env` (committed defaults). When asking the user to choose their environment, have them pick among venv/conda/pyenv/system Python, then record the result in `VT_PYTHON` in `~/.vt.env`.

#### Step 1: Detect OS

```bash
uname -s  # Darwin=macOS, Linux=Linux/WSL2
grep -qi microsoft /proc/version 2>/dev/null && echo "WSL2" || echo "Native"
```

Confirm with the user: "Is this macOS / WSL2 / Linux?"

#### Step 2: Choose install profile

Ask the user:

> Which features would you like to install?
>
> 1. **Terminal only** — terminal access from mobile (~500MB)
>    - FastAPI server + xterm.js web terminal + Cloudflare Tunnel
>    - No voice features
>
> 2. **Terminal + voice mode** — code by voice (~3GB)
>    - Everything above + Whisper STT + edge-tts TTS + Voice Daemon
>    - macOS hotkey (Ctrl+Shift+V), mobile voice input

#### Step 3: Prepare Python environment

Ask the user which environment they want to use (venv / conda / pyenv / system Python). Record the result in `VT_PYTHON` in Step 6.

**Default recommendation — venv:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**If preferring conda:**
```bash
conda create -n fsh python=3.11 -y && conda activate fsh
```

**If preferring pyenv:**
```bash
pyenv install 3.11.7 && pyenv local 3.11.7
```

#### Step 4: Install packages (per profile)

**Terminal only (option 1):**
```bash
pip install -r requirements-core.txt
```

**Terminal + voice (option 2):**
```bash
pip install -r requirements-core.txt -r requirements-voice.txt
```

Additional package for macOS voice mode:
```bash
pip install pyobjc-framework-Cocoa
```

#### Step 5: Register the fsh CLI

```bash
mkdir -p ~/.local/bin
chmod +x bin/fsh
ln -sf "$(pwd)/bin/fsh" ~/.local/bin/fsh
```

Check PATH:
```bash
echo "$PATH" | grep -q "$HOME/.local/bin" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

#### Step 6: Create the config file (`~/.vt.env`, gitignored)

Record the Python path chosen in Step 3. See `config/vt.defaults.env` for the full list of keys.

```bash
# Use the absolute python path from the environment you created in Step 3 (example)
PY_PATH="$(pwd)/.venv/bin/python"   # for venv
# PY_PATH="$(conda info --base)/envs/vt/bin/python"   # if using conda
# PY_PATH="$(pyenv which python)"                       # if using pyenv

cat > ~/.vt.env << EOF
VT_PORT=7777
VT_PYTHON=$PY_PATH
# VT_TOKEN=my-secret-token  # auth for remote access (optional)
EOF
```

#### Step 7: Install cloudflared (for mobile remote access)

```bash
# macOS
brew install cloudflared

# Linux/WSL2
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o ~/.local/bin/cloudflared && chmod +x ~/.local/bin/cloudflared
```

#### Step 8: Register the Claude Code skill (optional)

```bash
mkdir -p ~/.claude/skills/fsh
cp .claude/skills/fsh/SKILL.md ~/.claude/skills/fsh/SKILL.md 2>/dev/null || true
```

#### Step 9: Verify the install

```bash
fsh status
```

Guidance for the user:
- `fsh mobile` — mobile access (QR code)
- `fsh voice` — voice mode (if option 2 was chosen)
- `fsh stop` — shut down

#### Platform-specific notes

**macOS:** for voice mode, the terminal app must be allowed under System Settings → Privacy → Accessibility
**WSL2:** the voice hotkey requires WSLg (Windows 11). If unavailable, use the browser 🎤. PowerShell: `.\bin\fsh.ps1 voice`

---

