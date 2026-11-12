# fsh help target — Voice target lock

Controls which tmux session the Voice Daemon (the `fsh voice` desktop hotkey) sends input to.

## Two modes

### AUTO (default)
- Sends to tmux's `most-recent active pane`
- Goes to the session you last attached to or typed in
- Lets you switch freely between sessions
- Downside: it's hard to tell up front where the input will land

### LOCK
- Pins voice input to one named session
- Voice goes only to the locked session, even when another session is active
- Safer

## Commands

    fsh voice-target              # current mode + available sessions
    fsh voice-target dev          # LOCK to the dev session
    fsh voice-target --auto       # clear the lock, back to AUTO

The session name and that session's own key (its tmux id and creation time) are stored in `~/.vt/voice_target`. The daemon reads it on every utterance, so no restart is needed. Because the key is used, renaming the locked session keeps the lock, and once that session ends a new session with the same name does not receive the input.

## Scenarios

### Scenario A: code by voice while using another app
    fsh voice                # start the daemon
    fsh voice-target dev     # lock to dev
    [work in another app]
    [Ctrl+Shift+M] "git status"
    → always goes to the active pane of the dev session

### Scenario B: switching between several terminals
    fsh voice                # start the daemon
    [default AUTO]
    [Ctrl+Shift+M] "ls"     → goes to the session you looked at last
    [attach p9 in iTerm]
    [Ctrl+Shift+M] "npm run" → now goes to p9

### Scenario C: keep input out of a session that's building
    fsh voice-target dev     # lock to dev
    [p9 is running npm test while you browse other sessions]
    [Ctrl+Shift+M] "next"
    → does not go to p9 (the test session stays safe); goes to dev

## If the locked session disappears

When the session is killed, the daemon falls back to AUTO automatically and logs a warning.
To recover, create a new session and lock again.

## What about mobile?

On mobile, the tab you're viewing is the target, so no lock is needed.
Switching tabs changes the target.
