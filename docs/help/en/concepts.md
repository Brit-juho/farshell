# fsh help concepts — Core concepts

FarShell is easier to use once you understand the three layers of the terminal ecosystem.

## 1. The three layers

    ┌───────────────────────────────────────────┐
    │ Layer 1: terminal emulator (GUI app)      │
    │   iTerm2, Terminal.app, Ghostty, gnome-   │
    │   terminal, konsole, alacritty, kitty...  │
    │   → the GUI program that draws the        │
    │     "black window"                        │
    └───────────────────────────────────────────┘
                         ↓ creates a PTY for
    ┌───────────────────────────────────────────┐
    │ Layer 2: process running in that window   │
    │   - usually a shell (zsh, bash, fish)     │
    │   - or vim, less, python, claude, ...     │
    │   - and ★ tmux ★ can run here too         │
    └───────────────────────────────────────────┘
                         ↓ tmux creates its own PTY
    ┌───────────────────────────────────────────┐
    │ Layer 3: the shell inside tmux            │
    │   zsh, bash, etc. — running in the PTY    │
    │   tmux created                            │
    └───────────────────────────────────────────┘

The short version:
- **iTerm2** = the GUI that draws the black window
- **zsh / bash** = the command interpreter (type `ls` and it runs)
- **tmux** = a container that holds many shells and keeps them alive (not a kind of shell ❌)

## 2. What tmux does

### Persistence

Close the iTerm window and the sessions inside the tmux server stay alive, including whatever is
running in them (vim, claude, ...). Attach again and everything is restored as you left it.

### Multi-client attach

A desktop iTerm, a phone and a web browser can attach to the same tmux session at the same time.
They all see the same screen, and all can type.

→ **This is FarShell's core infrastructure.** Without tmux, mobile and voice features don't work.

### Multiplexing

One tmux session holds multiple windows and panes.

    tmux session "dev"
    ├─ window 0
    │  ├─ pane 0  ← claude is running
    │  └─ pane 1  ← npm run dev
    └─ window 1
       └─ pane 0  ← vim

## 3. The isolated socket `-L fsh`

FarShell uses a separate tmux server (socket at `/tmp/tmux-{uid}/fsh`).

    tmux ls          # your usual sessions (default socket)
    tmux -L fsh ls    # FarShell-only sessions (fsh socket)

→ The two worlds are fully separate. Your existing workflow is untouched.

## 4. Auto-attach

For new terminal windows to attach to FarShell's tmux automatically, register one of these:

### Option A — shell init snippet

    echo 'eval "$(fsh shell-init zsh)"' >> ~/.zshrc

Every new zsh passes five guards, then attaches automatically. This covers iTerm Cmd+N, splits and new tabs.

### Option B — terminal profile

    fsh install-profiles

Registers with iTerm Dynamic Profiles, Ghostty, WezTerm and others. The default command for new windows becomes tmux.

## 5. What `fsh stop` doesn't stop

`fsh stop` shuts down only the server, tunnel and voice daemon.
**tmux sessions stay alive.** That's the point of persistence.

For a full shutdown: `fsh stop --purge`

## 6. FAQ

- **Q. Do iTerm windows that were already open join FarShell automatically after I run fsh?**
  - A. ❌ No. The zsh guard only runs when a new window starts. Attach existing windows manually with `tmux -L fsh attach -t dev`.

- **Q. Should I start every iTerm window in tmux from the beginning?**
  - A. ✅ Yes, that's the recommended setup. `fsh install-profiles` or `fsh shell-init` does it.

- **Q. Is it a burden to keep a tmux server running all the time?**
  - A. About 5–10MB of memory and near-zero CPU when idle. When the system sleeps, it sleeps along with your other user processes. No real cost.
        For a full shutdown, run `fsh stop --purge`.
